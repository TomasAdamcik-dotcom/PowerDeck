"""powerdeck CLI: list | status | plan NAME | apply NAME [-y] | restore"""
from __future__ import annotations

import argparse
import sys

from . import engine
from .profile import load_profiles
from .status import read_status


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="powerdeck")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("status")
    sub.add_parser("restore")
    for c in ("plan", "apply"):
        s = sub.add_parser(c)
        s.add_argument("name")
        if c == "apply":
            s.add_argument("-y", "--yes", action="store_true", help="skip confirmation")
    a = ap.parse_args(argv)

    profiles = {p.name: p for p in load_profiles()}
    if a.cmd == "list":
        active = engine.active_profile()
        for n, p in profiles.items():
            print(f"{'*' if n == active else ' '} {n:14} {p.description}")
        return 0
    if a.cmd == "status":
        print(read_status())
        return 0
    if a.cmd == "restore":
        errs = engine.restore_last()
        print("\n".join(errs) or "Restored.")
        return 1 if errs else 0
    if a.name not in profiles:
        print(f"Unknown profile: {a.name}", file=sys.stderr)
        return 2
    plan = engine.make_plan(profiles[a.name])
    print(plan.diff_text())
    if a.cmd == "plan" or plan.empty:
        return 0
    if not a.yes and input("Apply? [y/N] ").strip().lower() != "y":
        return 1
    errs = engine.apply_plan(plan)
    print("\n".join(errs) or "Applied.")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
