"""The line list: one row per spoken line, the single source of truth."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

HERO_VOICE = "15"  # Gothic's hero voice number (suffix _15_ in line IDs)
CSV_FIELDS = ["id", "npc", "scene", "speaker", "order", "text", "who", "file", "line"]


@dataclass(frozen=True)
class Line:
    id: str
    npc: str          # e.g. BDT_1060_Dexter
    scene: str        # e.g. Hallo
    speaker: str      # voice number as in the ID, e.g. "09" or "15"
    order: str        # e.g. "00"
    text: str
    who: str = ""     # "self"/"other" as written in AI_Output (first argument)
    file: str = ""    # source script
    line: int = 0     # line number in the source script

    @property
    def npc_name(self) -> str:
        """Short character name: VLK_001_Remus -> Remus."""
        tokens = [t for t in self.npc.split("_") if t]
        for i in range(len(tokens) - 1, -1, -1):
            if tokens[i].isdigit() and i + 1 < len(tokens):
                return "_".join(tokens[i + 1:])
        return self.npc

    @property
    def is_hero(self) -> bool:
        return self.speaker == HERO_VOICE

    @property
    def filename(self) -> str:
        return f"{self.id}.wav"


def write_lines_csv(lines: list[Line], path: Path) -> None:
    # utf-8 with BOM so Excel opens Polish characters correctly; the reader accepts both.
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        w.writeheader()
        for ln in lines:
            w.writerow({k: getattr(ln, k) for k in CSV_FIELDS})


def read_lines_csv(path: Path) -> list[Line]:
    """Read the CSV. Only id, npc, speaker and text are strictly needed, so hand-written
    CSVs in the documented format work; the speaker/order are derived from the ID if blank."""
    out: list[Line] = []
    seen: set[str] = set()
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for n, row in enumerate(csv.DictReader(fh), start=2):
            lid = (row.get("id") or "").strip()
            if not lid:
                continue
            if lid.lower() in seen:
                raise ValueError(f"{path}:{n}: duplicate line id {lid}")
            seen.add(lid.lower())
            npc = (row.get("npc") or "").strip()
            if not npc:
                raise ValueError(f"{path}:{n}: line {lid} has no npc")
            parts = lid.rsplit("_", 2)
            speaker = (row.get("speaker") or "").strip() or (parts[1] if len(parts) == 3 else "")
            order = (row.get("order") or "").strip() or (parts[2] if len(parts) == 3 else "")
            try:
                src_line = int(row.get("line") or 0)
            except ValueError:
                src_line = 0
            out.append(Line(lid, npc, (row.get("scene") or "").strip(), speaker, order,
                            (row.get("text") or "").strip(), (row.get("who") or "").strip(),
                            (row.get("file") or "").strip(), src_line))
    return out
