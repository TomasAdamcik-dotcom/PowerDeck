"""Backends: read current state and build Change objects for a profile."""
from __future__ import annotations

import os

from . import helper
from . import cpu
from .changes import Change
from .profile import Profile, parse_task
from .runner import Runner

PS = "org.gnome.settings-daemon.plugins.power"
WIFI_VALUES = {"default": "0", "off": "2", "on": "3"}
LID_ORDER = ("battery", "ac", "docked")


def _unquote(s: str) -> str:
    return s.strip().strip("'")


# Fixed, root-owned path matched by the polkit policy (data/*.policy).
# Never fall back to running code from the user's own directories as root.
HELPER_PATH = "/usr/libexec/powerdeck-helper"


def helper_installed() -> bool:
    return os.path.isfile(HELPER_PATH) and os.access(HELPER_PATH, os.X_OK)


def _helper_argv(*args: str) -> list[str]:
    return ["pkexec", HELPER_PATH, *args]


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


# -- Tethering autoconnect -------------------------------------------------
def plan_tethering(p: Profile, run: Runner) -> list[Change]:
    t = p.tethering
    if not t:
        return []
    name, prio = t["connection"], str(t.get("priority", 100))
    rc, out = run(["nmcli", "-g", "connection.autoconnect,connection.autoconnect-priority",
                   "connection", "show", name])
    if rc != 0:
        return []
    lines = (out.splitlines() + ["yes", "0"])[:2]
    cur_auto, cur_prio = lines[0].strip(), lines[1].strip()
    if cur_auto == "yes" and cur_prio == prio:
        return []
    mod = ["nmcli", "connection", "modify", name]
    return [Change("nmcli", f"Tethering autoconnect [{name}]", f"{cur_auto}/prio {cur_prio}",
                   f"yes/prio {prio}",
                   mod + ["connection.autoconnect", "yes", "connection.autoconnect-priority", prio],
                   mod + ["connection.autoconnect", cur_auto, "connection.autoconnect-priority", cur_prio])]


# -- Display: blank/lock timers, night light, brightness -------------------
def _gs_change(run, label, schema, key, new, parse=lambda s: s.strip().split()[-1].strip("'")):
    rc, out = run(["gsettings", "get", schema, key])
    if rc != 0:
        return []
    cur = parse(out)
    if cur == new:
        return []
    return [Change("gsettings", label, cur, new, ["gsettings", "set", schema, key, new],
                   ["gsettings", "set", schema, key, cur])]


def plan_display(p: Profile, run: Runner) -> list[Change]:
    d = p.display
    out: list[Change] = []
    if "blank_after_minutes" in d:
        secs = str(d["blank_after_minutes"] * 60)
        out += _gs_change(run, "Screen blank delay (s)", "org.gnome.desktop.session", "idle-delay", secs)
    if "lock_after_minutes" in d:
        lock_min = d["lock_after_minutes"]
        blank_min = d.get("blank_after_minutes", 0)
        out += _gs_change(run, "Screen lock enabled", "org.gnome.desktop.screensaver", "lock-enabled",
                          "true" if lock_min else "false")
        if lock_min:
            delay = str(max(0, lock_min - blank_min) * 60)
            out += _gs_change(run, "Lock delay after blank (s)", "org.gnome.desktop.screensaver",
                              "lock-delay", delay)
    if "lock_screen" in d:
        # GNOME locks the screen when the lid closes (gsd-power), and a locked session
        # inhibits all remote desktop sessions, so remote profiles must turn locking off.
        out += _gs_change(run, "Screen lock enabled", "org.gnome.desktop.screensaver", "lock-enabled",
                          "true" if d["lock_screen"] else "false")
    if "night_light" in d:
        out += _gs_change(run, "Night light", "org.gnome.settings-daemon.plugins.color",
                          "night-light-enabled", "true" if d["night_light"] else "false")
    if "brightness_percent" in d:
        rc, o = run(["brightnessctl", "-m"])
        if rc == 0 and o.count(",") >= 4:
            cur = o.split(",")[3].rstrip("%")
            new = str(d["brightness_percent"])
            if cur != new:
                out.append(Change("brightnessctl", "Brightness (%)", cur, new,
                                  ["brightnessctl", "set", new + "%"], ["brightnessctl", "set", cur + "%"]))
    return out


# -- CPU limits (turbo, max frequency); writes go through the root helper -
def plan_cpu(p: Profile, run: Runner) -> list[Change]:
    out: list[Change] = []
    if "turbo" in p.cpu:
        cur = cpu.read_turbo()
        if cur is not None and cur != p.cpu["turbo"]:
            on, off = _helper_argv("set-cpu-turbo", "on"), _helper_argv("set-cpu-turbo", "off")
            out.append(Change("cpu", "CPU turbo/boost", "on" if cur else "off",
                              "on" if p.cpu["turbo"] else "off",
                              on if p.cpu["turbo"] else off, on if cur else off, privileged=True))
    if "max_freq_percent" in p.cpu:
        cur = cpu.read_max_percent()
        new = p.cpu["max_freq_percent"]
        if cur is not None and cur != new:
            out.append(Change("cpu", "CPU max frequency", f"{cur}%", f"{new}%",
                              _helper_argv("set-cpu-max-percent", str(new)),
                              _helper_argv("set-cpu-max-percent", str(cur)), privileged=True))
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
    on, off = ["rfkill", "unblock", "bluetooth"], ["rfkill", "block", "bluetooth"]
    return [Change("rfkill", "Bluetooth", "off" if blocked else "on", "on" if want_on else "off",
                   on if want_on else off, off if want_on else on)]


# -- User tasks (whitelisted systemctl --user only) ------------------------
_INVERSE = {"start": "stop", "stop": "start"}


def _task_change(cmd: str, label: str, reversible: bool) -> Change:
    argv = parse_task(cmd)
    inv = _INVERSE.get(argv[2])
    revert = ["systemctl", "--user", inv, argv[3]] if (reversible and inv) else []
    return Change("task", f"{label}: {argv[2]} {argv[3]}", "-", "run", argv, revert)


def plan_tasks(p: Profile, run: Runner, previous: Profile | None = None) -> list[Change]:
    out = []
    if previous is not None and previous.name != p.name:
        out += [_task_change(c, "On leaving " + previous.name, False)
                for c in previous.tasks.get("on_deactivate", [])]
    out += [_task_change(c, "On activate", True) for c in p.tasks.get("on_activate", [])]
    return out


BACKENDS = (plan_lid, plan_auto_suspend, plan_wifi, plan_tethering, plan_power_mode,
            plan_display, plan_bluetooth, plan_cpu)
