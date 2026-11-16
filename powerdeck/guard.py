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
from dataclasses import dataclass, replace

from . import commands, engine, history
from .profile import Profile, load_profiles
from .runner import Runner, system_runner
from .i18n import _
from .status import read_status, thermal_level

log = logging.getLogger(__name__)

LOW_BATTERY_PERCENT = 15
LOW_BATTERY_REARM = 3  # % above the threshold before a low battery suspends again
ON_POWER_STATES = ("Charging", "Full", "Not charging")  # kernel power_supply status on AC
# One reading at the critical temperature is not enough to suspend: a load spike (turbo burst
# before the thermal regulation reacts) reached 84 °C for a few seconds in the bag test and
# suspended a laptop that was meant to stay reachable. The guard confirms with a second
# reading CONFIRM_INTERVAL_S later, unless the first one is already this far above critical.
CRITICAL_IMMEDIATE_MARGIN = 8
CONFIRM_INTERVAL_S = 10
_RANK = {"unknown": 0, "ok": 0, "warn": 1, "critical": 2}


@dataclass
class GuardState:
    thermal: str = "ok"
    low_battery_notified: bool = False
    critical_pending: bool = False  # one critical reading seen, waiting for a confirming one
    rdp_closed_ticks: int = 0       # consecutive polls with nothing listening on the RDP port
    rdp_restarts: int = 0           # restarts since the port was last seen open
    rdp_last_restart: float | None = None


def evaluate(temp, battery_pct, battery_state, profile: Profile | None, state: GuardState):
    """Return (new_state, [events]); events: warn / critical / low_battery.

    low_battery fires only for profiles that keep the laptop awake with the lid closed on
    battery (RemoteWork, Travel, Rucksack); elsewhere GNOME's own power settings apply.
    """
    events: list[str] = []
    new = replace(state, critical_pending=False)
    if profile is not None and profile.thermal_guard:
        tg = profile.thermal_guard
        level = thermal_level(temp, tg["warn_celsius"], tg["critical_celsius"])
        if (level == "critical" and state.thermal != "critical" and not state.critical_pending
                and temp < tg["critical_celsius"] + CRITICAL_IMMEDIATE_MARGIN):
            level, new.critical_pending = "warn", True  # confirm on the next reading
        if _RANK[level] > _RANK[state.thermal]:
            events.append(level)
        new.thermal = level
    keeps_awake = profile is not None and profile.lid.get("battery") == "ignore"
    low = (keeps_awake and battery_pct is not None and battery_pct <= LOW_BATTERY_PERCENT
           and battery_state == "Discharging")
    if low and not state.low_battery_notified:
        events.append("low_battery")
    # Re-arm only on clear evidence (power plugged in or charged a bit), not on one odd reading
    # (None or "Unknown" right after resume), or the laptop would be suspended again and again.
    recovered = (battery_state in ON_POWER_STATES or
                 (battery_pct is not None and battery_pct > LOW_BATTERY_PERCENT + LOW_BATTERY_REARM))
    new.low_battery_notified = bool(low) or (state.low_battery_notified and not recovered)
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
    rc, out = run(commands.screensaver("GetActive"))
    if rc != 0 or "true" not in out:
        return False
    run(commands.screensaver("SetActive", "false"))
    log.info("screensaver dismissed to keep remote desktop reachable")
    return True


SUSPEND_DELAY_S = 5          # time for the notification to show before the system sleeps
SUSPEND_HELPER = "/usr/libexec/powerdeck-suspend"  # root, ignores inhibitors (data/*.policy)


def suspend_now(run: Runner) -> bool:
    """Suspend the laptop. Block inhibitors (e.g. an app keeping the PC awake) make plain
    ``systemctl suspend`` fail, so fall back to the root helper that ignores them."""
    rc, out = run(["systemctl", "suspend"])
    if rc == 0:
        return True
    log.warning("systemctl suspend failed (%s); trying %s", out, SUSPEND_HELPER)
    if os.path.isfile(SUSPEND_HELPER) and os.access(SUSPEND_HELPER, os.X_OK):
        rc, out = run(["pkexec", SUSPEND_HELPER])
        if rc == 0:
            return True
    log.error("suspend failed: %s", out)
    return False


def _suspend_after_notice(run: Runner, title: str, body: str, failed: str, sleep=None) -> bool:
    _notify(run, title, body, True)
    (sleep or time.sleep)(SUSPEND_DELAY_S)
    if suspend_now(run):
        return True
    _notify(run, _("PowerDeck: could not suspend"), failed, True)
    return False


def critical_reaction(run: Runner, temp: float | None, critical: float, sleep=None) -> bool:
    """Maximum temperature reached: tell the user why, then suspend. Returns True if suspended."""
    t = "?" if temp is None else f"{temp:.0f}"
    log.info("suspending: CPU %s °C >= critical %s °C", t, critical)
    return _suspend_after_notice(
        run, _("PowerDeck: suspending – maximum temperature reached"),
        _("CPU reached {} °C (maximum allowed {} °C). The laptop will be suspended in {} s "
          "to protect the hardware.").format(t, f"{critical:.0f}", SUSPEND_DELAY_S),
        _("CPU is at {} °C. Shut the laptop down or take it out of the bag now.").format(t), sleep)


def low_battery_reaction(run: Runner, percent, sleep=None) -> bool:
    """Battery low on a lid-ignore profile: suspend before it runs flat. Returns True if suspended."""
    log.info("suspending: battery %s %% <= %s %%, discharging", percent, LOW_BATTERY_PERCENT)
    return _suspend_after_notice(
        run, _("PowerDeck: suspending – battery low"),
        _("Battery at {} % and not charging. The laptop will be suspended in {} s.").format(
            percent, SUSPEND_DELAY_S),
        _("Battery at {} %. Plug in power or shut the laptop down.").format(percent), sleep)


def run_once(state: GuardState, run: Runner = system_runner, profiles=None):
    prof = _active(profiles)
    if prof is not None and prof.remote_access:
        dismiss_screensaver(run)  # fallback for a missed ActiveChanged signal
    s = read_status(run)
    state, events = evaluate(s.cpu_temp_c, s.battery_percent, s.battery_state, prof, state)
    if prof is not None and prof.remote_access:
        keep_rdp_listening(run, state, time.monotonic())
    for ev in events:
        if ev == "warn":
            _notify(run, _("PowerDeck: CPU is hot"),
                    _("{} °C – take the laptop out of the bag or reduce load.").format(f"{s.cpu_temp_c:.0f}"))
        elif ev == "critical":
            critical_reaction(run, s.cpu_temp_c, prof.thermal_guard["critical_celsius"])
        elif ev == "low_battery":
            low_battery_reaction(run, s.battery_percent)
    return state


RDP_PORT = 3389


def rdp_listening(run: Runner = system_runner) -> bool | None:
    """Whether anything listens on the RDP port; None if ``ss`` could not tell.

    gnome-remote-desktop with "Negotiate port" moves to 3390 when 3389 is busy at start, and
    clients saved with 3389 then get "PC not found" (seen on the T14s, 2026-11-16).
    """
    rc, out = run(["ss", "-Htln", f"sport = :{RDP_PORT}"])
    if rc != 0:
        return None
    return bool(out.strip())


RDP_RESTART_EVERY_S = 600  # gnome-remote-desktop needs ~25 s to listen again after a restart
RDP_MAX_RESTARTS = 3       # then stop trying until the port is seen open again


def keep_rdp_listening(run: Runner, state: GuardState, now: float) -> bool:
    """Restart gnome-remote-desktop when nothing listens on the RDP port; True if restarted.

    In the bag nobody sees the "apply" warning, only "PC not found" on the phone. Needs two
    closed polls in a row (the daemon starts listening ~25 s after login), RDP sharing turned
    on in GNOME, and at most RDP_MAX_RESTARTS tries RDP_RESTART_EVERY_S apart.
    """
    listening = rdp_listening(run)
    if listening is not False:
        if listening:
            state.rdp_closed_ticks = state.rdp_restarts = 0
        return False
    state.rdp_closed_ticks += 1
    if (state.rdp_closed_ticks < 2 or state.rdp_restarts >= RDP_MAX_RESTARTS
            or (state.rdp_last_restart is not None and now - state.rdp_last_restart < RDP_RESTART_EVERY_S)):
        return False
    rc, out = run(["gsettings", "get", "org.gnome.desktop.remote-desktop.rdp", "enable"])
    if rc != 0 or out.strip() != "true":
        return False  # desktop sharing is off on purpose: nothing to keep reachable
    run(["systemctl", "--user", "restart", "gnome-remote-desktop"])
    state.rdp_restarts += 1
    state.rdp_last_restart = now
    log.warning("nothing listened on RDP port %s: restarted gnome-remote-desktop (%s/%s)",
                RDP_PORT, state.rdp_restarts, RDP_MAX_RESTARTS)
    return True


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
    if rdp_listening(run) is False:
        notes.append(f"! Nothing listens on the RDP port {RDP_PORT}, so remote desktop cannot connect. "
                     "GNOME may have moved it to another port when this one was busy. Restart it: "
                     "systemctl --user restart gnome-remote-desktop (and to keep the port: "
                     "grdctl rdp disable-port-negotiation)")
    if read_remote_login(run) != "enabled":
        notes.append("Tip: desktop sharing still drops once when the lid closes; reconnect right away. "
                     "(GNOME Remote Login would avoid that, but see README: it can hang on a black screen.)")
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
    history.start_recording()  # temperature/frequency graph in the GUI, every 20 s
    st = GuardState()
    while True:
        st = run_once(st, run)
        time.sleep(CONFIRM_INTERVAL_S if st.critical_pending else interval)
