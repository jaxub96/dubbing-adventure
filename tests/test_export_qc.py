import shutil

import numpy as np
import pytest
import soundfile as sf

from conftest import speechlike
from gdub import dsp
from gdub.export import ExportSettings, export_all
from gdub.qc import QCSettings, format_report, run_qc
from gdub.session import Session

SR = 48000
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg needed")


def test_dsp_trim_and_normalize():
    x = speechlike(level=0.1, hum=0.02).astype(np.float64)
    y = dsp.highpass(x, SR, 80)
    assert np.std(y[: int(.4 * SR)]) < np.std(x[: int(.4 * SR)])       # 50 Hz hum reduced
    t = dsp.trim_silence(y, SR)
    assert 1.5 < len(t) / SR < 1.9                                      # ~1.5 s speech + margins
    n, gain, limited = dsp.normalize(t, SR, "rms", -20.0, -1.0)
    assert abs(dsp.speech_level_db(n, SR) + 20) < 0.5 and not limited
    loud = speechlike(level=0.95).astype(np.float64)
    _, g2, lim2 = dsp.normalize(loud, SR, "rms", -3.0, -1.0)
    assert lim2 and dsp.to_db(dsp.peak(loud) * 10 ** (g2 / 20)) <= -0.99


def test_export_makes_game_files_with_equal_levels(project, tmp_path):
    lines = project.load_lines()
    s = Session.load(project)
    s.store_take(lines[0], speechlike(level=0.05, seed=1), SR)         # quiet actor
    s.store_take(lines[1], speechlike(level=0.5, seed=2), SR)          # loud actor
    out = tmp_path / "speech"
    res = export_all(project, lines, s, out, ExportSettings(), progress=None)
    assert res.count("ok") == 2 and res.count("error") == 0 and res.count("skipped") == len(lines) - 2
    levels = []
    for ln in lines[:2]:
        f = out / f"{ln.id}.wav"
        info = sf.info(str(f))
        assert (info.samplerate, info.channels, info.subtype) == (44100, 1, "PCM_16")
        data, sr = sf.read(str(f))
        assert 1.6 < len(data) / sr < 1.9                                # silence trimmed (was 2.6 s)
        levels.append(dsp.speech_level_db(data, sr))
    assert abs(levels[0] - levels[1]) < 0.7 and abs(levels[0] + 20) < 1.0
    assert project.export_log.exists()
    assert b"LIST" not in (out / f"{lines[0].id}.wav").read_bytes()[:64]   # no extra metadata chunks


def test_export_adpcm_and_missing_file(project, tmp_path):
    lines = project.load_lines()
    s = Session.load(project)
    s.store_take(lines[0], speechlike(), SR)
    t = s.store_take(lines[1], speechlike(), SR)
    s.path_of(t).unlink()
    res = export_all(project, lines, s, tmp_path / "o", ExportSettings(codec="adpcm_ms", sample_rate=22050))
    assert res.count("ok") == 1 and res.count("error") == 1
    assert sf.info(str(tmp_path / "o" / f"{lines[0].id}.wav")).subtype == "MS_ADPCM"


def test_qc_report(project, tmp_path):
    lines = project.load_lines()
    s = Session.load(project)
    s.store_take(lines[0], speechlike(), SR)                                      # fine (~1.5 s, 106 chars... short?)
    s.store_take(lines[1], np.clip(speechlike(level=3.0), -1, 1), SR)             # clipped
    s.store_take(lines[2], speechlike(seconds=0.2), SR)                           # too short
    s.store_take(lines[3], np.zeros(SR, np.float32), SR)                          # silent
    s.store_take(lines[4], speechlike(seconds=25), SR)                            # too long
    stray = project.raw_dir / "BDT_1060_Dexter" / "DIA_Nope_09_00__take01.wav"
    sf.write(str(stray), speechlike(), SR)
    exp = tmp_path / "speech"
    exp.mkdir()
    sf.write(str(exp / "DIA_Stray_15_00.wav"), speechlike(), SR)
    rep = run_qc(project, lines, s, exp, QCSettings())
    msgs = {i: m for i, m in rep.problems}
    assert "clipped" in msgs[lines[1].id]
    assert "short" in msgs[lines[2].id]
    assert "silent" in msgs[lines[3].id]
    assert "long" in msgs[lines[4].id]
    assert len(rep.missing) == len(lines) - 5
    assert any("DIA_Nope_09_00" in o for o in rep.orphans) and any("DIA_Stray_15_00" in o for o in rep.orphans)
    assert rep.total == (5, len(lines)) and rep.progress["BDT_1060_Dexter"] == (5, len(lines))
    assert len(rep.not_exported) == 5
    text = format_report(rep)
    assert "Progress per character" in text and "BDT_1060_Dexter: 5/" in text
