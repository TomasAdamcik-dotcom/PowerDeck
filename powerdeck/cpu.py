"""Read-only CPU frequency/turbo state from sysfs (writes happen only in the root helper)."""
from __future__ import annotations

import glob
import json
import os

from . import thermal_limit

CPU_ROOT = "/sys/devices/system/cpu"  # tests point these at fake trees
POWERCAP_ROOT = thermal_limit.POWERCAP
THERMAL_ROOT = thermal_limit.THERMAL
THERMAL_STATE_DIR = thermal_limit.STATE_DIR


def _read(path: str) -> str | None:
    try:
        return open(path).read().strip()
    except OSError:
        return None


def _int(path: str) -> int | None:
    v = _read(path)
    return int(v) if v and v.lstrip("-").isdigit() else None


def cpufreq_dirs(root: str | None = None) -> list[str]:
    return sorted(glob.glob((root or CPU_ROOT) + "/cpu[0-9]*/cpufreq"))


def read_turbo(root: str | None = None) -> bool | None:
    """True = turbo/boost enabled, None = not controllable on this machine."""
    root = root or CPU_ROOT
    v = _read(f"{root}/intel_pstate/no_turbo")
    if v in ("0", "1"):
        return v == "0"
    v = _read(f"{root}/cpufreq/boost")
    if v in ("0", "1"):
        return v == "1"
    return None


def read_max_percent(root: str | None = None) -> int | None:
    """Current scaling_max_freq as % of the hardware maximum (cpu0)."""
    dirs = cpufreq_dirs(root)
    if not dirs:
        return None
    cur, hw = _int(dirs[0] + "/scaling_max_freq"), _int(dirs[0] + "/cpuinfo_max_freq")
    if not cur or not hw:
        return None
    pct = round(cur * 100 / hw)
    return 100 if pct >= 99 else pct


def read_freqs(root: str | None = None) -> tuple[float | None, float | None]:
    """(average current MHz over all cores, hardware max MHz)."""
    cur, hw = [], []
    for d in cpufreq_dirs(root):
        c, m = _int(d + "/scaling_cur_freq"), _int(d + "/cpuinfo_max_freq")
        if c is not None:
            cur.append(c)
        if m:
            hw.append(m)
    return (sum(cur) / len(cur) / 1000 if cur else None, max(hw) / 1000 if hw else None)


def thermal_available() -> bool:
    """True if the root daemon (thermal_limit) could regulate the CPU temperature here."""
    return (bool(thermal_limit.discover(POWERCAP_ROOT, CPU_ROOT, need_write=False)[1])
            and thermal_limit.read_temp(THERMAL_ROOT) is not None)


def read_thermal(state_dir: str | None = None) -> dict | None:
    """The running regulation (target °C, current limit), from the daemon's root-owned state file."""
    path = os.path.join(state_dir or THERMAL_STATE_DIR, thermal_limit.STATE_FILE)
    try:
        with open(path) as f:
            s = json.load(f)
    except (OSError, ValueError):
        return None
    t = s.get("target") if isinstance(s, dict) else None
    if not (isinstance(t, int) and not isinstance(t, bool)
            and thermal_limit.MIN_TARGET <= t <= thermal_limit.MAX_TARGET):
        return None
    limit = s.get("limit")
    return {"target": t, "limit": limit if isinstance(limit, str) else ""}


def read_thermal_target(state_dir: str | None = None) -> int | None:
    """Target °C of the running regulation, None if it is not running."""
    s = read_thermal(state_dir)
    return s["target"] if s else None
