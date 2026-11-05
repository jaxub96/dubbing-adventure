"""Step 3: export the chosen takes - trim, high-pass, (denoise), normalise, convert with
ffmpeg and write DIA_xxx.wav files into the game's speech folder."""
from __future__ import annotations

import csv
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf

from . import dsp
from .lines import Line
from .project import Project
from .session import Session


@dataclass
class ExportSettings:
    # target game format (confirm for your game and change here / in project.json)
    sample_rate: int = 44100
    codec: str = "pcm_s16le"          # or adpcm_ms for MS-ADPCM
    channels: int = 1
    # processing
    highpass_hz: float = 80.0
    denoise: bool = False
    trim_below_peak_db: float = 40.0
    lead_ms: float = 50.0
    tail_ms: float = 150.0
    normalize: str = "rms"            # rms | peak | none
    target_db: float = -20.0
    ceiling_db: float = -1.0


@dataclass
class ExportRow:
    id: str
    status: str                       # ok | skipped | error
    detail: str = ""
    take: int = 0
    seconds: float = 0.0
    gain_db: float = 0.0
    limited: bool = False


@dataclass
class ExportResult:
    rows: list[ExportRow] = field(default_factory=list)

    def count(self, status: str) -> int:
        return sum(r.status == status for r in self.rows)


def find_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg not found on PATH - install it (e.g. `winget install ffmpeg`)")
    return exe


def _ffmpeg(ffmpeg: str, src: Path, dst: Path, *args: str) -> None:
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), *args, str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "ffmpeg failed")


def process_audio(data: np.ndarray, sr: int, cfg: ExportSettings, ffmpeg: str, tmp: Path):
    """Returns (processed float array, gain_db, limited)."""
    x = data.astype(np.float64)
    x = x - np.mean(x) if len(x) else x                       # remove DC offset
    x = dsp.highpass(x, sr, cfg.highpass_hz)
    x = dsp.trim_silence(x, sr, cfg.trim_below_peak_db, cfg.lead_ms, cfg.tail_ms)
    if cfg.denoise:
        a, b = tmp / "dn_in.wav", tmp / "dn_out.wav"
        sf.write(str(a), x.astype(np.float32), sr, subtype="FLOAT")
        _ffmpeg(ffmpeg, a, b, "-af", "afftdn=nr=10:nf=-45", "-c:a", "pcm_f32le")
        x, _ = sf.read(str(b), dtype="float64")
    return dsp.normalize(x, sr, cfg.normalize, cfg.target_db, cfg.ceiling_db)


def export_line(line: Line, src: Path, out_dir: Path, cfg: ExportSettings, ffmpeg: str):
    with tempfile.TemporaryDirectory(prefix="gdub_") as td:
        tmp = Path(td)
        data, sr = sf.read(str(src), dtype="float64")
        if data.ndim > 1:
            data = data[:, 0]
        y, gain, limited = process_audio(data, sr, cfg, ffmpeg, tmp)
        mid = tmp / "processed.wav"
        sf.write(str(mid), y.astype(np.float32), sr, subtype="FLOAT")
        dst = out_dir / line.filename
        args = ["-ar", str(cfg.sample_rate), "-ac", str(cfg.channels), "-c:a", cfg.codec,
                "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:a", "+bitexact"]
        if cfg.codec.startswith("pcm_s"):
            args = ["-af", "aresample=dither_method=triangular"] + args
        _ffmpeg(ffmpeg, mid, dst, *args)
        return len(y) / sr, gain, limited


def export_all(project: Project, lines: list[Line], session: Session, out_dir: Path,
               cfg: ExportSettings, voice: str | None = None, npc: str | None = None,
               dry_run: bool = False, progress=None) -> ExportResult:
    ffmpeg = find_ffmpeg()
    out_dir = Path(out_dir)
    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
    res = ExportResult()
    todo = [ln for ln in lines if (not voice or ln.speaker == voice) and (not npc or ln.npc == npc)]
    for i, ln in enumerate(todo, 1):
        take = session.selected_take(ln.id)
        if take is None:
            res.rows.append(ExportRow(ln.id, "skipped", "no chosen take"))
        elif not session.path_of(take).exists():
            res.rows.append(ExportRow(ln.id, "error", f"missing file {take.file}", take.n))
        elif dry_run:
            res.rows.append(ExportRow(ln.id, "ok", "dry run", take.n))
        else:
            try:
                secs, gain, limited = export_line(ln, session.path_of(take), out_dir, cfg, ffmpeg)
                res.rows.append(ExportRow(ln.id, "ok", "", take.n, secs, gain, limited))
            except Exception as exc:                    # keep going; report at the end
                res.rows.append(ExportRow(ln.id, "error", str(exc), take.n))
        if progress:
            progress(i, len(todo), res.rows[-1])
    if not dry_run:
        with open(project.export_log, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["id", "status", "take", "seconds", "gain_db", "limited", "detail"])
            for r in res.rows:
                w.writerow([r.id, r.status, r.take, f"{r.seconds:.2f}", f"{r.gain_db:.1f}",
                            int(r.limited), r.detail])
    return res
