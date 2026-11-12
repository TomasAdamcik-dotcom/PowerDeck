"""Capture the CURRENT system state as a profile (used to build a truthful 'Default')."""
from __future__ import annotations

from . import backends
from .profile import Profile
from .runner import Runner, system_runner


def capture_current(run: Runner = system_runner, name: str = "Default") -> Profile:
    auto = {}
    for src, key in (("battery", "sleep-inactive-battery-type"), ("ac", "sleep-inactive-ac-type")):
        rc, out = run(["gsettings", "get", backends.PS, key])
        if rc == 0:
            auto[src] = backends._unquote(out) != "nothing"
    rc, mode = run(["powerprofilesctl", "get"])
    lid = backends.read_lid() or {k: "suspend" for k in backends.LID_ORDER}
    p = Profile(name=name, description="Snapshot of your system settings.", lid=lid,
                automatic_suspend=auto, wifi_powersave="default",
                power_mode=mode.strip() if rc == 0 and mode.strip() in ("power-saver", "balanced", "performance") else None)
    p.validate()
    return p
