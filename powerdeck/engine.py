"""Plan → confirm → backup → apply → rollback."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from . import backends, paths
from .changes import Change
from .profile import Profile
from .runner import Runner, system_runner


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


def make_plan(p: Profile, run: Runner = system_runner) -> Plan:
    p.validate()
    changes: list[Change] = []
    for planner in backends.BACKENDS:
        changes.extend(planner(p, run))
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
    snap.write_text(json.dumps({"profile": plan.profile,
                                "changes": [c.to_dict() for c in plan.changes]}, indent=2))
    done: list[Change] = []
    errors: list[str] = []
    for c in plan.changes:
        rc, out = run(c.apply_argv)
        if rc != 0:
            errors.append(f"{c.target}: {out or 'failed (rc=%d)' % rc}")
            for d in reversed(done):  # roll back what we already did
                run(d.revert_argv)
            return errors
        done.append(c)
    (active_file or paths.state_dir() / "active.json").parent.mkdir(parents=True, exist_ok=True)
    (active_file or paths.state_dir() / "active.json").write_text(json.dumps({"profile": plan.profile}))
    return errors


def restore_last(run: Runner = system_runner, backup_dir: Path | None = None) -> list[str]:
    """Revert the most recent backup (undo last applied profile)."""
    backup_dir = backup_dir or paths.backups_dir()
    f = _last_backup_file(backup_dir)
    if f is None:
        return ["No backup to restore."]
    data = json.loads(f.read_text())
    errors = []
    for d in reversed(data["changes"]):
        c = Change.from_dict(d)
        rc, out = run(c.revert_argv)
        if rc != 0:
            errors.append(f"{c.target}: {out or 'failed'}")
    if not errors:
        f.rename(f.with_suffix(".restored"))
    return errors


def active_profile(active_file: Path | None = None) -> str | None:
    try:
        return json.loads((active_file or paths.state_dir() / "active.json").read_text())["profile"]
    except (OSError, ValueError, KeyError):
        return None
