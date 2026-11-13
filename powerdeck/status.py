"""Read-only system status for the dashboard."""
from __future__ import annotations

import glob
from dataclasses import dataclass

from .runner import Runner, system_runner

CPU_ZONE_TYPES = ("x86_pkg_temp", "coretemp", "k10temp", "cpu", "cpu-thermal", "acpitz")


@dataclass
class Status:
    battery_percent: int | None
    battery_state: str | None
    cpu_temp_c: float | None
    active_connection: str | None
    power_mode: str | None
    ssh_connections: int = 0
    rdp_connections: int = 0
    login_sessions: int = 0
    wifi_powersave: str | None = None
    remote_login: str | None = None  # GNOME Remote Login (system RDP): enabled / disabled


def _read(path: str) -> str | None:
    try:
        return open(path).read().strip()
    except OSError:
        return None


def read_battery(root: str = "/sys/class/power_supply") -> tuple[int | None, str | None]:
    """Average capacity over all batteries; state is 'Discharging' if any discharges."""
    caps, states = [], []
    for bat in sorted(glob.glob(root + "/BAT*")):
        v = _read(bat + "/capacity")
        if v and v.isdigit():
            caps.append(int(v))
            states.append(_read(bat + "/status") or "")
    if not caps:
        return None, None
    state = "Discharging" if "Discharging" in states else (states[0] or None)
    return round(sum(caps) / len(caps)), state


def read_cpu_temp(root: str = "/sys/class/thermal") -> float | None:
    """Prefer zones that look like the CPU package; fall back to the hottest zone."""
    cpu, anyz = [], []
    for z in glob.glob(root + "/thermal_zone*"):
        v, typ = _read(z + "/temp"), (_read(z + "/type") or "")
        if v and v.lstrip("-").isdigit():
            t = int(v) / 1000
            anyz.append(t)
            if typ in CPU_ZONE_TYPES:
                cpu.append(t)
    pool = cpu or anyz
    return max(pool) if pool else None


def _count_lines(out: str, rc: int) -> int:
    return len([l for l in out.splitlines() if l.strip()]) if rc == 0 else 0


def read_status(run: Runner = system_runner) -> Status:
    pct, state = read_battery()
    rc, out = run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show", "--active"])
    conn = None
    if rc == 0:
        for line in out.splitlines():
            name, _, typ = line.rpartition(":")
            if "wireless" in typ or "ethernet" in typ or "gsm" in typ:
                conn = name
                break
    rc, pm = run(["powerprofilesctl", "get"])
    ssh = _count_lines(*reversed(run(["ss", "-Htn", "state", "established", "sport", "=", ":22"])))
    rdp = _count_lines(*reversed(run(["ss", "-Htn", "state", "established", "sport", "=", ":3389"])))
    sess = _count_lines(*reversed(run(["loginctl", "list-sessions", "--no-legend"])))
    return Status(pct, state, read_cpu_temp(), conn, pm if rc == 0 else None,
                  ssh, rdp, sess, read_wifi_powersave(run), read_remote_login(run))


def read_remote_login(run: Runner) -> str | None:
    """RDP status of the system-wide gnome-remote-desktop (headless "Remote Login")."""
    rc, out = run(["grdctl", "--system", "status"])
    if rc != 0:
        return None
    in_rdp = False
    for line in out.splitlines():
        if not line.startswith((" ", "\t")):
            in_rdp = line.strip() == "RDP:"
        elif in_rdp and line.strip().startswith("Status:"):
            return line.split(":", 1)[1].strip()
    return None


def read_wifi_powersave(run: Runner) -> str | None:
    rc, out = run(["iw", "dev"])
    if rc != 0:
        return None
    iface = next((l.split()[1] for l in out.splitlines() if l.strip().startswith("Interface")), None)
    if not iface:
        return None
    rc, out = run(["iw", "dev", iface, "get", "power_save"])
    if rc != 0 or ":" not in out:
        return None
    return out.split(":", 1)[1].strip().lower()


def thermal_level(temp: float | None, warn: float = 85, crit: float = 92) -> str:
    if temp is None:
        return "unknown"
    return "critical" if temp >= crit else "warn" if temp >= warn else "ok"
