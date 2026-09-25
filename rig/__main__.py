"""Command line of the bench rig: `python3 -m rig <command>`."""

import argparse
import json
import sys
from pathlib import Path

from rig.compare import compare
from rig.publish import publish
from rig.report import render


def cmd_report(args) -> int:
    text, status = render(json.loads(Path(args.run).read_text()))
    sys.stdout.write(text)
    return status


def cmd_compare(args) -> int:
    a = json.loads(Path(args.a).read_text())
    b = json.loads(Path(args.b).read_text())
    text, status = compare(a, b, force=args.force)
    sys.stdout.write(text)
    return status


def cmd_publish(args) -> int:
    print(publish(json.loads(Path(args.run).read_text()), Path(args.pages_dir)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m rig")
    sub = parser.add_subparsers(dest="command", required=True)

    report = sub.add_parser("report", help="print the tables of one run file")
    report.add_argument("run", help="path to run.json")
    report.set_defaults(func=cmd_report)

    comp = sub.add_parser("compare", help="print the held and peak deltas of two run files")
    comp.add_argument("a", help="path to the first run.json")
    comp.add_argument("b", help="path to the second run.json")
    comp.add_argument("--force", action="store_true", help="compare runs whose rig identity differs")
    comp.set_defaults(func=cmd_compare)

    pub = sub.add_parser("publish", help="write a run file into a gh-pages checkout")
    pub.add_argument("--pages-dir", required=True, help="path to the gh-pages checkout")
    pub.add_argument("run", help="path to run.json")
    pub.set_defaults(func=cmd_publish)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
