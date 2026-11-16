"""Guards for the rules in CLAUDE.md about code that runs as root (cheap checks on the sources)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_helper_python_is_isolated():
    # -I: no cwd/script dir or PYTHON* variables on sys.path for the root helper
    assert (ROOT / "powerdeck/helper.py").read_text().splitlines()[0] == "#!/usr/bin/python3 -I"


def test_thermal_daemon_is_isolated_and_stdlib_only():
    src = (ROOT / "powerdeck/thermal_limit.py").read_text()
    assert src.splitlines()[0] == "#!/usr/bin/python3 -I"
    imports = set(re.findall(r"^(?:from|import) (\S+)", src, re.M))
    assert imports <= {"__future__", "glob", "json", "os", "signal", "sys", "time"}, imports


def test_thermal_unit_runs_only_the_daemon_and_is_never_enabled():
    unit = (ROOT / "data/powerdeck-thermal@.service").read_text()
    execs = re.findall(r"^Exec\w*=(.*)$", unit, re.M)
    assert execs == ["/usr/libexec/powerdeck-thermal %i", "/usr/libexec/powerdeck-thermal --restore"]
    assert "[Install]" not in unit
    assert re.search(r"^ProtectSystem=strict$", unit, re.M) and re.search(r"^ProtectHome=yes$", unit, re.M)


def test_uninstall_reset_keeps_cwd_off_sys_path():
    script = (ROOT / "scripts/uninstall-reset.sh").read_text()
    assert re.search(r"^cd / \|\| exit 0$", script, re.M)
    calls = re.findall(r'"\$PY"( -P)? -m', script)
    assert calls and all(calls), "every root/user Python call must use -P"


def test_suspend_helper_takes_no_arguments():
    lines = [l for l in (ROOT / "data/powerdeck-suspend").read_text().splitlines()
             if l and not l.startswith("#")]
    assert lines == ["exec /usr/bin/systemctl suspend --ignore-inhibitors"]


def test_polkit_actions_point_only_at_the_two_helpers():
    policy = (ROOT / "data/io.github.powerdeck.policy").read_text()
    paths = re.findall(r'org\.freedesktop\.policykit\.exec\.path">([^<]+)<', policy)
    assert sorted(paths) == ["/usr/libexec/powerdeck-helper", "/usr/libexec/powerdeck-suspend"]
