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


CPU_ROOT = "/sys/devices/system/cpu"
UNLIMITED_KHZ = 100_000_000  # 100 GHz, clamped by the kernel


def set_turbo(on: bool, root: str = CPU_ROOT) -> int:
    for path, val in ((f"{root}/intel_pstate/no_turbo", "0" if on else "1"),
                      (f"{root}/cpufreq/boost", "1" if on else "0")):
        if os.path.exists(path):
            with open(path, "w") as f:
                f.write(val)
            return 0
    print("turbo control not available on this CPU", file=sys.stderr)
    return 3


def set_max_percent(pct: int, root: str = CPU_ROOT) -> int:
    """Limit scaling_max_freq to pct % of each core's hardware maximum.

    100 % means "no limit": the kernel keeps whatever is written as the user's ceiling, and
    cpuinfo_max_freq shrinks to the base clock while turbo is off, so writing the current
    hardware maximum would leave the CPU capped once turbo is switched back on. A value above
    any real frequency is clamped by the kernel to the current maximum and never goes stale.
    """
    import glob
    dirs = sorted(glob.glob(f"{root}/cpu[0-9]*/cpufreq"))
    if not dirs:
        print("cpufreq not available", file=sys.stderr)
        return 3
    failed = 0
    for d in dirs:
        try:
            hw = int(open(f"{d}/cpuinfo_max_freq").read())
            lo = int(open(f"{d}/scaling_min_freq").read())
            with open(f"{d}/scaling_max_freq", "w") as f:
                f.write(str(UNLIMITED_KHZ if pct == 100 else max(lo, hw * pct // 100)))
        except (OSError, ValueError) as e:
            print(f"{d}: {e}", file=sys.stderr)
            failed += 1
    return 1 if failed else 0


USAGE = "usage: set-lid B AC D | remove-lid | set-cpu-turbo on|off | set-cpu-max-percent 10-100"


def valid_args(argv: list[str]) -> bool:
    """True if ``argv`` is one of the few commands this helper accepts (checked before anything runs).

    Also used by ``powerdeck reset-all --only system`` (run as root while uninstalling) to vet
    helper commands read from the user's backup files.
    """
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        return False
    if len(argv) == 2 and argv[0] == "set-cpu-turbo":
        return argv[1] in ("on", "off")
    if len(argv) == 2 and argv[0] == "set-cpu-max-percent":
        return argv[1].isascii() and argv[1].isdigit() and 10 <= int(argv[1]) <= 100
    if len(argv) == 1:
        return argv[0] == "remove-lid"
    if len(argv) == 4 and argv[0] == "set-lid":
        return all(v in ACTIONS for v in argv[1:])
    return False


def main(argv: list[str], dropin_dir: str = DROPIN_DIR, reload: bool = True,
         cpu_root: str = CPU_ROOT) -> int:
    if not valid_args(argv):
        print(USAGE, file=sys.stderr)
        return 2
    if argv[0] == "set-cpu-turbo":
        return set_turbo(argv[1] == "on", cpu_root)
    if argv[0] == "set-cpu-max-percent":
        return set_max_percent(int(argv[1]), cpu_root)
    if argv[0] == "remove-lid":
        try:
            os.remove(os.path.join(dropin_dir, DROPIN))
        except FileNotFoundError:
            pass
    else:  # set-lid
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
    if reload:
        _reload_logind()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
