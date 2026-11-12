"""Thermal / low-battery guard (run as `powerdeck guard`, e.g. systemd user service).

``evaluate`` is pure so it can be tested; ``run_once`` does the side effects.
Events fire only when the level worsens, so the user is not spammed every tick.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from . import engine
from .profile import Profile, load_profiles
from .runner import Runner, system_runner
from .status import read_status, thermal_level

LOW_BATTERY_PERCENT = 15
_RANK = {"unknown": 0, "ok": 0, "warn": 1, "critical": 2}


@dataclass
class GuardState:
    thermal: str = "ok"
    low_battery_notified: bool = False


def evaluate(temp, battery_pct, battery_state, profile: Profile | None, state: GuardState):
    """Return (new_state, [events]); events: warn / critical / low_battery."""
    events: list[str] = []
    new = GuardState(state.thermal, state.low_battery_notified)
    if profile is not None and profile.thermal_guard:
        tg = profile.thermal_guard
        level = thermal_level(temp, tg["warn_celsius"], tg["critical_celsius"])
        if _RANK[level] > _RANK[state.thermal]:
            events.append(level)
        new.thermal = level
    keeps_awake = profile is not None and profile.lid.get("battery") == "ignore"
    low = (keeps_awake and battery_pct is not None and battery_pct <= LOW_BATTERY_PERCENT
           and battery_state == "Discharging")
    if low and not state.low_battery_notified:
        events.append("low_battery")
    new.low_battery_notified = bool(low)
    return new, events


def _notify(run: Runner, title: str, body: str, urgent: bool = False) -> None:
    run(["notify-send", "-a", "PowerDeck", "-u", "critical" if urgent else "normal", title, body])


def run_once(state: GuardState, run: Runner = system_runner, profiles=None):
    profiles = profiles or {p.name: p for p in load_profiles()}
    prof = profiles.get(engine.active_profile() or "")
    s = read_status(run)
    state, events = evaluate(s.cpu_temp_c, s.battery_percent, s.battery_state, prof, state)
    for ev in events:
        if ev == "warn":
            _notify(run, "PowerDeck: CPU is hot", f"{s.cpu_temp_c:.0f} °C – take the laptop out of the bag or reduce load.")
        elif ev == "critical":
            run(["powerprofilesctl", "set", "power-saver"])  # reversible, non-destructive reaction
            _notify(run, "PowerDeck: CPU critical", f"{s.cpu_temp_c:.0f} °C – switched to power-saver. Let it cool down.", True)
        elif ev == "low_battery":
            _notify(run, "PowerDeck: battery low", f"{s.battery_percent} % with the lid-ignore profile active. "
                    "Plug in power or restore the default profile.", True)
    return state


def loop(interval: int = 30, run: Runner = system_runner) -> None:  # pragma: no cover
    st = GuardState()
    while True:
        st = run_once(st, run)
        time.sleep(interval)
