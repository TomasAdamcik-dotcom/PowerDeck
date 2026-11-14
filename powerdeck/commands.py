"""The commands PowerDeck runs to change the system, and which of them root may run.

Pure: no system access, no GTK.
"""
from __future__ import annotations

import re

from . import helper
from .profile import POWER_MODES

# Fixed, root-owned path matched by the polkit policy (data/*.policy).
# Never fall back to running code from the user's own directories as root.
HELPER_PATH = "/usr/libexec/powerdeck-helper"

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
