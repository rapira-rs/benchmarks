"""Build and write the run file."""

import json
from pathlib import Path

from rig import VERSION

SCHEMA = "rapira-bench-run/1"


def run_status(plan: list[str], cells: list[dict]) -> tuple[str, list[str]]:
    """The run status and one reason per missing, void, incomplete, or unplanned cell."""
    by_key = {cell["key"]: cell for cell in cells}
    reasons = []
    for key in plan:
        cell = by_key.get(key)
        if cell is None:
            reasons.append(f"{key}: missing")
        elif cell["status"] == "void":
            reasons.append(f"{key}: void: {cell['reason']}")
        elif cell["status"] != "ok":
            reasons.append(f"{key}: {cell['status']}")
    for cell in cells:
        if cell["key"] not in plan:
            reasons.append(f"{cell['key']}: unplanned cell")
    if reasons:
        return "incomplete", reasons
    return "complete", []


class RunFile:
    """The `rapira-bench-run/1` document of one run."""

    def __init__(self, *, run_id: str, suite: dict, rig: dict, rapira: dict, servers: dict, apps: dict, loaders: list[dict], ladder: dict, processes: int, plan: list[str], smoke: bool, started: str):
        self.doc = {
            "schema": SCHEMA,
            "id": run_id,
            "suite": suite,
            "smoke": smoke,
            "started": started,
            "finished": None,
            "rig": rig,
            "rapira": rapira,
            "servers": servers,
            "apps": apps,
            "loaders": loaders,
            "ladder": ladder,
            "processes": processes,
            "plan": plan,
            "cells": [],
            "status": "incomplete",
            "reasons": [],
            "reporter": VERSION,
        }

    def add_cell(self, cell: dict) -> None:
        """Append one finished cell."""
        self.doc["cells"].append(cell)

    def finish(self, finished: str) -> dict:
        """Set the end time and the status, and return the document."""
        self.doc["finished"] = finished
        self.doc["status"], self.doc["reasons"] = run_status(self.doc["plan"], self.doc["cells"])
        return self.doc

    def write(self, path: Path) -> None:
        """Write the document as JSON with a trailing newline."""
        path.write_text(json.dumps(self.doc, indent=1, sort_keys=False) + "\n")
