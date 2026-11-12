"""Read-only CPU frequency/turbo state from sysfs (writes happen only in the root helper)."""
from __future__ import annotations

import glob
import os

CPU_ROOT = "/sys/devices/system/cpu"  # tests point this at a fake tree


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
