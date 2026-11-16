"""powerdeck CLI: list | status | plan NAME | apply NAME [-y] | restore | reset-all | snapshot | monitor | guard"""
from __future__ import annotations

import argparse
import sys

from . import engine, guard
from .capture import capture_current
from .describe import describe
from .logs import setup_logging
from .profile import load_profiles
from .status import read_status


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="powerdeck")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("status")
    sub.add_parser("restore")
    r = sub.add_parser("reset-all", help="undo every applied profile (used when uninstalling)")
    r.add_argument("--only", choices=("user", "system"))
    r.add_argument("--backups", help="backup directory (default: your own)")
    r.add_argument("--no-mark", action="store_true", help="keep backups for a second pass")
    sub.add_parser("snapshot", help="save current system settings as the Default profile")
    m = sub.add_parser("monitor", help="live temperature/frequency/load/power monitor")
    m.add_argument("--interval", type=float, default=2.0)
    m.add_argument("--duration", type=float, help="stop after N seconds")
    m.add_argument("--csv", help="also write samples to this CSV file")
    g = sub.add_parser("guard", help="run thermal/battery guard")
    g.add_argument("--once", action="store_true")
    g.add_argument("--interval", type=int, default=30)
    for c in ("plan", "apply"):
        s = sub.add_parser(c)
        s.add_argument("name")
        if c == "apply":
            s.add_argument("-y", "--yes", action="store_true", help="skip confirmation")
    a = ap.parse_args(argv)

    setup_logging()
    profiles = {p.name: p for p in load_profiles()}
    if a.cmd == "list":
        active = engine.active_profile()
        w = max(map(len, profiles)) + 2
        for n, p in profiles.items():
            print(f"{'*' if n == active else ' '} {n:{w}} {p.description}")
        return 0
    if a.cmd == "status":
        from dataclasses import asdict
        for k, v in asdict(read_status(remote_login=True)).items():
            print(f"{k}: {v}")
        return 0
    if a.cmd == "monitor":
        from . import monitor
        monitor.run_cli(a.interval, a.duration, a.csv)
        return 0
    if a.cmd == "snapshot":
        from .profile import save_profile
        print("Saved:", save_profile(capture_current()))
        return 0
    if a.cmd == "guard":
        if a.once:
            guard.run_once(guard.GuardState())
        else:
            guard.loop(a.interval)
        return 0
    if a.cmd == "reset-all":
        from pathlib import Path
        errs = engine.reset_all(backup_dir=Path(a.backups) if a.backups else None,
                                only=a.only, mark_restored=not a.no_mark)
        print("\n".join(errs) or "All PowerDeck changes reverted.")
        return 1 if errs else 0
    if a.cmd == "restore":
        errs = engine.restore_last()
        print("\n".join(errs) or "Restored.")
        return 1 if errs else 0
    if a.name not in profiles:
        print(f"Unknown profile: {a.name}", file=sys.stderr)
        return 2
    p, previous = profiles[a.name], profiles.get(engine.active_profile() or "")
    plan = engine.make_plan(p, previous=previous)
    # switching undoes the previous profile first, then applies NAME on that clean state
    undo = engine.pending_undo() if previous is None or previous.name != p.name else []
    if undo:
        print("Undo the active profile's changes:\n" + engine.undo_text(undo))
        print(f"Then apply {p.name}:")
        print("\n".join("• " + it.text for it in describe(p).items))
    else:
        print(plan.diff_text())
    if p.remote_access:
        print("\n".join(guard.remote_access_notes()))
    if a.cmd == "plan" or (plan.empty and not undo):
        return 0
    if not a.yes and input("Apply? [y/N] ").strip().lower() != "y":
        return 1
    errs = engine.switch_to(p, previous=previous)
    print("\n".join(errs) or "Applied.")
    return 1 if errs else 0

if __name__ == "__main__":
    sys.exit(main())
