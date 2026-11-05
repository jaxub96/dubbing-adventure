import time
from pathlib import Path

import pytest

from conftest import FIXTURES, speechlike
from gdub.reset import reset_lines
from gdub.session import Session

SR = 48000


def test_reset_moves_files_to_trash_and_forgets(project):
    lines = project.load_lines()
    s = Session.load(project)
    s.set_actor("15", mic="SM7B")
    for ln in lines[:3]:
        s.store_take(ln, speechlike(), SR)
        s.store_take(ln, speechlike(), SR)
    moved, trash = reset_lines(project, s, lines[:1])
    assert moved == 2 and trash.is_dir()
    assert not list((project.raw_dir / lines[0].npc).glob(f"{lines[0].id}__take*"))
    assert len(list(trash.rglob("*.wav"))) == 2                       # moved, not deleted
    assert not s.is_done(lines[0].id) and s.is_done(lines[1].id)
    s2 = Session.load(project)                                       # persisted
    assert not s2.is_done(lines[0].id) and s2.actors["15"]["mic"] == "SM7B"
    assert s2.reconcile(lines) == 0                                   # nothing re-adopted
    time.sleep(1.1)                                                   # distinct trash folder name
    moved, _ = reset_lines(project, s, lines, clear_actors=True)
    assert moved == 4 and not Session.load(project).actors and not any(project.raw_dir.rglob("*.wav"))
    assert reset_lines(project, s, lines)[1] is None                  # nothing left


def test_launcher_end_to_end(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"no display: {exc}")
    from tkinter import messagebox
    from gdub import launcher
    monkeypatch.setattr(launcher, "PREFS", tmp_path / "prefs.json")
    monkeypatch.setattr(messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(messagebox, "showwarning", lambda *a, **k: (_ for _ in ()).throw(AssertionError(a)))
    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: (_ for _ in ()).throw(AssertionError(a)))
    app = launcher.Launcher(root)

    def pump(until, secs=20):
        end = time.time() + secs
        while time.time() < end and not until():
            root.update()
            time.sleep(0.02)
        assert until()

    try:
        app.proj_var.set(str(tmp_path / "proj"))
        app.refresh()
        assert "No lines.csv" in app.status.cget("text")
        app.run_extract(app.project(), [FIXTURES])                    # what the Extract button does
        assert "53 lines" in app.status.cget("text")
        from gdub.session import Session
        p, lines, s = app.lines_session()
        s.store_take(lines[0], speechlike(), SR)
        app.refresh()
        assert "1/53 lines recorded" in app.status.cget("text")
        # actor notes
        app.act_voice_var.set("15"); app.act_vars["mic"].set("U87"); app.save_actor()
        assert Session.load(p).actors["15"]["mic"] == "U87"
        # recorder command line built from the form
        app.sim_var.set(True); app.rec_voice_var.set("15")
        args = app.recorder_args(p)
        assert args[1:4] == ["-m", "gdub", "record"] and "--simulate" in args and "15" in args
        # export + qc run in background threads
        import shutil
        if shutil.which("ffmpeg"):
            app.out_var.set(str(tmp_path / "speech")); app.export()
            pump(lambda: "Done: exported 1" in app.logs["export"].get("1.0", "end"))
            assert (tmp_path / "speech" / f"{lines[0].id}.wav").exists()
        app.qc_dir.set(""); app.qc()
        pump(lambda: "Quality check" in app.logs["qc"].get("1.0", "end"))
        # reset one line through the UI
        app.scope_var.set("One line"); app.refresh()
        app.target_var.set(app.target_box["values"][0]); app.reset()
        assert "0/53 lines recorded" in app.status.cget("text")
        app.save_export_defaults()
        assert '"sample_rate"' in p.config_file.read_text()
    finally:
        root.destroy()
