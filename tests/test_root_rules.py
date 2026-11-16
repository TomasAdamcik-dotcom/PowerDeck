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


def _ours(paths):
    """scripts/install-paths.sh's verdict for each path (True = install.sh may write/remove it)."""
    import subprocess
    script = '. scripts/install-paths.sh; for p in "$@"; do ours "$p" && echo y || echo n; done'
    out = subprocess.run(["sh", "-c", script, "sh", *paths], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout.split()
    return [v == "y" for v in out]


def _staged(tmp_path, pylib):
    import subprocess
    subprocess.run(["sh", "scripts/stage.sh", str(tmp_path), pylib], cwd=ROOT, check=True)
    return {"/" + str(p.relative_to(tmp_path)): p for p in tmp_path.rglob("*") if not p.is_dir()}


def test_install_script_may_write_every_staged_file(tmp_path):
    for pylib in ("/usr/lib/python3/dist-packages", "/usr/lib/python3.14/site-packages",
                  "/usr/lib64/python3.9/site-packages"):
        files = _staged(tmp_path / pylib.replace("/", "_"), pylib)
        assert len(files) > 40 and all(_ours(sorted(files))), files


def test_install_script_refuses_other_paths():
    assert not any(_ours([
        "/etc/passwd", "/usr/bin/sh", "/usr/bin/powerdeck-x/../sh", "/usr/libexec/powerdeck-x/y",
        "/usr/lib/python3.12/site-packages/other/powerdeck/cli.py",
        "/usr/lib/python3.12/site-packages/powerdeck/sub/x.py",
        "/usr/lib/python3/x/site-packages/powerdeck/cli.py", "/usr/lib/python3.14/site-packages/x.py",
        "/usr/share/applications/other.desktop", "/usr/bin/powerdeck:x", "/usr/bin/powerdeck\n/etc/passwd",
        "usr/bin/powerdeck", "/home/u/.local/bin/powerdeck",
    ]))


def test_staged_modes(tmp_path):
    files = _staged(tmp_path, "/usr/lib/python3/dist-packages")
    exe = {f for f, p in files.items() if not p.is_symlink() and p.stat().st_mode & 0o777 == 0o755}
    assert exe == {"/usr/bin/powerdeck", "/usr/bin/powerdeck-gtk", "/usr/libexec/powerdeck-helper",
                   "/usr/libexec/powerdeck-suspend", "/usr/libexec/powerdeck-thermal",
                   "/usr/libexec/powerdeck-uninstall-reset"}
    others = {p.stat().st_mode & 0o777 for f, p in files.items() if f not in exe and not p.is_symlink()}
    assert others == {0o644}


def test_install_script_needs_root_and_isolates_python():
    script = (ROOT / "scripts/install.sh").read_text()
    assert '[ "$(id -u)" -ne 0 ]' in script
    # the only Python it runs itself: isolated, from /
    code = "\n".join(l for l in script.splitlines() if not l.lstrip().startswith("#") and "echo" not in l)
    assert re.findall(r"(?:^|[ (])(\S*python3) (\S+)", code, re.M) == [("/usr/bin/python3", "-I")]
    assert "PYLIB=$(cd / && /usr/bin/python3 -I -c" in script
