"""Thermal / low-battery guard (run as `powerdeck guard`, e.g. systemd user service).

``evaluate`` is pure so it can be tested; ``run_once`` does the side effects.
Events fire only when the level worsens, so the user is not spammed every tick.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass

from . import engine
from .profile import Profile, load_profiles
from .runner import Runner, system_runner
from .status import read_status, thermal_level

log = logging.getLogger(__name__)

LOW_BATTERY_PERCENT = 15
_SCREENSAVER = ["gdbus", "call", "--session", "--dest", "org.gnome.ScreenSaver",
                "--object-path", "/org/gnome/ScreenSaver", "--method"]
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


def _active(profiles=None) -> Profile | None:
    profiles = profiles or {p.name: p for p in load_profiles()}
    return profiles.get(engine.active_profile() or "")


def dismiss_screensaver(run: Runner = system_runner) -> bool:
    """Turn GNOME's (unlocked) screensaver off; True if it was on.

    While it is active GNOME Shell inhibits remote desktop: running RDP sessions are
    closed and new ones refused ("Session creation inhibited").
    """
    rc, out = run(_SCREENSAVER + ["org.gnome.ScreenSaver.GetActive"])
    if rc != 0 or "true" not in out:
        return False
    run(_SCREENSAVER + ["org.gnome.ScreenSaver.SetActive", "false"])
    log.info("screensaver dismissed to keep remote desktop reachable")
    return True


def run_once(state: GuardState, run: Runner = system_runner, profiles=None):
    prof = _active(profiles)
    if prof is not None and prof.remote_access:
        dismiss_screensaver(run)  # fallback for a missed ActiveChanged signal
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


def remote_access_notes(run: Runner = system_runner) -> list[str]:
    """What still has to be in place for a remote_access profile to keep RDP reachable."""
    from .status import read_remote_login
    notes = []
    rc, out = run(["systemctl", "--user", "is-active", "powerdeck-guard"])
    running = out.strip() == "active" or run(["pgrep", "-u", str(os.getuid()), "-f",
                                               r"powerdeck(\.cli)? guard"])[0] == 0
    if not running:
        notes.append("! The guard is not running, so closing the lid will drop RDP desktop sharing. "
                     "Start it: systemctl --user enable --now powerdeck-guard (or: powerdeck guard)")
    if read_remote_login(run) != "enabled":
        notes.append("Tip: desktop sharing still drops once when the lid closes. GNOME Remote Login "
                     "(headless RDP, see README) is not affected by the lid or the lock screen.")
    return notes


def on_monitor_line(line: str, run: Runner = system_runner, profiles=None) -> bool:
    """Handle one ``gdbus monitor`` line; dismiss the screensaver right when it activates."""
    if "ActiveChanged" not in line or "true" not in line:
        return False
    prof = _active(profiles)
    return bool(prof is not None and prof.remote_access and dismiss_screensaver(run))


def watch_screensaver(run: Runner = system_runner) -> None:  # pragma: no cover
    """React to org.gnome.ScreenSaver.ActiveChanged within milliseconds (the poll is 30 s)."""
    while True:
        try:
            with subprocess.Popen(["gdbus", "monitor", "--session", "--dest", "org.gnome.ScreenSaver",
                                   "--object-path", "/org/gnome/ScreenSaver"],
                                  stdout=subprocess.PIPE, text=True) as p:
                for line in p.stdout:
                    on_monitor_line(line, run)
        except OSError as e:
            log.error("screensaver watch failed: %s", e)
        time.sleep(5)  # shell restarted or gdbus missing; retry


def loop(interval: int = 30, run: Runner = system_runner) -> None:  # pragma: no cover
    threading.Thread(target=watch_screensaver, args=(run,), daemon=True).start()
    st = GuardState()
    while True:
        st = run_once(st, run)
        time.sleep(interval)
