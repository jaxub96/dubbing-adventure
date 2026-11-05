"""Audio capture/playback. sounddevice is imported lazily so the rest of the toolkit
(extract/export/qc) works on machines without PortAudio."""
from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path

import numpy as np

MASTER_RATE = 48000
PREROLL_MS = 300


class PreRollBuffer:
    """Keeps the last `preroll_ms` of audio while idle; start() turns that into the
    beginning of the take so the first syllable is never cut off."""

    def __init__(self, samplerate: int, preroll_ms: int = PREROLL_MS):
        self.preroll_samples = int(samplerate * preroll_ms / 1000)
        self._ring: deque[np.ndarray] = deque()
        self._ring_len = 0
        self._take: list[np.ndarray] = []
        self.recording = False
        self._lock = threading.Lock()

    def feed(self, block: np.ndarray) -> None:
        with self._lock:
            if self.recording:
                self._take.append(block)
                return
            self._ring.append(block)
            self._ring_len += len(block)
            while self._ring and self._ring_len - len(self._ring[0]) >= self.preroll_samples:
                self._ring_len -= len(self._ring.popleft())

    def start(self) -> None:
        with self._lock:
            if self.recording:
                return
            pre = np.concatenate(list(self._ring)) if self._ring else np.zeros(0, np.float32)
            pre = pre[-self.preroll_samples:] if self.preroll_samples else pre[:0]
            self._take = [pre]
            self._ring.clear()
            self._ring_len = 0
            self.recording = True

    def stop(self) -> np.ndarray:
        with self._lock:
            self.recording = False
            data = np.concatenate(self._take) if self._take else np.zeros(0, np.float32)
            self._take = []
            return data

    @property
    def take_samples(self) -> int:
        with self._lock:
            return sum(len(b) for b in self._take)


class AudioEngine:
    """Always-on input stream feeding a PreRollBuffer, plus simple playback."""

    def __init__(self, samplerate: int = MASTER_RATE, device=None, channel: int = 1,
                 preroll_ms: int = PREROLL_MS, output_device=None):
        self.samplerate = samplerate
        self.device = device
        self.channel = max(1, channel)          # 1-based input channel
        self.output_device = output_device
        self.buf = PreRollBuffer(samplerate, preroll_ms)
        self.device_name = ""
        self.overflows = 0
        self._peak = 0.0
        self._stream = None
        self._sd = None

    # -- lifecycle
    def open(self) -> None:
        import sounddevice as sd
        self._sd = sd
        info = sd.query_devices(self.device, "input")
        if info["max_input_channels"] < self.channel:
            raise RuntimeError(f"input device '{info['name']}' has only "
                               f"{info['max_input_channels']} channel(s)")
        self.device_name = info["name"]
        self._stream = sd.InputStream(samplerate=self.samplerate, device=self.device,
                                      channels=self.channel, dtype="float32",
                                      blocksize=1024, callback=self._callback)
        self._stream.start()

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _callback(self, indata, frames, time_info, status) -> None:
        if status.input_overflow:
            self.overflows += 1
        block = indata[:, self.channel - 1].copy()
        self._peak = max(self._peak, float(np.max(np.abs(block))) if len(block) else 0.0)
        self.buf.feed(block)

    # -- recording
    @property
    def recording(self) -> bool:
        return self.buf.recording

    def start_take(self) -> None:
        self.stop_playback()
        self.buf.start()

    def stop_take(self) -> np.ndarray:
        return self.buf.stop()

    def take_seconds(self) -> float:
        return self.buf.take_samples / self.samplerate

    def poll_peak(self) -> float:
        """Peak (0..1) since the last call."""
        p, self._peak = self._peak, 0.0
        return p

    # -- playback
    def play_file(self, path: Path) -> None:
        import soundfile as sf
        data, sr = sf.read(str(path), dtype="float32")
        self._sd.play(data, sr, device=self.output_device)

    def stop_playback(self) -> None:
        if self._sd is not None:
            self._sd.stop()


class SimulatedEngine:
    """No hardware: 'records' a quiet tone. For trying the UI and for tests."""

    def __init__(self, samplerate: int = MASTER_RATE, **_):
        self.samplerate = samplerate
        self.device_name = "simulated input"
        self.overflows = 0
        self._t0: float | None = None
        self._phase = 0.0
        self.last_played: Path | None = None

    def open(self) -> None: ...
    def close(self) -> None: ...

    @property
    def recording(self) -> bool:
        return self._t0 is not None

    def start_take(self) -> None:
        self._t0 = time.monotonic()

    def stop_take(self) -> np.ndarray:
        dur = 0.3 + (time.monotonic() - (self._t0 or time.monotonic()))
        self._t0 = None
        t = np.arange(int(dur * self.samplerate)) / self.samplerate
        return (0.3 * np.sin(2 * np.pi * 220 * t) * np.hanning(len(t))).astype(np.float32)

    def take_seconds(self) -> float:
        return 0.0 if self._t0 is None else time.monotonic() - self._t0

    def poll_peak(self) -> float:
        return 0.3 if self.recording else 0.01

    def play_file(self, path: Path) -> None:
        self.last_played = path

    def stop_playback(self) -> None: ...


def list_devices() -> str:
    import sounddevice as sd
    return str(sd.query_devices())
