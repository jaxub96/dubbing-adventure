"""Step 4: quality-check report."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import soundfile as sf

from . import dsp
from .lines import Line
from .project import Project
from .session import TAKE_RE, Session


@dataclass
class QCSettings:
    chars_per_second: float = 13.0    # typical speech rate used to estimate expected length
    min_ratio: float = 0.30           # shorter than this share of expected = suspicious
    max_ratio: float = 2.5            # longer than this multiple (+1 s) = suspicious
    min_seconds: float = 0.20
    silent_db: float = -50.0


@dataclass
class QCReport:
    missing: list[Line] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)
    problems: list[tuple[str, str]] = field(default_factory=list)       # (id, message)
    not_exported: list[str] = field(default_factory=list)
    progress: dict[str, tuple[int, int]] = field(default_factory=dict)
    voice_progress: dict[str, tuple[int, int]] = field(default_factory=dict)
    total: tuple[int, int] = (0, 0)
    checked_export: bool = False

    @property
    def issue_count(self) -> int:
        return len(self.missing) + len(self.orphans) + len(self.problems) + len(self.not_exported)


def run_qc(project: Project, lines: list[Line], session: Session, export_dir: Path | None = None,
           cfg: QCSettings | None = None) -> QCReport:
    cfg = cfg or QCSettings()
    rep = QCReport()
    rep.checked_export = bool(export_dir and Path(export_dir).is_dir())
    by_id = {ln.id.lower(): ln for ln in lines}
    exported = ({f.stem.lower() for f in Path(export_dir).glob("*.wav")}
                if export_dir and Path(export_dir).is_dir() else None)

    per_npc: dict[str, list[int]] = {}
    per_voice: dict[str, list[int]] = {}
    for ln in lines:
        take = session.selected_take(ln.id)
        for tbl, key in ((per_npc, ln.npc), (per_voice, ln.speaker)):
            tbl.setdefault(key, [0, 0])[1] += 1
        if take is None:
            rep.missing.append(ln)
            continue
        for tbl, key in ((per_npc, ln.npc), (per_voice, ln.speaker)):
            tbl[key][0] += 1
        path = session.path_of(take)
        if not path.exists():
            rep.problems.append((ln.id, f"chosen take {take.n} file is missing ({take.file})"))
        else:
            rep.problems += [(ln.id, m) for m in check_audio(ln, path, cfg)]
        if exported is not None and ln.id.lower() not in exported:
            rep.not_exported.append(ln.id)

    # recordings (raw takes / exported files) that match no script line
    raw = project.raw_dir
    if raw.is_dir():
        for f in sorted(raw.rglob("*.wav")):
            m = TAKE_RE.match(f.name)
            if not m or m["id"].lower() not in by_id:
                rep.orphans.append(f.relative_to(project.root).as_posix())
    if exported is not None:
        for f in sorted(Path(export_dir).glob("*.wav")):
            if f.stem.lower() not in by_id:
                rep.orphans.append(f"{export_dir.name}/{f.name}")

    rep.progress = {k: (v[0], v[1]) for k, v in sorted(per_npc.items(), key=lambda kv: kv[0].lower())}
    rep.voice_progress = {k: (v[0], v[1]) for k, v in sorted(per_voice.items())}
    rep.total = (sum(v[0] for v in per_npc.values()), sum(v[1] for v in per_npc.values()))
    return rep


def check_audio(ln: Line, path: Path, cfg: QCSettings) -> list[str]:
    msgs = []
    try:
        data, sr = sf.read(str(path), dtype="float64")
    except Exception as exc:
        return [f"cannot read {path.name}: {exc}"]
    if data.ndim > 1:
        data = data[:, 0]
    pk = dsp.peak(data)
    if dsp.to_db(pk) < cfg.silent_db:
        return [f"silent take ({dsp.to_db(pk):.0f} dBFS peak)"]
    if dsp.is_clipped(data):
        msgs.append(f"clipped (peak {dsp.to_db(pk):.1f} dBFS)")
    b = dsp.speech_bounds(dsp.highpass(data, sr, 80.0), sr)
    speech = (b[1] - b[0]) / sr if b else 0.0
    expected = max(len(ln.text), 1) / cfg.chars_per_second
    if speech < max(cfg.min_seconds, cfg.min_ratio * expected):
        msgs.append(f"suspiciously short: {speech:.1f}s of speech, ~{expected:.1f}s expected "
                    f"for {len(ln.text)} characters")
    elif speech > cfg.max_ratio * expected + 1.0:
        msgs.append(f"suspiciously long: {speech:.1f}s of speech, ~{expected:.1f}s expected "
                    f"for {len(ln.text)} characters")
    return msgs


def format_report(rep: QCReport, limit: int = 0) -> str:
    def block(title: str, items: list[str]) -> list[str]:
        out = [f"## {title} ({len(items)})"]
        shown = items if not limit else items[:limit]
        out += [f"- {i}" for i in shown] or ["- none"]
        if limit and len(items) > limit:
            out.append(f"- ... and {len(items) - limit} more")
        return out + [""]

    d, t = rep.total
    out = ["# Quality check", "", f"Overall: {d}/{t} lines recorded"
           + (f" ({100 * d // t}%)" if t else ""), ""]
    out += ["## Progress per character"]
    out += [f"- {k}: {a}/{b}" for k, (a, b) in rep.progress.items()] + [""]
    out += ["## Progress per voice"]
    out += [f"- {k}{' (hero)' if k == '15' else ''}: {a}/{b}"
            for k, (a, b) in rep.voice_progress.items()] + [""]
    out += block("Lines in the scripts with no recording", [f"{l.id}  \"{l.text[:60]}\"" for l in rep.missing])
    out += block("Recordings that match no script line", rep.orphans)
    out += block("Technical problems", [f"{i}: {m}" for i, m in rep.problems])
    if rep.checked_export:
        out += block("Recorded but not exported", rep.not_exported)
    return "\n".join(out)
