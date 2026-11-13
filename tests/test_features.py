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
        if a[0] in ("systemctl", "notify-send", "pkexec"):
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
    st, ev = guard.evaluate(93, 80, "Discharging", p, st); assert ev == ["critical"]
    st, ev = guard.evaluate(60, 80, "Discharging", p, st); assert ev == [] and st.thermal == "ok"
    st, ev = guard.evaluate(86, 80, "Discharging", p, st); assert ev == ["warn"]  # re-arms after cooling
    st, ev = guard.evaluate(60, 10, "Discharging", p, st); assert ev == ["low_battery"]
    st, ev = guard.evaluate(60, 9, "Discharging", p, st); assert ev == []
    _, ev = guard.evaluate(95, 5, "Discharging", BY["Default"], guard.GuardState()); assert ev == []


def test_guard_critical_throttles_and_notifies(monkeypatch):
    f = Fake()
    monkeypatch.setattr(guard, "read_status", lambda run: status.Status(50, "Discharging", 95, None, None))
    monkeypatch.setattr(engine, "active_profile", lambda: "RemoteWork")
    guard.run_once(guard.GuardState(), f, {"RemoteWork": BY["RemoteWork"]})
    assert f.mode == "power-saver" and any(c[0] == "notify-send" for c in f.calls)


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
    out = ("Overall:\n\tUnit status: inactive\nRDP:\n\tStatus: disabled\n\tPort: 3389\n"
           "VNC:\n\tStatus: enabled\n")
    assert status.read_remote_login(lambda a: (0, out)) == "disabled"
    assert status.read_remote_login(lambda a: (0, out.replace("disabled", "enabled", 1))) == "enabled"
    assert status.read_remote_login(lambda a: (127, "")) is None


def test_remote_access_notes():
    def run(state, login):
        return lambda a: ((0, state) if a[0] == "systemctl" else (1, "") if a[0] == "pgrep"
                          else (0, f"RDP:\n\tStatus: {login}\n"))
    assert guard.remote_access_notes(run("active", "enabled")) == []
    notes = guard.remote_access_notes(run("inactive", "disabled"))
    assert len(notes) == 2 and "guard is not running" in notes[0] and "Remote Login" in notes[1]
    manual = lambda a: (0, "inactive") if a[0] == "systemctl" else (0, "123") if a[0] == "pgrep" else (0, "")
    assert not any("guard" in n for n in guard.remote_access_notes(manual))   # `powerdeck guard` by hand
