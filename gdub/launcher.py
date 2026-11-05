"""Launcher window: every gdub command as a button/form (no terminal needed).

Tabs: Project (extract, reset) - Record (devices, start recorder, actor notes) -
Export - Quality check. The recorder itself opens in its own window/process.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .export import ExportSettings, export_all
from .extract import extract_paths
from .lines import write_lines_csv
from .project import Project
from .qc import QCSettings, format_report, run_qc
from .reset import reset_lines
from .session import Session

PREFS = Path.home() / ".gdub.json"
CODECS = ["pcm_s16le", "adpcm_ms", "pcm_s24le"]


class UserError(Exception):
    """Something the user can fix; shown in a dialog."""


def load_prefs() -> dict:
    try:
        return json.loads(PREFS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_prefs(d: dict) -> None:
    try:
        PREFS.write_text(json.dumps(d), encoding="utf-8")
    except Exception:
        pass


def open_path(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    elif sys.platform.startswith("win"):
        os.startfile(str(path))          # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])


class Worker:
    """Runs a function in a thread; messages/results are delivered on the Tk thread."""

    def __init__(self, root, on_msg, on_done):
        self.root, self.on_msg, self.on_done = root, on_msg, on_done
        self.q: queue.Queue = queue.Queue()

    def start(self, fn) -> None:
        def run():
            try:
                self.q.put(("done", fn(lambda *m: self.q.put(("msg", m))), None))
            except Exception as exc:                 # reported in the UI
                self.q.put(("done", None, exc))
        threading.Thread(target=run, daemon=True).start()
        self._poll()

    def _poll(self) -> None:
        try:
            while True:
                item = self.q.get_nowait()
                if item[0] == "msg":
                    self.on_msg(*item[1])
                else:
                    self.on_done(item[1], item[2])
                    return
        except queue.Empty:
            pass
        self.root.after(80, self._poll)


class Launcher:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Gothic Dub")
        root.geometry("940x720")
        root.minsize(760, 560)
        self.prefs = load_prefs()
        self.logs: dict[str, tk.Text] = {}
        self.last_qc = ""

        top = ttk.Frame(root, padding=(10, 8))
        top.pack(fill="x")
        ttk.Label(top, text="Project folder:").pack(side="left")
        self.proj_var = tk.StringVar(value=self.prefs.get("project", ""))
        e = ttk.Entry(top, textvariable=self.proj_var)
        e.pack(side="left", fill="x", expand=True, padx=6)
        e.bind("<Return>", lambda _e: self.refresh())
        e.bind("<FocusOut>", lambda _e: self.refresh())
        ttk.Button(top, text="Choose / create…", command=self.choose_project).pack(side="left")
        ttk.Button(top, text="Open in Finder/Explorer", command=self.open_project).pack(side="left", padx=(6, 0))
        self.status = ttk.Label(root, text="", padding=(12, 0), justify="left")
        self.status.pack(fill="x")

        self.nb = ttk.Notebook(root)
        self.nb.pack(fill="both", expand=True, padx=10, pady=10)
        self._project_tab()
        self._record_tab()
        self._export_tab()
        self._qc_tab()
        self.refresh()
        self.refresh_devices(quiet=True)

    # ------------------------------------------------------------ helpers
    def _tab(self, title: str) -> ttk.Frame:
        f = ttk.Frame(self.nb, padding=12)
        f.columnconfigure(1, weight=1)
        self.nb.add(f, text=title)
        return f

    def _log(self, name: str, height=10) -> tk.Text:
        t = tk.Text(self.nb.nametowidget(self.nb.tabs()[-1]), height=height, wrap="word")
        self.logs[name] = t
        return t

    def log(self, name: str, msg: str, clear: bool = False) -> None:
        t = self.logs[name]
        if clear:
            t.delete("1.0", "end")
        t.insert("end", msg + "\n")
        t.see("end")

    def guard(self, fn):
        def wrapped(*a, **k):
            try:
                return fn(*a, **k)
            except UserError as exc:
                messagebox.showwarning("Gothic Dub", str(exc))
            except Exception as exc:
                messagebox.showerror("Gothic Dub", f"{type(exc).__name__}: {exc}")
        return wrapped

    def project(self, need_lines: bool = False) -> Project:
        v = self.proj_var.get().strip()
        if not v:
            raise UserError("Choose a project folder first.")
        p = Project(Path(v).expanduser())
        if need_lines and not p.lines_csv.exists():
            raise UserError("This project has no line list yet - use 'Extract lines' on the Project tab.")
        return p

    def lines_session(self):
        p = self.project(need_lines=True)
        try:
            return p, p.load_lines(), Session.load(p)
        except ValueError as exc:
            raise UserError(f"lines.csv problem: {exc}")

    def choose_project(self) -> None:
        d = filedialog.askdirectory(title="Choose or create the project folder", mustexist=False)
        if d:
            self.proj_var.set(d)
            self.refresh()

    def open_project(self) -> None:
        p = self.project()
        p.root.mkdir(parents=True, exist_ok=True)
        open_path(p.root)

    # ------------------------------------------------------------ refresh
    def refresh(self) -> None:
        v = self.proj_var.get().strip()
        if not v:
            self.status.config(text="No project selected.")
            return
        self.prefs["project"] = v
        save_prefs(self.prefs)
        p = Project(Path(v).expanduser())
        try:
            if not p.lines_csv.exists():
                self.status.config(text="No lines.csv yet - extract the line list from your .d scripts "
                                        "(Project tab).")
                return
            lines, s = p.load_lines(), Session.load(p)
            done = sum(s.is_done(ln.id) for ln in lines)
            takes = len(list(p.raw_dir.rglob("*.wav"))) if p.raw_dir.is_dir() else 0
            self.status.config(text=f"{len(lines)} lines · {len({l.npc for l in lines})} character(s) · "
                                    f"{done}/{len(lines)} lines recorded · {takes} take file(s) in raw/")
            voices = sorted({l.speaker for l in lines})
            npcs = sorted({l.npc for l in lines}, key=str.lower)
            self.rec_voice["values"] = ["all"] + voices
            self.rec_npc["values"] = ["all"] + npcs
            self.exp_voice["values"] = ["all"] + voices
            self.exp_npc["values"] = ["all"] + npcs
            self.act_voice["values"] = voices
            self._reset_targets(lines)
            self._load_export_defaults(p)
        except Exception as exc:
            self.status.config(text=f"Could not read project: {exc}")

    # ------------------------------------------------------------ project tab
    def _project_tab(self) -> None:
        f = self._tab("1  Project")
        ttk.Label(f, text="Line list from the game scripts", font=("", 12, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(f, text="Script encoding:").grid(row=1, column=0, sticky="w", pady=4)
        self.enc_var = tk.StringVar(value="auto")
        ttk.Combobox(f, textvariable=self.enc_var, values=["auto", "cp1250", "cp1252", "utf-8"],
                     width=10, state="readonly").grid(row=1, column=1, sticky="w")
        bf = ttk.Frame(f)
        bf.grid(row=2, column=0, columnspan=3, sticky="w", pady=4)
        ttk.Button(bf, text="Extract from a folder of .d scripts…",
                   command=self.guard(lambda: self.extract("folder"))).pack(side="left")
        ttk.Button(bf, text="Extract from .d files…",
                   command=self.guard(lambda: self.extract("files"))).pack(side="left", padx=6)

        ttk.Separator(f).grid(row=3, column=0, columnspan=3, sticky="ew", pady=12)
        ttk.Label(f, text="Reset takes", font=("", 12, "bold")).grid(row=4, column=0, columnspan=3, sticky="w")
        ttk.Label(f, text="Scope:").grid(row=5, column=0, sticky="w", pady=4)
        self.scope_var = tk.StringVar(value="Everything")
        sc = ttk.Combobox(f, textvariable=self.scope_var, state="readonly", width=16,
                          values=["Everything", "One character", "One line"])
        sc.grid(row=5, column=1, sticky="w")
        sc.bind("<<ComboboxSelected>>", lambda _e: self.refresh())
        ttk.Label(f, text="Which:").grid(row=6, column=0, sticky="w")
        self.target_var = tk.StringVar()
        self.target_box = ttk.Combobox(f, textvariable=self.target_var, state="readonly", width=60)
        self.target_box.grid(row=6, column=1, sticky="w")
        self.keep_actors = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="Keep actor/mic notes", variable=self.keep_actors).grid(
            row=7, column=1, sticky="w", pady=4)
        ttk.Button(f, text="Reset…", command=self.guard(self.reset)).grid(row=8, column=1, sticky="w")
        ttk.Label(f, foreground="#777", wraplength=700, justify="left", text=
                  "Takes are moved to <project>/_trash/<date-time>/ - nothing is deleted. Close the "
                  "recorder before resetting.").grid(row=9, column=0, columnspan=3, sticky="w", pady=6)
        t = self._log("project", 9)
        t.grid(row=10, column=0, columnspan=3, sticky="nsew")
        f.rowconfigure(10, weight=1)

    def _reset_targets(self, lines) -> None:
        scope = self.scope_var.get()
        if scope == "One character":
            vals = sorted({l.npc for l in lines}, key=str.lower)
        elif scope == "One line":
            vals = [f"{l.id}   {l.text[:50]}" for l in lines]
        else:
            vals = []
        self.target_box["values"] = vals
        self.target_box.config(state="readonly" if vals else "disabled")
        if self.target_var.get() not in vals:
            self.target_var.set(vals[0] if vals else "")

    def extract(self, mode: str) -> None:
        p = self.project()
        if mode == "folder":
            src = filedialog.askdirectory(title="Folder containing the .d dialogue scripts")
            paths = [Path(src)] if src else []
        else:
            paths = [Path(x) for x in filedialog.askopenfilenames(
                title="Choose .d dialogue scripts", filetypes=[("Daedalus scripts", "*.d"), ("All", "*")])]
        if not paths:
            return
        self.run_extract(p, paths)

    def run_extract(self, p: Project, paths: list[Path]) -> None:
        res = extract_paths(paths, self.enc_var.get())
        if not res.lines:
            raise UserError("No dialogue lines found in those scripts.")
        if p.lines_csv.exists() and not messagebox.askyesno(
                "Replace line list?", "This project already has a lines.csv. Replace it?\n\n"
                "Existing recordings stay valid - they are matched by line ID."):
            return
        p.root.mkdir(parents=True, exist_ok=True)
        write_lines_csv(res.lines, p.lines_csv)
        self.log("project", f"{len(res.lines)} lines, {len({l.npc for l in res.lines})} character(s) "
                            f"from {res.files} file(s) -> {p.lines_csv}", clear=True)
        for w in res.warnings:
            self.log("project", "warning: " + w)
        self.refresh()

    def reset(self) -> None:
        p, lines, s = self.lines_session()
        scope, tgt = self.scope_var.get(), self.target_var.get()
        if scope == "One character":
            sel, what = [l for l in lines if l.npc == tgt], f"all takes of {tgt}"
        elif scope == "One line":
            lid = tgt.split()[0] if tgt else ""
            sel, what = [l for l in lines if l.id == lid], f"all takes of line {lid}"
        else:
            sel, what = lines, "ALL takes of ALL lines"
        if not sel:
            raise UserError("Nothing selected to reset.")
        if not messagebox.askyesno("Reset", f"Reset {what}?\n\nFiles go to the _trash folder in the "
                                            "project; chosen takes are forgotten."):
            return
        moved, trash = reset_lines(p, s, sel, clear_actors=scope == "Everything" and not self.keep_actors.get())
        self.log("project", f"Reset {what}: {moved} file(s) moved to {trash}" if trash
                 else f"Reset {what}: no take files found", clear=True)
        self.refresh()

    # ------------------------------------------------------------ record tab
    def _record_tab(self) -> None:
        f = self._tab("2  Record")
        ttk.Label(f, text="Recorder", font=("", 12, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
        self.in_dev = tk.StringVar(value="default")
        self.out_dev = tk.StringVar(value="default")
        ttk.Label(f, text="Microphone / input:").grid(row=1, column=0, sticky="w", pady=3)
        self.in_box = ttk.Combobox(f, textvariable=self.in_dev, state="readonly", values=["default"])
        self.in_box.grid(row=1, column=1, sticky="ew")
        ttk.Button(f, text="Refresh devices", command=self.refresh_devices).grid(row=1, column=2, padx=6)
        ttk.Label(f, text="Playback / output:").grid(row=2, column=0, sticky="w", pady=3)
        self.out_box = ttk.Combobox(f, textvariable=self.out_dev, state="readonly", values=["default"])
        self.out_box.grid(row=2, column=1, sticky="ew")
        ttk.Label(f, text="Input channel:").grid(row=3, column=0, sticky="w", pady=3)
        self.chan_var = tk.StringVar(value="1")
        ttk.Spinbox(f, from_=1, to=32, textvariable=self.chan_var, width=5).grid(row=3, column=1, sticky="w")
        ttk.Label(f, text="Pre-roll (ms):").grid(row=4, column=0, sticky="w", pady=3)
        self.pre_var = tk.StringVar(value="300")
        ttk.Spinbox(f, from_=0, to=2000, increment=50, textvariable=self.pre_var, width=6).grid(
            row=4, column=1, sticky="w")
        ttk.Label(f, text="Start with voice:").grid(row=5, column=0, sticky="w", pady=3)
        self.rec_voice_var = tk.StringVar(value="all")
        self.rec_voice = ttk.Combobox(f, textvariable=self.rec_voice_var, state="readonly", width=8, values=["all"])
        self.rec_voice.grid(row=5, column=1, sticky="w")
        ttk.Label(f, text="Start with character:").grid(row=6, column=0, sticky="w", pady=3)
        self.rec_npc_var = tk.StringVar(value="all")
        self.rec_npc = ttk.Combobox(f, textvariable=self.rec_npc_var, state="readonly", width=30, values=["all"])
        self.rec_npc.grid(row=6, column=1, sticky="w")
        self.sim_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="Simulate (no microphone - just try the recorder)", variable=self.sim_var
                        ).grid(row=7, column=1, sticky="w", pady=3)
        ttk.Button(f, text="Open recorder", command=self.guard(self.start_recorder)).grid(
            row=8, column=1, sticky="w", pady=6)
        ttk.Label(f, foreground="#777", wraplength=700, justify="left", text=
                  "On a Mac, allow microphone access for this app when macOS asks "
                  "(System Settings > Privacy & Security > Microphone).").grid(
            row=9, column=0, columnspan=3, sticky="w")

        ttk.Separator(f).grid(row=10, column=0, columnspan=3, sticky="ew", pady=10)
        ttk.Label(f, text="Actor / mic notes (saved in session.json)", font=("", 12, "bold")).grid(
            row=11, column=0, columnspan=3, sticky="w")
        self.act_voice_var = tk.StringVar()
        ttk.Label(f, text="Voice:").grid(row=12, column=0, sticky="w", pady=3)
        self.act_voice = ttk.Combobox(f, textvariable=self.act_voice_var, state="readonly", width=8)
        self.act_voice.grid(row=12, column=1, sticky="w")
        self.act_voice.bind("<<ComboboxSelected>>", lambda _e: self._load_actor())
        self.act_vars = {}
        for i, key in enumerate(["name", "mic", "distance", "gain", "notes"]):
            ttk.Label(f, text=key.capitalize() + ":").grid(row=13 + i, column=0, sticky="w", pady=2)
            self.act_vars[key] = tk.StringVar()
            ttk.Entry(f, textvariable=self.act_vars[key]).grid(row=13 + i, column=1, sticky="ew")
        ttk.Button(f, text="Save notes", command=self.guard(self.save_actor)).grid(
            row=18, column=1, sticky="w", pady=6)

    def refresh_devices(self, quiet: bool = False) -> None:
        try:
            import sounddevice as sd
            devs = sd.query_devices()
        except Exception as exc:
            if not quiet:
                messagebox.showwarning("Audio devices", f"Could not list audio devices:\n{exc}")
            return
        self.in_box["values"] = ["default"] + [f"{i}: {d['name']}" for i, d in enumerate(devs)
                                               if d["max_input_channels"] > 0]
        self.out_box["values"] = ["default"] + [f"{i}: {d['name']}" for i, d in enumerate(devs)
                                                if d["max_output_channels"] > 0]

    def recorder_args(self, p: Project) -> list[str]:
        a = [sys.executable, "-m", "gdub", "record", "-p", str(p.root)]
        if self.sim_var.get():
            a.append("--simulate")
        for var, flag in ((self.in_dev, "--device"), (self.out_dev, "--output-device")):
            v = var.get()
            if v and v != "default":
                a += [flag, v.split(":")[0]]
        if self.rec_voice_var.get() not in ("", "all"):
            a += ["--voice", self.rec_voice_var.get()]
        if self.rec_npc_var.get() not in ("", "all"):
            a += ["--npc", self.rec_npc_var.get()]
        a += ["--channel", self.chan_var.get() or "1", "--preroll", self.pre_var.get() or "300"]
        return a

    def start_recorder(self) -> None:
        p = self.project(need_lines=True)
        subprocess.Popen(self.recorder_args(p))

    def _load_actor(self) -> None:
        p = self.project()
        a = Session.load(p).actors.get(self.act_voice_var.get(), {})
        for k, v in self.act_vars.items():
            v.set(a.get(k, ""))

    def save_actor(self) -> None:
        p = self.project()
        if not self.act_voice_var.get():
            raise UserError("Choose a voice first.")
        s = Session.load(p)
        s.set_actor(self.act_voice_var.get(), **{k: v.get() for k, v in self.act_vars.items()})
        messagebox.showinfo("Saved", f"Notes for voice {self.act_voice_var.get()} saved.")

    # ------------------------------------------------------------ export tab
    def _export_tab(self) -> None:
        f = self._tab("3  Export")
        d = ExportSettings()
        ttk.Label(f, text="Game speech folder:").grid(row=0, column=0, sticky="w", pady=3)
        self.out_var = tk.StringVar(value=self.prefs.get("export_out", ""))
        ttk.Entry(f, textvariable=self.out_var).grid(row=0, column=1, sticky="ew")
        ttk.Button(f, text="Choose…", command=lambda: self._pick_dir(self.out_var)).grid(row=0, column=2, padx=6)
        self.x = {
            "sample_rate": tk.StringVar(value=str(d.sample_rate)), "codec": tk.StringVar(value=d.codec),
            "channels": tk.StringVar(value=str(d.channels)), "highpass_hz": tk.StringVar(value=str(d.highpass_hz)),
            "normalize": tk.StringVar(value=d.normalize), "target_db": tk.StringVar(value=str(d.target_db)),
            "denoise": tk.BooleanVar(value=d.denoise),
        }
        rows = [("Sample rate (Hz):", "sample_rate", ["22050", "32000", "44100", "48000"]),
                ("Format / codec:", "codec", CODECS), ("Channels:", "channels", ["1", "2"]),
                ("High-pass (Hz, 0 = off):", "highpass_hz", None),
                ("Loudness mode:", "normalize", ["rms", "peak", "none"]),
                ("Target level (dBFS):", "target_db", None)]
        for i, (label, key, vals) in enumerate(rows, start=1):
            ttk.Label(f, text=label).grid(row=i, column=0, sticky="w", pady=2)
            w = (ttk.Combobox(f, textvariable=self.x[key], values=vals, width=14) if vals
                 else ttk.Entry(f, textvariable=self.x[key], width=16))
            w.grid(row=i, column=1, sticky="w")
        ttk.Checkbutton(f, text="Light noise reduction", variable=self.x["denoise"]).grid(
            row=7, column=1, sticky="w")
        ttk.Label(f, text="Only voice / character:").grid(row=8, column=0, sticky="w", pady=2)
        sub = ttk.Frame(f)
        sub.grid(row=8, column=1, sticky="w")
        self.exp_voice_var, self.exp_npc_var = tk.StringVar(value="all"), tk.StringVar(value="all")
        self.exp_voice = ttk.Combobox(sub, textvariable=self.exp_voice_var, state="readonly", width=6, values=["all"])
        self.exp_voice.pack(side="left")
        self.exp_npc = ttk.Combobox(sub, textvariable=self.exp_npc_var, state="readonly", width=28, values=["all"])
        self.exp_npc.pack(side="left", padx=6)
        self.dry_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="Dry run (check only, write nothing)", variable=self.dry_var).grid(
            row=9, column=1, sticky="w")
        bf = ttk.Frame(f)
        bf.grid(row=10, column=1, sticky="w", pady=6)
        self.exp_btn = ttk.Button(bf, text="Export", command=self.guard(self.export))
        self.exp_btn.pack(side="left")
        ttk.Button(bf, text="Save these as project defaults", command=self.guard(self.save_export_defaults)
                   ).pack(side="left", padx=6)
        ttk.Button(bf, text="Open speech folder", command=self.guard(self.open_export)).pack(side="left")
        ttk.Label(f, foreground="#777", wraplength=700, justify="left", text=
                  "Defaults (44.1 kHz, mono, 16-bit) are a guess - set what your game needs, then save "
                  "as project defaults. Needs ffmpeg installed.").grid(row=11, column=0, columnspan=3, sticky="w")
        self.exp_bar = ttk.Progressbar(f)
        self.exp_bar.grid(row=12, column=0, columnspan=3, sticky="ew", pady=4)
        self._log("export", 10).grid(row=13, column=0, columnspan=3, sticky="nsew")
        f.rowconfigure(13, weight=1)

    def _pick_dir(self, var: tk.StringVar) -> None:
        d = filedialog.askdirectory(title="Choose folder")
        if d:
            var.set(d)

    def export_settings(self) -> ExportSettings:
        try:
            x = self.x
            return ExportSettings(
                sample_rate=int(x["sample_rate"].get()), codec=x["codec"].get().strip(),
                channels=int(x["channels"].get()), highpass_hz=float(x["highpass_hz"].get()),
                normalize=x["normalize"].get(), target_db=float(x["target_db"].get()),
                denoise=bool(x["denoise"].get()))
        except ValueError:
            raise UserError("Check the export numbers (sample rate, channels, high-pass, target level).")

    def _load_export_defaults(self, p: Project) -> None:
        try:
            cfg = p.config().get("export", {})
        except Exception:
            return
        for k, v in cfg.items():
            if k in self.x:
                self.x[k].set(v)

    def save_export_defaults(self) -> None:
        p = self.project()
        cfg = self.export_settings()
        data = p.config()
        data["export"] = {k: getattr(cfg, k) for k in ("sample_rate", "codec", "channels", "highpass_hz",
                                                         "normalize", "target_db", "denoise")}
        p.root.mkdir(parents=True, exist_ok=True)
        p.config_file.write_text(json.dumps(data, indent=1), encoding="utf-8")
        messagebox.showinfo("Saved", f"Saved to {p.config_file}")

    def open_export(self) -> None:
        out = self.out_var.get().strip()
        if not out or not Path(out).is_dir():
            raise UserError("The speech folder doesn't exist yet.")
        open_path(Path(out))

    def export(self) -> None:
        p, lines, s = self.lines_session()
        out = self.out_var.get().strip()
        if not out:
            raise UserError("Choose the game's speech folder first.")
        cfg = self.export_settings()
        self.prefs["export_out"] = out
        save_prefs(self.prefs)
        voice = None if self.exp_voice_var.get() in ("", "all") else self.exp_voice_var.get()
        npc = None if self.exp_npc_var.get() in ("", "all") else self.exp_npc_var.get()
        dry = self.dry_var.get()
        self.exp_btn.state(["disabled"])
        self.log("export", "Exporting…", clear=True)

        def work(emit):
            return export_all(p, lines, s, Path(out), cfg, voice, npc, dry,
                              progress=lambda i, n, row: emit(i, n, row))

        def on_msg(i, n, row):
            self.exp_bar.config(maximum=n, value=i)
            if row.status == "error":
                self.log("export", f"ERROR {row.id}: {row.detail}")

        def on_done(res, exc):
            self.exp_btn.state(["!disabled"])
            if exc:
                self.log("export", f"Failed: {exc}")
                return
            self.log("export", f"Done: exported {res.count('ok')}, skipped {res.count('skipped')} "
                               f"(no chosen take), errors {res.count('error')}")
            lim = [r.id for r in res.rows if r.limited]
            if lim:
                self.log("export", f"{len(lim)} line(s) could not reach the target level without "
                                   "exceeding the peak ceiling.")

        Worker(self.root, on_msg, on_done).start(work)

    # ------------------------------------------------------------ qc tab
    def _qc_tab(self) -> None:
        f = self._tab("4  Quality check")
        ttk.Label(f, text="Exported speech folder (optional):").grid(row=0, column=0, sticky="w", pady=3)
        self.qc_dir = tk.StringVar()
        ttk.Entry(f, textvariable=self.qc_dir).grid(row=0, column=1, sticky="ew")
        ttk.Button(f, text="Choose…", command=lambda: self._pick_dir(self.qc_dir)).grid(row=0, column=2, padx=6)
        ttk.Label(f, text="Expected characters/second:").grid(row=1, column=0, sticky="w", pady=3)
        self.cps_var = tk.StringVar(value="13")
        ttk.Entry(f, textvariable=self.cps_var, width=6).grid(row=1, column=1, sticky="w")
        bf = ttk.Frame(f)
        bf.grid(row=2, column=1, sticky="w", pady=6)
        self.qc_btn = ttk.Button(bf, text="Run check", command=self.guard(self.qc))
        self.qc_btn.pack(side="left")
        ttk.Button(bf, text="Save report…", command=self.guard(self.save_report)).pack(side="left", padx=6)
        self._log("qc", 20).grid(row=3, column=0, columnspan=3, sticky="nsew")
        f.rowconfigure(3, weight=1)

    def qc(self) -> None:
        p, lines, s = self.lines_session()
        try:
            cps = float(self.cps_var.get())
        except ValueError:
            raise UserError("Characters/second must be a number.")
        exp = self.qc_dir.get().strip()
        self.qc_btn.state(["disabled"])
        self.log("qc", "Checking…", clear=True)

        def work(_emit):
            return run_qc(p, lines, s, Path(exp) if exp else None, QCSettings(chars_per_second=cps))

        def on_done(rep, exc):
            self.qc_btn.state(["!disabled"])
            if exc:
                self.log("qc", f"Failed: {exc}", clear=True)
                return
            self.last_qc = format_report(rep)
            self.log("qc", self.last_qc, clear=True)

        Worker(self.root, lambda *a: None, on_done).start(work)

    def save_report(self) -> None:
        if not self.last_qc:
            raise UserError("Run the check first.")
        path = filedialog.asksaveasfilename(defaultextension=".md", initialfile="qc_report.md")
        if path:
            Path(path).write_text(self.last_qc, encoding="utf-8")


def run() -> None:
    root = tk.Tk()
    Launcher(root)
    root.lift()
    root.attributes("-topmost", True)
    root.after(300, lambda: root.attributes("-topmost", False))
    root.mainloop()
