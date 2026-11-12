"""Read-only system status for the dashboard."""
from __future__ import annotations

import glob
from dataclasses import dataclass

from .runner import Runner, system_runner


@dataclass
class Status:
    battery_percent: int | None
    battery_state: str | None
    cpu_temp_c: float | None
    active_connection: str | None
    power_mode: str | None


def _read(path: str) -> str | None:
    try:
        return open(path).read().strip()
    except OSError:
        return None


def read_status(run: Runner = system_runner) -> Status:
    pct = state = None
    for bat in glob.glob("/sys/class/power_supply/BAT*"):
        v = _read(bat + "/capacity")
        if v and v.isdigit():
            pct, state = int(v), _read(bat + "/status")
            break
    temps = []
    for z in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
        v = _read(z)
        if v and v.lstrip("-").isdigit():
            temps.append(int(v) / 1000)
    rc, out = run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show", "--active"])
    conn = None
    if rc == 0:
        for line in out.splitlines():
            name, _, typ = line.rpartition(":")
            if "wireless" in typ or "ethernet" in typ or "gsm" in typ:
                conn = name
                break
    rc, pm = run(["powerprofilesctl", "get"])
    return Status(pct, state, max(temps) if temps else None, conn, pm if rc == 0 else None)


def thermal_level(temp: float | None, warn: float = 85, crit: float = 92) -> str:
    if temp is None:
        return "unknown"
    return "critical" if temp >= crit else "warn" if temp >= warn else "ok"
