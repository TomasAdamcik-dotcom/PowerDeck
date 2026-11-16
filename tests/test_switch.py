"""One-click profile switch (engine.switch_to): the previous profile leaves nothing behind."""
import pytest

from powerdeck import backends, engine, helper
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
