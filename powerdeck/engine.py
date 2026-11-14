"""Plan → confirm → backup → apply → rollback."""
from __future__ import annotations

import json
import os
import shlex
import stat
import time
from dataclasses import dataclass
from pathlib import Path

import logging

from . import backends, commands, paths
from .changes import Change
from .profile import Profile
from .runner import Runner, system_runner


log = logging.getLogger("powerdeck")


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


def _last_backup_file(directory: Path) -> Path | None:
    files = sorted(directory.glob("*.json")) if directory.is_dir() else []
    return files[-1] if files else None


def apply_plan(plan: Plan, run: Runner = system_runner,
               backup_dir: Path | None = None, active_file: Path | None = None) -> list[str]:
    """Apply a *confirmed* plan. The backup is written BEFORE anything changes.

    On failure of any change, already-applied changes are reverted.
    Returns a list of error strings (empty on success).
    """
    backup_dir = backup_dir or paths.backups_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if plan.needs_admin and not backends.helper_installed():
        return [f"Admin helper not installed ({backends.HELPER_PATH}). "
                "Run: sudo scripts/install-helper.sh"]
    snap = backup_dir / f"{stamp}-{plan.profile}.json"
    n = 1
    while snap.exists():  # two applies within the same second
        snap = backup_dir / f"{stamp}-{n}-{plan.profile}.json"
        n += 1
    snap.write_text(json.dumps({"profile": plan.profile, "previous": active_profile(active_file),
                                "changes": [c.to_dict() for c in plan.changes]}, indent=2))
    done: list[Change] = []
    errors: list[str] = []
    log.info("applying profile %s (%d changes)", plan.profile, len(plan.changes))
    for c in plan.changes:
        rc, out = run(c.apply_argv)
        if rc != 0:
            errors.append(f"{c.target}: {out or 'failed (rc=%d)' % rc}")
            log.error("change failed: %s -> %s; rolling back %d", c.describe(), errors[-1], len(done))
            for d in reversed(done):  # roll back what we already did
                if d.revert_argv:
                    run(d.revert_argv)
            # nothing stays applied, so this backup must not be the one `restore` undoes next
            snap.unlink(missing_ok=True)
            return errors
        log.info("applied: %s", c.describe())
        done.append(c)
    (active_file or paths.state_dir() / "active.json").parent.mkdir(parents=True, exist_ok=True)
    (active_file or paths.state_dir() / "active.json").write_text(json.dumps({"profile": plan.profile}))
    return errors


def restore_last(run: Runner = system_runner, backup_dir: Path | None = None,
                 active_file: Path | None = None) -> list[str]:
    """Revert the most recent backup (undo last applied profile)."""
    backup_dir = backup_dir or paths.backups_dir()
    f = _last_backup_file(backup_dir)
    if f is None:
        return ["No backup to restore."]
    data = json.loads(f.read_text())
    log.info("restoring backup %s", f.name)
    errors = []
    for d in reversed(data["changes"]):
        c = Change.from_dict(d)
        if not c.revert_argv:
            continue
        rc, out = run(c.revert_argv)
        if rc != 0:
            errors.append(f"{c.target}: {out or 'failed'}")
    if not errors:
        f.rename(f.with_suffix(".restored"))
        # the profile that was active before this backup's apply is active again
        active_file = active_file or paths.state_dir() / "active.json"
        if data.get("previous"):
            active_file.write_text(json.dumps({"profile": data["previous"]}))
        else:
            active_file.unlink(missing_ok=True)
    return errors


# Which reverts can run where during uninstall: gsettings live in the user's dconf and
# user tasks in the user's systemd, everything else is system state that root can revert.
USER_BACKENDS = ("gsettings", "task")


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


def _read_backup(f: Path) -> list[Change]:
    """The changes recorded in one backup file. Symlinks and malformed files raise ValueError/OSError."""
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
    return changes


def _printable(s: str) -> str:
    # target names and commands come from user-writable files; keep terminal escapes out of logs
    return "".join(ch if ch.isprintable() else "?" for ch in s)


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
    if only == "system":
        link = _symlinked_dir(backup_dir)
        if link is not None:
            log.error("reset: refusing backups below symlink %s", link)
            return [f"{_printable(str(link))}: symbolic link, refused"]
    files = sorted(backup_dir.glob("*.json"), reverse=True) if backup_dir.is_dir() else []
    errors: list[str] = []
    for f in files:
        try:
            changes = _read_backup(f)
        except (OSError, ValueError) as e:
            errors.append(_printable(f"{f.name}: {e}"))
            continue
        for c in reversed(changes):
            if not c.revert_argv:
                continue
            is_user = c.backend in USER_BACKENDS
            if (only == "user" and not is_user) or (only == "system" and is_user):
                continue
            argv = c.revert_argv
            if only == "system":
                argv = commands.without_pkexec(argv)
                if not commands.allowed_as_root(argv):
                    errors.append(_printable(f"{c.target}: not an allowed revert command, refused: "
                                             + shlex.join(argv)))
                    log.warning("reset: refused to run %r from %s as root", argv, f.name)
                    continue
            rc, out = run(argv)
            if rc != 0:
                errors.append(_printable(f"{c.target}: {out or 'failed'}"))
        log.info("reset: reverted %s (only=%s)", f.name, only or "all")
    if mark_restored:
        for f in files:
            if not f.is_symlink():
                f.rename(f.with_suffix(".restored"))
        (active_file or paths.state_dir() / "active.json").unlink(missing_ok=True)
    return errors


def active_profile(active_file: Path | None = None) -> str | None:
    try:
        return json.loads((active_file or paths.state_dir() / "active.json").read_text())["profile"]
    except (OSError, ValueError, KeyError):
        return None
