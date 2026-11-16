"""CPU temperature and frequency over time, with the active profile, for the history graph.

The guard records one line every INTERVAL_S to ``~/.local/state/powerdeck/history.csv`` and keeps
KEEP_S of it. Reading, gaps, profile switches and axis ticks are pure functions, tested without
GTK; the graph itself is drawn by ``history_view``.
"""
from __future__ import annotations

import csv
import logging
import math
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import cpu, engine, paths
from .status import read_cpu_temp

log = logging.getLogger(__name__)

INTERVAL_S = 20
KEEP_S = 7 * 24 * 3600
PRUNE_EVERY_S = 6 * 3600
MAX_GAP_S = 3 * INTERVAL_S  # longer without a line (suspended, logged out): the graph breaks
FIELDS = ("ts", "temp_c", "freq_mhz", "profile")


@dataclass
class Point:
    ts: float
    temp_c: float | None
    freq_mhz: float | None
    profile: str  # "" = no profile active


def history_file() -> Path:
    return paths.state_dir() / "history.csv"


def take(now: float | None = None) -> Point:
    """One reading of this machine (sysfs only, no root)."""
    avg, _hw = cpu.read_freqs()
    temp = read_cpu_temp()
    return Point(time.time() if now is None else now,
                 None if temp is None else round(temp, 1),
                 None if avg is None else round(avg),
                 engine.active_profile() or "")


def append(p: Point, path: Path | None = None) -> None:
    path = path or history_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(FIELDS)
        w.writerow([f"{p.ts:.0f}", "" if p.temp_c is None else p.temp_c,
                    "" if p.freq_mhz is None else p.freq_mhz, p.profile])


def _num(s: str) -> float | None:
    try:
        v = float(s)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def read(path: Path | None = None, since: float | None = None) -> list[Point]:
    """The recorded points (oldest first) newer than ``since``; damaged lines are skipped."""
    path = path or history_file()
    out = []
    try:
        with open(path, newline="", encoding="utf-8", errors="replace") as f:
            for row in csv.reader(f):
                if len(row) != len(FIELDS) or (ts := _num(row[0])) is None:
                    continue  # header or a damaged line
                if since is not None and ts < since:
                    continue
                out.append(Point(ts, _num(row[1]), _num(row[2]), row[3]))
    except OSError:
        return []
    out.sort(key=lambda p: p.ts)
    return out


def prune(path: Path | None = None, now: float | None = None, keep: float = KEEP_S) -> None:
    """Drop lines older than ``keep`` (rewrites the file atomically)."""
    path = path or history_file()
    if not path.exists():
        return
    points = read(path, since=(time.time() if now is None else now) - keep)
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    for p in points:
        append(p, tmp)
    if points:
        os.replace(tmp, path)
    else:
        path.unlink(missing_ok=True)


def segments(points: list[Point], max_gap: float = MAX_GAP_S) -> list[list[Point]]:
    """Runs of points without a gap longer than ``max_gap``; each is drawn as one line."""
    out: list[list[Point]] = []
    for p in points:
        if out and p.ts - out[-1][-1].ts <= max_gap:
            out[-1].append(p)
        else:
            out.append([p])
    return out


def profile_marks(points: list[Point]) -> list[tuple[float, str]]:
    """(time, new profile) wherever the active profile differs from the line before."""
    return [(b.ts, b.profile) for a, b in zip(points, points[1:]) if a.profile != b.profile]


def nice_step(span: float, target: int = 5) -> float:
    """A round step (1, 2, 2.5 or 5 × 10^n) giving about ``target`` ticks over ``span``."""
    if span <= 0:
        return 1.0
    raw = span / target
    mag = 10 ** math.floor(math.log10(raw))
    return next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)


def value_ticks(lo: float, hi: float, target: int = 5) -> list[float]:
    step = nice_step(hi - lo, target)
    first = math.ceil(lo / step) * step
    return [round(first + i * step, 6) for i in range(int((hi - first) / step + 1e-9) + 1)]


TIME_STEPS = (300, 600, 900, 1800, 3600, 2 * 3600, 3 * 3600, 6 * 3600, 12 * 3600, 24 * 3600)


def time_ticks(start: float, end: float, target: int = 6) -> list[float]:
    """Local-time-aligned ticks (e.g. every full hour) between ``start`` and ``end``."""
    step = next((s for s in TIME_STEPS if (end - start) / s <= target), TIME_STEPS[-1])
    offset = time.localtime(start).tm_gmtoff
    first = math.ceil((start + offset) / step) * step - offset
    return [first + i * step for i in range(int((end - first) // step) + 1)]


def axis_range(values: list[float], lo: float, hi: float, pad: float) -> tuple[float, float]:
    """At least ``lo``..``hi``, widened to the data plus ``pad``."""
    if values:
        lo, hi = min(lo, min(values) - pad), max(hi, max(values) + pad)
    return lo, hi


def record_forever(path: Path | None = None, interval: float = INTERVAL_S) -> None:  # pragma: no cover
    """Guard thread: one line every ``interval`` seconds, old lines pruned now and then."""
    last_prune = 0.0
    while True:
        try:
            now = time.time()
            if now - last_prune >= PRUNE_EVERY_S:
                prune(path, now)
                last_prune = now
            append(take(now), path)
        except Exception:  # the guard must keep running whatever happens here
            log.exception("history: recording failed")
        time.sleep(interval)


def start_recording() -> None:  # pragma: no cover
    threading.Thread(target=record_forever, daemon=True, name="history").start()
