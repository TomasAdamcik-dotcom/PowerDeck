"""Which desktop's settings PowerDeck changes: GNOME (also Ubuntu, Zorin, Debian, Fedora, Manjaro)
or Cinnamon (Linux Mint). Cinnamon keeps the same keys as GNOME under ``org.cinnamon.*`` and
ignores the GNOME ones, so the gsettings commands are translated (commands.gsettings_get/set).
"""
from __future__ import annotations

import os
import shutil

GNOME = "gnome"
CINNAMON = "cinnamon"

# GNOME schemas Cinnamon has under the same key names (verified on Linux Mint 22)
CINNAMON_SCHEMAS = {
    "org.gnome.settings-daemon.plugins.power": "org.cinnamon.settings-daemon.plugins.power",
    "org.gnome.desktop.session": "org.cinnamon.desktop.session",
    "org.gnome.desktop.screensaver": "org.cinnamon.desktop.screensaver",
    "org.gnome.settings-daemon.plugins.color": "org.cinnamon.settings-daemon.plugins.color",
}
# Cinnamon's settings daemon handles the lid itself (logind's setting alone does not stop it)
CINNAMON_LID = {"battery": ("org.cinnamon.settings-daemon.plugins.power", "lid-close-battery-action"),
                "ac": ("org.cinnamon.settings-daemon.plugins.power", "lid-close-ac-action")}
CINNAMON_LID_ACTION = {"ignore": "nothing", "suspend": "suspend", "lock": "blank", "poweroff": "shutdown"}


def current(env=None, which=shutil.which) -> str:
    """The session's desktop; without session variables (early service start, root), the one
    installed: Cinnamon only if it is there and GNOME Shell is not."""
    env = os.environ if env is None else env
    names = ":".join(env.get(v, "") for v in ("XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP",
                                              "DESKTOP_SESSION")).lower()
    if "cinnamon" in names:
        return CINNAMON
    if names.strip(":"):
        return GNOME
    return CINNAMON if which("cinnamon-session") and not which("gnome-shell") else GNOME


def schema(name: str, env=None) -> str:
    return CINNAMON_SCHEMAS.get(name, name) if current(env) == CINNAMON else name
