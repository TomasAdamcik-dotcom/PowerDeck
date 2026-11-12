"""Privileged helper, run via ``pkexec python3 -m powerdeck.helper …``.

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
        with open(os.path.join(dropin_dir, DROPIN), "w") as f:
            f.write(content)
    else:
        print("usage: set-lid BATTERY AC DOCKED | remove-lid", file=sys.stderr)
        return 2
    if reload:
        _reload_logind()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
