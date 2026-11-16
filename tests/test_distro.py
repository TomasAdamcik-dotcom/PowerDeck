"""Other distributions: Cinnamon (Linux Mint) settings and Fedora's power-mode D-Bus API."""
from powerdeck import backends, capture, commands, desktop, engine, status
from powerdeck.profile import Profile


def test_desktop_from_session_variables():
    assert desktop.current({"XDG_CURRENT_DESKTOP": "X-Cinnamon"}) == desktop.CINNAMON
    assert desktop.current({"XDG_CURRENT_DESKTOP": "zorin:GNOME"}) == desktop.GNOME
    assert desktop.current({"XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}) == desktop.GNOME
    assert desktop.current({"DESKTOP_SESSION": "cinnamon"}) == desktop.CINNAMON


def test_desktop_without_session_falls_back_to_installed_one():
    only_cinnamon = lambda b: "/usr/bin/x" if b == "cinnamon-session" else None
    both = lambda b: "/usr/bin/x"
    assert desktop.current({}, which=only_cinnamon) == desktop.CINNAMON
    assert desktop.current({}, which=both) == desktop.GNOME
    assert desktop.current({}, which=lambda b: None) == desktop.GNOME


def test_gsettings_keys_translated_on_cinnamon(monkeypatch):
    assert commands.gsettings_get(commands.IDLE_DELAY)[2] == "org.gnome.desktop.session"
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "X-Cinnamon")
    assert commands.gsettings_get(commands.IDLE_DELAY) == [
        "gsettings", "get", "org.cinnamon.desktop.session", "idle-delay"]
    assert commands.gsettings_set(commands.AUTO_SUSPEND["ac"], "nothing") == [
        "gsettings", "set", "org.cinnamon.settings-daemon.plugins.power",
        "sleep-inactive-ac-type", "nothing"]
    # keys Cinnamon does not have keep their GNOME schema (and are skipped when missing)
    assert commands.gsettings_get(("org.gnome.desktop.remote-desktop.rdp", "enable"))[2] == \
        "org.gnome.desktop.remote-desktop.rdp"


class GS:
    """Fake gsettings store plus a power-mode tool."""

    def __init__(self, values, ppctl=True, dbus_mode="balanced"):
        self.values, self.ppctl, self.dbus_mode, self.calls = dict(values), ppctl, dbus_mode, []

    def __call__(self, argv):
        self.calls.append(argv)
        if argv[:2] == ["gsettings", "get"]:
            v = self.values.get((argv[2], argv[3]))
            return (0, v) if v is not None else (1, "No such schema")
        if argv[:2] == ["gsettings", "set"]:
            self.values[(argv[2], argv[3])] = argv[4]
            return 0, ""
        if argv[0] == "powerprofilesctl":
            return (0, "performance\n") if self.ppctl else (127, "")
        if argv == commands.GET_POWER_MODE_DBUS:
            return (0, f"(<'{self.dbus_mode}'>,)\n") if self.dbus_mode else (1, "no service")
        return 1, ""


CP = "org.cinnamon.settings-daemon.plugins.power"


def test_cinnamon_auto_suspend_and_lid(monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "X-Cinnamon")
    run = GS({(CP, "sleep-inactive-ac-type"): "'suspend'",
              (CP, "lid-close-ac-action"): "'suspend'",
              (CP, "lid-close-battery-action"): "'suspend'"})
    p = Profile(name="X", automatic_suspend={"ac": False}, lid={"ac": "ignore", "battery": "lock"})
    changes = backends.plan_auto_suspend(p, run) + backends.plan_cinnamon_lid(p, run)
    assert [(c.target, c.old, c.new) for c in changes] == [
        ("Auto-suspend (ac)", "suspend", "nothing"),
        ("Cinnamon lid close (battery)", "suspend", "blank"),
        ("Cinnamon lid close (ac)", "suspend", "nothing")]
    assert changes[2].revert_argv == ["gsettings", "set", CP, "lid-close-ac-action", "suspend"]


def test_cinnamon_lid_keys_not_touched_on_gnome():
    p = Profile(name="X", lid={"ac": "ignore"})
    assert backends.plan_cinnamon_lid(p, GS({(CP, "lid-close-ac-action"): "'suspend'"})) == []


def test_power_mode_through_dbus_without_powerprofilesctl():
    run = GS({}, ppctl=False, dbus_mode="balanced")
    assert backends.read_power_mode(run) == ("balanced", True)
    [c] = backends.plan_power_mode(Profile(name="X", power_mode="power-saver"), run)
    assert c.apply_argv == commands.power_mode("power-saver", dbus=True)
    assert c.apply_argv[-1] == "<'power-saver'>"
    assert c.revert_argv == commands.power_mode("balanced", dbus=True)
    assert commands.allowed_as_root(c.revert_argv)
    assert status.read_status(run).power_mode == "balanced"
    assert capture.capture_current(run).power_mode == "balanced"


def test_powerprofilesctl_preferred_and_no_daemon_means_no_change():
    assert backends.read_power_mode(GS({})) == ("performance", False)
    none = GS({}, ppctl=False, dbus_mode=None)
    assert backends.read_power_mode(none) is None
    assert backends.plan_power_mode(Profile(name="X", power_mode="balanced"), none) == []


def test_no_change_planned_when_dbus_mode_already_set():
    run = GS({}, ppctl=False, dbus_mode="balanced")
    assert engine.make_plan(Profile(name="X", power_mode="balanced"), run).changes == []


def test_doctor_cinnamon_without_powerprofilesctl(monkeypatch, tmp_path):
    from powerdeck import doctor
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "X-Cinnamon")
    keys = [*commands.AUTO_SUSPEND.values(), commands.IDLE_DELAY, commands.LOCK_ENABLED,
            commands.LOCK_DELAY, commands.NIGHT_LIGHT, *desktop.CINNAMON_LID.values()]
    run = GS({tuple(commands.gsettings_get(k)[2:]): "'x'" for k in keys}, ppctl=False)
    lines = doctor.check_system(run, which=lambda t: "/usr/bin/" + t, service_dirs=())
    text = "\n".join(f"{l} {t}" for l, t in lines)
    assert "OK power mode: balanced (D-Bus)" in text
    assert "OK cinnamon settings: all 8 keys" in text
    assert "OK remote desktop: xrdp" in text
    assert "FAIL" not in text


def test_doctor_warns_about_missing_pieces(monkeypatch, tmp_path):
    from powerdeck import doctor
    (tmp_path / doctor.PP_SERVICE).write_text("")
    lines = doctor.check_system(GS({}, ppctl=False, dbus_mode=None), which=lambda t: None,
                                service_dirs=(str(tmp_path),))
    text = "\n".join(f"{l} {t}" for l, t in lines)
    assert "OK power mode: power-profiles daemon installed" in text
    assert "WARN settings missing (skipped): org.gnome.settings-daemon.plugins.power" in text
    assert "WARN nmcli missing" in text
    assert "WARN remote desktop: gnome-remote-desktop not installed" in text
    assert "WARN guard service not running" in text


def test_doctor_toolkit_check_runs_here():
    from powerdeck import doctor
    assert [l for l, t in doctor.check_toolkit() if l == doctor.FAIL] == []
