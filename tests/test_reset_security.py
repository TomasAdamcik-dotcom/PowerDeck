"""The root pass of reset-all reads backups from the user's home: it must never run their commands blindly."""
import json
import os

import pytest

from powerdeck import backends, commands, cpu, engine, helper
from powerdeck.builtin import BUILTIN_PROFILES
from test_cpu import make_cpu
from test_features import Fake, _two_applies

H = commands.HELPER_PATH


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(helper, "DROPIN_DIR", str(tmp_path / "l"))
    monkeypatch.setattr(backends, "helper_installed", lambda: True)

ALLOWED = [
    [H, "set-lid", "ignore", "suspend", "lock"],
    [H, "remove-lid"],
    [H, "set-cpu-turbo", "on"],
    [H, "set-cpu-max-percent", "100"],
    [H, "set-thermal-target", "65"],
    [H, "clear-thermal-target"],
    ["nmcli", "connection", "modify", "Home Wi-Fi", "802-11-wireless.powersave", "2"],
    ["nmcli", "connection", "modify", "Café:5G", "802-11-wireless.powersave", "default"],
    ["nmcli", "connection", "modify", "Tel", "connection.autoconnect", "no",
     "connection.autoconnect-priority", "-5"],
    ["powerprofilesctl", "set", "balanced"],
    commands.power_mode("power-saver", dbus=True),            # Fedora (tuned-ppd)
    ["rfkill", "unblock", "bluetooth"],
    ["rfkill", "block", "bluetooth"],
    ["brightnessctl", "set", "40%"],
]

REFUSED = [
    ["sh", "-c", "id > /tmp/pwned"],
    ["pkexec", "sh", "-c", "id"],
    ["/tmp/powerdeck-helper", "remove-lid"],                  # helper from elsewhere
    [H, "set-lid", "ignore", "ignore", "rm -rf /"],
    [H, "set-cpu-max-percent", "5"],
    [H, "remove-lid", "extra"],
    [H, "set-thermal-target", "95"],                         # outside 55-90
    [H, "set-thermal-target", "54"],
    [H, "set-thermal-target", "６５"],                         # non-ASCII digits
    [H, "set-thermal-target", "65", "x"],
    [H, "set-thermal-target"],
    [H, "clear-thermal-target", "65"],
    [H, "start-unit", "powerdeck-thermal@65.service"],
    [H],
    ["nmcli", "connection", "delete", "Tel"],
    ["nmcli", "connection", "modify", "--help", "802-11-wireless.powersave", "2"],
    ["nmcli", "connection", "modify", "X", "802-11-wireless.powersave", "2", "ipv4.dns", "6.6.6.6"],
    ["nmcli", "connection", "modify", "X", "connection.autoconnect", "yes",
     "connection.autoconnect-priority", "1; id"],
    ["nmcli", "connection", "modify", "X", "802-1x.ca-cert", "/root/x"],
    ["powerprofilesctl", "set", "turbo"],
    ["powerprofilesctl", "launch", "sh"],
    commands.power_mode("turbo", dbus=True),
    commands.power_mode("balanced'>; <'x", dbus=True),
    commands.power_mode("balanced", dbus=True) + ["x"],
    [*commands.power_mode("balanced", dbus=True)[:-2], "Other", "<'balanced'>"],
    ["gdbus", "call", "--session", *commands.power_mode("balanced", dbus=True)[3:]],
    ["gdbus", "call", "--system", "--dest", "org.freedesktop.systemd1", "--object-path",
     "/org/freedesktop/systemd1", "--method", "org.freedesktop.systemd1.Manager.StartUnit",
     "x.service", "replace"],
    ["rfkill", "block", "all"],
    ["brightnessctl", "set", "500%"],
    ["brightnessctl", "-d", "x", "set", "50%"],
    ["gsettings", "set", "a", "b", "c"],                          # user state, never as root
    ["systemctl", "--user", "start", "x.service"],
    "sh -c id",
    [["sh"], "-c"],
    [],
]


@pytest.mark.parametrize("argv", ALLOWED)
def test_whitelist_accepts_powerdeck_reverts(argv):
    assert commands.allowed_as_root(argv)


@pytest.mark.parametrize("argv", REFUSED)
def test_whitelist_refuses_everything_else(argv):
    assert not commands.allowed_as_root(argv)


def test_every_system_revert_of_builtin_profiles_is_allowed(tmp_path, monkeypatch):
    """Legitimate reverts must pass the whitelist, whatever profile was applied."""
    make_cpu(tmp_path / "c", cur_max=3_000_000, no_turbo="1")
    monkeypatch.setattr(cpu, "CPU_ROOT", str(tmp_path / "c"))
    monkeypatch.setattr(helper, "DROPIN_DIR", str(tmp_path / "l"))
    helper.main(["set-lid", "lock", "lock", "lock"], str(tmp_path / "l"), reload=False)
    seen = 0
    for p in BUILTIN_PROFILES:
        f = Fake()
        f.bt_blocked = not p.bluetooth
        f.mode = "balanced" if p.power_mode == "performance" else "performance"
        for c in engine.make_plan(p, f).changes:
            if c.backend not in engine.USER_BACKENDS and c.revert_argv:
                assert commands.allowed_as_root(commands.without_pkexec(c.revert_argv)), c
                seen += 1
    assert seen > 10


def _write_backup(directory, name, changes):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(json.dumps({"profile": "X", "previous": None, "changes": changes}))


def _change(revert, backend="nmcli", target="Evil"):
    return {"backend": backend, "target": target, "old": "a", "new": "b",
            "apply_argv": ["true"], "revert_argv": revert, "privileged": False}


def test_system_pass_skips_and_reports_malicious_reverts(tmp_path):
    f = _two_applies(tmp_path)            # legitimate backups: lid, tethering, Bluetooth, ...
    _write_backup(tmp_path / "b", "99999999999999999999-Evil.json", [
        _change(["sh", "-c", "id > /tmp/pwned"]),
        _change(["pkexec", "sh", "-c", "id"], backend="logind"),
        _change([H, "set-lid", "ignore", "ignore", "x;id"], backend="cpu", target="Lid\x1b[2J"),
        _change(["sh", "-c", "user-side"], backend="gsettings"),   # not run as root at all
    ])
    n = len(f.calls)
    errs = engine.reset_all(f, tmp_path / "b", only="system", mark_restored=False)
    calls = f.calls[n:]
    assert not any(c[0] in ("sh", "pkexec") or "x;id" in c for c in calls)
    assert len(errs) == 3 and all("refused" in e for e in errs)
    assert all("\x1b" not in e for e in errs)                         # no terminal escapes
    # the legitimate reverts still ran, directly (no pkexec)
    assert [H, "remove-lid"] in calls
    assert ["rfkill", "unblock", "bluetooth"] in calls and not f.bt_blocked
    assert f.nm["Tel"] == ["no", "0"]


def test_user_pass_unaffected_by_whitelist(tmp_path):
    f = _two_applies(tmp_path)
    assert engine.reset_all(f, tmp_path / "b", only="user", active_file=tmp_path / "a.json") == []
    assert f.gs["idle-delay"] == "300"


def test_malformed_backups_are_reported_not_fatal(tmp_path):
    f = _two_applies(tmp_path)
    b = tmp_path / "b"
    (b / "00000000000000000001-bad1.json").write_text("[1, 2]")
    _write_backup(b, "00000000000000000002-bad2.json", [{"backend": "nmcli"}])
    _write_backup(b, "00000000000000000003-bad3.json", [_change("sh -c id")])
    _write_backup(b, "00000000000000000004-bad4.json", ["not a dict"])
    errs = engine.reset_all(f, b, only="system", mark_restored=False)
    assert len(errs) == 4 and not f.bt_blocked


def test_system_pass_refuses_symlinked_backup_file(tmp_path):
    f = _two_applies(tmp_path)
    target = tmp_path / "elsewhere.json"
    _write_backup(tmp_path, "elsewhere.json", [_change(["rfkill", "block", "bluetooth"], "rfkill")])
    os.symlink(target, tmp_path / "b" / "99999999999999999999-Link.json")
    n = len(f.calls)
    errs = engine.reset_all(f, tmp_path / "b", only="system", mark_restored=False)
    assert any("Link.json" in e and "symbolic link" in e for e in errs)
    assert ["rfkill", "block", "bluetooth"] not in f.calls[n:]
    assert ["rfkill", "unblock", "bluetooth"] in f.calls[n:]         # the real backups still reverted


def test_system_pass_refuses_symlinked_backup_dir(tmp_path, monkeypatch):
    f = _two_applies(tmp_path)
    os.symlink(tmp_path / "b", tmp_path / "link")
    real_lstat = os.lstat

    def lstat(path):   # tests may run as root, where every symlink is root-owned
        st = real_lstat(path)
        return os.stat_result(st[:4] + (1000,) + st[5:10]) if str(path).endswith("link") else st
    monkeypatch.setattr(os, "lstat", lstat)
    n = len(f.calls)
    errs = engine.reset_all(f, tmp_path / "link", only="system", mark_restored=False)
    assert errs and "symbolic link" in errs[0]
    assert f.calls[n:] == []
    assert backends.HELPER_PATH == H


def test_uninstall_script_never_imports_from_cwd_or_runs_backup_commands(tmp_path):
    """scripts/uninstall-reset.sh runs Python as root: a planted powerdeck/ in the cwd must not load."""
    import shutil
    import subprocess
    import sys
    from pathlib import Path
    if not (shutil.which("sh") and shutil.which("awk")):
        pytest.skip("needs sh and awk")
    repo = Path(__file__).resolve().parent.parent
    evil = tmp_path / "cwd"
    (evil / "powerdeck").mkdir(parents=True)
    planted = f"open({str(tmp_path / 'planted')!r}, 'w').close()\n"
    (evil / "powerdeck" / "__init__.py").write_text(planted)
    (evil / "json.py").write_text(planted)
    home = tmp_path / "home"
    backups = home / ".local" / "state" / "powerdeck" / "backups"
    _write_backup(backups, "20250101-120000-X.json",
                  [_change(["sh", "-c", f"touch {tmp_path / 'pwned'}"])])
    (tmp_path / "passwd").write_text(f"pdtest:x:59999:59999::{home}:/bin/sh\n")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(repo),
           "POWERDECK_PYTHON": sys.executable, "POWERDECK_PASSWD": str(tmp_path / "passwd"),
           "POWERDECK_HELPER": str(tmp_path / "no-helper"),
           "HOME": str(tmp_path / "root"), "XDG_STATE_HOME": str(tmp_path / "state"),
           "XDG_CONFIG_HOME": str(tmp_path / "config")}
    r = subprocess.run(["sh", str(repo / "scripts" / "uninstall-reset.sh")], cwd=evil, env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
    assert not (tmp_path / "planted").exists(), r.stdout + r.stderr
    assert not (tmp_path / "pwned").exists()
    assert "refused" in r.stdout, r.stdout + r.stderr
