import pytest

from powerdeck import backends, engine
from powerdeck.builtin import BUILTIN_PROFILES
from powerdeck.describe import describe
from powerdeck.profile import Profile
from test_cpu import make_cpu

BY = {p.name: p for p in BUILTIN_PROFILES}


@pytest.fixture(autouse=True)
def english(monkeypatch):
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")


def test_every_builtin_profile_is_described():
    for p in BUILTIN_PROFILES:
        d = describe(p)
        assert d.summary and d.items
        for it in d.items:
            assert it.text and it.commands, (p.name, it.text)
            assert all(isinstance(a, str) for c in it.commands for a in c)


def test_rucksack_bullets_in_plain_language():
    texts = [i.text for i in describe(BY["Rucksack"]).items]
    assert "Closing the lid does nothing – the PC keeps running" in texts
    assert any("turbo/boost off" in t for t in texts)
    assert any("limited to 50 %" in t for t in texts)
    assert any(t.startswith("Thermal guard: notification at 70") and "can only warn" in t for t in texts)


def test_lid_warning_flag_and_commands():
    lid = describe(BY["RemoteWork"]).items[0]
    assert lid.warning and lid.command_text() == "pkexec /usr/libexec/powerdeck-helper set-lid ignore ignore ignore"
    d = describe(BY["Default"]).items[0]
    assert not d.warning and d.command_text().endswith("remove-lid")


def test_wifi_with_networks_lists_one_command_each():
    p = Profile(name="W", wifi_powersave="off", preferred_networks=["My Phone", "Doma"])
    it = describe(p).items[0]
    assert "My Phone, Doma" in it.text
    assert it.command_text().splitlines()[0] == "nmcli connection modify 'My Phone' 802-11-wireless.powersave 2"


def test_empty_profile_has_fallback_summary():
    d = describe(Profile(name="Empty"))
    assert d.items == [] and "0 setting" in d.summary


def test_described_commands_match_what_plan_runs(tmp_path, monkeypatch):
    """The GUI tooltip must show the same commands the engine really executes."""
    from powerdeck import cpu
    make_cpu(tmp_path / "c")
    monkeypatch.setattr(cpu, "CPU_ROOT", str(tmp_path / "c"))
    monkeypatch.setattr(backends.helper, "DROPIN_DIR", str(tmp_path / "l"))
    plan = engine.make_plan(BY["Rucksack"], lambda a: (127, ""))
    shown = {c for it in describe(BY["Rucksack"]).items for c in map(tuple, it.commands)}
    for ch in plan.changes:
        assert tuple(ch.apply_argv) in shown, ch.apply_argv


def test_slovak(monkeypatch):
    monkeypatch.setenv("LC_ALL", "sk_SK.UTF-8")
    d = describe(BY["Rucksack"])
    assert "Zavretie veka nič nerobí – PC beží ďalej" in [i.text for i in d.items]
    assert d.summary.startswith("Notebook zavretý v taške")


def test_no_contradicting_texts_in_builtin_profiles():
    for p in BUILTIN_PROFILES:
        texts = [i.text for i in describe(p).items]
        joined = " | ".join(texts)
        if p.power_mode == "power-saver":
            assert "switches to power-saver" not in joined, p.name
        if p.cpu.get("turbo") is False:
            assert "no limit" not in joined, p.name
        assert "listed in the profile" not in joined           # no promise the code would not keep
        for t in texts:
            assert texts.count(t) == 1, (p.name, t)              # no duplicate bullets
        cmds = [c for i in describe(p).items for c in map(tuple, i.commands)]
        writes = [c[:4] for c in cmds if c[:2] == ("gsettings", "set")]
        assert len(writes) == len(set(writes)), p.name          # no gsettings key written twice


def test_thermal_text_for_balanced_profile():
    t = [i.text for i in describe(BY["RemoteWork"]).items if i.text.startswith("Thermal")][0]
    assert "switches to power-saver" in t


def test_language_selection(monkeypatch):
    from powerdeck import i18n
    for k in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LANG", "sk_SK.UTF-8")
    assert i18n._("Battery") == "Batéria"
    monkeypatch.setenv("LANGUAGE", "de_DE:sk")            # GNOME language wins; no German → English
    assert i18n._("Battery") == "Battery"
    monkeypatch.setenv("LANGUAGE", "sk:en")
    assert i18n._("Battery") == "Batéria"
    monkeypatch.setenv("LANGUAGE", "")
    monkeypatch.setenv("LC_ALL", "C")
    assert i18n._("Battery") == "Battery"
