"""XDG paths used by PowerDeck."""
import os
from pathlib import Path


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "powerdeck"


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(base) / "powerdeck"


def profiles_dir() -> Path:
    return config_dir() / "profiles"


def backups_dir() -> Path:
    return state_dir() / "backups"


def logs_dir() -> Path:
    return state_dir() / "logs"
