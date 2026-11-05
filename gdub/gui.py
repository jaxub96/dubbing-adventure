"""Tkinter recorder: line-ID-driven, keyboard/foot-pedal friendly.

Keys: Space record/stop - Right/Left next/previous line - N next unrecorded - P play selected
take - 1..9 pick take - Del/Backspace reject selected take - F cycle speaker filter - Esc stop playback
"""
from __future__ import annotations

import time
import tkinter as tk
from tkinter import messagebox, ttk

from .workspace import Workspace

GREY = "#8a8a8a"


def _pick_font(root, candidates, fallback):
    """First installed font family: Segoe UI/Consolas (Windows), Helvetica Neue/Menlo (macOS),
    DejaVu (Linux)."""
    from tkinter import font as tkfont
    have = set(tkfont.families(root))
    return next((f for f in candidates if f in have), fallback)
DEBOUNCE_S = 0.25      # ignore key auto-repeat from pedals / held keys


class RecorderApp:
    def __init__(self, root: tk.Tk, ws: Workspace, engine):
        self.root, self.ws, self.engine = root, ws, engine
        self.session = ws.session
        self._last_toggle = 0.0
        self._meter = 0.0
        self._status_until = 0.0
        self._build()
        self._bind()
        self.refresh()
        self._tick()

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        r = self.root
        ui = _pick_font(r, ["Segoe UI", "Helvetica Neue", "SF Pro Text", "DejaVu Sans"], "Helvetica")
        mono = _pick_font(r, ["Consolas", "Menlo", "DejaVu Sans Mono"], "Courier")
        r.title("Gothic Dub Recorder")
        r.geometry("1000x680")
        r.minsize(700, 520)
        r.columnconfigure(0, weight=1)

        top = ttk.Frame(r, padding=(10, 8))
        top.grid(row=0, column=0, sticky="ew")
        ttk.Label(top, text="Voice:").pack(side="left")
        self.voice_var = tk.StringVar()
        self.voice_box = ttk.Combobox(top, textvariable=self.voice_var, state="readonly", width=12,
                                      takefocus=0, values=["all"] + [self.ws.speaker_label(v)
                                                                       for v in self.ws.voices()])
        self.voice_box.pack(side="left", padx=(4, 12))
        self.voice_box.bind("<<ComboboxSelected>>", self._on_filter)
        ttk.Label(top, text="Character:").pack(side="left")
        self.npc_var = tk.StringVar()
        self.npc_box = ttk.Combobox(top, textvariable=self.npc_var, state="readonly", width=22,
                                    takefocus=0, values=["all"] + self.ws.npcs())
        self.npc_box.pack(side="left", padx=(4, 12))
        self.npc_box.bind("<<ComboboxSelected>>", self._on_filter)
        self.missing_var = tk.BooleanVar(value=self.ws.only_missing)
        self.adv_var = tk.BooleanVar(value=self.ws.auto_advance)
        ttk.Checkbutton(top, text="Only unrecorded", variable=self.missing_var, takefocus=0,
                        command=self._on_filter).pack(side="left", padx=6)
        ttk.Checkbutton(top, text="Auto-advance", variable=self.adv_var, takefocus=0,
                        command=self._on_filter).pack(side="left", padx=6)

        self.context = tk.Label(r, text="", fg=GREY, font=(ui, 13), anchor="w",
                                justify="left", wraplength=940)
        self.context.grid(row=1, column=0, sticky="ew", padx=16, pady=(14, 0))
        self.header = tk.Label(r, text="", fg="#3a6ea5", font=(ui, 11, "bold"), anchor="w")
        self.header.grid(row=2, column=0, sticky="ew", padx=16, pady=(14, 0))
        self.main = tk.Label(r, text="", font=(ui, 28), anchor="nw", justify="left",
                             wraplength=940)
        self.main.grid(row=3, column=0, sticky="nsew", padx=16, pady=(2, 6))
        r.rowconfigure(3, weight=1)
        self.ids = tk.Label(r, text="", fg=GREY, font=(mono, 10), anchor="w", justify="left")
        self.ids.grid(row=4, column=0, sticky="ew", padx=16)

        rec = ttk.Frame(r, padding=(16, 8))
        rec.grid(row=5, column=0, sticky="ew")
        self.rec_label = tk.Label(rec, text="", font=(ui, 14, "bold"), width=16, anchor="w")
        self.rec_label.pack(side="left")
        self.meter = tk.Canvas(rec, height=16, bg="#222", highlightthickness=0)
        self.meter.pack(side="left", fill="x", expand=True)

        tk_frame = ttk.Frame(r, padding=(16, 0))
        tk_frame.grid(row=6, column=0, sticky="ew")
        tk_frame.columnconfigure(0, weight=1)
        self.takes = tk.Listbox(tk_frame, height=6, font=(mono, 11), exportselection=False,
                                activestyle="none", takefocus=0)
        self.takes.grid(row=0, column=0, sticky="ew")
        self.takes.bind("<ButtonRelease-1>", self._on_take_click)
        self.takes.bind("<Double-Button-1>", lambda e: self.play())

        self.progress = tk.Label(r, text="", font=(ui, 12, "bold"), anchor="w")
        self.progress.grid(row=7, column=0, sticky="ew", padx=16, pady=(8, 0))
        self.status = tk.Label(r, text="", fg=GREY, anchor="w")
        self.status.grid(row=8, column=0, sticky="ew", padx=16)
        tk.Label(r, fg=GREY, anchor="w", text="Space rec/stop · ←/→ line · N next unrecorded · "
                 "P play · 1-9 pick take · Del/⌫ reject · F speaker filter · Esc stop playback"
                 ).grid(row=9, column=0, sticky="ew", padx=16, pady=(0, 8))
        r.bind("<Configure>", self._on_resize)

    def _bind(self) -> None:
        self.root.bind_all("<Key>", self._on_key)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        # bring the window to the front (on macOS it otherwise stays behind the terminal)
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(300, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

    def _on_resize(self, event) -> None:
        if event.widget is self.root:
            w = max(300, event.width - 40)
            self.main.config(wraplength=w)
            self.context.config(wraplength=w)

    # ------------------------------------------------------------------ keys
    def _on_key(self, e) -> str | None:
        k = e.keysym
        if k.endswith(("_L", "_R")) or k in ("Caps_Lock", "Num_Lock", "Scroll_Lock"):
            return None                      # bare modifier keys
        if k == "space":
            self.toggle_record()
        elif self.engine.recording:
            self.flash("Recording - press Space to stop first")
        elif k in ("Right", "Next"):
            self.go(+1)
        elif k in ("Left", "Prior"):
            self.go(-1)
        elif k.lower() == "n":
            self.next_unrecorded()
        elif k.lower() == "p":
            self.play()
        elif k.lower() == "f":
            self.ws.cycle_voice_filter()
            self.refresh()
        elif k in ("Delete", "BackSpace"):   # Mac keyboards: the "delete" key is BackSpace
            self.reject()
        elif k == "Escape":
            self.engine.stop_playback()
        elif k.isdigit() and k != "0" or (k.startswith("KP_") and k[3:].isdigit() and k[3:] != "0"):
            self.pick(int(k[-1]))
        else:
            return None
        return "break"

    # ------------------------------------------------------------------ actions
    def toggle_record(self) -> None:
        now = time.monotonic()
        if now - self._last_toggle < DEBOUNCE_S:
            return
        self._last_toggle = now
        line = self.ws.current
        if line is None:
            return
        if not self.engine.recording:
            self.engine.start_take()
            self.refresh()
            return
        audio = self.engine.stop_take()
        if len(audio) < self.engine.samplerate * 0.15:
            self.flash("Take too short - discarded")
            self.refresh()
            return
        take = self.session.store_take(line, audio, self.engine.samplerate, self.engine.device_name)
        self.flash(f"Saved take {take.n:02d}" + ("  - CLIPPED, consider re-recording" if take.clipped else ""))
        if self.ws.auto_advance:
            self.ws.next_unrecorded()
        else:
            self.ws.refresh()
        self.refresh()

    def go(self, delta: int) -> None:
        if self.ws.move(delta):
            self.refresh()

    def next_unrecorded(self) -> None:
        if self.ws.next_unrecorded():
            self.refresh()
        else:
            self.flash("No more unrecorded lines in this view")

    def play(self) -> None:
        line = self.ws.current
        take = self.session.selected_take(line.id) if line else None
        if take is None:
            self.flash("No selected take to play")
            return
        try:
            self.engine.play_file(self.session.path_of(take))
        except Exception as exc:
            self.flash(f"Playback failed: {exc}")

    def pick(self, n: int) -> None:
        line = self.ws.current
        if line and self.session.select(line.id, n):
            self.ws.refresh()
            self.refresh()
        else:
            self.flash(f"No take {n} for this line")

    def reject(self) -> None:
        line = self.ws.current
        take = self.session.selected_take(line.id) if line else None
        if take is None:
            self.flash("No selected take to reject")
            return
        self.session.reject(line.id, take.n)
        self.flash(f"Rejected take {take.n:02d}")
        self.ws.refresh()
        self.refresh()

    def _on_take_click(self, _e) -> None:
        sel = self.takes.curselection()
        line = self.ws.current
        if sel and line:
            takes = self.session.takes(line.id)
            if sel[0] < len(takes):
                self.session.select(line.id, takes[sel[0]].n)
                self.ws.refresh()
                self.refresh()
        self.root.focus_set()

    def _on_filter(self, _e=None) -> None:
        v = self.voice_var.get()
        voice = None
        if v != "all":
            voice = next((x for x in self.ws.voices() if self.ws.speaker_label(x) == v), None)
        npc = self.npc_var.get()
        self.ws.auto_advance = self.adv_var.get()
        self.ws.set_filters(voice=voice, npc=None if npc in ("all", "") else npc,
                            only_missing=self.missing_var.get())
        self.session.save()
        self.refresh()
        self.root.focus_set()

    # ------------------------------------------------------------------ drawing
    def flash(self, msg: str, secs: float = 3.0) -> None:
        self.status.config(text=msg)
        self._status_until = time.monotonic() + secs

    def refresh(self) -> None:
        ws, line = self.ws, self.ws.current
        # sync widgets with filter state (F key changes it from outside the combobox)
        self.voice_var.set("all" if not ws.voice_filter else ws.speaker_label(ws.voice_filter))
        self.npc_var.set(ws.npc_filter or "all")
        self.missing_var.set(ws.only_missing)
        self.adv_var.set(ws.auto_advance)
        self.takes.delete(0, "end")
        if line is None:
            self.main.config(text="No lines match the current filter.")
            self.context.config(text="")
            self.header.config(text="")
            self.ids.config(text="")
            self.progress.config(text="")
            return
        ctx = ws.context_line(line)
        self.context.config(text=(f"{ws.speaker_label(ctx.speaker)}: {ctx.text}" if ctx
                                  else "(start of conversation)"))
        pos = ws._pos() + 1
        self.header.config(text=f"{line.npc_name} · {ws.speaker_label(line.speaker)} · "
                                f"{line.scene}   [{pos}/{len(ws.visible)}]")
        self.main.config(text=line.text or "(no text)")
        takes = self.session.takes(line.id)
        nxt = max([t.n for t in takes], default=0) + 1
        self.ids.config(text=f"ID:   {line.id}\nFile: raw/{line.npc}/{line.id}__take{nxt:02d}.wav (next take)"
                             f"\nExport: {line.filename}")
        sel = self.session.selected_take(line.id)
        for t in takes:
            mark = "★" if sel and sel.n == t.n else " "
            flags = ("  CLIP" if t.clipped else "") + ("  ✗ rejected" if t.rejected else "")
            self.takes.insert("end", f" {mark} take {t.n:02d}   {t.duration:5.1f}s   {t.peak_db:6.1f} dBFS{flags}")
            if t.rejected:
                self.takes.itemconfig("end", fg=GREY)
            if sel and sel.n == t.n:
                self.takes.selection_set("end")
        if not takes:
            self.takes.insert("end", "  (no takes yet - press Space to record)")
        d, t = ws.progress(npc=line.npc)
        text = f"{line.npc_name}: {d}/{t} lines done"
        if ws.voice_filter:
            d2, t2 = ws.progress(npc=line.npc, voice=ws.voice_filter)
            text += f"   ({ws.speaker_label(ws.voice_filter)}: {d2}/{t2})"
        d3, t3 = ws.progress(voice=ws.voice_filter)
        text += f"   ·   overall{' ' + ws.speaker_label(ws.voice_filter) if ws.voice_filter else ''}: {d3}/{t3}"
        self.progress.config(text=text)
        actor = self.session.actors.get(line.speaker)
        if actor and time.monotonic() > self._status_until:
            self.status.config(text="Actor: " + ", ".join(f"{k}={v}" for k, v in actor.items() if v))

    def _tick(self) -> None:
        peak = self.engine.poll_peak()
        self._meter = max(peak, self._meter * 0.85)
        w = max(self.meter.winfo_width(), 1)
        self.meter.delete("all")
        color = "#d33" if self._meter >= 0.98 else "#e0b030" if self._meter > 0.7 else "#3c3"
        self.meter.create_rectangle(0, 0, int(w * min(self._meter, 1.0) ** 0.5), 16, fill=color, width=0)
        if self.engine.recording:
            self.rec_label.config(text=f"● REC {self.engine.take_seconds():5.1f}s", fg="#d33")
        else:
            self.rec_label.config(text="ready", fg=GREY)
        if self._status_until and time.monotonic() > self._status_until:
            self._status_until = 0.0
            self.status.config(text="")
        self.root.after(50, self._tick)

    def close(self) -> None:
        line = self.ws.current
        if self.engine.recording and line is not None:
            audio = self.engine.stop_take()
            if len(audio) >= self.engine.samplerate * 0.15:
                self.session.store_take(line, audio, self.engine.samplerate, self.engine.device_name)
        self.session.save()
        self.engine.close()
        self.root.destroy()


def run(ws: Workspace, engine) -> None:
    root = tk.Tk()
    try:
        engine.open()
    except Exception as exc:
        root.withdraw()
        messagebox.showerror("Audio device", f"Could not open the input device:\n{exc}\n\n"
                                             "Run `gdub devices` to list devices, then use --device.")
        root.destroy()
        raise SystemExit(1)
    RecorderApp(root, ws, engine)
    root.mainloop()
