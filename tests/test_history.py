"""History of CPU temperature/frequency for the graph (powerdeck.history)."""
import time

from powerdeck import history
from powerdeck.history import Point


def test_append_and_read_back(tmp_path):
    f = tmp_path / "h" / "history.csv"
    history.append(Point(1000, 55.5, 2100, "Default"), f)
    history.append(Point(1020, None, None, ""), f)
    assert f.read_text().splitlines()[0] == "ts,temp_c,freq_mhz,profile"
    assert history.read(f) == [Point(1000, 55.5, 2100, "Default"), Point(1020, None, None, "")]
    assert history.read(f, since=1010) == [Point(1020, None, None, "")]


def test_damaged_lines_are_skipped(tmp_path):
    f = tmp_path / "history.csv"
    f.write_text("ts,temp_c,freq_mhz,profile\n1000,50,2000,A\ngarbage\n1020,nan,x,A\n,1,2,A\n1,2\n")
    assert history.read(f) == [Point(1000, 50, 2000, "A"), Point(1020, None, None, "A")]
    assert history.read(tmp_path / "missing.csv") == []


def test_prune_keeps_only_recent_lines(tmp_path):
    f = tmp_path / "history.csv"
    for ts in (100, 200, 300):
        history.append(Point(ts, 50, 2000, "A"), f)
    history.prune(f, now=350, keep=100)
    assert [p.ts for p in history.read(f)] == [300]
    history.prune(f, now=10_000, keep=100)
    assert not f.exists()


def test_gaps_break_the_line_and_profile_switches_are_marked():
    pts = [Point(0, 50, 1, "A"), Point(20, 50, 1, "A"), Point(500, 50, 1, "B"), Point(520, 50, 1, "")]
    assert [[p.ts for p in s] for s in history.segments(pts, max_gap=60)] == [[0, 20], [500, 520]]
    assert history.profile_marks(pts) == [(500, "B"), (520, "")]
    assert history.profile_marks(pts[:2]) == []


def test_axis_ticks():
    assert history.value_ticks(40, 80, 4) == [40, 50, 60, 70, 80]
    assert history.value_ticks(0, 4.8, 4) == [0, 2, 4]
    assert history.axis_range([], 40, 80, 3) == (40, 80)
    assert history.axis_range([35, 92], 40, 80, 3) == (32, 95)
    start = time.mktime((2026, 11, 16, 10, 7, 0, 0, 0, -1))
    ticks = history.time_ticks(start, start + 3600)
    assert [time.localtime(t)[3:5] for t in ticks] == [(10, 10), (10, 20), (10, 30), (10, 40), (10, 50), (11, 0)]


def test_take_reads_the_active_profile(monkeypatch):
    monkeypatch.setattr(history.cpu, "read_freqs", lambda: (2345.6, 4000.0))
    monkeypatch.setattr(history, "read_cpu_temp", lambda: 61.23)
    monkeypatch.setattr(history.engine, "active_profile", lambda: None)
    assert history.take(now=5) == Point(5, 61.2, 2346, "")
