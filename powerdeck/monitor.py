"""Live monitoring: temperature, frequency, load, power draw, fan. Sysfs only (cheap, no root)."""
from __future__ import annotations

import csv
import glob
import time
from dataclasses import dataclass, asdict, fields

from . import cpu
from .status import read_battery, read_cpu_temp


@dataclass
class Sample:
    ts: float
    temp_c: float | None = None
    freq_mhz: float | None = None          # average current frequency
    freq_percent: float | None = None      # average current freq as % of hardware max
    limit_percent: int | None = None       # configured max-frequency limit
    turbo: bool | None = None
    regulation: str | None = None          # running temperature regulation, e.g. "65 °C → 12.0 W"
    load_percent: float | None = None
    power_w: float | None = None           # battery discharge (+) / charge (-), if reported
    fan_rpm: int | None = None
    battery_percent: int | None = None


def _proc_stat() -> tuple[int, int] | None:
    try:
        parts = open("/proc/stat").readline().split()
        vals = list(map(int, parts[1:9]))
    except (OSError, ValueError, IndexError):
        return None
    idle = vals[3] + vals[4]
    return sum(vals), idle


def read_power_w(root: str = "/sys/class/power_supply") -> float | None:
    for bat in sorted(glob.glob(root + "/BAT*")):
        pw = cpu._int(bat + "/power_now")
        if pw is None:
            i, v = cpu._int(bat + "/current_now"), cpu._int(bat + "/voltage_now")
            pw = i * v // 1_000_000 if i is not None and v else None  # µA * µV → µW
        if pw is not None:
            w = abs(pw) / 1_000_000
            return -w if w and cpu._read(bat + "/status") == "Charging" else w  # no "-0.0"
    return None


def read_fan_rpm(root: str = "/sys/class/hwmon") -> int | None:
    rpms = [cpu._int(f) for f in glob.glob(root + "/hwmon*/fan*_input")]
    rpms = [r for r in rpms if r is not None]
    return max(rpms) if rpms else None


class Monitor:
    """Keeps the previous /proc/stat reading so load can be computed between samples."""

    def __init__(self):
        self._prev = _proc_stat()

    def sample(self) -> Sample:
        s = Sample(ts=time.time())
        s.temp_c = read_cpu_temp()
        avg, hw = cpu.read_freqs()
        s.freq_mhz = round(avg, 1) if avg is not None else None
        s.freq_percent = round(avg * 100 / hw, 1) if avg is not None and hw else None
        s.limit_percent = cpu.read_max_percent()
        s.turbo = cpu.read_turbo()
        if (r := cpu.read_thermal()) is not None:
            s.regulation = f"{r['target']} °C → {r['limit']}" if r["limit"] else f"{r['target']} °C"
        cur = _proc_stat()
        if cur and self._prev and cur[0] > self._prev[0]:
            dt, di = cur[0] - self._prev[0], cur[1] - self._prev[1]
            s.load_percent = round(100 * (dt - di) / dt, 1)
        self._prev = cur or self._prev
        s.power_w = read_power_w()
        s.fan_rpm = read_fan_rpm()
        s.battery_percent = read_battery()[0]
        return s


CSV_FIELDS = [f.name for f in fields(Sample)]


def write_csv_row(writer, s: Sample) -> None:
    writer.writerow(asdict(s))


def format_line(s: Sample) -> str:
    f = lambda v, fmt="{}": "n/a" if v is None else fmt.format(v)
    return (f"temp {f(s.temp_c, '{:.0f}')}°C | freq {f(s.freq_mhz, '{:.0f}')} MHz "
            f"({f(s.freq_percent, '{:.0f}')}% of max, limit {f(s.limit_percent)}%, "
            f"turbo {'on' if s.turbo else 'off' if s.turbo is False else 'n/a'}"
            f"{', regulated ' + s.regulation if s.regulation else ''}) | "
            f"load {f(s.load_percent, '{:.0f}')}% | power {f(s.power_w, '{:.1f}')} W | "
            f"fan {f(s.fan_rpm)} rpm | batt {f(s.battery_percent)}%")


def run_cli(interval: float, duration: float | None, csv_path: str | None) -> None:
    mon, out, w = Monitor(), None, None
    if csv_path:
        out = open(csv_path, "w", newline="")
        w = csv.DictWriter(out, fieldnames=CSV_FIELDS)
        w.writeheader()
    end = time.time() + duration if duration else None
    try:
        time.sleep(0.5)  # first load figure needs two readings
        while True:
            s = mon.sample()
            print(format_line(s), flush=True)
            if w:
                write_csv_row(w, s)
                out.flush()
            if end and time.time() >= end:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        if out:
            out.close()
