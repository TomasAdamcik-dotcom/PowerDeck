"""Human-readable description of a profile: one summary line + bullets, each with the exact command.

Pure (no GTK, no system access) so it can be tested. Commands are what PowerDeck would run
when the profile is applied; they are shown to technical users in the GUI tooltips.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass, field

from . import backends
from .i18n import _
from .profile import Profile

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
WIFI_VALUE = {"default": "0", "off": "2", "on": "3"}


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


def _gs(schema, key, value):
    return ["gsettings", "set", schema, key, str(value)]


def describe(p: Profile) -> Description:
    items: list[Item] = []

    if p.lid:
        want = {k: p.lid.get(k, "suspend") for k in backends.LID_ORDER}
        if len(set(want.values())) == 1:
            v = next(iter(want.values()))
            text = _("Closing the lid {}").format(_(LID_VERB[v]))
        else:
            text = _("Lid close – battery: {}, AC: {}, dock: {}").format(
                *(_(want[k]) for k in backends.LID_ORDER))
        if all(v == "suspend" for v in want.values()):
            cmd = backends._helper_argv("remove-lid")
            note = _("Removes the PowerDeck logind drop-in, so the system default (suspend) applies.")
        else:
            cmd = backends._helper_argv("set-lid", *(want[k] for k in backends.LID_ORDER))
            note = _("Writes /etc/systemd/logind.conf.d/99-powerdeck-lid.conf (HandleLidSwitch*) "
                     "and reloads logind with SIGHUP. Needs admin rights.")
        items.append(Item(text, [cmd], note, warning=want["battery"] == "ignore"))

    if p.automatic_suspend:
        a = p.automatic_suspend
        keys = {"battery": "sleep-inactive-battery-type", "ac": "sleep-inactive-ac-type"}
        cmds = [_gs(backends.PS, keys[k], "suspend" if a[k] else "nothing") for k in keys if k in a]
        state = lambda on: _("on") if on else _("off")
        if len(a) == 2 and a["battery"] == a["ac"]:
            text = _("Automatic suspend when idle is {} (battery and AC)").format(state(a["battery"]))
        else:
            text = _("Automatic suspend when idle: {}").format(
                ", ".join(f"{_(k)} {state(v)}" for k, v in a.items()))
        items.append(Item(text, cmds, _("GNOME power setting, no admin rights needed.")))

    if p.wifi_powersave is not None:
        val = WIFI_VALUE[p.wifi_powersave]
        if p.preferred_networks:
            text = _("Wi-Fi powersave: {} – for: {}").format(_(WIFI_TEXT[p.wifi_powersave]),
                                                          ", ".join(p.preferred_networks))
            cmds = [["nmcli", "connection", "modify", n, "802-11-wireless.powersave", val]
                    for n in p.preferred_networks]
        else:
            text = _("Wi-Fi powersave: {} (for the saved networks listed in the profile)").format(
                _(WIFI_TEXT[p.wifi_powersave]))
            cmds = [["nmcli", "connection", "modify", "<" + _("network") + ">",
                     "802-11-wireless.powersave", val]]
        items.append(Item(text, cmds, _("NetworkManager values: 0 default, 2 off, 3 on. "
                                        "Networks not saved on this PC are skipped.")))

    if p.tethering:
        t = p.tethering
        prio = str(t.get("priority", 100))
        items.append(Item(
            _("Phone tethering “{}” connects automatically (priority {})").format(t["connection"], prio),
            [["nmcli", "connection", "modify", t["connection"], "connection.autoconnect", "yes",
              "connection.autoconnect-priority", prio]]))

    if p.power_mode:
        items.append(Item(_("Power mode: {}").format(_(POWER_MODE_TEXT[p.power_mode])),
                          [["powerprofilesctl", "set", p.power_mode]],
                          _("Uses power-profiles-daemon.")))

    if "turbo" in p.cpu:
        on = p.cpu["turbo"]
        text = (_("CPU turbo/boost on") if on
                else _("CPU turbo/boost off – CPU stays at base clock (less heat)"))
        items.append(Item(text, [backends._helper_argv("set-cpu-turbo", "on" if on else "off")],
                          _("Writes intel_pstate/no_turbo or cpufreq/boost in /sys. "
                            "Resets after reboot. Needs admin rights.")))
    if "max_freq_percent" in p.cpu:
        m = p.cpu["max_freq_percent"]
        text = (_("CPU max frequency: no limit (100 %)") if m == 100
                else _("CPU max frequency limited to {} % of the hardware maximum").format(m))
        items.append(Item(text, [backends._helper_argv("set-cpu-max-percent", str(m))],
                          _("Writes scaling_max_freq for every core in /sys/devices/system/cpu. "
                            "Resets after reboot. Needs admin rights.")))

    d = p.display
    if "blank_after_minutes" in d:
        m = d["blank_after_minutes"]
        text = _("Screen never blanks when idle") if m == 0 else _("Screen blanks after {} min idle").format(m)
        items.append(Item(text, [_gs("org.gnome.desktop.session", "idle-delay", m * 60)]))
    if "lock_after_minutes" in d:
        m = d["lock_after_minutes"]
        cmds = [_gs("org.gnome.desktop.screensaver", "lock-enabled", "true" if m else "false")]
        if m:
            cmds.append(_gs("org.gnome.desktop.screensaver", "lock-delay",
                            max(0, m - d.get("blank_after_minutes", 0)) * 60))
        items.append(Item(_("Screen lock off") if m == 0 else _("Screen locks after {} min").format(m), cmds))
    if "lock_screen" in d:
        on = d["lock_screen"]
        items.append(Item(
            _("Screen lock on") if on else _("Screen lock off – a locked session would block remote desktop (RDP)"),
            [_gs("org.gnome.desktop.screensaver", "lock-enabled", "true" if on else "false")],
            "" if on else _("GNOME locks the screen when the lid closes; a locked session inhibits all "
                            "remote desktop sessions.")))
    if "night_light" in d:
        on = d["night_light"]
        items.append(Item(_("Night light on") if on else _("Night light off"),
                          [_gs("org.gnome.settings-daemon.plugins.color", "night-light-enabled",
                               "true" if on else "false")]))
    if "brightness_percent" in d:
        b = d["brightness_percent"]
        items.append(Item(_("Screen brightness {} %").format(b), [["brightnessctl", "set", f"{b}%"]],
                          _("Needs the brightnessctl package.")))

    if p.bluetooth is not None:
        items.append(Item(_("Bluetooth on") if p.bluetooth else _("Bluetooth off"),
                          [["rfkill", "unblock" if p.bluetooth else "block", "bluetooth"]]))

    if p.remote_access:
        items.append(Item(
            _("Keeps remote desktop (RDP) reachable with the lid closed"),
            [["gdbus", "call", "--session", "--dest", "org.gnome.ScreenSaver", "--object-path",
              "/org/gnome/ScreenSaver", "--method", "org.gnome.ScreenSaver.SetActive", "false"]],
            _("The guard (powerdeck guard) dismisses the GNOME screensaver as soon as it turns on, "
              "because it closes RDP sessions. The guard service must be running.")))

    tg = p.thermal_guard
    if tg:
        items.append(Item(
            _("Thermal guard: warning at {} °C, critical at {} °C (switches to power-saver)").format(
                tg["warn_celsius"], tg["critical_celsius"]),
            [["systemctl", "--user", "enable", "--now", "powerdeck-guard.service"]],
            _("The guard service runs `powerdeck guard` and sends desktop notifications. "
              "It must be enabled once.")))

    for key, label in (("on_activate", "On activate"), ("on_deactivate", "On leaving")):
        for cmd in p.tasks.get(key, []):
            items.append(Item(f"{_(label)}: {cmd.split(None, 2)[2]}", [cmd.split()],
                              _("User service only (whitelisted: systemctl --user start|stop|restart).")))

    summary = _(p.description) if p.description else ""
    if not summary:
        summary = _("{} setting(s) in this profile.").format(len(items))
    return Description(summary, items)
