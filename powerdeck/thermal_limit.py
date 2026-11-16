#!/usr/bin/python3 -I
"""Root daemon: keep the CPU near a target temperature by lowering its power or frequency limit.

Installed as /usr/libexec/powerdeck-thermal and run only by ``powerdeck-thermal@<°C>.service``,
which the root helper starts (``set-thermal-target``) and stops (``clear-thermal-target``).
Stdlib only: it runs as root with ``python3 -I`` and must not import the powerdeck package.

Usage: powerdeck-thermal 55-90 | --restore | --check
"""
from __future__ import annotations

import glob
import json
import os
import signal
import sys
import time

POWERCAP = "/sys/class/powercap"
CPU_ROOT = "/sys/devices/system/cpu"
THERMAL = "/sys/class/thermal"
STATE_DIR = "/run/powerdeck-thermal"  # RuntimeDirectory= of the unit, root-owned 0755
STATE_FILE = "state.json"

MIN_TARGET, MAX_TARGET = 55, 90
RAPL_DOMAINS = ("intel-rapl:0", "intel-rapl-mmio:0")  # package domains (MSR and MMIO)
RAPL_CONSTRAINTS = ("long_term", "short_term")         # PL1 and PL2
RAPL_FLOOR_UW = 5_000_000
UNLIMITED_KHZ = 100_000_000  # same as helper.UNLIMITED_KHZ
CPU_ZONE_TYPES = ("x86_pkg_temp", "coretemp", "k10temp", "cpu", "cpu-thermal", "acpitz")

INTERVAL_S = 2
LEVEL_MIN, LEVEL_STEP, HYSTERESIS_C = 0.1, 0.02, 3


def valid_target(s) -> bool:
    """Canonical decimal 55-90 (no leading zero, so one target = one unit name)."""
    return (isinstance(s, str) and s.isascii() and s.isdigit() and not s.startswith("0")
            and MIN_TARGET <= int(s) <= MAX_TARGET)


def _read(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _int(path: str) -> int | None:
    v = _read(path)
    return int(v) if v and v.isdigit() else None


def _write(path: str, value: int) -> bool:
    try:
        with open(path, "w") as f:
            f.write(str(value))
        return True
    except OSError as e:
        print(f"{path}: {e}", file=sys.stderr)
        return False


class Knob:
    """One sysfs limit: the value found at start (``original``) and the lowest allowed value.

    For frequency knobs ``original`` None means "no cap" (scaling_max_freq was at the hardware
    maximum); restoring then writes a value above any real frequency, see helper.set_max_percent.
    """

    def __init__(self, path: str, original: int | None, floor: int, hw_max: str | None = None):
        self.path, self.original, self.floor, self.hw_max = path, original, floor, hw_max
        self.written: int | None = None
        self.firmware_cap: int | None = None  # a lower limit firmware set while we regulate

    def ceiling(self) -> int | None:
        hw = _int(self.hw_max) if self.hw_max else None
        if self.original is None:
            return hw
        return min(self.original, hw) if hw else self.original

    def restore_value(self) -> int:
        return UNLIMITED_KHZ if self.original is None else self.original



def rapl_knobs(powercap: str = POWERCAP, need_write: bool = True, any_value: bool = False) -> list[Knob]:
    """Package PL1/PL2. ``any_value`` (for --restore) also returns limits at or below the floor,
    which is exactly where a killed daemon may have left them."""
    knobs = []
    for dom in RAPL_DOMAINS:
        d = f"{powercap}/{dom}"
        if not (_read(f"{d}/name") or "").startswith("package") or _read(f"{d}/enabled") != "1":
            continue
        for i in range(3):
            if _read(f"{d}/constraint_{i}_name") not in RAPL_CONSTRAINTS:
                continue
            path = f"{d}/constraint_{i}_power_limit_uw"
            v = _int(path)
            if v is not None and (any_value or v > RAPL_FLOOR_UW) and (not need_write or os.access(path, os.W_OK)):
                knobs.append(Knob(path, v, RAPL_FLOOR_UW))
    return knobs


def freq_knobs(cpu_root: str = CPU_ROOT, need_write: bool = True) -> list[Knob]:
    knobs = []
    for d in sorted(glob.glob(f"{cpu_root}/cpu[0-9]*/cpufreq")):
        cur, hw, lo = (_int(f"{d}/{n}") for n in ("scaling_max_freq", "cpuinfo_max_freq", "scaling_min_freq"))
        path = f"{d}/scaling_max_freq"
        if cur and hw and lo and (not need_write or os.access(path, os.W_OK)):
            knobs.append(Knob(path, None if cur >= hw else cur, lo, f"{d}/cpuinfo_max_freq"))
    return knobs


def discover(powercap: str = POWERCAP, cpu_root: str = CPU_ROOT,
             need_write: bool = True) -> tuple[str, list[Knob]]:
    """The actuator to use: RAPL power limits if writable, else per-core max frequency.

    ``need_write=False`` lets the app (not root) ask what root would find.
    """
    knobs = rapl_knobs(powercap, need_write)
    if knobs:
        return "power", knobs
    knobs = freq_knobs(cpu_root, need_write)
    return ("frequency", knobs) if knobs else ("", [])


def read_temp(root: str = THERMAL) -> float | None:
    temps = []
    for z in glob.glob(root + "/thermal_zone*"):
        v = _read(z + "/temp")
        if v and v.isdigit() and _read(z + "/type") in CPU_ZONE_TYPES:
            temps.append(int(v) / 1000)
    return max(temps) if temps else None


def next_level(level: float, temp: float | None, target: int) -> float:
    """Proportional step down when hot, slow step up when clearly below target."""
    if temp is None:
        return level
    if temp > target:
        level -= LEVEL_STEP * max(1.0, temp - target)
    elif temp < target - HYSTERESIS_C:
        level += LEVEL_STEP
    return round(min(1.0, max(LEVEL_MIN, level)), 3)


def apply_level(knobs: list[Knob], level: float) -> list[int]:
    """Write ``level`` × original to every knob; returns the values now in effect."""
    out = []
    for k in knobs:
        cur = _int(k.path)
        if k.written is not None and cur is not None and cur != k.written:
            print(f"{k.path}: changed to {cur} by someone else", file=sys.stderr)
            if k.hw_max is not None:  # frequency: turbo toggled or another cap set
                k.original = cur if cur < (_int(k.hw_max) or 0) else None
            elif cur > (k.original or 0):  # power: firmware raised it, that is the new original
                k.original, k.firmware_cap = cur, None
            else:  # power: firmware lowered it (lap mode, power profile): respect it, but never
                k.firmware_cap = cur  # make it the value restored when we stop
        ceiling = k.ceiling()
        if ceiling is None:
            continue
        want = max(k.floor, int(ceiling * level)) if level < 1 else ceiling
        if k.firmware_cap is not None:
            want = min(want, k.firmware_cap)  # also below our own floor
        if level >= 1 and k.original is None:
            want = UNLIMITED_KHZ  # no cap; the kernel clamps it to the hardware maximum
            if cur is not None and cur >= ceiling:
                want = cur  # already uncapped, nothing to write
        if cur != want and _write(k.path, want):
            cur = _int(k.path)
        k.written = cur
        if cur is not None:
            out.append(cur)
    return out


def describe_limit(kind: str, values: list[int]) -> str:
    if not values:
        return ""
    if kind == "power":
        return f"{min(values) / 1_000_000:.1f} W"
    return f"{min(values) // 1000} MHz"


def write_state(state_dir: str, state: dict) -> None:
    path = os.path.join(state_dir, STATE_FILE)
    tmp = path + ".tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, "w") as f:
            json.dump(state, f)
        os.replace(tmp, path)
    except OSError as e:
        print(f"{path}: {e}", file=sys.stderr)


def restore(knobs: list[Knob], state_dir: str = STATE_DIR) -> int:
    """Put the original limits back and drop the state file."""
    failed = sum(not _write(k.path, k.restore_value()) for k in knobs)
    try:
        os.remove(os.path.join(state_dir, STATE_FILE))
    except FileNotFoundError:
        pass
    return 1 if failed else 0


def restore_from_state(state_dir: str = STATE_DIR, powercap: str = POWERCAP,
                       cpu_root: str = CPU_ROOT) -> int:
    """ExecStopPost: after a crash the sysfs values are the lowered ones, so the originals come
    from the state file. Only paths this daemon discovers itself are written, and only ints."""
    try:
        with open(os.path.join(state_dir, STATE_FILE)) as f:
            originals = json.load(f).get("originals", {})
    except (OSError, ValueError, AttributeError):
        return 0
    # every path the daemon could have used, whatever their values are now
    knobs = rapl_knobs(powercap, need_write=False, any_value=True) + freq_knobs(cpu_root, need_write=False)
    known = {k.path: k for k in knobs}
    for path, v in originals.items() if isinstance(originals, dict) else ():
        if path in known and (v is None or (isinstance(v, int) and not isinstance(v, bool) and 0 < v < 10**9)):
            known[path].original = v
    return restore([known[p] for p in originals if p in known] if isinstance(originals, dict) else [],
                   state_dir)


def run(target: int, state_dir: str = STATE_DIR, powercap: str = POWERCAP, cpu_root: str = CPU_ROOT,
        thermal: str = THERMAL, ticks: int | None = None, sleep=time.sleep) -> int:
    kind, knobs = discover(powercap, cpu_root)
    if not knobs or read_temp(thermal) is None:
        print("no writable CPU power/frequency limit or no CPU temperature sensor", file=sys.stderr)
        return 3
    print(f"regulating CPU to {target} °C via {kind} limits ({len(knobs)} knobs)", file=sys.stderr)
    originals = {k.path: k.original for k in knobs}  # as found at start, for --restore after a crash
    level, n = 1.0, 0
    # before the first write, so a daemon killed at any point can be undone by --restore
    write_state(state_dir, {"target": target, "kind": kind, "level": level, "temp_c": None,
                            "limit": "", "originals": originals})
    try:
        while ticks is None or n < ticks:
            temp = read_temp(thermal)
            level = next_level(level, temp, target)
            values = apply_level(knobs, level)
            originals.update({k.path: k.original for k in knobs})
            write_state(state_dir, {"target": target, "kind": kind, "level": level, "temp_c": temp,
                                    "limit": describe_limit(kind, values), "originals": originals})
            n += 1
            if ticks is None or n < ticks:
                sleep(INTERVAL_S)
    finally:
        restore(knobs, state_dir)
    return 0


def _stop(_sig, _frame):
    raise SystemExit(0)  # unwinds into run()'s finally: limits are restored


def main(argv: list[str]) -> int:
    if argv == ["--restore"]:
        return restore_from_state()
    if argv == ["--check"]:
        return 0 if discover()[1] and read_temp() is not None else 3
    if len(argv) != 1 or not valid_target(argv[0]):
        print(f"usage: powerdeck-thermal {MIN_TARGET}-{MAX_TARGET} | --restore | --check", file=sys.stderr)
        return 2
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    return run(int(argv[0]))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
