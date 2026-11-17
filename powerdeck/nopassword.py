"""`powerdeck no-password on|off|status`: change profiles without the admin password (opt-in).

`on` asks for the password one last time and lets the root program /usr/libexec/powerdeck-nopassword
(nopassword_rule.py, its own polkit action that always asks) install PowerDeck's polkit rule:
administrators at the machine then switch profiles without it.
"""
from __future__ import annotations

import os

from . import commands
from .runner import Runner, system_runner

ACTION = "io.github.powerdeck.manage-power"
ON, OFF, UNKNOWN = "on", "off", "unknown"


def status(run: Runner = system_runner) -> str:
    """ON if polkit lets this process change PowerDeck's settings without asking, as the rule
    does; a password typed a few minutes ago (auth_admin_keep) does not count."""
    rc, out = run(["pkcheck", "--action-id", ACTION, "--process", str(os.getpid())])
    if rc == 127:
        return UNKNOWN
    return ON if rc == 0 and "temporary_authorization" not in out else OFF


def set_enabled(on: bool, run: Runner = system_runner) -> tuple[int, str]:
    return run(["pkexec", commands.NOPASSWORD_PATH, "on" if on else "off"])


TEXT = {
    ON: "In this session, profiles change without the admin password.\n"
        "Ask again: powerdeck no-password off",
    OFF: "In this session, changing the lid or CPU settings asks for the admin password "
         "(remembered for a few minutes).\n"
         "Skip it for administrators at this computer: powerdeck no-password on",
    UNKNOWN: "Cannot tell (pkcheck missing).",
}


def run_cli(what: str) -> int:
    if what == "status":
        print(TEXT[status()])
        return 0
    rc, out = set_enabled(what == "on")
    if rc != 0:
        print(out or f"failed ({rc})")
        return 1
    print(out)  # what was done: "rule installed" / "rule removed"
    print(TEXT[status()])
    return 0
