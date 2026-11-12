"""Backends: read current state and build Change objects for a profile."""
from __future__ import annotations

import os
import sys

from . import helper
from .changes import Change
from .profile import Profile
from .runner import Runner

PS = "org.gnome.settings-daemon.plugins.power"
WIFI_VALUES = {"default": "0", "off": "2", "on": "3"}
LID_ORDER = ("battery", "ac", "docked")


def _unquote(s: str) -> str:
    return s.strip().strip("'")


def _helper_argv(*args: str) -> list[str]:
    return ["pkexec", sys.executable, "-m", "powerdeck.helper", *args]


# -- GNOME automatic suspend ------------------------------------------------
def plan_auto_suspend(p: Profile, run: Runner) -> list[Change]:
    out = []
    for src, key in (("battery", "sleep-inactive-battery-type"), ("ac", "sleep-inactive-ac-type")):
        if src not in p.automatic_suspend:
            continue
        rc, cur = run(["gsettings", "get", PS, key])
        if rc != 0:
            continue  # schema/key missing on this system: skip, never fail
        cur = _unquote(cur)
        new = "suspend" if p.automatic_suspend[src] else "nothing"
        if cur != new:
            out.append(Change("gsettings", f"Auto-suspend ({src})", cur, new,
                              ["gsettings", "set", PS, key, new],
                              ["gsettings", "set", PS, key, cur]))
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


def plan_lid(p: Profile, run: Runner, dropin_dir: str | None = None) -> list[Change]:
    if not p.lid:
        return []
    cur = read_lid(dropin_dir)
    base = cur or {k: "suspend" for k in LID_ORDER}
    want = {k: p.lid.get(k, base[k]) for k in LID_ORDER}
    if want == base:
        return []
    fmt = lambda d: "/".join(d[k] for k in LID_ORDER)
    old_s = fmt(base) if cur else "system default"
    if all(v == "suspend" for v in want.values()):
        apply = _helper_argv("remove-lid")
        new_s = "system default"
    else:
        apply = _helper_argv("set-lid", *(want[k] for k in LID_ORDER))
        new_s = fmt(want)
    revert = (_helper_argv("set-lid", *(cur[k] for k in LID_ORDER)) if cur
              else _helper_argv("remove-lid"))
    return [Change("logind", "Lid close (battery/AC/dock)", old_s, new_s, apply, revert, privileged=True)]


# -- Wi-Fi powersave per NetworkManager connection -------------------------
def plan_wifi(p: Profile, run: Runner) -> list[Change]:
    if p.wifi_powersave is None or not p.preferred_networks:
        return []
    new = WIFI_VALUES[p.wifi_powersave]
    out = []
    for name in p.preferred_networks:
        rc, cur = run(["nmcli", "-g", "802-11-wireless.powersave", "connection", "show", name])
        if rc != 0:
            continue  # connection not saved on this machine
        cur = cur.strip() or "0"
        if cur != new:
            out.append(Change("nmcli", f"Wi-Fi powersave [{name}]", cur, new,
                              ["nmcli", "connection", "modify", name, "802-11-wireless.powersave", new],
                              ["nmcli", "connection", "modify", name, "802-11-wireless.powersave", cur]))
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
    return [Change("power-profiles", "Power mode", cur, p.power_mode,
                   ["powerprofilesctl", "set", p.power_mode],
                   ["powerprofilesctl", "set", cur])]


BACKENDS = (plan_lid, plan_auto_suspend, plan_wifi, plan_power_mode)
