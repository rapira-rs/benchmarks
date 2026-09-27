"""Build and write the run file."""

import json
from pathlib import Path

from rig import VERSION
from rig.summary import summarize

SCHEMA = "rapira-bench-run/3"


def run_status(plan: list[str], cells: list[dict]) -> tuple[str, list[str]]:
    """The run status and its reasons.

    A missing, incomplete or unplanned cell makes the run incomplete. Else a target whose every new cell is void makes it broken.
    """
    by_key = {cell["key"]: cell for cell in cells}
    reasons = []
    for key in plan:
        cell = by_key.get(key)
        if cell is None:
            reasons.append(f"{key}: missing")
        elif cell["status"] not in ("ok", "void"):
            reasons.append(f"{key}: {cell['status']}")
    for cell in cells:
        if cell["key"] not in plan:
            reasons.append(f"{cell['key']}: unplanned cell")
    if reasons:
        return "incomplete", reasons
    new_void = {}
    for cell in cells:
        if cell["build"] == "new":
            name = cell["target"]["name"]
            new_void[name] = new_void.get(name, True) and cell["status"] == "void"
    reasons = [f"{name}: every new cell is void" for name, void in new_void.items() if void]
    if reasons:
        return "broken", reasons
    return "complete", []


class RunFile:
    """The `rapira-bench-run/3` document of one run. `rapira` is the record of the new build, `base` of the base build."""

    def __init__(self, *, run_id: str, suite: dict, rig: dict, rapira: dict, base: dict, servers: dict, apps: dict, loaders: list[dict], plan: list[str], smoke: bool, started: str):
        self.doc = {
            "schema": SCHEMA,
            "id": run_id,
            "suite": suite,
            "smoke": smoke,
            "started": started,
            "finished": None,
            "rig": rig,
            "rapira": rapira,
            "base": base,
            "servers": servers,
            "apps": apps,
            "loaders": loaders,
            "plan": plan,
            "cells": [],
            "status": "incomplete",
            "reasons": [],
            "summary": {},
            "reporter": VERSION,
        }

    def add_cell(self, cell: dict) -> None:
        """Append one finished cell."""
        self.doc["cells"].append(cell)

    def finish(self, finished: str) -> dict:
        """Set the end time, the status and the summary, and return the document."""
        self.doc["finished"] = finished
        self.doc["status"], self.doc["reasons"] = run_status(self.doc["plan"], self.doc["cells"])
        self.doc["summary"] = summarize(self.doc["cells"])
        return self.doc

    def write(self, path: Path) -> None:
        """Write the document as JSON with a trailing newline."""
        path.write_text(json.dumps(self.doc, indent=1, sort_keys=False) + "\n")
