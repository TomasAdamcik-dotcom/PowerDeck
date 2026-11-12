"""Profile model, validation and JSON storage."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

from . import paths

LID_ACTIONS = ("ignore", "suspend", "lock", "poweroff")
WIFI_POWERSAVE = ("default", "off", "on")
POWER_MODES = ("power-saver", "balanced", "performance")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,40}$")


class ProfileError(ValueError):
    pass


@dataclass
class Profile:
    """A set of laptop settings. ``None`` means "leave unmanaged"."""

    name: str
    description: str = ""
    lid: dict = field(default_factory=dict)  # keys: battery, ac, docked
    automatic_suspend: dict = field(default_factory=dict)  # keys: battery, ac (bool)
    wifi_powersave: str | None = None
    preferred_networks: list = field(default_factory=list)
    power_mode: str | None = None
    thermal_guard: dict = field(default_factory=dict)  # warn_celsius, critical_celsius
    tasks: dict = field(default_factory=dict)  # on_activate / on_deactivate
    builtin: bool = False

    def validate(self) -> None:
        if not _NAME_RE.match(self.name or ""):
            raise ProfileError("Invalid profile name")
        for k, v in self.lid.items():
            if k not in ("battery", "ac", "docked"):
                raise ProfileError(f"Unknown lid key: {k}")
            if v not in LID_ACTIONS:
                raise ProfileError(f"Invalid lid action: {v}")
        for k, v in self.automatic_suspend.items():
            if k not in ("battery", "ac") or not isinstance(v, bool):
                raise ProfileError(f"Invalid automatic_suspend: {k}={v}")
        if self.wifi_powersave not in (None, *WIFI_POWERSAVE):
            raise ProfileError("Invalid wifi_powersave")
        if self.power_mode not in (None, *POWER_MODES):
            raise ProfileError("Invalid power_mode")
        for n in self.preferred_networks:
            if not isinstance(n, str) or not n or n.startswith("-"):
                raise ProfileError(f"Invalid network name: {n!r}")
        tg = self.thermal_guard
        if tg and tg.get("warn_celsius", 0) >= tg.get("critical_celsius", 999):
            raise ProfileError("thermal_guard: warn must be below critical")

    # -- (de)serialisation -------------------------------------------------
    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("builtin")
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        d = dict(d)
        d.pop("builtin", None)
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        unknown = set(d) - known
        if unknown:
            raise ProfileError(f"Unknown fields: {sorted(unknown)}")
        p = cls(**d)
        p.validate()
        return p


def _file_for(name: str, directory: Path) -> Path:
    return directory / (re.sub(r"[^A-Za-z0-9_-]", "_", name) + ".json")


def save_profile(p: Profile, directory: Path | None = None) -> Path:
    p.validate()
    directory = directory or paths.profiles_dir()
    directory.mkdir(parents=True, exist_ok=True)
    f = _file_for(p.name, directory)
    f.write_text(json.dumps(p.to_dict(), indent=2, ensure_ascii=False) + "\n")
    return f


def load_profiles(directory: Path | None = None) -> list[Profile]:
    """Built-in profiles overlaid with valid user profiles from disk."""
    from .builtin import BUILTIN_PROFILES

    result = {p.name: p for p in BUILTIN_PROFILES}
    directory = directory or paths.profiles_dir()
    if directory.is_dir():
        for f in sorted(directory.glob("*.json")):
            try:
                p = Profile.from_dict(json.loads(f.read_text()))
            except (ValueError, OSError):
                continue  # ignore broken files; never crash the UI
            result[p.name] = p
    return list(result.values())
