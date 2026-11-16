"""Human-readable description of a profile: one summary line + bullets, each with the exact command.

Pure (no GTK, no system access) so it can be tested. Commands are what PowerDeck would run
when the profile is applied; they are shown to technical users in the GUI tooltips.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass, field

from . import backends
from . import commands as cmd
from .i18n import _
from .profile import Profile, parse_task

LID_VERB = {
    "ignore": "does nothing – the PC keeps running",
    "suspend": "suspends the PC",
    "lock": "locks the screen",
    "poweroff": "powers the PC off",
}
POWER_MODE_TEXT = {
    "power-saver": "power-saver (lower clocks, longer battery, cooler)",
    "balanced": "balanced (default)",
    "performance": "performance (highest clocks, hotter)",
}
WIFI_TEXT = {"off": "off", "on": "on", "default": "system default"}


@dataclass
class Item:
    text: str
    commands: list[list[str]] = field(default_factory=list)  # argv lists, shown joined
    note: str = ""  # extra explanation line in the tooltip
    warning: bool = False

    def command_text(self) -> str:
        return "\n".join(shlex.join(c) for c in self.commands) if self.commands else ""


@dataclass
class Description:
    summary: str
    items: list[Item]


def _on_off(on: bool) -> str:
    return _("on") if on else _("off")


def _lid(p: Profile) -> list[Item]:
    if not p.lid:
        return []
    want = backends.lid_settings(p)
    if len(set(want.values())) == 1:
        text = _("Closing the lid {}").format(_(LID_VERB[want["battery"]]))
    else:
        text = _("Lid close – battery: {}, AC: {}, dock: {}").format(*(_(want[k]) for k in cmd.LID_ORDER))
    if all(v == "suspend" for v in want.values()):
        note = _("Removes the PowerDeck logind drop-in, so the system default (suspend) applies.")
    else:
        note = _("Writes /etc/systemd/logind.conf.d/99-powerdeck-lid.conf (HandleLidSwitch*) "
                 "and reloads logind with SIGHUP. Needs admin rights.")
    return [Item(text, [cmd.lid(want)], note, warning=want["battery"] == "ignore")]


def _auto_suspend(p: Profile) -> list[Item]:
    a = p.automatic_suspend
    if not a:
        return []
    cmds = [cmd.gsettings_set(setting, value) for _src, setting, value in backends.auto_suspend_settings(p)]
    if len(a) == 2 and a["battery"] == a["ac"]:
        text = _("Automatic suspend when idle is {} (battery and AC)").format(_on_off(a["battery"]))
    else:
        text = _("Automatic suspend when idle: {}").format(
            ", ".join(f"{_(k)} {_on_off(v)}" for k, v in a.items()))
    return [Item(text, cmds, _("GNOME power setting, no admin rights needed."))]


def _wifi(p: Profile) -> list[Item]:
    if p.wifi_powersave is None:
        return []
    val = cmd.WIFI_POWERSAVE_VALUES[p.wifi_powersave]
    if p.preferred_networks:
        text = _("Wi-Fi powersave: {} – for: {}").format(_(WIFI_TEXT[p.wifi_powersave]),
                                                        ", ".join(p.preferred_networks))
        cmds = [cmd.wifi_powersave(n, val) for n in p.preferred_networks]
    else:
        text = _("Wi-Fi powersave: {} – for all saved Wi-Fi networks").format(_(WIFI_TEXT[p.wifi_powersave]))
        cmds = [cmd.LIST_CONNECTIONS, cmd.wifi_powersave("<" + _("each Wi-Fi network") + ">", val)]
    return [Item(text, cmds, _("NetworkManager values: 0 default, 2 off, 3 on. "
                               "Networks not saved on this PC are skipped."))]


def _tethering(p: Profile) -> list[Item]:
    if not p.tethering:
        return []
    name, prio = backends.tethering_settings(p)
    return [Item(_("Phone tethering “{}” connects automatically (priority {})").format(name, prio),
                 [cmd.autoconnect(name, "yes", prio)])]


def _power_mode(p: Profile) -> list[Item]:
    if not p.power_mode:
        return []
    return [Item(_("Power mode: {}").format(_(POWER_MODE_TEXT[p.power_mode])),
                 [cmd.power_mode(p.power_mode)], _("Uses power-profiles-daemon."))]


def _cpu(p: Profile) -> list[Item]:
    items = []
    if "turbo" in p.cpu:
        on = p.cpu["turbo"]
        text = (_("CPU turbo/boost on") if on
                else _("CPU turbo/boost off – CPU stays at base clock (less heat)"))
        items.append(Item(text, [cmd.cpu_turbo(on)],
                          _("Writes intel_pstate/no_turbo or cpufreq/boost in /sys. "
                            "Resets after reboot. Needs admin rights.")))
    if "max_freq_percent" in p.cpu:
        m = p.cpu["max_freq_percent"]
        if m == 100 and p.cpu.get("turbo") is False:
            text = _("No extra CPU frequency cap on top of turbo off (removes a cap left by another profile)")
        elif m == 100:
            text = _("CPU max frequency: no limit (100 %)")
        else:
            text = _("CPU max frequency limited to {} % of the hardware maximum").format(m)
        items.append(Item(text, [cmd.cpu_max_percent(m)],
                          _("Writes scaling_max_freq for every core in /sys/devices/system/cpu. "
                            "Resets after reboot. Needs admin rights.")))
    if "thermal_target_celsius" in p.cpu:
        t = p.cpu["thermal_target_celsius"]
        text = (_("No CPU temperature regulation") if t is None
                else _("CPU held at {} °C: its power limit is lowered only as much as needed").format(t))
        items.append(Item(text, [cmd.thermal_target(t)],
                          _("Runs the root service powerdeck-thermal, which every 2 s adjusts the RAPL "
                            "power limit (or scaling_max_freq) in /sys and restores it when stopped. "
                            "Resets after reboot. Needs admin rights.")))
    return items


def _display(p: Profile) -> list[Item]:
    d = p.display
    writes = backends.display_settings(d)

    def cmds(key):  # the gsettings writes plan_display makes for this display key
        return [cmd.gsettings_set(setting, value) for k, _label, setting, value in writes if k == key]

    items = []
    if "blank_after_minutes" in d:
        m = d["blank_after_minutes"]
        text = _("Screen never blanks when idle") if m == 0 else _("Screen blanks after {} min idle").format(m)
        note = (_("Blanking starts the GNOME screensaver, which would drop remote desktop (RDP).")
                if m == 0 and p.remote_access else "")
        items.append(Item(text, cmds("blank_after_minutes"), note))
    if "lock_after_minutes" in d:
        m = d["lock_after_minutes"]
        items.append(Item(_("Screen lock off") if m == 0 else _("Screen locks after {} min").format(m),
                          cmds("lock_after_minutes")))
    if "lock_screen" in d:
        on = d["lock_screen"]
        items.append(Item(
            _("Screen lock on") if on else _("Screen lock off – a locked session would block remote desktop (RDP)"),
            cmds("lock_screen"),
            "" if on else _("GNOME locks the screen when the lid closes; a locked session inhibits all "
                            "remote desktop sessions.")))
    if "night_light" in d:
        items.append(Item(_("Night light on") if d["night_light"] else _("Night light off"), cmds("night_light")))
    if "brightness_percent" in d:
        b = d["brightness_percent"]
        items.append(Item(_("Screen brightness {} %").format(b), [cmd.brightness(b)],
                          _("Needs the brightnessctl package.")))
    return items


def _bluetooth(p: Profile) -> list[Item]:
    if p.bluetooth is None:
        return []
    return [Item(_("Bluetooth on") if p.bluetooth else _("Bluetooth off"), [cmd.bluetooth(p.bluetooth)])]


def _remote_access(p: Profile) -> list[Item]:
    if not p.remote_access:
        return []
    return [Item(
        _("Keeps remote desktop (RDP) reachable with the lid closed"),
        [cmd.screensaver("SetActive", "false")],
        _("The guard (powerdeck guard) dismisses the GNOME screensaver as soon as it turns on, "
          "because it closes RDP sessions. The guard service must be running."))]


def _thermal_guard(p: Profile) -> list[Item]:
    tg = p.thermal_guard
    if not tg:
        return []
    text = _("Thermal guard: notification at {} °C; at {} °C it notifies and suspends the laptop")
    return [Item(
        text.format(tg["warn_celsius"], tg["critical_celsius"]), [cmd.ENABLE_GUARD],
        _("The guard service runs `powerdeck guard`. At the critical temperature (confirmed by a "
          "second reading 10 s later) it sends a notification, waits 5 s and runs `systemctl "
          "suspend` (if an app blocks sleep: pkexec /usr/libexec/powerdeck-suspend, which ignores "
          "inhibitors). If the profile keeps the laptop awake with the lid closed, it also "
          "suspends it at 15 % battery when not charging. Enable it once."))]


def _tasks(p: Profile) -> list[Item]:
    items = []
    for key, label in (("on_activate", "On activate"), ("on_deactivate", "On leaving")):
        for task in p.tasks.get(key, []):
            argv = parse_task(task)
            items.append(Item(f"{_(label)}: {argv[2]} {argv[3]}", [argv],
                              _("User service only (whitelisted: systemctl --user start|stop|restart).")))
    return items


# bullet order in the GUI
_SECTIONS = (_lid, _auto_suspend, _wifi, _tethering, _power_mode, _cpu, _display, _bluetooth,
             _remote_access, _thermal_guard, _tasks)


def describe(p: Profile) -> Description:
    items = [it for section in _SECTIONS for it in section(p)]
    summary = _(p.description) if p.description else ""
    if not summary:
        summary = _("{} setting(s) in this profile.").format(len(items))
    return Description(summary, items)
