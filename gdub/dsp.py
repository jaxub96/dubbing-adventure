"""Small DSP helpers used by export and QC (all mono float32/64 numpy arrays)."""
from __future__ import annotations

import numpy as np
from scipy.signal import butter, sosfilt, sosfiltfilt

EPS = 1e-12


def to_db(x: float) -> float:
    return float(20.0 * np.log10(max(float(x), EPS)))


def peak(x: np.ndarray) -> float:
    return float(np.max(np.abs(x))) if len(x) else 0.0


def is_clipped(x: np.ndarray, threshold: float = 0.999, min_samples: int = 3) -> bool:
    return int(np.count_nonzero(np.abs(x) >= threshold)) >= min_samples


def frame_rms_db(x: np.ndarray, frame: int) -> np.ndarray:
    n = len(x) // frame
    if n == 0:
        return np.array([to_db(np.sqrt(np.mean(x ** 2))) if len(x) else -120.0])
    f = x[: n * frame].reshape(n, frame)
    return 20 * np.log10(np.maximum(np.sqrt(np.mean(f ** 2, axis=1)), EPS))


def highpass(x: np.ndarray, sr: int, fc: float = 80.0, order: int = 4) -> np.ndarray:
    if fc <= 0 or len(x) == 0:
        return x
    sos = butter(order, fc, btype="highpass", fs=sr, output="sos")
    if len(x) > 3 * (2 * len(sos) + 1) * 4:
        return sosfiltfilt(sos, x)   # zero-phase
    return sosfilt(sos, x)


def speech_bounds(x: np.ndarray, sr: int, below_peak_db: float = 40.0):
    """(start, end) sample indices of the speech region, or None if the signal is silent.

    Threshold: within `below_peak_db` of the loudest 10 ms frame, but never lower than
    12 dB above the estimated noise floor (10th percentile of frame levels), so quiet
    recordings don't get their room noise mistaken for speech. Capped at peak-20 dB so
    continuous speech without pauses is never cut."""
    frame = max(1, int(sr * 0.010))
    db = frame_rms_db(x, frame)
    if len(db) == 0 or db.max() < -80:
        return None
    floor = float(np.percentile(db, 10))
    thr = min(max(db.max() - below_peak_db, floor + 12.0), db.max() - 20.0)
    active = np.nonzero(db > thr)[0]
    return int(active[0] * frame), int((active[-1] + 1) * frame)


def trim_silence(x: np.ndarray, sr: int, below_peak_db: float = 40.0,
                 lead_ms: float = 50.0, tail_ms: float = 150.0, fade_ms: float = 5.0) -> np.ndarray:
    b = speech_bounds(x, sr, below_peak_db)
    if b is None:
        return x
    s = max(0, b[0] - int(sr * lead_ms / 1000))
    e = min(len(x), b[1] + int(sr * tail_ms / 1000))
    y = x[s:e].copy()
    k = min(int(sr * fade_ms / 1000), len(y) // 2)
    if k > 1:
        ramp = np.linspace(0.0, 1.0, k)
        y[:k] *= ramp
        y[-k:] *= ramp[::-1]
    return y


def speech_level_db(x: np.ndarray, sr: int) -> float:
    """RMS level (dBFS) over the active speech frames only (50 ms frames within 30 dB of
    the loudest), so pauses and room tone don't drag the measurement down."""
    frame = max(1, int(sr * 0.050))
    db = frame_rms_db(x, frame)
    n = len(x) // frame
    if n == 0:
        return float(db[0])
    power = np.mean(x[: n * frame].reshape(n, frame) ** 2, axis=1)
    active = db > db.max() - 30
    return float(10 * np.log10(max(np.mean(power[active]), EPS)))


def normalize(x: np.ndarray, sr: int, mode: str = "rms", target_db: float = -20.0,
              ceiling_db: float = -1.0):
    """Returns (y, gain_db, limited). 'rms' = equal speech level, 'peak' = peak to ceiling,
    'none'. Gain is reduced if it would push the peak over the ceiling (limited=True)."""
    pk = peak(x)
    if mode == "none" or pk <= EPS:
        return x, 0.0, False
    if mode == "peak":
        gain = ceiling_db - to_db(pk)
    elif mode == "rms":
        gain = target_db - speech_level_db(x, sr)
    else:
        raise ValueError(f"unknown normalize mode {mode}")
    limited = False
    max_gain = ceiling_db - to_db(pk)
    if gain > max_gain:
        gain, limited = max_gain, mode != "peak"
    return (x * (10 ** (gain / 20))).astype(x.dtype), float(gain), limited
