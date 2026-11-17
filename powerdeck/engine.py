"""Plan → confirm → backup → apply → rollback."""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import stat
import time
from dataclasses import dataclass, replace
from pathlib import Path

from . import backends, commands, paths
from .changes import BRIGHTNESS, USER_BACKENDS, Change
from .profile import Profile
from .runner import Runner, system_runner

log = logging.getLogger("powerdeck")


def _active_file(active_file: Path | None) -> Path:
    return active_file or paths.state_dir() / "active.json"


# A switch keeps the display brightness the user had right before it: something outside
# PowerDeck (GNOME, after the logind reload or the power mode change) sometimes sets the
# panel to 50 % - the level systemd-backlight restored at boot - a moment later.
BRIGHTNESS_SETTLE_S = 3.0      # keep checking this long: the jump can come a moment later
BRIGHTNESS_POLL_S = 0.5


def keep_brightness(run: Runner, raw: int, settle: float | None = None,
                    sleep=time.sleep) -> None:
    """Hold the backlight at ``raw`` and put it back if it changes within ``settle`` s."""
    settle = BRIGHTNESS_SETTLE_S if settle is None else settle
    for i in range(int(settle / BRIGHTNESS_POLL_S) + 1):
        b = backends.read_backlight(run)
        if b is None:
            return  # no backlight
        if b.raw != raw:
            log.info("brightness: %s %d → %d (back to the level before the switch)", b.device, b.raw, raw)
            rc, out = run(commands.brightness(b.device, raw))
            if rc != 0:
                log.warning("brightness: %s", _printable(out.strip()))
                return
        if i * BRIGHTNESS_POLL_S < settle:
            sleep(BRIGHTNESS_POLL_S)


def _brightness_to_keep(p: Profile, light, undo: list[Change]) -> int | None:
    """The backlight level a switch to ``p`` ends with: the one from before the switch, or the
    one from before PowerDeck if a pending profile changed it; None if ``p`` sets its own."""
    if light is None or "brightness_percent" in p.display:
        return None
    for c in undo:
        if c.backend == BRIGHTNESS:
            if c.revert_argv[:1] == ["busctl"] and c.revert_argv[-1].isdigit():
                return int(c.revert_argv[-1])               # the exact level it puts back
            if c.old.isdigit():
                return light.raw_for(int(c.old))            # older backups: brightnessctl NN%
    return light.raw


@dataclass
class Plan:
    profile: str
    changes: list[Change]

    @property
    def empty(self) -> bool:
        return not self.changes

    @property
    def needs_admin(self) -> bool:
        return any(c.privileged for c in self.changes)

    def diff_text(self) -> str:
        if self.empty:
            return "No changes needed."
        return "\n".join("• " + c.describe() for c in self.changes)


def make_plan(p: Profile, run: Runner = system_runner, previous: Profile | None = None) -> Plan:
    """``previous`` = currently active profile, whose on_deactivate tasks should run."""
    p.validate()
    changes: list[Change] = []
    for planner in backends.BACKENDS:
        changes.extend(planner(p, run))
    changes.extend(backends.plan_tasks(p, run, previous))
    return Plan(p.name, changes)


# Backup file names: "<time_ns, 20 digits>-<profile>.json". PowerDeck <= 0.3 wrote
# "<YYYYmmdd-HHMMSS>-<profile>.json", then "<YYYYmmdd-HHMMSS>-<n>-<profile>.json" for further
# applies within the same second; those are all older than any file in the new format.
_NAME_NS_RE = re.compile(r"([0-9]{20})-")
_NAME_OLD_RE = re.compile(r"([0-9]{8}-[0-9]{6})-(?:([0-9]+)-)?")


def _backup_key(f: Path) -> tuple:
    """Sort key, oldest first. (An old name of a profile called e.g. "2-Days" reads as counter 2;
    that only matters for two applies within one second.)"""
    if m := _NAME_NS_RE.match(f.name):
        return (2, int(m[1]), f.name)
    if m := _NAME_OLD_RE.match(f.name):
        return (1, m[1], int(m[2] or 0), f.name)
    return (0, f.name)


def _backup_files(directory: Path) -> list[Path]:
    """Pending backups (not yet restored), oldest first."""
    files = directory.glob("*.json") if directory.is_dir() else []
    return sorted(files, key=_backup_key)


def _new_backup_file(directory: Path, profile: str) -> Path:
    """A name that sorts after every pending backup, even if the clock did not advance."""
    ns = time.time_ns()
    files = _backup_files(directory)
    if files and (last := _backup_key(files[-1]))[0] == 2:
        ns = max(ns, last[1] + 1)
    return directory / f"{ns:020d}-{profile}.json"


def apply_plan(plan: Plan, run: Runner = system_runner,
               backup_dir: Path | None = None, active_file: Path | None = None) -> list[str]:
    """Apply a *confirmed* plan. The backup is written BEFORE anything changes.

    On failure of any change, already-applied changes are reverted.
    Returns a list of error strings (empty on success).
    """
    if plan.needs_admin and not backends.helper_installed():
        return [f"Admin helper not installed ({backends.HELPER_PATH}). "
                "Run: sudo scripts/install-helper.sh"]
    backup_dir = backup_dir or paths.backups_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)
    snap = _new_backup_file(backup_dir, plan.profile)
    snap.write_text(json.dumps({"profile": plan.profile, "previous": active_profile(active_file),
                                "changes": [c.to_dict() for c in plan.changes]}, indent=2))
    log.info("applying profile %s (%d changes)", plan.profile, len(plan.changes))
    for i, c in enumerate(plan.changes):
        rc, out = run(c.apply_argv)
        if rc != 0:
            error = f"{c.target}: {out or 'failed (rc=%d)' % rc}"
            log.error("change failed: %s -> %s; rolling back %d", c.describe(), error, i)
            _revert(plan.changes[:i], run)  # roll back what we already did
            # nothing stays applied, so this backup must not be the one `restore` undoes next
            snap.unlink(missing_ok=True)
            return [error]
        log.info("applied: %s", c.describe())
    _set_active(plan.profile, active_file)
    return []


def _set_active(name: str, active_file: Path | None) -> None:
    active = _active_file(active_file)
    active.parent.mkdir(parents=True, exist_ok=True)
    active.write_text(json.dumps({"profile": name}))


def _revert(changes: list[Change], run: Runner) -> list[str]:
    """Run the revert commands of ``changes``, last change first; returns the errors."""
    errors = []
    for c in reversed(changes):
        if not c.revert_argv:
            continue
        rc, out = run(c.revert_argv)
        if rc != 0:
            errors.append(_printable(f"{c.target}: {out or 'failed'}"))
    return errors


def _read_backup(f: Path) -> tuple[dict, list[Change]]:
    """The content of one backup file and its changes.

    Symlinks (backups live in the user's home, see reset_all) and malformed files raise
    ValueError or OSError.
    """
    if f.is_symlink():
        raise ValueError("symbolic link, refused")
    fd = os.open(f, os.O_RDONLY | os.O_NOFOLLOW)  # no symlink swapped in after the check
    with os.fdopen(fd, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or not isinstance(data.get("changes", []), list):
        raise ValueError("not a PowerDeck backup")
    changes = []
    for d in data.get("changes", []):
        if not isinstance(d, dict):
            raise ValueError("malformed change")
        changes.append(Change.from_dict(d))
    return data, changes


def _printable(s: str) -> str:
    # target names and commands come from user-writable files; keep terminal escapes out of logs
    return "".join(ch if ch.isprintable() else "?" for ch in s)


def restore_last(run: Runner = system_runner, backup_dir: Path | None = None,
                 active_file: Path | None = None) -> list[str]:
    """Revert the most recent backup (undo last applied profile)."""
    files = _backup_files(backup_dir or paths.backups_dir())
    if not files:
        return ["No backup to restore."]
    f = files[-1]
    try:
        data, changes = _read_backup(f)
    except (OSError, ValueError) as e:
        return [_printable(f"{f.name}: {e}")]
    log.info("restoring backup %s", f.name)
    errors = _revert(changes, run)
    if not errors:
        f.rename(f.with_suffix(".restored"))
        # the profile that was active before this backup's apply is active again
        active = _active_file(active_file)
        if data.get("previous"):
            active.write_text(json.dumps({"profile": data["previous"]}))
        else:
            active.unlink(missing_ok=True)
    return errors


def pending_undo(backup_dir: Path | None = None) -> list[Change]:
    """What undoing every pending backup changes, one entry per setting, for the confirmation
    text: ``old`` = the value from before PowerDeck (oldest backup), ``new`` = the value now
    (newest backup). Unreadable backups are left out; ``switch_to`` reports them."""
    merged: dict[tuple[str, str], Change] = {}
    for f in _backup_files(backup_dir or paths.backups_dir()):
        try:
            _data, changes = _read_backup(f)
        except (OSError, ValueError):
            continue
        for c in changes:
            if not c.revert_argv:
                continue
            key = (c.backend, c.target)
            merged[key] = replace(merged[key], new=c.new) if key in merged else c
    return list(merged.values())


NEEDS_HELPER, NOTHING_TO_DO = "needs-helper", "nothing-to-do"


def quick_switch(p: Profile, run: Runner = system_runner, previous: Profile | None = None,
                 helper_installed=None, backup_dir: Path | None = None,
                 active_file: Path | None = None) -> list[str] | str:
    """Switch at once, without a confirmation (GUI Apply button and top-bar icon): the errors of
    ``switch_to``, or NEEDS_HELPER (admin changes but no root helper; nothing is changed) or
    NOTHING_TO_DO (``p`` is active and already in place)."""
    from . import backends
    switching = previous is None or previous.name != p.name
    plan = make_plan(p, run, previous=previous)
    undo = pending_undo(backup_dir) if switching else []
    installed = backends.helper_installed() if helper_installed is None else helper_installed
    if (plan.needs_admin or any(c.privileged for c in undo)) and not installed:
        return NEEDS_HELPER
    if not undo and plan.empty:
        return NOTHING_TO_DO
    return switch_to(p, run, previous=previous, backup_dir=backup_dir, active_file=active_file)


def undo_text(changes: list[Change]) -> str:
    return "\n".join("• " + _printable(f"{c.target}: {c.new} → {c.old}") for c in changes)


def switch_to(p: Profile, run: Runner = system_runner, previous: Profile | None = None,
              backup_dir: Path | None = None, active_file: Path | None = None) -> list[str]:
    """One-click profile switch: undo every pending profile (newest first), then plan and apply
    ``p`` on that clean state, so nothing the previous profile set stays behind.

    Re-applying the profile that is already active only adds what changed since. If an undo
    fails, ``p`` is not applied (the failed backup stays pending for ``restore``).
    """
    backup_dir = backup_dir or paths.backups_dir()
    switching = previous is None or previous.name != p.name
    keep = _brightness_to_keep(p, backends.read_backlight(run), pending_undo(backup_dir)) \
        if switching else None
    if switching:
        while _backup_files(backup_dir):
            if errors := restore_last(run, backup_dir, active_file):
                return errors
    plan = make_plan(p, run, previous)
    if plan.empty:  # nothing to change: no empty backup that `restore` would "undo"
        _set_active(p.name, active_file)
        errors = []
    else:
        errors = apply_plan(plan, run, backup_dir, active_file)
    if keep is not None and not errors:
        keep_brightness(run, keep)
    return errors


def _symlinked_dir(directory: Path) -> Path | None:
    """The first component of ``directory`` that is a symlink a non-root user could have planted."""
    for d in (directory, *directory.absolute().parents):
        try:
            st = os.lstat(d)
        except OSError:
            continue
        if stat.S_ISLNK(st.st_mode) and st.st_uid != 0:
            return d
    return None


def _reverts_for_pass(changes: list[Change], only: str | None) -> tuple[list[Change], list[str]]:
    """The changes to revert in this pass of reset_all, with the command to run, and refusals.

    gsettings and user tasks live in the user's dconf/systemd and are reverted as the user
    (``only="user"``); everything else is system state that root reverts (``only="system"``).
    """
    todo, refused = [], []
    for c in changes:
        if not c.revert_argv:
            continue
        is_user = c.backend in USER_BACKENDS
        if (only == "user" and not is_user) or (only == "system" and is_user):
            continue
        if only == "system":
            argv = commands.without_pkexec(c.revert_argv)
            if not commands.allowed_as_root(argv):
                refused.append(_printable(f"{c.target}: not an allowed revert command, refused: "
                                          + shlex.join(argv)))
                continue
            c = replace(c, revert_argv=argv)
        todo.append(c)
    return todo, refused


def reset_all(run: Runner = system_runner, backup_dir: Path | None = None, only: str | None = None,
              mark_restored: bool = True, active_file: Path | None = None) -> list[str]:
    """Undo EVERY applied profile (newest first) → settings as they were before PowerDeck.

    ``only="user"`` reverts just gsettings/tasks (run as the user), ``only="system"`` everything
    else and calls the root helper directly instead of via pkexec (run as root while
    uninstalling). The system pass runs only commands that ``commands.allowed_as_root``
    accepts and refuses symlinked backup files and directories, because the backups live in
    the user's home. Errors are collected; the remaining reverts still run.
    """
    backup_dir = backup_dir or paths.backups_dir()
    if only == "system" and (link := _symlinked_dir(backup_dir)) is not None:
        log.error("reset: refusing backups below symlink %s", link)
        return [_printable(f"{link}: symbolic link, refused")]
    files = _backup_files(backup_dir)[::-1]  # newest first
    errors: list[str] = []
    for f in files:
        try:
            _data, changes = _read_backup(f)
        except (OSError, ValueError) as e:
            errors.append(_printable(f"{f.name}: {e}"))
            continue
        todo, refused = _reverts_for_pass(changes, only)
        for r in refused:
            log.warning("reset: %s (%s)", r, f.name)
        errors += refused + _revert(todo, run)
        log.info("reset: reverted %s (only=%s)", f.name, only or "all")
    if mark_restored:
        for f in files:
            if not f.is_symlink():
                f.rename(f.with_suffix(".restored"))
        _active_file(active_file).unlink(missing_ok=True)
    return errors


def active_profile(active_file: Path | None = None) -> str | None:
    try:
        name = json.loads(_active_file(active_file).read_text())["profile"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return name if isinstance(name, str) else None
