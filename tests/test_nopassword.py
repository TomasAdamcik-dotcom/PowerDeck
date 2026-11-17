"""`powerdeck no-password`: opt-in polkit rule, written and removed only by its own root program."""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from powerdeck import commands, helper, nopassword
from powerdeck import nopassword_rule as rule

REPO = Path(__file__).resolve().parent.parent
PATH = os.path.join(rule.RULES_DIR, rule.RULE)


def test_on_writes_the_fixed_rule_atomically_and_world_readable(tmp_path):
    assert rule.main(["on"], rules_dir=str(tmp_path)) == 0
    f = tmp_path / rule.RULE
    assert f.read_text() == rule.CONTENT
    assert f.stat().st_mode & 0o777 == 0o644
    assert sorted(os.listdir(tmp_path)) == [rule.RULE]  # no tmp left behind


def test_rule_covers_only_powerdecks_profile_action_for_local_active_admins():
    c = rule.CONTENT
    assert c.count("action.id") == 1 and 'action.id == "io.github.powerdeck.manage-power"' in c
    assert "subject.local && subject.active" in c
    assert 'isInGroup("sudo")' in c and 'isInGroup("wheel")' in c
    assert "no-password" not in c.split("polkit.addRule")[1]  # never covers its own toggle
    assert c.count("polkit.Result.YES") == 1


def test_off_removes_the_rule_and_a_leftover_tmp_and_is_idempotent(tmp_path):
    rule.main(["on"], rules_dir=str(tmp_path))
    (tmp_path / (rule.RULE + ".tmp")).write_text("half")
    assert rule.main(["off"], rules_dir=str(tmp_path)) == 0
    assert os.listdir(tmp_path) == []
    assert rule.main(["off"], rules_dir=str(tmp_path)) == 0


def test_on_never_writes_through_a_planted_tmp_symlink(tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("keep")
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / (rule.RULE + ".tmp")).symlink_to(victim)
    assert rule.main(["on"], rules_dir=str(rules)) == 0
    assert victim.read_text() == "keep"
    assert (rules / rule.RULE).read_text() == rule.CONTENT and not (rules / rule.RULE).is_symlink()


def test_on_refuses_a_missing_or_symlinked_rules_directory(tmp_path, capsys):
    assert rule.main(["on"], rules_dir=str(tmp_path / "missing")) == 3
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    assert rule.main(["on"], rules_dir=str(tmp_path / "link")) == 3
    assert os.listdir(tmp_path / "real") == []
    assert "no JavaScript rules" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [[], ["yes"], ["on", "x"], ["/tmp/r.rules"], ["ON"], ["on", "--dir", "/tmp"]])
def test_refuses_other_arguments(argv, tmp_path):
    assert rule.main(argv, rules_dir=str(tmp_path)) == 2
    assert os.listdir(tmp_path) == []


def test_the_profile_helper_cannot_toggle_it():
    """The helper's action remembers the password for minutes (auth_admin_keep): it must not be a
    way to turn the prompt off for good. Neither can the uninstall's root pass from a backup."""
    for argv in (["set-no-password", "on"], ["no-password", "on"], ["on"]):
        assert not helper.valid_args(argv)
        assert not commands.allowed_as_root([commands.HELPER_PATH, *argv])
    assert not commands.allowed_as_root([commands.NOPASSWORD_PATH, "on"])
    assert not commands.allowed_as_root([commands.NOPASSWORD_PATH, "off"])


def test_its_polkit_action_always_asks_for_the_password():
    policy = (REPO / "data/io.github.powerdeck.policy").read_text()
    action = re.search(r'<action id="io.github.powerdeck.no-password">(.*?)</action>', policy, re.S).group(1)
    assert "<allow_active>auth_admin</allow_active>" in action
    assert "<allow_inactive>no</allow_inactive>" in action and "<allow_any>no</allow_any>" in action
    assert f'exec.path">{commands.NOPASSWORD_PATH}<' in action


def test_root_program_is_isolated_and_stdlib_only():
    src = (REPO / "powerdeck/nopassword_rule.py").read_text()
    assert src.splitlines()[0] == "#!/usr/bin/python3 -I"
    assert set(re.findall(r"^(?:from|import) (\S+)", src, re.M)) <= {"__future__", "os", "sys"}


def test_cli_runs_the_root_program_through_pkexec():
    calls = []
    nopassword.set_enabled(True, lambda argv: calls.append(argv) or (0, ""))
    nopassword.set_enabled(False, lambda argv: calls.append(argv) or (0, ""))
    assert calls == [["pkexec", commands.NOPASSWORD_PATH, "on"], ["pkexec", commands.NOPASSWORD_PATH, "off"]]


@pytest.mark.parametrize("rc,out,want", [
    (0, "", nopassword.ON),
    (0, "polkit\\56temporary_authorization_id=tmpauthz3", nopassword.OFF),  # password typed recently
    (2, "polkit\\56result=auth_admin_keep", nopassword.OFF),
    (1, "", nopassword.OFF),
    (127, "", nopassword.UNKNOWN),
])
def test_status_from_pkcheck(rc, out, want):
    seen = []
    assert nopassword.status(lambda argv: seen.append(argv) or (rc, out)) == want
    assert seen[0][:3] == ["pkcheck", "--action-id", "io.github.powerdeck.manage-power"]


def test_uninstall_reset_turns_the_rule_off(tmp_path):
    """Removing PowerDeck must not leave administrators without a password prompt."""
    log = tmp_path / "calls.log"
    for name in ("helper", "nopassword"):
        fake = tmp_path / name
        fake.write_text(f'#!/bin/sh\necho "{name} $@" >> {log}\n')
        fake.chmod(0o755)
    (tmp_path / "passwd").write_text("")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "POWERDECK_PYTHON": sys.executable,
           "POWERDECK_PASSWD": str(tmp_path / "passwd"), "POWERDECK_HELPER": str(tmp_path / "helper"),
           "POWERDECK_NOPASSWORD": str(tmp_path / "nopassword")}
    r = subprocess.run(["sh", str(REPO / "scripts" / "uninstall-reset.sh")], env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
    assert "nopassword off" in log.read_text().splitlines()


def test_every_package_removal_deletes_the_rule():
    for f in ("scripts/build-deb.sh", "packaging/rpm/powerdeck.spec", "packaging/arch/powerdeck.install",
              "scripts/install-helper.sh"):
        assert f"{PATH} {PATH}.tmp" in (REPO / f).read_text(), f
    assert f"NOPASSWORD={PATH}\n" in (REPO / "scripts/install.sh").read_text()
