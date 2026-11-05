"""Reset takes. Files are MOVED to <project>/_trash/<timestamp>/ (not deleted), so a reset
can be undone by moving them back."""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from .lines import Line
from .project import Project
from .session import TAKE_RE, Session


def reset_lines(project: Project, session: Session, lines: list[Line],
                clear_actors: bool = False) -> tuple[int, Path | None]:
    """Move all takes of `lines` to the trash folder and forget them in session.json.
    Returns (files moved, trash folder or None if nothing was on disk)."""
    trash = project.root / "_trash" / time.strftime("%Y%m%d-%H%M%S")
    moved = 0
    for ln in lines:
        folder = project.raw_dir / ln.npc
        if not folder.is_dir():
            continue
        for f in sorted(folder.iterdir()):
            m = TAKE_RE.match(f.name)
            if m and m["id"].lower() == ln.id.lower():
                dst = trash / "raw" / ln.npc / f.name
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(f), str(dst))
                moved += 1
    session.forget([ln.id for ln in lines])
    if clear_actors:
        session.actors.clear()
        session.save()
    return moved, (trash if moved else None)
