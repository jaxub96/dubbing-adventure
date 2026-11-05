"""Project folder layout. Everything lives in one folder:

    lines.csv        the line list (from `gdub extract`)
    raw/<NPC>/       master takes, never overwritten
    session.json     chosen takes, actor/mic notes, UI state
    project.json     optional defaults, e.g. {"export": {"sample_rate": 22050}}
    export_log.csv   written by `gdub export`
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .lines import Line, read_lines_csv


@dataclass
class Project:
    root: Path

    def __post_init__(self):
        self.root = Path(self.root)

    @property
    def lines_csv(self) -> Path:
        return self.root / "lines.csv"

    @property
    def raw_dir(self) -> Path:
        return self.root / "raw"

    @property
    def session_file(self) -> Path:
        return self.root / "session.json"

    @property
    def config_file(self) -> Path:
        return self.root / "project.json"

    @property
    def export_log(self) -> Path:
        return self.root / "export_log.csv"

    def load_lines(self) -> list[Line]:
        if not self.lines_csv.exists():
            raise FileNotFoundError(
                f"{self.lines_csv} not found - run `gdub extract <scripts> -o {self.lines_csv}` first")
        return read_lines_csv(self.lines_csv)

    def config(self) -> dict:
        if self.config_file.exists():
            return json.loads(self.config_file.read_text(encoding="utf-8"))
        return {}
