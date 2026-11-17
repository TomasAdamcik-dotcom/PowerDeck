#!/usr/bin/python3 -I
"""Root program installed as /usr/libexec/powerdeck-nopassword, run as
``pkexec /usr/libexec/powerdeck-nopassword on|off`` (polkit action io.github.powerdeck.no-password).

It can ONLY write or remove PowerDeck's own polkit rule, whose content is fixed below: with it,
administrators at the computer change PowerDeck's lid and CPU settings without the password.
A separate program and action from the helper on purpose: its action asks for the password
every time (auth_admin, not auth_admin_keep), so a password typed for a profile change a few
minutes ago can never be reused to turn the prompt off for good.
Stdlib only.
"""
from __future__ import annotations

import os
import sys

RULES_DIR = "/etc/polkit-1/rules.d"
RULE = "49-powerdeck-no-password.rules"
CONTENT = """\
// Created by PowerDeck (`powerdeck no-password on`); remove with `powerdeck no-password off`.
// Administrators sitting at the machine change PowerDeck's lid and CPU settings without a password.
polkit.addRule(function(action, subject) {
    if (action.id == "io.github.powerdeck.manage-power" && subject.local && subject.active &&
        (subject.isInGroup("sudo") || subject.isInGroup("wheel") || subject.isInGroup("admin"))) {
        return polkit.Result.YES;
    }
});
"""
USAGE = "usage: powerdeck-nopassword on|off"


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def set_rule(on: bool, rules_dir: str = RULES_DIR) -> int:
    target = os.path.join(rules_dir, RULE)
    tmp = target + ".tmp"
    if not on:
        _remove(target)
        _remove(tmp)
        print("rule removed")
        return 0
    if not os.path.isdir(rules_dir) or os.path.islink(rules_dir):
        print(f"{rules_dir} not found: this polkit has no JavaScript rules", file=sys.stderr)
        return 3
    _remove(tmp)  # never write through a leftover
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "w") as f:
        f.write(CONTENT)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o644)  # polkitd reads it as its own user
    os.replace(tmp, target)
    print("rule installed")
    return 0


def main(argv: list[str], rules_dir: str = RULES_DIR) -> int:
    if argv not in (["on"], ["off"]):
        print(USAGE, file=sys.stderr)
        return 2
    return set_rule(argv[0] == "on", rules_dir)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
