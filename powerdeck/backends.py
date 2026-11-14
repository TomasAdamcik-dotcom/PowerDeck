"""Backends: read current state and build Change objects for a profile.

The commands themselves come from ``commands``; the pure ``*_settings`` helpers say what a
profile wants and are shared with describe.py, so the GUI shows exactly what is run here.
"""
from __future__ import annotations

import os

from . import commands as cmd
from . import cpu, helper
from .changes import (BRIGHTNESS, CPU, GSETTINGS, LOGIND, NMCLI, POWER_PROFILES, RFKILL, TASK,
                      Change)
from .commands import HELPER_PATH, LID_ORDER  # noqa: F401  (HELPER_PATH: public name)
from .profile import Profile, parse_task
from .runner import Runner

PS = cmd.POWER_SCHEMA
WIFI_VALUES = cmd.WIFI_POWERSAVE_VALUES


def _unquote(s: str) -> str:
    return s.strip().strip("'")


def helper_installed() -> bool:
    return os.path.isfile(HELPER_PATH) and os.access(HELPER_PATH, os.X_OK)


def _gs_change(run: Runner, label: str, setting: tuple[str, str], new: str,
               parse=lambda s: s.strip().split()[-1].strip("'")) -> list[Change]:
    """A gsettings change, or nothing if the key is missing or already has the value."""
    rc, out = run(cmd.gsettings_get(setting))
    if rc != 0:
        return []  # schema/key missing on this system: skip, never fail
    cur = parse(out)
    if cur == new:
        return []
    return [Change(GSETTINGS, label, cur, new, cmd.gsettings_set(setting, new),
                   cmd.gsettings_set(setting, cur))]


# -- GNOME automatic suspend ------------------------------------------------
def auto_suspend_settings(p: Profile) -> list[tuple[str, tuple[str, str], str]]:
    """(power source, gsettings key, value) for every source the profile manages."""
    return [(src, cmd.AUTO_SUSPEND[src], "suspend" if p.automatic_suspend[src] else "nothing")
            for src in ("battery", "ac") if src in p.automatic_suspend]


def plan_auto_suspend(p: Profile, run: Runner) -> list[Change]:
    out = []
    for src, setting, new in auto_suspend_settings(p):
        out += _gs_change(run, f"Auto-suspend ({src})", setting, new, parse=_unquote)
    return out


# -- Lid (logind drop-in, privileged) --------------------------------------
def read_lid(dropin_dir: str | None = None) -> dict | None:
    """Return the values of our drop-in, or None if it does not exist."""
    f = os.path.join(dropin_dir or helper.DROPIN_DIR, helper.DROPIN)
    try:
        text = open(f).read()
    except OSError:
        return None
    kv = dict(l.split("=", 1) for l in text.splitlines() if "=" in l)
    return {n: kv.get(k, "suspend") for n, k in zip(LID_ORDER, helper.KEYS)}


def lid_settings(p: Profile, current: dict | None = None) -> dict:
    """Wanted action per lid situation; situations the profile leaves out keep ``current``."""
    base = current or {k: "suspend" for k in LID_ORDER}
    return {k: p.lid.get(k, base[k]) for k in LID_ORDER}


def _lid_text(want: dict) -> str:
    return "/".join(want[k] for k in LID_ORDER)


def plan_lid(p: Profile, run: Runner, dropin_dir: str | None = None) -> list[Change]:
    if not p.lid:
        return []
    cur = read_lid(dropin_dir)
    base = cur or {k: "suspend" for k in LID_ORDER}
    want = lid_settings(p, base)
    if want == base:
        return []
    default = all(v == "suspend" for v in want.values())
    return [Change(LOGIND, "Lid close (battery/AC/dock)",
                   _lid_text(base) if cur else "system default",
                   "system default" if default else _lid_text(want),
                   cmd.lid(want), cmd.set_lid(cur) if cur else cmd.remove_lid(), privileged=True)]


# -- Wi-Fi powersave per NetworkManager connection -------------------------
def saved_wifi_connections(run: Runner) -> list[str]:
    """Names of all saved Wi-Fi connections (terse nmcli escapes ':' in names as '\\:')."""
    rc, out = run(cmd.LIST_CONNECTIONS)
    if rc != 0:
        return []
    names = []
    for line in out.splitlines():
        name, _, typ = line.rpartition(":")
        if typ == "802-11-wireless" and name:
            names.append(name.replace("\\:", ":"))
    return names


def plan_wifi(p: Profile, run: Runner) -> list[Change]:
    """Powersave for the listed networks, or for every saved Wi-Fi network if none are listed."""
    if p.wifi_powersave is None:
        return []
    new = WIFI_VALUES[p.wifi_powersave]
    out = []
    for name in p.preferred_networks or saved_wifi_connections(run):
        rc, cur = run(["nmcli", "-g", "802-11-wireless.powersave", "connection", "show", name])
        if rc != 0:
            continue  # connection not saved on this machine
        cur = cur.strip() or "0"
        if cur != new:
            out.append(Change(NMCLI, f"Wi-Fi powersave [{name}]", cur, new,
                              cmd.wifi_powersave(name, new), cmd.wifi_powersave(name, cur)))
    return out


# -- power-profiles-daemon -------------------------------------------------
def plan_power_mode(p: Profile, run: Runner) -> list[Change]:
    if p.power_mode is None:
        return []
    rc, cur = run(["powerprofilesctl", "get"])
    if rc != 0:
        return []
    cur = cur.strip()
    if cur == p.power_mode:
        return []
    return [Change(POWER_PROFILES, "Power mode", cur, p.power_mode,
                   cmd.power_mode(p.power_mode), cmd.power_mode(cur))]


# -- Tethering autoconnect -------------------------------------------------
def tethering_settings(p: Profile) -> tuple[str, str]:
    """(connection, autoconnect priority) of the phone tethering connection."""
    return p.tethering["connection"], str(p.tethering.get("priority", 100))


def plan_tethering(p: Profile, run: Runner) -> list[Change]:
    if not p.tethering:
        return []
    name, prio = tethering_settings(p)
    rc, out = run(["nmcli", "-g", "connection.autoconnect,connection.autoconnect-priority",
                   "connection", "show", name])
    if rc != 0:
        return []
    lines = (out.splitlines() + ["yes", "0"])[:2]
    cur_auto, cur_prio = lines[0].strip(), lines[1].strip()
    if cur_auto == "yes" and cur_prio == prio:
        return []
    return [Change(NMCLI, f"Tethering autoconnect [{name}]", f"{cur_auto}/prio {cur_prio}",
                   f"yes/prio {prio}", cmd.autoconnect(name, "yes", prio),
                   cmd.autoconnect(name, cur_auto, cur_prio))]


# -- Display: blank/lock timers, night light, brightness -------------------
def display_settings(d: dict) -> list[tuple[str, str, tuple[str, str], str]]:
    """(display key of the profile, change label, gsettings key, value) for every gsettings write."""
    out = []
    if "blank_after_minutes" in d:
        out.append(("blank_after_minutes", "Screen blank delay (s)", cmd.IDLE_DELAY,
                    str(d["blank_after_minutes"] * 60)))
    if "lock_after_minutes" in d:
        lock_min = d["lock_after_minutes"]
        out.append(("lock_after_minutes", "Screen lock enabled", cmd.LOCK_ENABLED,
                    cmd.bool_value(bool(lock_min))))
        if lock_min:
            delay = max(0, lock_min - d.get("blank_after_minutes", 0)) * 60
            out.append(("lock_after_minutes", "Lock delay after blank (s)", cmd.LOCK_DELAY, str(delay)))
    if "lock_screen" in d:
        # GNOME locks the screen when the lid closes (gsd-power), and a locked session
        # inhibits all remote desktop sessions, so remote profiles must turn locking off.
        out.append(("lock_screen", "Screen lock enabled", cmd.LOCK_ENABLED,
                    cmd.bool_value(d["lock_screen"])))
    if "night_light" in d:
        out.append(("night_light", "Night light", cmd.NIGHT_LIGHT, cmd.bool_value(d["night_light"])))
    return out


def plan_display(p: Profile, run: Runner) -> list[Change]:
    d = p.display
    out: list[Change] = []
    for _key, label, setting, value in display_settings(d):
        out += _gs_change(run, label, setting, value)
    if "brightness_percent" in d:
        rc, o = run(["brightnessctl", "-m"])
        if rc == 0 and o.count(",") >= 4:
            cur = o.split(",")[3].rstrip("%")
            new = str(d["brightness_percent"])
            if cur != new:
                out.append(Change(BRIGHTNESS, "Brightness (%)", cur, new,
                                  cmd.brightness(new), cmd.brightness(cur)))
    return out


# -- CPU limits (turbo, max frequency); writes go through the root helper -
def plan_cpu(p: Profile, run: Runner) -> list[Change]:
    out: list[Change] = []
    if "turbo" in p.cpu:
        cur, new = cpu.read_turbo(), p.cpu["turbo"]
        if cur is not None and cur != new:
            out.append(Change(CPU, "CPU turbo/boost", "on" if cur else "off", "on" if new else "off",
                              cmd.cpu_turbo(new), cmd.cpu_turbo(cur), privileged=True))
    if "max_freq_percent" in p.cpu:
        cur, new = cpu.read_max_percent(), p.cpu["max_freq_percent"]
        if cur is not None and cur != new:
            out.append(Change(CPU, "CPU max frequency", f"{cur}%", f"{new}%",
                              cmd.cpu_max_percent(new), cmd.cpu_max_percent(cur), privileged=True))
    return out


# -- Bluetooth (rfkill) ----------------------------------------------------
def plan_bluetooth(p: Profile, run: Runner) -> list[Change]:
    if p.bluetooth is None:
        return []
    rc, out = run(["rfkill", "list", "bluetooth"])
    if rc != 0 or "Soft blocked" not in out:
        return []
    blocked = "Soft blocked: yes" in out
    want_on = p.bluetooth
    if blocked != want_on:  # already in desired state
        return []
    return [Change(RFKILL, "Bluetooth", "off" if blocked else "on", "on" if want_on else "off",
                   cmd.bluetooth(want_on), cmd.bluetooth(not want_on))]


# -- User tasks (whitelisted systemctl --user only) ------------------------
_TASK_UNDO = {"start": "stop", "stop": "start"}  # restart has no inverse


def _task_change(task: str, label: str, reversible: bool) -> Change:
    argv = parse_task(task)
    action, unit = argv[2], argv[3]
    undo = _TASK_UNDO.get(action)
    revert = cmd.user_task(undo, unit) if (reversible and undo) else []
    return Change(TASK, f"{label}: {action} {unit}", "-", "run", argv, revert)


def plan_tasks(p: Profile, run: Runner, previous: Profile | None = None) -> list[Change]:
    out = []
    if previous is not None and previous.name != p.name:
        out += [_task_change(t, "On leaving " + previous.name, False)
                for t in previous.tasks.get("on_deactivate", [])]
    out += [_task_change(t, "On activate", True) for t in p.tasks.get("on_activate", [])]
    return out


BACKENDS = (plan_lid, plan_auto_suspend, plan_wifi, plan_tethering, plan_power_mode,
            plan_display, plan_bluetooth, plan_cpu)
