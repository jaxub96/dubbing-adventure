"""Recording session state: takes per line, the chosen take, actor/mic notes, UI state.
Stored as session.json; master WAVs in raw/ are never overwritten or deleted."""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from .lines import Line
from .project import Project

TAKE_RE = re.compile(r"^(?P<id>.+)__take(?P<n>\d+)\.wav$", re.IGNORECASE)


@dataclass
class Take:
    n: int
    file: str                    # relative to the project folder, posix style
    rejected: bool = False
    duration: float = 0.0
    peak_db: float = -120.0
    clipped: bool = False
    device: str = ""
    samplerate: int = 0
    created: str = ""


class Session:
    def __init__(self, project: Project):
        self.project = project
        self.actors: dict[str, dict] = {}
        self.ui: dict = {}
        self._lines: dict[str, dict] = {}   # id -> {"selected": int|None, "takes": [Take-dicts]}

    # ---- persistence
    @classmethod
    def load(cls, project: Project) -> "Session":
        s = cls(project)
        if project.session_file.exists():
            d = json.loads(project.session_file.read_text(encoding="utf-8"))
            s.actors = d.get("actors", {})
            s.ui = d.get("ui", {})
            s._lines = d.get("lines", {})
        return s

    def save(self) -> None:
        data = {"version": 1, "actors": self.actors, "ui": self.ui, "lines": self._lines}
        tmp = self.project.session_file.with_suffix(".json.tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.project.session_file)

    # ---- queries
    def takes(self, line_id: str) -> list[Take]:
        return [Take(**t) for t in self._lines.get(line_id, {}).get("takes", [])]

    def selected_take(self, line_id: str) -> Take | None:
        e = self._lines.get(line_id)
        if not e or e.get("selected") is None:
            return None
        for t in self.takes(line_id):
            if t.n == e["selected"] and not t.rejected:
                return t
        return None

    def is_done(self, line_id: str) -> bool:
        return self.selected_take(line_id) is not None

    def path_of(self, take: Take) -> Path:
        return self.project.root / take.file

    # ---- changes
    def _entry(self, line_id: str) -> dict:
        return self._lines.setdefault(line_id, {"selected": None, "takes": []})

    def _next_n(self, line: Line) -> int:
        used = [t.n for t in self.takes(line.id)]
        folder = self.project.raw_dir / line.npc
        if folder.is_dir():
            for f in folder.iterdir():
                m = TAKE_RE.match(f.name)
                if m and m["id"].lower() == line.id.lower():
                    used.append(int(m["n"]))
        return max(used, default=0) + 1

    def store_take(self, line: Line, audio: np.ndarray, samplerate: int, device: str = "") -> Take:
        """Write a new master take (24-bit WAV, never overwriting) and select it."""
        import soundfile as sf
        from . import dsp
        n = self._next_n(line)
        rel = f"raw/{line.npc}/{line.id}__take{n:02d}.wav"
        path = self.project.root / rel
        if path.exists():
            raise FileExistsError(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(path), np.clip(audio, -1.0, 1.0 - 1e-7), samplerate, subtype="PCM_24")
        take = Take(n, rel, False, len(audio) / samplerate, dsp.to_db(dsp.peak(audio)),
                    dsp.is_clipped(audio), device, samplerate, time.strftime("%Y-%m-%dT%H:%M:%S"))
        e = self._entry(line.id)
        e["takes"].append(asdict(take))
        e["selected"] = n
        self.save()
        return take

    def select(self, line_id: str, n: int) -> bool:
        """Pick take n as the best one (also un-rejects it)."""
        e = self._lines.get(line_id)
        if not e:
            return False
        for t in e["takes"]:
            if t["n"] == n:
                t["rejected"] = False
                e["selected"] = n
                self.save()
                return True
        return False

    def reject(self, line_id: str, n: int) -> bool:
        """Mark take n rejected (file stays on disk). If it was selected, fall back to
        the newest remaining take."""
        e = self._lines.get(line_id)
        if not e:
            return False
        hit = False
        for t in e["takes"]:
            if t["n"] == n:
                t["rejected"], hit = True, True
        if hit and e.get("selected") == n:
            alive = [t["n"] for t in e["takes"] if not t["rejected"]]
            e["selected"] = max(alive) if alive else None
        if hit:
            self.save()
        return hit

    def forget(self, line_ids) -> None:
        """Drop all take bookkeeping for these lines (files are handled by reset.py)."""
        for i in line_ids:
            self._lines.pop(i, None)
        self.save()

    def set_actor(self, voice: str, **fields) -> dict:
        a = self.actors.setdefault(voice, {})
        a.update({k: v for k, v in fields.items() if v is not None})
        self.save()
        return a

    def reconcile(self, lines: list[Line]) -> int:
        """Adopt take files found on disk that the session file doesn't know (e.g. after
        losing session.json). Returns number adopted."""
        import soundfile as sf
        from . import dsp
        by_id = {ln.id.lower(): ln for ln in lines}
        adopted = 0
        auto_select: set[str] = set()
        raw = self.project.raw_dir
        if not raw.is_dir():
            return 0
        for f in sorted(raw.rglob("*.wav")):
            m = TAKE_RE.match(f.name)
            ln = by_id.get(m["id"].lower()) if m else None
            if ln is None:
                continue
            n = int(m["n"])
            if any(t.n == n for t in self.takes(ln.id)):
                continue
            try:
                data, sr = sf.read(str(f), dtype="float32")
            except Exception:
                continue
            if data.ndim > 1:
                data = data[:, 0]
            take = Take(n, f.relative_to(self.project.root).as_posix(), False, len(data) / sr,
                        dsp.to_db(dsp.peak(data)), dsp.is_clipped(data), "", sr,
                        time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(f.stat().st_mtime)))
            e = self._entry(ln.id)
            e["takes"].append(asdict(take))
            e["takes"].sort(key=lambda t: t["n"])
            if e.get("selected") is None or ln.id in auto_select:
                e["selected"] = n          # nothing chosen yet: default to the newest take
                auto_select.add(ln.id)
            adopted += 1
        if adopted:
            self.save()
        return adopted
