"""Command line of the rig: python3 -m rig <command>."""

import argparse
import json
import sys
from pathlib import Path

from rig import ssh
from rig.compare import compare
from rig.publish import publish
from rig.registry import load_suite, load_targets, suite_needs
from rig.report import render
from rig.rig import arm_ttl, ensure_ttl, from_terraform, provision, remaining_ttl_s

TF_DIR = Path("terraform")
SUITES_DIR = Path("suites")
TARGETS_FILE = SUITES_DIR / "targets.toml"
SYNC_TTL_S = 1800


def load_json(path: str) -> dict:
    return json.loads(Path(path).read_text())


def cmd_report(args: argparse.Namespace) -> int:
    text, status = render(load_json(args.run))
    print(text, end="")
    return status


def cmd_compare(args: argparse.Namespace) -> int:
    text, status = compare(load_json(args.a), load_json(args.b), force=args.force)
    print(text, end="")
    return status


def cmd_publish(args: argparse.Namespace) -> int:
    print(publish(load_json(args.run), Path(args.pages_dir)))
    return 0


def cmd_needs(args: argparse.Namespace) -> int:
    # The loader count does not change the needs. The value 1 passes every loader check.
    suite = load_suite(SUITES_DIR / f"{args.suite}.toml", load_targets(TARGETS_FILE), 1)
    print(" ".join(suite_needs(suite)))
    return 0


def cmd_provision(args: argparse.Namespace) -> int:
    if not args.nightly and not args.ref:
        raise ValueError("set NIGHTLY=<sha7> or REF=<ref>, for example: make up REF=pr/97")
    env = {
        "NIGHTLY": args.nightly,
        "REF": args.ref,
        "BASE_REF": args.base_ref,
        "NEEDS": args.needs,
        "FRAME_POINTERS": args.frame_pointers,
    }
    provision(from_terraform(TF_DIR), ttl_min=args.ttl, server_env=env)
    print("==> rig ready; run: make bench")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    rig = from_terraform(TF_DIR)
    ensure_ttl([rig.server], SYNC_TTL_S)
    ssh.stage_tree([rig.server])
    ssh.stage_dir(Path(args.src), rig.server, "core-sync")
    print(ssh.run(rig.server, f"bash {ssh.RIG_DIR}/box/build-local.sh"), end="")
    return 0


def cmd_ttl(args: argparse.Namespace) -> int:
    rig = from_terraform(TF_DIR)
    if args.set:
        arm_ttl(rig.hosts, args.set)
        print(f"TTL set to {args.set} minutes on {len(rig.hosts)} boxes")
    for host in rig.hosts:
        print(f"{host.name} {host.public_ip}: TTL {remaining_ttl_s(host) // 60} min left")
    return 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="python3 -m rig")
    sub = top.add_subparsers(dest="command", required=True)

    p = sub.add_parser("report", help="print the tables of one run file")
    p.add_argument("run")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("compare", help="print the deltas between two run files")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("publish", help="add one run file to a gh-pages checkout")
    p.add_argument("--pages-dir", required=True)
    p.add_argument("run")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("needs", help="print the server kinds and apps of a suite")
    p.add_argument("--suite", required=True)
    p.set_defaults(func=cmd_needs)

    p = sub.add_parser("provision", help="provision the server and the loaders")
    p.add_argument("--ttl", type=int, required=True)
    p.add_argument("--needs", required=True)
    p.add_argument("--nightly", default="")
    p.add_argument("--ref", default="")
    p.add_argument("--base-ref", default="main")
    p.add_argument("--frame-pointers", default="0", choices=("0", "1"))
    p.set_defaults(func=cmd_provision)

    p = sub.add_parser("sync", help="build a local rapira tree on the server")
    p.add_argument("--src", default="../core")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("ttl", help="print or set the TTL of every box")
    p.add_argument("--set", type=int, metavar="MINUTES")
    p.set_defaults(func=cmd_ttl)
    return top


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.func(args)
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
