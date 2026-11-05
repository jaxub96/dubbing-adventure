import numpy as np
from gdub.audio import PreRollBuffer, SimulatedEngine

SR = 48000


def blocks(x, n=1024):
    return [x[i:i + n] for i in range(0, len(x), n)]


def test_preroll_keeps_last_300ms_only():
    buf = PreRollBuffer(SR, 300)
    x = np.arange(SR, dtype=np.float32)          # 1 s ramp, value == sample index
    for b in blocks(x[:SR // 2]):
        buf.feed(b)
    buf.start()
    for b in blocks(x[SR // 2:]):
        buf.feed(b)
    out = buf.stop()
    pre = int(SR * 0.3)
    assert len(out) == pre + SR // 2 and out[0] == SR // 2 - pre   # 300 ms before start point
    assert np.array_equal(out, x[SR // 2 - pre:])                     # contiguous, nothing lost


def test_preroll_when_less_audio_available_and_reuse():
    buf = PreRollBuffer(SR, 300)
    buf.feed(np.ones(100, np.float32))
    buf.start()
    buf.feed(np.full(50, 2, np.float32))
    assert len(buf.stop()) == 150
    buf.feed(np.full(SR, 3, np.float32))     # second take must not contain the first take's audio
    buf.start()
    out = buf.stop()
    assert len(out) == int(SR * .3) and np.all(out == 3)


def test_ring_is_bounded():
    buf = PreRollBuffer(SR, 300)
    for _ in range(500):
        buf.feed(np.zeros(1024, np.float32))
    assert buf._ring_len < int(SR * .3) + 1024


def test_simulated_engine_roundtrip():
    e = SimulatedEngine(SR)
    e.start_take()
    assert e.recording
    assert len(e.stop_take()) > 0 and not e.recording
