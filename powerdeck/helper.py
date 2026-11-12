#!/usr/bin/python3
"""Privileged helper, installed as /usr/libexec/powerdeck-helper (root-owned)
and run via ``pkexec /usr/libexec/powerdeck-helper …`` (see data/*.policy).

It can ONLY manage the PowerDeck logind drop-in. It never executes
arbitrary commands and validates every argument against a whitelist.
"""
from __future__ import annotations

import os
import subprocess
import sys

DROPIN_DIR = "/etc/systemd/logind.conf.d"
DROPIN = "99-powerdeck-lid.conf"
ACTIONS = {"ignore", "suspend", "lock", "poweroff"}
KEYS = ("HandleLidSwitch", "HandleLidSwitchExternalPower", "HandleLidSwitchDocked")


def render(battery: str, ac: str, docked: str) -> str:
    vals = (battery, ac, docked)
    if any(v not in ACTIONS for v in vals):
        raise ValueError("invalid action")
    lines = ["# Managed by PowerDeck. Remove this file to restore defaults.", "[Login]"]
    lines += [f"{k}={v}" for k, v in zip(KEYS, vals)]
    return "\n".join(lines) + "\n"


def _reload_logind() -> None:
    # HUP makes logind re-read its config without restarting the session.
    subprocess.run(["systemctl", "kill", "-s", "HUP", "systemd-logind"], check=False)


def main(argv: list[str], dropin_dir: str = DROPIN_DIR, reload: bool = True) -> int:
    if len(argv) == 1 and argv[0] == "remove-lid":
        try:
            os.remove(os.path.join(dropin_dir, DROPIN))
        except FileNotFoundError:
            pass
    elif len(argv) == 4 and argv[0] == "set-lid":
        try:
            content = render(*argv[1:])
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2
        os.makedirs(dropin_dir, exist_ok=True)
        target = os.path.join(dropin_dir, DROPIN)
        tmp = target + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp, target)  # atomic: never leaves a half-written config
    else:
        print("usage: set-lid BATTERY AC DOCKED | remove-lid", file=sys.stderr)
        return 2
    if reload:
        _reload_logind()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
