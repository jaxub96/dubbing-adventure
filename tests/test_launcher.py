import time
from pathlib import Path

import pytest

from conftest import FIXTURES, speechlike
from gdub.session import Session

SR = 48000


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
        app.save_export_defaults()
        assert '"sample_rate"' in p.config_file.read_text()
    finally:
        root.destroy()
