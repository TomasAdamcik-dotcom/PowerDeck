"""CPU temperature regulation: root daemon (thermal_limit), helper commands, profile and plan."""
import json

import pytest

from powerdeck import backends, capture, cpu, engine, helper, monitor
from powerdeck import thermal_limit as tl
from powerdeck.builtin import BUILTIN_PROFILES
from powerdeck.profile import Profile, ProfileError
from test_cpu import make_cpu

BY = {p.name: p for p in BUILTIN_PROFILES}
W = 1_000_000


def make_rapl(root, pl1=55 * W, pl2=55 * W, mmio=(30 * W, 55 * W)):
    for dom, (a, b) in (("intel-rapl:0", (pl1, pl2)), ("intel-rapl-mmio:0", mmio)):
        d = root / dom
        d.mkdir(parents=True)
        (d / "name").write_text("package-0\n")
        (d / "enabled").write_text("1\n")
        for i, (name, v) in enumerate((("long_term", a), ("short_term", b), ("peak_power", 114 * W))):
            (d / f"constraint_{i}_name").write_text(name + "\n")
            (d / f"constraint_{i}_power_limit_uw").write_text(f"{v}\n")
    core = root / "intel-rapl:0:0"  # subdomain: never touched
    core.mkdir()
    (core / "name").write_text("core\n")


def make_zone(root, celsius, typ="x86_pkg_temp"):
    z = root / "thermal_zone0"
    z.mkdir(parents=True, exist_ok=True)
    (z / "type").write_text(typ + "\n")
    (z / "temp").write_text(f"{int(celsius * 1000)}\n")
    return z


def limits(root):
    return {f"{p.parent.name}/{p.name[:12]}": int(p.read_text())
            for p in sorted(root.glob("intel-rapl*/constraint_*_power_limit_uw"))}


@pytest.fixture
def sysfs(tmp_path):
    pc, th, st, cr = (tmp_path / n for n in ("powercap", "thermal", "state", "cpu"))
    make_rapl(pc)
    make_zone(th, 50)
    st.mkdir()
    return pc, th, st, cr


def run(sysfs, target, ticks):
    pc, th, st, cr = sysfs
    return tl.run(target, str(st), str(pc), str(cr), str(th), ticks=ticks, sleep=lambda s: None)


# -- discovery ----------------------------------------------------------------
def test_discovers_package_pl1_pl2_only(sysfs):
    pc, _th, _st, cr = sysfs
    kind, knobs = tl.discover(str(pc), str(cr))
    assert kind == "power"
    assert sorted(k.path.split("/powercap/")[1] for k in knobs) == [
        "intel-rapl-mmio:0/constraint_0_power_limit_uw", "intel-rapl-mmio:0/constraint_1_power_limit_uw",
        "intel-rapl:0/constraint_0_power_limit_uw", "intel-rapl:0/constraint_1_power_limit_uw"]


def test_disabled_or_missing_rapl_falls_back_to_frequency(sysfs):
    pc, _th, _st, cr = sysfs
    for d in ("intel-rapl:0", "intel-rapl-mmio:0"):
        (pc / d / "enabled").write_text("0\n")
    assert tl.discover(str(pc), str(cr)) == ("", [])
    make_cpu(cr)
    kind, knobs = tl.discover(str(pc), str(cr))
    assert kind == "frequency" and len(knobs) == 2


def test_check_needs_a_sensor(sysfs, monkeypatch):
    pc, th, _st, cr = sysfs
    assert tl.read_temp(str(th)) == 50
    assert tl.read_temp(str(th / "none")) is None
    make_zone(th, 99, typ="iwlwifi_1")
    assert tl.read_temp(str(th)) is None  # only CPU zones count


# -- controller ---------------------------------------------------------------
def test_next_level_steps():
    assert tl.next_level(1.0, 60, 65) == 0.98 + 0.02            # cool: already at max
    assert tl.next_level(0.5, 60, 65) == 0.52                   # clearly below: slow step up
    assert tl.next_level(0.5, 63, 65) == 0.5                    # within hysteresis: hold
    assert tl.next_level(0.5, 65.5, 65) == 0.48                 # just above: at least one step
    assert tl.next_level(0.5, 67, 65) == 0.46                   # 2 °C above: two steps
    assert tl.next_level(0.5, 75, 65) == 0.42                   # 10 °C above: capped, no overshoot
    assert tl.next_level(1.0, 82, 65) == 0.92                   # load spike: capped as well
    assert tl.next_level(0.15, 95, 65) == tl.LEVEL_MIN          # never below the floor
    assert tl.next_level(0.5, None, 65) == 0.5                  # no reading: hold


def test_hot_cpu_lowers_both_domains_and_stop_restores(sysfs):
    pc, th, st, _cr = sysfs
    before = limits(pc)
    make_zone(th, 75)  # 10 °C over target → capped step, level 0.92 after one tick
    seen = {}
    orig_restore = tl.restore

    def spy(knobs, state_dir):
        seen.update(limits(pc))
        seen["state"] = json.loads((st / "state.json").read_text())
        return orig_restore(knobs, state_dir)

    tl.restore, saved = spy, tl.restore
    try:
        assert run(sysfs, 65, ticks=1) == 0
    finally:
        tl.restore = saved
    assert seen["intel-rapl:0/constraint_0"] == 50_600_000             # 55 W × 0.92
    assert seen["intel-rapl-mmio:0/constraint_0"] == 27_600_000        # 30 W × 0.92
    assert seen["intel-rapl-mmio:0/constraint_2"] == 114 * W           # peak power untouched
    assert seen["state"]["target"] == 65 and seen["state"]["limit"] == "27.6 W"
    assert limits(pc) == before                                         # restored on exit
    assert not (st / "state.json").exists()


def test_floor_is_5_watts(sysfs):
    pc, th, _st, _cr = sysfs
    make_zone(th, 90)
    _kind, knobs = tl.discover(str(pc))
    assert set(tl.apply_level(knobs, tl.LEVEL_MIN)) == {5_500_000, tl.RAPL_FLOOR_UW}


def test_firmware_raise_becomes_the_new_original(sysfs):
    pc, _th, st, _cr = sysfs
    _kind, knobs = tl.discover(str(pc))
    tl.apply_level(knobs, 0.5)
    pl1 = pc / "intel-rapl:0/constraint_0_power_limit_uw"
    assert int(pl1.read_text()) == 27_500_000
    pl1.write_text(f"{60 * W}\n")  # e.g. power profile switch to performance
    tl.apply_level(knobs, 0.5)
    assert int(pl1.read_text()) == 30 * W
    tl.restore(knobs, str(st))
    assert int(pl1.read_text()) == 60 * W


def test_firmware_lowering_is_respected_but_not_restored(sysfs):
    pc, _th, st, _cr = sysfs
    _kind, knobs = tl.discover(str(pc))
    tl.apply_level(knobs, 0.5)
    pl1 = pc / "intel-rapl:0/constraint_0_power_limit_uw"
    pl1.write_text(f"{3 * W}\n")  # temporary firmware cap, even below our floor
    tl.apply_level(knobs, 1.0)
    assert int(pl1.read_text()) == 3 * W  # never raised above what firmware wants
    tl.restore(knobs, str(st))
    assert int(pl1.read_text()) == 55 * W  # the temporary cap does not become permanent


def test_frequency_fallback_keeps_an_uncapped_cpu_uncapped(tmp_path):
    cr = tmp_path / "cpu"
    make_cpu(cr, hw=4_000_000, cur_max=4_000_000)
    kind, knobs = tl.discover(str(tmp_path / "none"), str(cr))
    assert kind == "frequency"
    assert tl.apply_level(knobs, 0.5) == [2_000_000, 2_000_000]
    tl.restore(knobs, str(tmp_path))
    # no cap before → "no cap" after, even if turbo raises cpuinfo_max_freq meanwhile
    assert (cr / "cpu0/cpufreq/scaling_max_freq").read_text() == str(tl.UNLIMITED_KHZ)


def test_frequency_fallback_restores_an_existing_cap(tmp_path):
    cr = tmp_path / "cpu"
    make_cpu(cr, hw=4_000_000, cur_max=3_000_000)
    _kind, knobs = tl.discover(str(tmp_path / "none"), str(cr))
    assert tl.apply_level(knobs, 0.5) == [1_500_000, 1_500_000]
    tl.restore(knobs, str(tmp_path))
    assert (cr / "cpu1/cpufreq/scaling_max_freq").read_text() == "3000000"


def test_restore_from_state_after_a_crash_only_touches_known_paths(sysfs, tmp_path):
    pc, _th, st, cr = sysfs
    pl1 = pc / "intel-rapl:0/constraint_0_power_limit_uw"
    pl1.write_text(f"{10 * W}\n")  # lowered value left by a killed daemon
    victim = tmp_path / "victim"
    victim.write_text("keep")
    (st / "state.json").write_text(json.dumps({"target": 65, "originals": {
        str(pl1): 55 * W, str(victim): 1, str(pc / "intel-rapl:0/constraint_1_power_limit_uw"): "rm"}}))
    assert tl.restore_from_state(str(st), str(pc), str(cr)) == 0
    assert int(pl1.read_text()) == 55 * W
    assert victim.read_text() == "keep"
    assert not (st / "state.json").exists()
    assert tl.restore_from_state(str(st), str(pc), str(cr)) == 0  # nothing left: no-op


def test_restore_after_a_kill_at_the_floor(sysfs):
    """Limits at exactly 5 W are not discovered for regulation, but must still be restored."""
    pc, th, st, cr = sysfs
    make_cpu(cr)
    make_zone(th, 99)
    _kind, knobs = tl.discover(str(pc))
    tl.write_state(str(st), {"target": 55, "originals": {k.path: k.original for k in knobs}})
    tl.apply_level(knobs, tl.LEVEL_MIN)
    for p in pc.glob("intel-rapl*/constraint_[01]_power_limit_uw"):
        p.write_text(f"{tl.RAPL_FLOOR_UW}\n")  # all at the floor: discover() would fall back to cpufreq
    assert tl.discover(str(pc), str(cr))[0] == "frequency"
    assert tl.restore_from_state(str(st), str(pc), str(cr)) == 0
    assert limits(pc)["intel-rapl:0/constraint_0"] == 55 * W
    assert limits(pc)["intel-rapl-mmio:0/constraint_0"] == 30 * W
    assert (cr / "cpu0/cpufreq/scaling_max_freq").read_text() == "4000000"  # cpufreq untouched


def test_state_is_written_before_the_first_limit(sysfs, monkeypatch):
    pc, th, st, _cr = sysfs
    make_zone(th, 95)
    seen = []
    real_write = tl._write
    monkeypatch.setattr(tl, "_write", lambda path, v: seen.append((st / "state.json").exists()) or real_write(path, v))
    run(sysfs, 55, ticks=1)
    assert seen and seen[0] is True


def test_no_rapl_and_no_cpufreq_exits_3(tmp_path):
    make_zone(tmp_path / "th", 50)
    assert tl.run(65, str(tmp_path), str(tmp_path / "a"), str(tmp_path / "b"), str(tmp_path / "th"),
                  ticks=1, sleep=lambda s: None) == 3


@pytest.mark.parametrize("argv", [[], ["54"], ["91"], ["65", "70"], ["-1"], ["６５"], ["65.5"], ["../x"], ["065"]])
def test_daemon_refuses_bad_arguments(argv):
    assert tl.main(argv) == 2


# -- helper -------------------------------------------------------------------
@pytest.mark.parametrize("argv,ok", [
    (["set-thermal-target", "55"], True), (["set-thermal-target", "90"], True),
    (["clear-thermal-target"], True), (["set-thermal-target", "91"], False),
    (["set-thermal-target", "x"], False), (["clear-thermal-target", "1"], False),
    (["set-thermal-target", "065"], False)])
def test_helper_allowlist(argv, ok):
    assert helper.valid_args(argv) is ok


def test_helper_starts_exactly_one_instance(monkeypatch, tmp_path):
    calls = []

    class R:
        def __init__(self, rc):
            self.returncode = rc

    def fake_run(argv, check=False):
        calls.append(argv)
        return R(0)

    monkeypatch.setattr(helper.subprocess, "run", fake_run)
    lock = str(tmp_path / "lock")
    assert helper.main(["set-thermal-target", "65"], thermal_lock=lock) == 0
    assert calls == [[helper.THERMAL_DAEMON, "--check"],
                     ["systemctl", "stop", "powerdeck-thermal@*.service"],
                     ["systemctl", "start", "powerdeck-thermal@65.service"]]
    calls.clear()
    assert helper.main(["clear-thermal-target"], thermal_lock=lock) == 0
    assert calls == [["systemctl", "stop", "powerdeck-thermal@*.service"]]


def test_helper_reports_unavailable(monkeypatch):
    calls = []
    monkeypatch.setattr(helper.subprocess, "run",
                        lambda argv, check=False: calls.append(argv) or type("R", (), {"returncode": 3})())
    assert helper.main(["set-thermal-target", "65"]) == 3
    assert calls == [[helper.THERMAL_DAEMON, "--check"]]  # nothing started


# -- profile, plan, status ----------------------------------------------------
def test_profile_validation():
    for bad in (54, 91, "65", True, 65.0):
        with pytest.raises(ProfileError):
            Profile(name="X", cpu={"thermal_target_celsius": bad}).validate()
    Profile(name="X", cpu={"thermal_target_celsius": None}).validate()
    p = Profile.from_dict(Profile(name="X", cpu={"thermal_target_celsius": 70}).to_dict())
    assert p.cpu == {"thermal_target_celsius": 70}


@pytest.fixture
def available(tmp_path, monkeypatch):
    make_rapl(tmp_path / "pc")
    make_zone(tmp_path / "th", 50)
    (tmp_path / "st").mkdir()
    monkeypatch.setattr(cpu, "POWERCAP_ROOT", str(tmp_path / "pc"))
    monkeypatch.setattr(cpu, "THERMAL_ROOT", str(tmp_path / "th"))
    monkeypatch.setattr(cpu, "THERMAL_STATE_DIR", str(tmp_path / "st"))
    monkeypatch.setattr(backends, "helper_installed", lambda: True)
    return tmp_path / "st"


def _thermal_changes(p):
    return [c for c in engine.make_plan(p, lambda a: (127, "")).changes if c.target == "CPU temperature target"]


def test_plan_starts_and_reverts_regulation(available):
    [c] = _thermal_changes(BY["Rucksack"])
    assert (c.old, c.new) == ("off", "65 °C")
    assert c.privileged and c.apply_argv == ["pkexec", helper_path(), "set-thermal-target", "65"]
    assert c.revert_argv == ["pkexec", helper_path(), "clear-thermal-target"]


def test_plan_switches_back_to_the_running_target(available):
    (available / "state.json").write_text('{"target": 65, "limit": "12.0 W"}')
    assert _thermal_changes(BY["Rucksack"]) == []
    [c] = _thermal_changes(BY["Travel"])
    assert c.apply_argv[-1] == "clear-thermal-target" and c.revert_argv[-2:] == ["set-thermal-target", "65"]


def test_plan_skips_when_unavailable():
    assert _thermal_changes(BY["Rucksack"]) == []  # conftest: no sensors, no RAPL


def test_bad_state_file_is_ignored(available):
    for bad in ('{"target": 200}', '{"target": true}', "[]", "not json", '{"target": "65"}'):
        (available / "state.json").write_text(bad)
        assert cpu.read_thermal_target() is None


def test_capture_and_monitor_show_the_regulation(available):
    assert capture.capture_current(lambda a: (127, "")).cpu["thermal_target_celsius"] is None
    (available / "state.json").write_text('{"target": 70, "limit": "9.5 W"}')
    assert capture.capture_current(lambda a: (127, "")).cpu["thermal_target_celsius"] == 70
    s = monitor.Monitor().sample()
    assert s.regulation == "70 °C → 9.5 W"
    assert "regulated 70 °C → 9.5 W" in monitor.format_line(s)


def helper_path():
    from powerdeck import commands
    return commands.HELPER_PATH


def test_helper_waits_for_a_running_switch(monkeypatch, tmp_path):
    """stop+start of two parallel calls must not interleave (two daemons on the same limits)."""
    import fcntl
    import os
    import threading
    lock = str(tmp_path / "lock")
    calls = []
    monkeypatch.setattr(helper.subprocess, "run",
                        lambda argv, check=False: calls.append(argv) or type("R", (), {"returncode": 0})())
    fd = os.open(lock, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)  # another helper is switching right now
    t = threading.Thread(target=helper.main, args=(["set-thermal-target", "70"],), kwargs={"thermal_lock": lock})
    t.start()
    t.join(0.3)
    assert t.is_alive() and not any(a[0] == "systemctl" for a in calls)
    os.close(fd)
    t.join(2)
    assert calls[-1] == ["systemctl", "start", "powerdeck-thermal@70.service"]


def test_helper_refuses_a_planted_lock_symlink(monkeypatch, tmp_path):
    (tmp_path / "lock").symlink_to(tmp_path / "target")
    monkeypatch.setattr(helper.subprocess, "run", lambda argv, check=False: type("R", (), {"returncode": 0})())
    with pytest.raises(OSError):
        helper.main(["clear-thermal-target"], thermal_lock=str(tmp_path / "lock"))
    assert not (tmp_path / "target").exists()
