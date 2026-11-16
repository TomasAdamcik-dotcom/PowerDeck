import json
import pytest

from powerdeck import backends, engine, helper
from powerdeck.builtin import BUILTIN_PROFILES
from powerdeck.profile import Profile, ProfileError, load_profiles, save_profile

BY = {p.name: p for p in BUILTIN_PROFILES}


class FakeSystem:
    """Fake command runner holding a tiny in-memory system state."""

    def __init__(self):
        self.g = {"sleep-inactive-battery-type": "suspend", "sleep-inactive-ac-type": "suspend",
                  "lock-enabled": "true", "idle-delay": "uint32 300"}
        self.wifi = {"MojTelefon": "0"}
        self.mode = "balanced"
        self.calls = []
        self.fail_on = None

    def __call__(self, argv):
        self.calls.append(argv)
        if self.fail_on and self.fail_on in argv:
            return 1, "boom"
        if argv[0] == "gsettings":
            if argv[1] == "get":
                return 0, f"'{self.g[argv[3]]}'"
            self.g[argv[3]] = argv[4]; return 0, ""
        if argv[0] == "nmcli":
            if argv[1] == "-t":
                return 0, "\n".join(n.replace(":", "\\:") + ":802-11-wireless" for n in self.wifi) + "\nWired:802-3-ethernet"
            if argv[1] == "-g":
                n = argv[-1]
                return (0, self.wifi[n]) if n in self.wifi else (10, "")
            self.wifi[argv[3]] = argv[5]; return 0, ""
        if argv[0] == "powerprofilesctl":
            if argv[1] == "get":
                return 0, self.mode
            self.mode = argv[2]; return 0, ""
        if argv[0] == "pkexec":
            return 0, ""
        return 127, ""


@pytest.fixture(autouse=True)
def lid_dir(monkeypatch, tmp_path):
    d = tmp_path / "logind"
    monkeypatch.setattr(helper, "DROPIN_DIR", str(d))
    monkeypatch.setattr(backends, "helper_installed", lambda: True)
    return d


def test_validation():
    with pytest.raises(ProfileError):
        Profile(name="x", lid={"battery": "explode"}).validate()
    with pytest.raises(ProfileError):
        Profile(name="../evil").validate()
    with pytest.raises(ProfileError):
        Profile(name="ok", preferred_networks=["--bad"]).validate()
    for p in BUILTIN_PROFILES:
        p.validate()


def test_roundtrip_and_broken_file(tmp_path):
    p = Profile(name="Mine", power_mode="performance")
    save_profile(p, tmp_path)
    (tmp_path / "broken.json").write_text("{nope")
    names = [x.name for x in load_profiles(tmp_path)]
    assert "Mine" in names and "Default" in names


def test_plan_remote_work_and_diff(tmp_path):
    sysm = FakeSystem()
    p = Profile(**{**BY["RemoteWork"].__dict__, "preferred_networks": ["MojTelefon", "Neexistuje"]})
    plan = engine.make_plan(p, sysm)
    text = plan.diff_text()
    assert "Auto-suspend (battery): suspend → nothing" in text
    assert "Wi-Fi powersave [MojTelefon]: 0 → 2" in text
    assert "Neexistuje" not in text          # unknown connection skipped
    lid = [c for c in plan.changes if c.backend == "logind"]
    assert lid and lid[0].privileged and plan.needs_admin
    assert lid[0].new == "ignore/ignore/ignore" and lid[0].old == "system default"


def test_nothing_changes_before_apply():
    sysm = FakeSystem()
    engine.make_plan(BY["BatterySaver"], sysm)
    assert all(c[1] in ("get", "-g", "-t", "list", "-m") for c in sysm.calls)


def test_apply_and_restore(tmp_path):
    sysm = FakeSystem()
    plan = engine.make_plan(BY["RemoteWork"], sysm)
    errs = engine.apply_plan(plan, sysm, tmp_path / "b", tmp_path / "active.json")
    assert errs == []
    assert sysm.g["sleep-inactive-ac-type"] == "nothing"
    assert engine.active_profile(tmp_path / "active.json") == "RemoteWork"
    assert engine.restore_last(sysm, tmp_path / "b", tmp_path / "active.json") == []
    assert sysm.g["sleep-inactive-ac-type"] == "suspend"
    assert engine.active_profile(tmp_path / "active.json") is None   # nothing was active before


def test_restore_reactivates_previous_profile(tmp_path):
    sysm, active = FakeSystem(), tmp_path / "active.json"
    for name in ("Default", "RemoteWork"):
        assert engine.apply_plan(engine.make_plan(BY[name], sysm), sysm, tmp_path / "b", active) == []
    assert engine.active_profile(active) == "RemoteWork"
    assert engine.restore_last(sysm, tmp_path / "b", active) == []
    assert engine.active_profile(active) == "Default"


def test_failure_rolls_back(tmp_path):
    sysm = FakeSystem()
    plan = engine.make_plan(BY["BatterySaver"], sysm)  # gsettings no-op, power mode changes
    sysm.fail_on = "power-saver"
    # force a failing change after a successful one
    p2 = Profile(name="T", automatic_suspend={"battery": False}, power_mode="power-saver")
    plan = engine.make_plan(p2, sysm)
    errs = engine.apply_plan(plan, sysm, tmp_path, tmp_path / "a.json")
    assert errs and sysm.g["sleep-inactive-battery-type"] == "suspend"  # reverted
    assert list(tmp_path.glob("*-T.json")) == []   # restore must not undo a failed apply


def test_helper_writes_only_whitelisted(tmp_path):
    assert helper.main(["set-lid", "ignore", "ignore", "ignore"], str(tmp_path), reload=False) == 0
    text = (tmp_path / helper.DROPIN).read_text()
    assert "HandleLidSwitch=ignore" in text and "HandleLidSwitchDocked=ignore" in text
    assert helper.main(["set-lid", "ignore", "rm -rf", "ignore"], str(tmp_path), reload=False) == 2
    assert helper.main(["whatever"], str(tmp_path), reload=False) == 2
    assert helper.main(["remove-lid"], str(tmp_path), reload=False) == 0
    assert not (tmp_path / helper.DROPIN).exists()


def test_lid_plan_existing_dropin_and_default(lid_dir):
    run = FakeSystem()
    helper.main(["set-lid", "ignore", "ignore", "ignore"], str(lid_dir), reload=False)
    assert backends.plan_lid(BY["RemoteWork"], run) == []          # already applied
    ch = backends.plan_lid(BY["Default"], run)[0]                  # back to default
    assert ch.apply_argv[-1] == "remove-lid"
    assert ch.revert_argv[-4:] == ["set-lid", "ignore", "ignore", "ignore"]
    assert backends.plan_lid(Profile(name="NoLid"), run) == []     # unmanaged


def test_policy_matches_helper_path_and_is_valid_xml():
    import xml.etree.ElementTree as ET
    from pathlib import Path
    root = ET.parse(Path(__file__).parent.parent / "data" / "io.github.powerdeck.policy").getroot()
    ann = root.find(".//annotate[@key='org.freedesktop.policykit.exec.path']")
    assert ann is not None and ann.text == backends.HELPER_PATH
    assert backends.HELPER_PATH.startswith("/usr/libexec/")   # root-owned location
    assert root.find(".//allow_active").text == "auth_admin_keep"


def test_privileged_changes_use_fixed_helper_not_python():
    ch = backends.plan_lid(BY["RemoteWork"], FakeSystem())[0]
    assert ch.apply_argv[:2] == ["pkexec", backends.HELPER_PATH]
    assert "python" not in " ".join(ch.apply_argv)


def test_apply_refuses_without_installed_helper(tmp_path, monkeypatch):
    monkeypatch.setattr(backends, "helper_installed", lambda: False)
    sysm = FakeSystem()
    plan = engine.make_plan(BY["RemoteWork"], sysm)
    errs = engine.apply_plan(plan, sysm, tmp_path / "b", tmp_path / "a.json")
    assert errs and "install-helper" in errs[0]
    assert sysm.g["sleep-inactive-ac-type"] == "suspend"   # nothing was changed


def test_helper_file_is_atomic_and_world_readable(tmp_path):
    helper.main(["set-lid", "lock", "ignore", "suspend"], str(tmp_path), reload=False)
    f = tmp_path / helper.DROPIN
    assert oct(f.stat().st_mode & 0o777) == "0o644" and not (tmp_path / (helper.DROPIN + ".tmp")).exists()


def test_wifi_without_listed_networks_covers_all_saved_wifi():
    sysm = FakeSystem()
    sysm.wifi["Café:5G"] = "0"          # nmcli -t prints it as Café\\:5G
    plan = engine.make_plan(Profile(name="W", wifi_powersave="off"), sysm)
    wifi = [c for c in plan.changes if c.target.startswith("Wi-Fi powersave")]
    assert {c.apply_argv[3] for c in wifi} == {"MojTelefon", "Café:5G"}   # not the wired one
    assert all(c.new == "2" for c in wifi)


def test_thermal_suspend_policy_matches_guard_and_is_minimal():
    import xml.etree.ElementTree as ET
    from pathlib import Path
    from powerdeck import guard
    base = Path(__file__).parent.parent
    act = ET.parse(base / "data" / "io.github.powerdeck.policy").getroot().find(
        ".//action[@id='io.github.powerdeck.thermal-suspend']")
    assert act.find("annotate").text == guard.SUSPEND_HELPER
    assert act.find(".//allow_active").text == "yes" and act.find(".//allow_any").text == "no"
    script = (base / "data" / "powerdeck-suspend").read_text()
    cmds = [l for l in script.splitlines() if l.strip() and not l.startswith("#")]
    assert cmds == ["exec /usr/bin/systemctl suspend --ignore-inhibitors"]   # nothing else, no args


def test_backups_same_second_undo_newest_first(tmp_path, monkeypatch):
    """Two applies within one clock tick: restore must undo the second one first."""
    sysm, b, active = FakeSystem(), tmp_path / "b", tmp_path / "active.json"
    monkeypatch.setattr(engine.time, "time_ns", lambda: 1_700_000_000_000_000_000)
    assert engine.apply_plan(engine.make_plan(Profile(name="B", power_mode="performance"), sysm),
                             sysm, b, active) == []
    assert engine.apply_plan(engine.make_plan(Profile(name="A", power_mode="power-saver"), sysm),
                             sysm, b, active) == []
    assert engine.restore_last(sysm, b, active) == []
    assert sysm.mode == "performance" and engine.active_profile(active) == "B"
    assert engine.restore_last(sysm, b, active) == []
    assert sysm.mode == "balanced"


def test_old_backup_names_still_read_in_order(tmp_path):
    """Backups written by PowerDeck <= 0.3 ("<stamp>[-<n>]-<profile>.json") keep working."""
    sysm, b = FakeSystem(), tmp_path / "b"
    b.mkdir()

    def backup(name, old, new):
        ch = {"backend": "power-profiles", "target": "Power mode", "old": old, "new": new,
              "apply_argv": ["powerprofilesctl", "set", new],
              "revert_argv": ["powerprofilesctl", "set", old], "privileged": False}
        (b / name).write_text(json.dumps({"profile": name, "previous": None, "changes": [ch]}))

    # applied in this order: m1 (balanced → power-saver) ... m4; newest must be undone first
    backup("20250101-120000-Zed.json", "balanced", "power-saver")
    backup("20250101-120000-1-Alpha.json", "power-saver", "performance")
    backup("20250101-120000-2-Beta.json", "performance", "power-saver")
    backup("01700000000000000000-New.json", "power-saver", "performance")
    sysm.mode = "performance"
    assert engine.reset_all(sysm, b, active_file=tmp_path / "a.json") == []
    sets = [c[2] for c in sysm.calls if c[:2] == ["powerprofilesctl", "set"]]
    assert sets == ["power-saver", "performance", "power-saver", "balanced"]
    assert sysm.mode == "balanced"


@pytest.mark.parametrize("bad", [
    {"name": "X", "lid": []}, {"name": "X", "lid": "ignore"}, {"name": "X", "automatic_suspend": [1]},
    {"name": "X", "display": None}, {"name": "X", "cpu": "fast"}, {"name": "X", "tethering": ["Tel"]},
    {"name": "X", "tasks": []}, {"name": "X", "tasks": {"on_activate": "systemctl --user start a.service"}},
    {"name": "X", "tasks": {"on_activate": [1]}}, {"name": "X", "thermal_guard": [80, 90]},
    {"name": "X", "thermal_guard": {"warn_celsius": 80}}, {"name": "X", "thermal_guard": {"warn_celsius": "80", "critical_celsius": 90}},
    {"name": "X", "preferred_networks": "Home"}, {"name": "X", "preferred_networks": [1]},
    {"name": 5}, {"name": None}, {"name": "X", "description": ["x"]}, {"name": "X", "bluetooth": 1},
    {"name": "X", "wifi_powersave": []}, {"name": "X", "power_mode": {}}, {"name": "X", "lid": {"battery": []}},
    {"name": "X", "display": {"blank_after_minutes": True}}, {"name": "X", "remote_access": "yes"},
    [1, 2], "Default", 3, None,
])
def test_malformed_user_profiles_are_skipped(tmp_path, bad):
    (tmp_path / "bad.json").write_text(json.dumps(bad))
    with pytest.raises(ProfileError):
        Profile.from_dict(bad)
    names = [p.name for p in load_profiles(tmp_path)]       # never crashes, built-ins still there
    assert "X" not in names and "Default" in names


def test_restore_reports_broken_backup_instead_of_crashing(tmp_path):
    b = tmp_path / "b"
    b.mkdir()
    (b / "01700000000000000000-X.json").write_text('{"changes": [{"backend": "x"}]}')
    errs = engine.restore_last(FakeSystem(), b, tmp_path / "a.json")
    assert len(errs) == 1 and "malformed" in errs[0]
    (tmp_path / "a.json").write_text("[]")
    assert engine.active_profile(tmp_path / "a.json") is None


def test_wifi_already_set_as_nmcli_prints_it_is_no_change():
    """nmcli -g prints "disable"/"default", PowerDeck writes 2/0: no "disable → 2" in the plan."""
    sysm = FakeSystem()
    sysm.wifi = {"Home": "disable", "Cafe": "default"}
    wifi = [c for c in engine.make_plan(Profile(name="W", wifi_powersave="off"), sysm).changes
            if c.target.startswith("Wi-Fi powersave")]
    assert [(c.target, c.old, c.new) for c in wifi] == [("Wi-Fi powersave [Cafe]", "default", "2")]
    assert wifi[0].revert_argv[-1] == "default"
