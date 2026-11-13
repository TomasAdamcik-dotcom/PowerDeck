import csv

import pytest

from powerdeck import backends, cpu, engine, helper, monitor
from powerdeck.builtin import BUILTIN_PROFILES
from powerdeck.profile import Profile, ProfileError

BY = {p.name: p for p in BUILTIN_PROFILES}


def make_cpu(root, cores=2, hw=4_000_000, cur_max=4_000_000, turbo_intel=True, no_turbo="0"):
    for i in range(cores):
        d = root / f"cpu{i}" / "cpufreq"
        d.mkdir(parents=True)
        for n, v in (("cpuinfo_max_freq", hw), ("cpuinfo_min_freq", 400_000), ("scaling_min_freq", 800_000),
                     ("scaling_max_freq", cur_max), ("scaling_cur_freq", 2_000_000 + i * 1_000_000)):
            (d / n).write_text(str(v))
    if turbo_intel:
        (root / "intel_pstate").mkdir()
        (root / "intel_pstate" / "no_turbo").write_text(no_turbo)


@pytest.fixture
def fake_cpu(tmp_path, monkeypatch):
    root = tmp_path / "cpu"
    make_cpu(root)
    monkeypatch.setattr(cpu, "CPU_ROOT", str(root))
    monkeypatch.setattr(backends, "helper_installed", lambda: True)
    return root


def test_read_state(fake_cpu):
    assert cpu.read_turbo() is True
    assert cpu.read_max_percent() == 100
    assert cpu.read_freqs() == (2500.0, 4000.0)


def test_amd_boost_and_unsupported(tmp_path):
    root = tmp_path / "amd"
    (root / "cpufreq").mkdir(parents=True)
    (root / "cpufreq" / "boost").write_text("0")
    assert cpu.read_turbo(str(root)) is False
    assert cpu.read_turbo(str(tmp_path / "none")) is None
    assert cpu.read_max_percent(str(tmp_path / "none")) is None


def test_helper_sets_percent_and_turbo(fake_cpu):
    assert helper.main(["set-cpu-max-percent", "70"], cpu_root=str(fake_cpu)) == 0
    assert (fake_cpu / "cpu0/cpufreq/scaling_max_freq").read_text() == "2800000"
    assert helper.main(["set-cpu-max-percent", "100"], cpu_root=str(fake_cpu)) == 0
    assert int((fake_cpu / "cpu0/cpufreq/scaling_max_freq").read_text()) > 5_000_000  # unlimited, not pinned to cpuinfo_max
    assert helper.main(["set-cpu-max-percent", "10"], cpu_root=str(fake_cpu)) == 0
    assert (fake_cpu / "cpu1/cpufreq/scaling_max_freq").read_text() == "800000"   # clamped to min
    assert helper.main(["set-cpu-turbo", "off"], cpu_root=str(fake_cpu)) == 0
    assert (fake_cpu / "intel_pstate/no_turbo").read_text() == "1"
    assert helper.main(["set-cpu-turbo", "on"], cpu_root=str(fake_cpu)) == 0
    assert (fake_cpu / "intel_pstate/no_turbo").read_text() == "0"


@pytest.mark.parametrize("argv", [["set-cpu-max-percent", "5"], ["set-cpu-max-percent", "101"],
                                  ["set-cpu-max-percent", "70; rm"], ["set-cpu-max-percent", "-1"],
                                  ["set-cpu-turbo", "maybe"], ["set-cpu-turbo"]])
def test_helper_rejects_bad_cpu_args(fake_cpu, argv):
    before = (fake_cpu / "cpu0/cpufreq/scaling_max_freq").read_text()
    assert helper.main(argv, cpu_root=str(fake_cpu), reload=False) == 2
    assert (fake_cpu / "cpu0/cpufreq/scaling_max_freq").read_text() == before


def test_helper_reports_unsupported(tmp_path):
    assert helper.main(["set-cpu-turbo", "off"], cpu_root=str(tmp_path)) == 3
    assert helper.main(["set-cpu-max-percent", "50"], cpu_root=str(tmp_path)) == 3


def test_plan_rucksack_cpu(fake_cpu):
    plan = engine.make_plan(BY["Rucksack"], lambda a: (127, ""))
    cpu_changes = [c for c in plan.changes if c.backend == "cpu"]
    assert [c.target for c in cpu_changes] == ["CPU turbo/boost", "CPU max frequency"]
    assert cpu_changes[0].apply_argv[-2:] == ["set-cpu-turbo", "off"]
    assert cpu_changes[0].revert_argv[-2:] == ["set-cpu-turbo", "on"]
    assert cpu_changes[1].apply_argv[-2:] == ["set-cpu-max-percent", "50"]
    assert cpu_changes[1].revert_argv[-2:] == ["set-cpu-max-percent", "100"]
    assert all(c.privileged and c.apply_argv[:2] == ["pkexec", backends.HELPER_PATH] for c in cpu_changes)


def test_plan_travel_cpu_turbo_only(fake_cpu):
    cpu_changes = [c for c in engine.make_plan(BY["Travel"], lambda a: (127, "")).changes if c.backend == "cpu"]
    assert [(c.target, c.apply_argv[-1]) for c in cpu_changes] == [("CPU turbo/boost", "off")]


def test_plan_travel_lifts_rucksack_cap(fake_cpu):
    for i in (0, 1):
        (fake_cpu / f"cpu{i}/cpufreq/scaling_max_freq").write_text("2000000")   # 50 % left by Rucksack
    cpu_changes = [c for c in engine.make_plan(BY["Travel"], lambda a: (127, "")).changes if c.backend == "cpu"]
    assert ("CPU max frequency", "100") in [(c.target, c.apply_argv[-1]) for c in cpu_changes]


def test_plan_noop_when_already_set_or_unsupported(fake_cpu, tmp_path, monkeypatch):
    (fake_cpu / "intel_pstate/no_turbo").write_text("1")
    for i in (0, 1):
        (fake_cpu / f"cpu{i}/cpufreq/scaling_max_freq").write_text("2000000")
    assert [c for c in engine.make_plan(BY["Rucksack"], lambda a: (127, "")).changes if c.backend == "cpu"] == []
    monkeypatch.setattr(cpu, "CPU_ROOT", str(tmp_path / "gone"))
    assert [c for c in engine.make_plan(BY["Travel"], lambda a: (127, "")).changes if c.backend == "cpu"] == []


def test_cpu_validation():
    for bad in ({"turbo": "yes"}, {"max_freq_percent": 5}, {"max_freq_percent": 150},
                {"max_freq_percent": True}, {"boost": True}):
        with pytest.raises(ProfileError):
            Profile(name="X", cpu=bad).validate()
    Profile(name="X", cpu={"turbo": False, "max_freq_percent": 70}).validate()


def test_monitor_sample_and_csv(fake_cpu, tmp_path, monkeypatch):
    bat = tmp_path / "ps" / "BAT0"
    bat.mkdir(parents=True)
    (bat / "current_now").write_text("1500000"); (bat / "voltage_now").write_text("12000000")
    assert monitor.read_power_w(str(tmp_path / "ps")) == 18.0
    (bat / "status").write_text("Charging")
    assert monitor.read_power_w(str(tmp_path / "ps")) == -18.0
    hw = tmp_path / "hw" / "hwmon3"
    hw.mkdir(parents=True)
    (hw / "fan1_input").write_text("3200")
    assert monitor.read_fan_rpm(str(tmp_path / "hw")) == 3200
    s = monitor.Monitor().sample()
    assert s.turbo is True and s.limit_percent == 100 and s.freq_mhz == 2500.0 and s.freq_percent == 62.5
    assert "limit 100%" in monitor.format_line(s)
    path = tmp_path / "m.csv"
    monitor.run_cli(0.01, 0.05, str(path))
    rows = list(csv.DictReader(open(path)))
    assert rows and set(rows[0]) == set(monitor.CSV_FIELDS)


def test_rucksack_is_strictest(fake_cpu):
    r, t = BY["Rucksack"], BY["Travel"]
    r.validate()
    assert r.lid == {"battery": "ignore", "ac": "ignore", "docked": "ignore"}
    assert r.cpu["turbo"] is False and r.cpu["max_freq_percent"] < t.cpu["max_freq_percent"]
    assert r.power_mode == "power-saver" and r.bluetooth is False
    assert r.thermal_guard["warn_celsius"] < t.thermal_guard["warn_celsius"]
    assert r.thermal_guard["critical_celsius"] < t.thermal_guard["critical_celsius"]
    plan = engine.make_plan(r, lambda a: (127, ""))
    assert "CPU max frequency: 100% → 50%" in plan.diff_text()
