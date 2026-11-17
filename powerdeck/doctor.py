"""`powerdeck doctor`: what PowerDeck can and cannot do on this system, one line per check.

OK = works, WARN = a feature is missing (PowerDeck skips it), FAIL = PowerDeck cannot run.
"""
from __future__ import annotations

import os
import sys

from . import backends, commands, desktop
from .runner import Runner, find, system_runner

OK, WARN, FAIL, INFO = "OK", "WARN", "FAIL", "INFO"
MIN_PYTHON = (3, 11)
MIN_GTK = (4, 10, 0)
MIN_ADW = (1, 5, 0)
PP_SERVICE_DIRS = ("/usr/share/dbus-1/system-services", "/etc/dbus-1/system-services")
PP_SERVICE = "org.freedesktop.UPower.PowerProfiles.service"
TOOLS = {"pkexec": "lid and CPU settings (root helper)", "nmcli": "Wi-Fi powersave and tethering",
         "rfkill": "Bluetooth", "brightnessctl": "brightness", "systemctl": "guard service",
         "gdbus": "screen lock and power mode"}


def _ver(v) -> str:
    return ".".join(map(str, v))


def check_toolkit() -> list[tuple[str, str]]:
    out = []
    py = sys.version_info[:3]
    out.append((OK if py >= MIN_PYTHON else FAIL, f"Python {_ver(py)} (needs {_ver(MIN_PYTHON)}+)"))
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gtk
    except (ImportError, ValueError) as e:
        return out + [(FAIL, f"GTK 4 / libadwaita not available: {e}")]
    g = (Gtk.get_major_version(), Gtk.get_minor_version(), Gtk.get_micro_version())
    a = (Adw.get_major_version(), Adw.get_minor_version(), Adw.get_micro_version())
    out.append((OK if g >= MIN_GTK else FAIL, f"GTK {_ver(g)} (needs {_ver(MIN_GTK)}+)"))
    out.append((OK if a >= MIN_ADW else FAIL, f"libadwaita {_ver(a)} (needs {_ver(MIN_ADW)}+)"))
    try:
        gi.require_foreign("cairo")
        out.append((OK, "PyGObject cairo support (history graph)"))
    except ImportError:
        out.append((FAIL, "PyGObject cairo support missing (python3-gi-cairo / python3-cairo)"))
    return out


def check_system(run: Runner = system_runner, which=find,
                 service_dirs=PP_SERVICE_DIRS) -> list[tuple[str, str]]:
    desk = desktop.current()
    out = [(INFO, f"desktop: {desk} ({os.environ.get('XDG_CURRENT_DESKTOP') or 'no session'})")]
    if desk == desktop.GNOME and os.environ.get("XDG_CURRENT_DESKTOP") and \
            "gnome" not in os.environ["XDG_CURRENT_DESKTOP"].lower():
        out.append((WARN, "not GNOME or Cinnamon: desktop settings are changed through GNOME keys"))
    out.append((OK, "root helper installed") if backends.helper_installed()
               else (WARN, f"root helper {commands.HELPER_PATH} missing: no lid and CPU settings"))
    mode = backends.read_power_mode(run)
    if mode:
        out.append((OK, f"power mode: {mode[0]} ({'D-Bus' if mode[1] else 'powerprofilesctl'})"))
    elif any(os.path.exists(os.path.join(d, PP_SERVICE)) for d in service_dirs):
        out.append((OK, "power mode: power-profiles daemon installed (not running now)"))
    else:
        out.append((WARN, "power mode: no power-profiles-daemon or tuned-ppd: power mode skipped"))
    keys = [*commands.AUTO_SUSPEND.values(), commands.IDLE_DELAY, commands.LOCK_ENABLED,
            commands.LOCK_DELAY, commands.NIGHT_LIGHT]
    if desk == desktop.CINNAMON:
        keys += list(desktop.CINNAMON_LID.values())
    missing = [" ".join(commands.gsettings_get(k)[2:]) for k in keys
               if run(commands.gsettings_get(k))[0] != 0]
    out.append((WARN, "settings missing (skipped): " + ", ".join(missing)) if missing
               else (OK, f"{desk} settings: all {len(keys)} keys"))
    for tool, what in TOOLS.items():
        if not which(tool):
            out.append((WARN, f"{tool} missing: no {what}"))
    if desk == desktop.GNOME:
        rc, _ = run(["gsettings", "get", "org.gnome.desktop.remote-desktop.rdp", "enable"])
        out.append((OK, "remote desktop: GNOME Remote Desktop") if rc == 0
                   else (WARN, "remote desktop: gnome-remote-desktop not installed"))
    else:
        out.append((OK, "remote desktop: xrdp") if which("xrdp")
                   else (WARN, "remote desktop: install xrdp (the RDP guard is GNOME-only)"))
    rc, out_ = run(["gdbus", "call", "--session", "--dest", "org.freedesktop.DBus", "--object-path",
                    "/org/freedesktop/DBus", "--method", "org.freedesktop.DBus.NameHasOwner",
                    "org.kde.StatusNotifierWatcher"])
    out.append((OK, "top-bar icon (StatusNotifierWatcher)") if rc == 0 and "true" in out_
               else (WARN, "no top-bar icon host" + (" (GNOME: AppIndicator extension)" if desk == desktop.GNOME else "")
                     + "; closing the window quits PowerDeck"))
    if backends.helper_installed():
        from . import nopassword
        np = nopassword.status(run)
        if np != nopassword.UNKNOWN:
            out.append((INFO, "admin password: not needed (powerdeck no-password off)" if np == nopassword.ON
                        else "admin password: asked for lid/CPU changes (skip it: powerdeck no-password on)"))
    rc, out_ = run(["systemctl", "--user", "is-active", "powerdeck-guard"])
    out.append((OK, "guard service running") if out_.strip() == "active"
               else (WARN, "guard service not running: systemctl --user enable --now powerdeck-guard"))
    return out


def run_cli() -> int:
    lines = check_toolkit() + check_system()
    for level, text in lines:
        print(f"{level:4} {text}")
    return 1 if any(level == FAIL for level, _ in lines) else 0
