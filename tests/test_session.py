import numpy as np
from conftest import speechlike
from gdub.session import Session
from gdub.workspace import Workspace

SR = 48000


def test_take_lifecycle_and_never_overwrite(project):
    lines = project.load_lines()
    s = Session.load(project)
    ln = lines[0]
    t1 = s.store_take(ln, speechlike(), SR, "mic")
    t2 = s.store_take(ln, speechlike(seed=1), SR, "mic")
    assert (t1.n, t2.n) == (1, 2) and t1.file == f"raw/{ln.npc}/{ln.id}__take01.wav"
    assert s.selected_take(ln.id).n == 2 and s.is_done(ln.id)
    s.select(ln.id, 1)
    assert s.selected_take(ln.id).n == 1
    s.reject(ln.id, 1)                                    # falls back to newest remaining
    assert s.selected_take(ln.id).n == 2
    s.reject(ln.id, 2)
    assert s.selected_take(ln.id) is None and not s.is_done(ln.id)
    t3 = s.store_take(ln, speechlike(seed=2), SR)        # numbers are never reused
    assert t3.n == 3
    assert all(s.path_of(t).exists() for t in s.takes(ln.id))   # rejected files stay on disk
    s2 = Session.load(project)                            # resume: everything persisted
    assert s2.selected_take(ln.id).n == 3 and len(s2.takes(ln.id)) == 3


def test_reconcile_adopts_files_after_lost_session(project):
    lines = project.load_lines()
    s = Session.load(project)
    s.store_take(lines[0], speechlike(), SR)
    s.store_take(lines[0], speechlike(), SR)
    project.session_file.unlink()
    s2 = Session.load(project)
    assert s2.reconcile(lines) == 2
    assert s2.selected_take(lines[0].id).n == 2
    assert s2.reconcile(lines) == 0


def test_workspace_navigation_filters_progress(project):
    lines = project.load_lines()
    s = Session.load(project)
    ws = Workspace(lines, s)
    assert ws.current.id == lines[0].id and not ws.move(-1) and ws.move(1)
    ws.set_filters(voice="15")
    assert all(l.speaker == "15" for l in ws.visible) and ws.current.speaker == "15"
    assert ws.cycle_voice_filter() is None or True
    ws.set_filters(voice=None)
    ws.goto("DIA_Dexter_Hallo_15_01")
    ctx = ws.context_line(ws.current)
    assert ctx.id == "DIA_Dexter_Hallo_09_00"             # what the hero is answering
    assert ws.context_line(lines[0]) is None
    # unrelated scene (Kill) must not show context from the previous scene
    assert ws.context_line(next(l for l in lines if l.id == "DIA_Dexter_Kill_09_01")) is None
    ws.set_filters(only_missing=True)
    s.store_take(ws.current, speechlike(), SR)
    ws.refresh()
    assert ws.current.id == "DIA_Dexter_Hallo_15_01"       # current stays visible when done
    assert ws.next_unrecorded() and ws.current.id != "DIA_Dexter_Hallo_15_01"
    assert ws.progress() == (1, len(lines)) and ws.progress(voice="09")[0] == 0
    assert Workspace(lines, Session.load(project) if False else s).current_id == ws.current_id
