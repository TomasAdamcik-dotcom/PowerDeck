import json
import pytest

from powerdeck import backends, engine, helper
from powerdeck.builtin import BUILTIN_PROFILES
from powerdeck.profile import Profile, ProfileError, load_profiles, save_profile

BY = {p.name: p for p in BUILTIN_PROFILES}


class FakeSystem:
    """Fake command runner holding a tiny in-memory system state."""

    def __init__(self):
        self.g = {"sleep-inactive-battery-type": "suspend", "sleep-inactive-ac-type": "suspend"}
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
    assert all(c[0] in ("gsettings", "nmcli", "powerprofilesctl") and c[1] in ("get", "-g") for c in sysm.calls)


def test_apply_and_restore(tmp_path):
    sysm = FakeSystem()
    plan = engine.make_plan(BY["RemoteWork"], sysm)
    errs = engine.apply_plan(plan, sysm, tmp_path / "b", tmp_path / "active.json")
    assert errs == []
    assert sysm.g["sleep-inactive-ac-type"] == "nothing"
    assert engine.active_profile(tmp_path / "active.json") == "RemoteWork"
    assert engine.restore_last(sysm, tmp_path / "b") == []
    assert sysm.g["sleep-inactive-ac-type"] == "suspend"


def test_failure_rolls_back(tmp_path):
    sysm = FakeSystem()
    plan = engine.make_plan(BY["BatterySaver"], sysm)  # gsettings no-op, power mode changes
    sysm.fail_on = "power-saver"
    # force a failing change after a successful one
    p2 = Profile(name="T", automatic_suspend={"battery": False}, power_mode="power-saver")
    plan = engine.make_plan(p2, sysm)
    errs = engine.apply_plan(plan, sysm, tmp_path, tmp_path / "a.json")
    assert errs and sysm.g["sleep-inactive-battery-type"] == "suspend"  # reverted


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
