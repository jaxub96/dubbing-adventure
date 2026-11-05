"""Navigation/filter logic of the recorder, independent of any GUI toolkit."""
from __future__ import annotations

from .lines import HERO_VOICE, Line
from .session import Session


def related_scenes(a: str, b: str) -> bool:
    a, b = a.lower(), b.lower()
    return a == b or a.startswith(b + "_") or b.startswith(a + "_")


class Workspace:
    def __init__(self, lines: list[Line], session: Session):
        if not lines:
            raise ValueError("the line list is empty")
        self.lines = lines
        self.session = session
        self._index = {ln.id: i for i, ln in enumerate(lines)}
        ui = session.ui
        self.voice_filter: str | None = ui.get("voice_filter")
        self.npc_filter: str | None = ui.get("npc_filter")
        self.only_missing: bool = bool(ui.get("only_missing", False))
        self.auto_advance: bool = bool(ui.get("auto_advance", True))
        self.current_id: str = ui.get("last_line") if ui.get("last_line") in self._index else lines[0].id
        self.visible: list[Line] = []
        self.refresh()

    # ---- filters
    def voices(self) -> list[str]:
        return sorted({ln.speaker for ln in self.lines})

    def npcs(self) -> list[str]:
        return sorted({ln.npc for ln in self.lines}, key=str.lower)

    def matches(self, ln: Line) -> bool:
        if self.voice_filter and ln.speaker != self.voice_filter:
            return False
        if self.npc_filter and ln.npc != self.npc_filter:
            return False
        return True

    def refresh(self) -> None:
        """Recompute the visible list. The current line always stays visible."""
        def shown(ln: Line) -> bool:
            if not self.matches(ln):
                return False
            return not self.only_missing or ln.id == self.current_id or not self.session.is_done(ln.id)

        self.visible = [ln for ln in self.lines if shown(ln)]
        if self.current_id not in {ln.id for ln in self.visible}:
            # current line filtered out: jump to the next visible line in script order
            gi = self._index[self.current_id]
            after = [ln for ln in self.visible if self._index[ln.id] >= gi]
            pick = after[0] if after else (self.visible[-1] if self.visible else None)
            if pick:
                self.current_id = pick.id
        self._persist()

    def set_filters(self, voice: str | None = ..., npc: str | None = ..., only_missing: bool | None = None):
        if voice is not ...:
            self.voice_filter = voice or None
        if npc is not ...:
            self.npc_filter = npc or None
        if only_missing is not None:
            self.only_missing = only_missing
        self.refresh()

    def cycle_voice_filter(self) -> str | None:
        order = [None] + self.voices()
        i = order.index(self.voice_filter) if self.voice_filter in order else 0
        self.set_filters(voice=order[(i + 1) % len(order)])
        return self.voice_filter

    # ---- navigation
    @property
    def current(self) -> Line | None:
        return self.lines[self._index[self.current_id]] if self.visible else None

    def _pos(self) -> int:
        for i, ln in enumerate(self.visible):
            if ln.id == self.current_id:
                return i
        return 0

    def goto(self, line_id: str) -> None:
        if line_id in self._index:
            self.current_id = line_id
            self.refresh()

    def move(self, delta: int) -> bool:
        if not self.visible:
            return False
        i = self._pos() + delta
        if not 0 <= i < len(self.visible):
            return False
        self.current_id = self.visible[i].id
        self.refresh()
        return True

    def next_unrecorded(self) -> bool:
        """Jump to the next not-yet-done line after the current one (no wrap)."""
        for ln in self.visible[self._pos() + 1:]:
            if not self.session.is_done(ln.id):
                self.current_id = ln.id
                self.refresh()
                return True
        return False

    # ---- info
    def context_line(self, ln: Line) -> Line | None:
        """Nearest earlier line of a different voice in the same conversation, so the actor
        hears what they are answering. None at the start of a conversation."""
        i = self._index[ln.id]
        while i > 0:
            i -= 1
            prev = self.lines[i]
            if prev.file != ln.file or not related_scenes(prev.scene, ln.scene):
                return None
            if prev.speaker != ln.speaker:
                return prev
        return None

    def progress(self, npc: str | None = None, voice: str | None = None) -> tuple[int, int]:
        sel = [ln for ln in self.lines
               if (npc is None or ln.npc == npc) and (voice is None or ln.speaker == voice)]
        return sum(self.session.is_done(ln.id) for ln in sel), len(sel)

    def speaker_label(self, voice: str) -> str:
        return f"HERO ({voice})" if voice == HERO_VOICE else f"voice {voice}"

    def _persist(self) -> None:
        self.session.ui.update(voice_filter=self.voice_filter, npc_filter=self.npc_filter,
                               only_missing=self.only_missing, auto_advance=self.auto_advance,
                               last_line=self.current_id)
