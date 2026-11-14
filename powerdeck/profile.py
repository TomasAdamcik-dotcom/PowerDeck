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


_UNIT_RE = re.compile(r"^[A-Za-z0-9_.@:-]{1,100}\.(service|target|timer)$")


def parse_task(cmd: str) -> list[str]:
    """Only ``systemctl --user (start|stop|restart) UNIT`` is allowed.

    Profiles may come from other people, so they must never run arbitrary commands.
    """
    argv = cmd.split()
    if (len(argv) != 4 or argv[:2] != ["systemctl", "--user"]
            or argv[2] not in ("start", "stop", "restart") or not _UNIT_RE.match(argv[3])):
        raise ProfileError(f"Task not allowed (only 'systemctl --user start|stop|restart UNIT'): {cmd!r}")
    return argv


_DICT_FIELDS = ("lid", "automatic_suspend", "thermal_guard", "tasks", "tethering", "display", "cpu")


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


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
    tethering: dict = field(default_factory=dict)  # connection, priority
    display: dict = field(default_factory=dict)  # blank_after_minutes, lock_after_minutes, lock_screen, brightness_percent, night_light
    bluetooth: bool | None = None
    cpu: dict = field(default_factory=dict)  # turbo (bool), max_freq_percent (10-100)
    # Keep GNOME desktop sharing (RDP) reachable: the guard dismisses the screensaver, which
    # GNOME activates when the lid closes and which inhibits all remote desktop sessions.
    remote_access: bool | None = None
    builtin: bool = False

    def validate(self) -> None:
        if not isinstance(self.name, str) or not _NAME_RE.match(self.name):
            raise ProfileError("Invalid profile name")
        if not isinstance(self.description, str):
            raise ProfileError("description must be a string")
        # wrong JSON types must end up as ProfileError too (load_profiles skips those files)
        for k in _DICT_FIELDS:
            if not isinstance(getattr(self, k), dict):
                raise ProfileError(f"{k} must be an object")
        if not isinstance(self.preferred_networks, list):
            raise ProfileError("preferred_networks must be a list")
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
        t = self.tethering
        if t:
            c = t.get("connection")
            if not isinstance(c, str) or not c or c.startswith("-"):
                raise ProfileError("tethering.connection invalid")
            if not _is_int(t.get("priority", 100)) or not -999 <= t.get("priority", 100) <= 999:
                raise ProfileError("tethering.priority invalid")
        d = self.display
        for k in d:
            if k not in ("blank_after_minutes", "lock_after_minutes", "lock_screen", "brightness_percent", "night_light"):
                raise ProfileError(f"Unknown display key: {k}")
        for k in ("blank_after_minutes", "lock_after_minutes"):
            if k in d and not (_is_int(d[k]) and 0 <= d[k] <= 1440):
                raise ProfileError(f"display.{k} invalid")
        if "brightness_percent" in d and not (_is_int(d["brightness_percent"]) and 1 <= d["brightness_percent"] <= 100):
            raise ProfileError("display.brightness_percent invalid")
        if "night_light" in d and not isinstance(d["night_light"], bool):
            raise ProfileError("display.night_light invalid")
        if "lock_screen" in d and not isinstance(d["lock_screen"], bool):
            raise ProfileError("display.lock_screen invalid")
        if "lock_screen" in d and "lock_after_minutes" in d:
            raise ProfileError("display.lock_screen and display.lock_after_minutes are exclusive")
        for k, v in self.cpu.items():
            if k == "turbo" and isinstance(v, bool):
                continue
            if k == "max_freq_percent" and _is_int(v) and 10 <= v <= 100:
                continue
            raise ProfileError(f"Invalid cpu setting: {k}={v!r}")
        if self.bluetooth is not None and not isinstance(self.bluetooth, bool):
            raise ProfileError("bluetooth must be true/false")
        if self.remote_access is not None and not isinstance(self.remote_access, bool):
            raise ProfileError("remote_access must be true/false")
        if self.remote_access and d.get("lock_screen") is not False:
            # a locked screen cannot be dismissed over D-Bus, so the guard could not help
            raise ProfileError("remote_access requires display.lock_screen = false")
        for k, cmds in self.tasks.items():
            if k not in ("on_activate", "on_deactivate"):
                raise ProfileError(f"Unknown tasks key: {k}")
            if not isinstance(cmds, list) or not all(isinstance(c, str) for c in cmds):
                raise ProfileError(f"tasks.{k} must be a list of commands")
            for cmd in cmds:
                parse_task(cmd)
        tg = self.thermal_guard
        if tg:
            if set(tg) != {"warn_celsius", "critical_celsius"} or not all(
                    _is_number(v) for v in tg.values()):
                raise ProfileError("thermal_guard needs numbers warn_celsius and critical_celsius")
            if tg["warn_celsius"] >= tg["critical_celsius"]:
                raise ProfileError("thermal_guard: warn must be below critical")

    # -- (de)serialisation -------------------------------------------------
    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("builtin")
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        if not isinstance(d, dict):
            raise ProfileError("A profile must be a JSON object")
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
