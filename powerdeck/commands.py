"""Every command PowerDeck runs to change the system, built in one place.

backends.py plans changes with these builders, describe.py shows the very same commands in
the GUI, and ``allowed_as_root`` says which revert commands root may run while uninstalling.
Pure: no system access, no GTK.
"""
from __future__ import annotations

import re

from . import helper
from .profile import POWER_MODES

# Fixed, root-owned path matched by the polkit policy (data/*.policy).
# Never fall back to running code from the user's own directories as root.
HELPER_PATH = "/usr/libexec/powerdeck-helper"

# gsettings (schema, key) pairs
POWER_SCHEMA = "org.gnome.settings-daemon.plugins.power"
AUTO_SUSPEND = {"battery": (POWER_SCHEMA, "sleep-inactive-battery-type"),
                "ac": (POWER_SCHEMA, "sleep-inactive-ac-type")}
IDLE_DELAY = ("org.gnome.desktop.session", "idle-delay")
LOCK_ENABLED = ("org.gnome.desktop.screensaver", "lock-enabled")
LOCK_DELAY = ("org.gnome.desktop.screensaver", "lock-delay")
NIGHT_LIGHT = ("org.gnome.settings-daemon.plugins.color", "night-light-enabled")

LID_ORDER = ("battery", "ac", "docked")  # order of the helper's set-lid arguments
WIFI_POWERSAVE_VALUES = {"default": "0", "off": "2", "on": "3"}  # NetworkManager values
LIST_CONNECTIONS = ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"]
ENABLE_GUARD = ["systemctl", "--user", "enable", "--now", "powerdeck-guard.service"]


def gsettings_get(setting: tuple[str, str]) -> list[str]:
    return ["gsettings", "get", *setting]


def gsettings_set(setting: tuple[str, str], value) -> list[str]:
    return ["gsettings", "set", *setting, str(value)]


def bool_value(on: bool) -> str:
    """gsettings spelling of a boolean."""
    return "true" if on else "false"


def privileged(*args: str) -> list[str]:
    """Run the root helper through polkit."""
    return ["pkexec", HELPER_PATH, *args]


def set_lid(want: dict) -> list[str]:
    return privileged("set-lid", *(want[k] for k in LID_ORDER))


def remove_lid() -> list[str]:
    return privileged("remove-lid")


def lid(want: dict) -> list[str]:
    """Lid handling for battery/AC/dock; "suspend" everywhere is the system default (no drop-in)."""
    return remove_lid() if all(want[k] == "suspend" for k in LID_ORDER) else set_lid(want)


def cpu_turbo(on: bool) -> list[str]:
    return privileged("set-cpu-turbo", "on" if on else "off")


def cpu_max_percent(percent: int) -> list[str]:
    return privileged("set-cpu-max-percent", str(percent))


def wifi_powersave(connection: str, value: str) -> list[str]:
    return ["nmcli", "connection", "modify", connection, "802-11-wireless.powersave", value]


def autoconnect(connection: str, auto: str, priority: str) -> list[str]:
    return ["nmcli", "connection", "modify", connection,
            "connection.autoconnect", auto, "connection.autoconnect-priority", priority]


def power_mode(mode: str) -> list[str]:
    return ["powerprofilesctl", "set", mode]


def bluetooth(on: bool) -> list[str]:
    return ["rfkill", "unblock" if on else "block", "bluetooth"]


def brightness(percent) -> list[str]:
    return ["brightnessctl", "set", f"{percent}%"]


def user_task(action: str, unit: str) -> list[str]:
    return ["systemctl", "--user", action, unit]


def screensaver(method: str, *args: str) -> list[str]:
    """Call org.gnome.ScreenSaver on the session bus."""
    return ["gdbus", "call", "--session", "--dest", "org.gnome.ScreenSaver", "--object-path",
            "/org/gnome/ScreenSaver", "--method", f"org.gnome.ScreenSaver.{method}", *args]


# -- what root may run (uninstall) ---------------------------------------
# NetworkManager 802-11-wireless.powersave: numbers as PowerDeck writes them, names as nmcli prints them
_NM_POWERSAVE = ("0", "1", "2", "3", "default", "ignore", "disable", "enable")
_NM_PRIORITY_RE = re.compile(r"-?[0-9]{1,3}")
_PERCENT_RE = re.compile(r"[0-9]{1,3}%")


def without_pkexec(argv: list[str]) -> list[str]:
    """The same command run directly (root already has the rights pkexec would ask for)."""
    return argv[1:] if argv[:1] == ["pkexec"] else argv


def _connection_name(name: str) -> bool:
    return bool(name) and not name.startswith("-")


def allowed_as_root(argv: list[str]) -> bool:
    """True only for the revert commands PowerDeck itself writes for system state.

    ``powerdeck reset-all --only system`` runs as root while uninstalling and reads the revert
    commands from backup files in the user's home, which any process of that user can write.
    So every command must match one of these exact shapes; anything else is refused.
    """
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        return False
    match argv:
        case [path, *args] if path == HELPER_PATH:
            return helper.valid_args(args)
        case ["nmcli", "connection", "modify", name, "802-11-wireless.powersave", value]:
            return _connection_name(name) and value in _NM_POWERSAVE
        case ["nmcli", "connection", "modify", name, "connection.autoconnect", auto,
              "connection.autoconnect-priority", prio]:
            return (_connection_name(name) and auto in ("yes", "no")
                    and _NM_PRIORITY_RE.fullmatch(prio) is not None)
        case ["powerprofilesctl", "set", mode]:
            return mode in POWER_MODES
        case ["rfkill", "block" | "unblock", "bluetooth"]:
            return True
        case ["brightnessctl", "set", value]:
            return _PERCENT_RE.fullmatch(value) is not None and int(value[:-1]) <= 100
    return False
