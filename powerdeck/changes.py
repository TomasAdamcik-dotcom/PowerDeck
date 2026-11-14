"""A single planned system change, with its own revert command."""
from __future__ import annotations

from dataclasses import dataclass

# Backend names. They are stored in backup files, so their values must never change.
GSETTINGS = "gsettings"
LOGIND = "logind"
NMCLI = "nmcli"
POWER_PROFILES = "power-profiles"
BRIGHTNESS = "brightnessctl"
CPU = "cpu"
RFKILL = "rfkill"
TASK = "task"
# Reverted as the user during uninstall (their dconf / their systemd); root reverts the rest.
USER_BACKENDS = (GSETTINGS, TASK)


@dataclass
class Change:
    backend: str
    target: str  # human-readable what is changed
    old: str
    new: str
    apply_argv: list[str]
    revert_argv: list[str]
    privileged: bool = False  # needs pkexec (argv already contains it)

    def describe(self) -> str:
        star = " (admin)" if self.privileged else ""
        return f"{self.target}: {self.old} → {self.new}{star}"

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> "Change":
        """Rebuild a change from a backup file; raises ValueError if it is malformed."""
        try:
            c = cls(**d)
        except TypeError as e:
            raise ValueError(f"malformed change: {e}") from None
        for argv in (c.apply_argv, c.revert_argv):
            if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
                raise ValueError(f"malformed command in change {c.target!r}")
        return c
