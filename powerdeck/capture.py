"""Capture the CURRENT system state as a profile (used to build a truthful 'Default')."""
from __future__ import annotations

from . import backends, commands, cpu
from .profile import POWER_MODES, Profile
from .runner import Runner, system_runner


def capture_current(run: Runner = system_runner, name: str = "Default") -> Profile:
    auto = {}
    for src, setting in commands.AUTO_SUSPEND.items():
        rc, out = run(commands.gsettings_get(setting))
        if rc == 0:
            auto[src] = backends._unquote(out) != "nothing"
    rc, mode = run(["powerprofilesctl", "get"])
    lid = backends.read_lid() or {k: "suspend" for k in commands.LID_ORDER}
    p = Profile(name=name, description="Snapshot of your system settings.", lid=lid,
                automatic_suspend=auto, wifi_powersave="default",
                power_mode=mode.strip() if rc == 0 and mode.strip() in POWER_MODES else None)
    rc, out = run(commands.gsettings_get(commands.LOCK_ENABLED))
    if rc == 0 and out.strip() in ("true", "false"):
        p.display["lock_screen"] = out.strip() == "true"
    t, m = cpu.read_turbo(), cpu.read_max_percent()
    if t is not None:
        p.cpu["turbo"] = t
    if m is not None:
        p.cpu["max_freq_percent"] = max(10, m)
    if cpu.thermal_available():
        p.cpu["thermal_target_celsius"] = cpu.read_thermal_target()
    p.validate()
    return p
