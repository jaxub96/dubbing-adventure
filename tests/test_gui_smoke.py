"""Drives the real Tk recorder with the simulated engine (no audio hardware needed)."""
import time

import pytest

tk = pytest.importorskip("tkinter")

from gdub.audio import SimulatedEngine
from gdub.session import Session
from gdub.workspace import Workspace


def test_gui_record_pick_reject_resume(project):
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"no display: {exc}")
    from gdub.gui import RecorderApp
    lines = project.load_lines()
    s = Session.load(project)
    ws = Workspace(lines, s)
    eng = SimulatedEngine(48000)
    app = RecorderApp(root, ws, eng)

    def pump(t=0.1):
        end = time.time() + t
        while time.time() < end:
            root.update()
            time.sleep(0.01)

    def key(k):
        app._on_key(type("E", (), {"keysym": k})())
        pump(0.28)       # longer than the key-repeat debounce

    try:
        pump()
        first, second = lines[0], lines[1]
        key("space"); pump(0.3); key("space")                 # record -> auto-advance
        assert s.selected_take(first.id).n == 1 and ws.current_id == second.id
        key("Left"); key("space"); pump(0.3); key("space")    # second take of line 1
        assert [t.n for t in s.takes(first.id)] == [1, 2]
        key("Left") if ws.current_id != first.id else None
        key("1")
        assert s.selected_take(first.id).n == 1
        key("p"); assert eng.last_played.name.endswith("take01.wav")
        key("BackSpace")                                      # Mac delete key rejects
        assert s.selected_take(first.id).n == 2
        key("f"); assert ws.voice_filter is not None          # speaker filter cycles
        key("Right")                                          # modifier keys are ignored
        assert app._on_key(type("E", (), {"keysym": "Shift_L"})()) is None
        assert "lines done" in app.progress.cget("text")
    finally:
        app.close()
    assert Session.load(project).selected_take(lines[0].id).n == 2   # resumed state persisted
