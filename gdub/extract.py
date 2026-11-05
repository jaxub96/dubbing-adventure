"""Step 1: build lines.csv from Daedalus dialogue scripts (.d files).

Each spoken line in a script looks like
    AI_Output (other, self, "DIA_VLK_001_Remus_INTRODUCTION_15_01"); //Who are you?
The ID is the audio filename, the text after // is the subtitle. Commented-out lines
(// ... and /* ... */) are ignored.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .lines import Line

AI_OUTPUT_RE = re.compile(
    r'^\s*AI_Output\s*\(\s*(\w+)\s*,\s*(\w+)\s*,\s*"([^"]+)"\s*\)\s*;?\s*$', re.IGNORECASE)
NPC_RE = re.compile(r"^\s*npc\s*=\s*(\w+)\s*;?\s*$", re.IGNORECASE)
ID_RE = re.compile(r"^(?P<base>.+)_(?P<voice>\d{2})_(?P<order>\d{2,3})$")

FALLBACK_ENCODING = "cp1250"  # Polish Gothic scripts; German ones are cp1252


@dataclass
class ExtractResult:
    lines: list[Line] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    files: int = 0


def read_script(path: Path, encoding: str = "auto") -> str:
    raw = Path(path).read_bytes()
    if encoding != "auto":
        return raw.decode(encoding, errors="replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode(FALLBACK_ENCODING, errors="replace")


def split_code_comment(line: str, in_block: bool) -> tuple[str, str | None, bool]:
    """Split one source line into (code, line_comment_text, still_in_block_comment).

    Block comments are removed from the code part; string literals are respected so a
    '//' inside quotes does not start a comment."""
    code: list[str] = []
    i, n, in_str = 0, len(line), False
    while i < n:
        if in_block:
            j = line.find("*/", i)
            if j < 0:
                return "".join(code), None, True
            in_block, i = False, j + 2
            continue
        c = line[i]
        if in_str:
            code.append(c)
            in_str = c != '"'
            i += 1
        elif c == '"':
            in_str = True
            code.append(c)
            i += 1
        elif line.startswith("//", i):
            return "".join(code), line[i + 2:], in_block
        elif line.startswith("/*", i):
            in_block = True
            i += 2
        else:
            code.append(c)
            i += 1
    return "".join(code), None, in_block


def scene_of(base: str, npc: str) -> str:
    """DIA_Addon_Dexter_Hallo (npc BDT_1060_Dexter) -> Hallo."""
    rest = base[4:] if base.upper().startswith("DIA_") else base
    padded = f"_{rest}_"
    short = Line("", npc, "", "", "", "").npc_name
    for name in (npc, short):
        k = padded.lower().find(f"_{name.lower()}_")
        if k >= 0:
            return padded[k + len(name) + 2:].strip("_") or rest
    return rest


def npc_from_filename(path: Path) -> str:
    stem = Path(path).stem
    return stem[4:] if stem.upper().startswith("DIA_") else stem


def extract_text(text: str, source: str = "", fallback_npc: str = "") -> ExtractResult:
    res = ExtractResult()
    npc = fallback_npc
    in_block = False
    seen: dict[str, Line] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        code, comment, in_block = split_code_comment(raw, in_block)
        m = NPC_RE.match(code)
        if m:
            npc = m.group(1)
            continue
        m = AI_OUTPUT_RE.match(code)
        if not m:
            continue
        who, _, lid = m.group(1).lower(), m.group(2), m.group(3)
        where = f"{source}:{lineno}"
        idm = ID_RE.match(lid)
        if not idm:
            res.warnings.append(f"{where}: ID '{lid}' does not end in _<voice>_<nr>, skipped")
            continue
        body = (comment or "").strip()
        if not body:
            res.warnings.append(f"{where}: {lid} has no text after //")
        if not npc:
            res.warnings.append(f"{where}: {lid} - no NPC known (no 'npc =' seen, no filename)")
        prev = seen.get(lid.lower())
        if prev is not None:
            if prev.text != body:
                res.warnings.append(f"{where}: duplicate ID {lid} with different text "
                                    f"(keeping {prev.file}:{prev.line})")
            continue
        ln = Line(lid, npc, scene_of(idm["base"], npc), idm["voice"], idm["order"], body,
                  who, source, lineno)
        seen[lid.lower()] = ln
        res.lines.append(ln)
    return res


def extract_paths(paths: list[Path], encoding: str = "auto") -> ExtractResult:
    """Extract from files and/or directories (searched recursively for *.d)."""
    files: list[Path] = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            files += sorted((f for f in p.rglob("*") if f.suffix.lower() == ".d"),
                            key=lambda f: str(f).lower())
        elif p.is_file():
            files.append(p)
        else:
            raise FileNotFoundError(p)
    total = ExtractResult()
    seen: dict[str, Line] = {}
    for f in files:
        r = extract_text(read_script(f, encoding), source=f.name, fallback_npc=npc_from_filename(f))
        total.files += 1
        total.warnings += r.warnings
        for ln in r.lines:
            prev = seen.get(ln.id.lower())
            if prev is not None:
                if prev.text != ln.text:
                    total.warnings.append(f"{ln.file}:{ln.line}: duplicate ID {ln.id} with different "
                                          f"text (keeping {prev.file}:{prev.line})")
                continue
            seen[ln.id.lower()] = ln
            total.lines.append(ln)
    return total
