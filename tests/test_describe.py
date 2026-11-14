import pytest

from powerdeck import commands as cmd
from powerdeck import engine
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
    assert any(t.startswith("Thermal guard: notification at 70") and "suspends the laptop" in t for t in texts)


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


class EverythingDiffers:
    """Fake system where every setting differs from what the profile wants, so all of it is planned."""

    def __init__(self, p):
        self.p = p

    def __call__(self, a):
        if a[:2] == ["gsettings", "get"]:
            return 0, "'something-else'"
        if a == cmd.LIST_CONNECTIONS:
            return 0, "Home:802-11-wireless\nWired:802-3-ethernet"
        if a[:3] == ["nmcli", "-g", "802-11-wireless.powersave"]:
            return 0, "1"
        if a[:2] == ["nmcli", "-g"]:
            return 0, "no\n0"
        if a == ["powerprofilesctl", "get"]:
            return 0, "something-else"
        if a[:2] == ["rfkill", "list"]:
            return 0, f"0: hci0: Bluetooth\n\tSoft blocked: {'yes' if self.p.bluetooth else 'no'}"
        if a == ["brightnessctl", "-m"]:
            return 0, "intel_backlight,backlight,1,1%,100"
        return 127, ""


# Shown so the user knows what happens, but not run by `apply` itself.
NOT_RUN_BY_APPLY = {tuple(cmd.LIST_CONNECTIONS), tuple(cmd.screensaver("SetActive", "false")),
                    tuple(cmd.ENABLE_GUARD)}

EXTRA = [
    Profile(name="Everything", lid={"battery": "lock", "ac": "ignore", "docked": "suspend"},
            automatic_suspend={"battery": True}, wifi_powersave="on", preferred_networks=["My Phone", "C"],
            tethering={"connection": "Tel", "priority": 7}, bluetooth=True, cpu={"max_freq_percent": 60},
            display={"blank_after_minutes": 3, "lock_after_minutes": 10, "brightness_percent": 30},
            tasks={"on_activate": ["systemctl --user start a.service", "systemctl --user restart c.service"],
                   "on_deactivate": ["systemctl --user stop b.service"]}),
    Profile(name="NoLock", automatic_suspend={"battery": False, "ac": True}, cpu={"turbo": True},
            display={"lock_after_minutes": 0, "night_light": True}),
]


def _matches(shown, argv):
    """``<…>`` in a shown command stands for any value (e.g. "<each Wi-Fi network>")."""
    return len(shown) == len(argv) and all(s == a or (s.startswith("<") and s.endswith(">"))
                                           for s, a in zip(shown, argv))


@pytest.mark.parametrize("profile", BUILTIN_PROFILES + EXTRA, ids=lambda p: p.name)
def test_described_commands_are_exactly_what_plan_runs(profile, tmp_path, monkeypatch):
    """The GUI tooltips must show the commands the engine really executes, and nothing else."""
    from powerdeck import cpu, helper
    make_cpu(tmp_path / "c", cur_max=3_080_000, no_turbo="0" if profile.cpu.get("turbo") is False else "1")
    monkeypatch.setattr(cpu, "CPU_ROOT", str(tmp_path / "c"))
    monkeypatch.setattr(helper, "DROPIN_DIR", str(tmp_path / "l"))
    helper.main(["set-lid", "poweroff", "poweroff", "poweroff"], str(tmp_path / "l"), reload=False)
    run = EverythingDiffers(profile)
    planned = [c.apply_argv for c in engine.make_plan(profile, run).changes]
    # on_deactivate tasks run when leaving this profile for another one
    planned += [c.apply_argv for c in engine.make_plan(Profile(name="Other"), run, previous=profile).changes]
    shown = [c for it in describe(profile).items for c in it.commands]
    for argv in planned:
        assert any(_matches(s, argv) for s in shown), f"run but not shown: {argv}"
    for s in shown:
        assert tuple(s) in NOT_RUN_BY_APPLY or any(_matches(s, a) for a in planned), f"shown but not run: {s}"


def test_slovak(monkeypatch):
    monkeypatch.delenv("LANGUAGE", raising=False)   # takes precedence over LC_ALL, as in glibc
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
    assert t == "Thermal guard: notification at 85 °C; at 92 °C it notifies and suspends the laptop"


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


def test_every_translatable_string_has_slovak():
    """Each literal passed to _() and each GUI help text/row title needs an SK entry."""
    import ast
    from pathlib import Path
    from powerdeck import gui_texts
    from powerdeck.describe import LID_VERB, POWER_MODE_TEXT, WIFI_TEXT
    from powerdeck.i18n import SK
    texts = set()
    for f in (Path(__file__).parent.parent / "powerdeck").glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_" and node.args
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                texts.add(node.args[0].value)
    texts |= {t for _k, t in gui_texts.STATUS_ROWS} | {t for _k, t, _b in gui_texts.MONITOR_ROWS}
    texts |= {h[0] for h in (*gui_texts.STATUS_HELP.values(), *gui_texts.MONITOR_HELP.values())}
    texts |= set(LID_VERB.values()) | set(POWER_MODE_TEXT.values()) | set(WIFI_TEXT.values())
    texts |= {p.description for p in BUILTIN_PROFILES}
    assert sorted(t for t in texts if t not in SK) == []
