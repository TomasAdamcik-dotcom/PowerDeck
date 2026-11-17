"""One-click profile switch (engine.switch_to): the previous profile leaves nothing behind."""
import pytest

from powerdeck import backends, commands, engine, helper
from powerdeck.profile import Profile

from test_core import FakeSystem

A = Profile(name="A", power_mode="power-saver", wifi_powersave="off")  # like Rucksack
B = Profile(name="B", power_mode="performance")                         # manages less than A
C = Profile(name="C", power_mode="power-saver")                         # same mode as A


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(helper, "DROPIN_DIR", str(tmp_path / "logind"))
    monkeypatch.setattr(backends, "helper_installed", lambda: True)
    return FakeSystem(), tmp_path / "b", tmp_path / "active.json"


def switch(sysm, b, active, p, previous):
    return engine.switch_to(p, sysm, previous, b, active)


def test_switch_reverts_what_the_new_profile_does_not_manage(env):
    sysm, b, active = env
    assert switch(sysm, b, active, A, None) == []
    assert sysm.wifi["MojTelefon"] == "2" and sysm.mode == "power-saver"
    assert switch(sysm, b, active, B, A) == []
    assert sysm.wifi["MojTelefon"] == "0"           # A's Wi-Fi change is gone
    assert sysm.mode == "performance" and engine.active_profile(active) == "B"
    assert len(engine._backup_files(b)) == 1         # only B is pending
    assert engine.restore_last(sysm, b, active) == []
    assert sysm.mode == "balanced" and engine.active_profile(active) is None


def test_switch_to_a_profile_with_the_same_value_keeps_it(env):
    sysm, b, active = env
    assert switch(sysm, b, active, A, None) == []
    assert switch(sysm, b, active, C, A) == []
    assert sysm.mode == "power-saver" and sysm.wifi["MojTelefon"] == "0"
    assert engine.restore_last(sysm, b, active) == []
    assert sysm.mode == "balanced"                   # restore goes back to before PowerDeck


def test_reapplying_the_active_profile_does_not_undo_it(env):
    sysm, b, active = env
    assert switch(sysm, b, active, A, None) == []
    sysm.calls.clear()
    assert switch(sysm, b, active, A, A) == []
    assert not [c for c in sysm.calls if c[0] == "powerprofilesctl" and c[1] == "set"]
    assert len(engine._backup_files(b)) == 1 and engine.active_profile(active) == "A"


def test_failed_undo_does_not_apply_the_new_profile(env):
    sysm, b, active = env
    assert switch(sysm, b, active, A, None) == []
    sysm.fail_on = "balanced"                        # reverting the power mode fails
    assert switch(sysm, b, active, B, A) != []
    assert sysm.mode == "power-saver" and engine.active_profile(active) == "A"
    assert len(engine._backup_files(b)) == 1         # A stays pending for `restore`


def test_pending_undo_shows_value_before_powerdeck_and_value_now(env):
    sysm, b, active = env
    for p in (A, B):  # stacked the old way, without switch_to
        assert engine.apply_plan(engine.make_plan(p, sysm), sysm, b, active) == []
    undo = {c.target: c for c in engine.pending_undo(b)}
    assert (undo["Power mode"].new, undo["Power mode"].old) == ("performance", "balanced")
    assert "Power mode: performance → balanced" in engine.undo_text(list(undo.values()))
    assert engine.pending_undo(b) and switch(sysm, b, active, C, B) == []
    assert sysm.mode == "power-saver" and sysm.wifi["MojTelefon"] == "0"
    assert [c.target for c in engine.pending_undo(b)] == ["Power mode"]


class Backlight:
    """Wraps a FakeSystem with a backlight (max 1000) that something outside PowerDeck may change."""

    def __init__(self, sysm, raw, jump_to=None):
        self.sysm, self.raw, self.jump_to, self.sets = sysm, raw, jump_to, []

    def __call__(self, argv):
        if argv == ["brightnessctl", "-m"]:
            return 0, f"intel_backlight,backlight,{self.raw},{round(self.raw / 10)}%,1000"
        if argv[:1] == ["busctl"] and "SetBrightness" in argv:
            assert argv == commands.brightness("intel_backlight", argv[-1])
            self.raw = int(argv[-1])
            self.sets.append(self.raw)
            return 0, ""
        if self.jump_to is not None and argv[:1] == ["powerprofilesctl"] and argv[1:2] == ["set"]:
            self.raw, self.jump_to = self.jump_to, None   # e.g. GNOME after a mode change
        return self.sysm(argv)


@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr(engine, "BRIGHTNESS_SETTLE_S", 0)


DEFAULT = Profile(name="Default", power_mode="balanced")
DIM = Profile(name="Dim", power_mode="power-saver", display={"brightness_percent": 40})


def test_a_switch_keeps_the_brightness_from_before_it(env, no_wait):
    sysm, b, active = env
    light = Backlight(sysm, 158, jump_to=500)               # 15.8 %, then a jump to 50 %
    assert engine.switch_to(A, light, None, b, active) == []
    assert light.raw == 158 and light.sets == [158]          # exact level, not a rounded percent
    light.raw, light.jump_to = 333, 500                      # the user changed it meanwhile
    assert engine.switch_to(DEFAULT, light, A, b, active) == []
    assert light.raw == 333 and light.sets == [158, 333]


def test_brightness_untouched_when_nothing_moves_it_or_when_staying(env, no_wait):
    sysm, b, active = env
    light = Backlight(sysm, 600)
    engine.switch_to(A, light, None, b, active)
    engine.switch_to(B, light, A, b, active)
    light.jump_to = 500
    engine.switch_to(B, light, B, b, active)                 # re-apply: not a switch
    assert light.sets == [] and light.raw == 600


def test_a_profile_with_its_own_brightness_and_switching_away_from_it(env, no_wait):
    sysm, b, active = env
    light = Backlight(sysm, 253)
    assert engine.switch_to(DIM, light, None, b, active) == []
    assert light.raw == 400                                  # 40 % of 1000, through logind
    light.jump_to = 500
    assert engine.switch_to(DEFAULT, light, DIM, b, active) == []
    assert light.raw == 253 and light.sets[-1] == 253        # exactly the level from before Dim


def test_keep_brightness_catches_a_late_jump():
    calls = []
    state = {"raw": 300, "polls": 0}

    def run(argv):
        if argv == ["brightnessctl", "-m"]:
            state["polls"] += 1
            if state["polls"] == 3:
                state["raw"] = 500                           # jumps a moment later
            return 0, f"x,backlight,{state['raw']},{state['raw'] // 10}%,1000"
        calls.append(argv)
        state["raw"] = int(argv[-1])
        return 0, ""
    engine.keep_brightness(run, 300, settle=2.0, sleep=lambda s: None)
    assert calls == [commands.brightness("x", 300)] and state["raw"] == 300


def test_keep_brightness_gives_up_when_logind_refuses():
    calls = []

    def run(argv):
        calls.append(argv)
        if argv == ["brightnessctl", "-m"]:
            return 0, "x,backlight,500,50%,1000"
        return 1, "Call failed: Your session has no seat, refusing."
    engine.keep_brightness(run, 300, settle=2.0, sleep=lambda s: None)
    assert calls == [["brightnessctl", "-m"], commands.brightness("x", 300)]


def test_keep_brightness_without_backlight_does_nothing():
    engine.keep_brightness(lambda argv: (127, ""), 30, sleep=lambda s: pytest.fail("waited"))


def test_backlight_parsing_refuses_odd_device_names():
    assert backends.read_backlight(lambda a: (0, "intel_backlight,backlight,3073,16%,19393\n")) \
        == backends.Backlight("intel_backlight", 3073, 19393)
    for out in ("x;id,backlight,1,1%,10", "-x,backlight,1,1%,10", "x,leds,1,1%,10", "x,backlight,1,1%,0", "x,backlight,a,1%,10", ""):
        assert backends.read_backlight(lambda a: (0, out)) is None


# -- GUI Apply button and top-bar icon: switch at once, no confirmation (engine.quick_switch)
L = Profile(name="L", lid={"battery": "ignore", "ac": "ignore", "docked": "ignore"})


def quick(sysm, b, active, p, previous, installed=True):
    return engine.quick_switch(p, sysm, previous, installed, b, active)


def test_quick_switch_applies_at_once(env):
    sysm, b, active = env
    assert quick(sysm, b, active, A, None) == []
    assert sysm.mode == "power-saver" and engine.active_profile(active) == "A"
    assert quick(sysm, b, active, B, A) == []
    assert sysm.mode == "performance" and sysm.wifi["MojTelefon"] == "0"


def test_quick_switch_to_the_active_profile_in_place_does_nothing(env):
    sysm, b, active = env
    assert quick(sysm, b, active, A, None) == []
    sysm.calls.clear()
    assert quick(sysm, b, active, A, A) == engine.NOTHING_TO_DO
    assert not [c for c in sysm.calls if c[0] == "powerprofilesctl" and c[1] == "set"]


def test_quick_switch_without_helper_changes_nothing(env):
    """Must not: apply half a profile when its lid/CPU part cannot run."""
    sysm, b, active = env
    mode = sysm.mode
    assert quick(sysm, b, active, L, None, installed=False) == engine.NEEDS_HELPER
    assert sysm.mode == mode and engine.active_profile(active) is None
    assert not engine._backup_files(b)


def test_quick_switch_away_from_a_lid_profile_needs_the_helper_too(env):
    sysm, b, active = env
    assert quick(sysm, b, active, L, None) == []
    assert quick(sysm, b, active, B, L, installed=False) == engine.NEEDS_HELPER
    assert engine.active_profile(active) == "L"
