"""Command line of the bench rig: `python3 -m rig <command>`."""

import argparse
import json
import sys
from pathlib import Path

from rig.report import render


def cmd_report(args) -> int:
    text, status = render(json.loads(Path(args.run).read_text()))
    sys.stdout.write(text)
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m rig")
    sub = parser.add_subparsers(dest="command", required=True)

    report = sub.add_parser("report", help="print the tables of one run file")
    report.add_argument("run", help="path to run.json")
    report.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
