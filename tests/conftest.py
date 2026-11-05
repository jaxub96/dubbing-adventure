import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
FIXTURES = Path(__file__).parent / "fixtures"


def speechlike(seconds=1.5, sr=48000, level=0.3, lead=0.5, tail=0.6, hum=0.0, noise=0.0005, seed=0):
    """Tone burst (amplitude-modulated, 'syllables') with silence/room-noise around it."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * sr)) / sr
    burst = level * np.sin(2 * np.pi * 180 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 4 * t))
    out = np.concatenate([np.zeros(int(lead * sr)), burst, np.zeros(int(tail * sr))])
    tt = np.arange(len(out)) / sr
    out = out + hum * np.sin(2 * np.pi * 50 * tt) + rng.normal(0, noise, len(out))
    return out.astype(np.float32)


@pytest.fixture
def project(tmp_path):
    from gdub.extract import extract_paths
    from gdub.lines import write_lines_csv
    from gdub.project import Project
    p = Project(tmp_path / "proj")
    p.root.mkdir()
    write_lines_csv(extract_paths([FIXTURES / "DIA_BDT_1060_Dexter.d"]).lines, p.lines_csv)
    return p
