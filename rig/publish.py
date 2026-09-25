"""Write a run file into a gh-pages checkout and update the board manifest."""

import json
from pathlib import Path

INDEX_SCHEMA = "rapira-bench-index/1"


def index_entry(run: dict) -> dict:
    return {
        "id": run["id"],
        "started": run["started"],
        "suite": run["suite"]["name"],
        "rapira_sha": run["rapira"]["sha"],
        "rapira_version": run["rapira"]["version"],
        "status": run["status"],
        "smoke": run["smoke"],
    }


def publish(run: dict, pages_dir: Path) -> Path:
    """Write `data/<id>.json`, add or replace the run in `data/index.json`, and return the data path."""
    data = pages_dir / "data" / f"{run['id']}.json"
    data.parent.mkdir(parents=True, exist_ok=True)
    data.write_text(json.dumps(run, indent=1) + "\n")
    index_path = data.parent / "index.json"
    if index_path.exists():
        index = json.loads(index_path.read_text())
    else:
        index = {"schema": INDEX_SCHEMA, "runs": []}
    runs = [r for r in index["runs"] if r["id"] != run["id"]]
    runs.append(index_entry(run))
    runs.sort(key=lambda r: r["started"])
    index["runs"] = runs
    index_path.write_text(json.dumps(index, indent=1) + "\n")
    return data
