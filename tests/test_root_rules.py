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


def _version():
    return re.search(r'^version = "(.+?)"', (ROOT / "pyproject.toml").read_text(), re.M).group(1)


def test_packages_have_the_project_version():
    import powerdeck
    v = _version()
    assert powerdeck.__version__ == v
    assert re.search(rf"^Version:\s+{re.escape(v)}$", (ROOT / "packaging/rpm/powerdeck.spec").read_text(), re.M)
    assert f"pkgver={v}\n" in (ROOT / "packaging/arch/PKGBUILD").read_text()
    assert f'<release version="{v}"' in (ROOT / "data/io.github.powerdeck.PowerDeck.metainfo.xml").read_text()


def _scriptlet_lines(text):
    return [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]


def test_package_scripts_only_run_the_root_owned_reset_and_fixed_commands():
    """rpm/pacman maintainer scripts: the same few fixed commands as the .deb, nothing from $HOME."""
    allowed = {"/usr/libexec/powerdeck-uninstall-reset || :", "/usr/libexec/powerdeck-uninstall-reset || true",
               "systemctl stop 'powerdeck-thermal@*.service' >/dev/null 2>&1 || :",
               "systemctl stop 'powerdeck-thermal@*.service' >/dev/null 2>&1 || true",
               "rm -f /etc/systemd/logind.conf.d/99-powerdeck-lid.conf",
               "systemctl daemon-reload >/dev/null 2>&1 || :", "systemctl daemon-reload >/dev/null 2>&1 || true"}
    spec = (ROOT / "packaging/rpm/powerdeck.spec").read_text()
    # every section that rpm runs as root on the target system: only these three, checked below
    run_as_root = re.findall(r"^%((?:pre|post|preun|postun)(?:trans|untrans)?|(?:file|transfile)?trigger\w*|"
                             r"verifyscript)\b", spec, re.M)
    assert sorted(run_as_root) == ["post", "postun", "preun"], run_as_root
    for sec in ("post", "preun", "postun"):
        body = re.search(rf"^%{sec}\n(.*?)(?=^%)", spec, re.M | re.S).group(1)
        cmds = [l for l in _scriptlet_lines(body) if not re.fullmatch(r'if \[ "\$1" -eq 0 \]; then|fi', l)]
        assert cmds and set(cmds) <= allowed, (sec, cmds)
    inst = (ROOT / "packaging/arch/powerdeck.install").read_text()
    assert set(re.findall(r"^(\w+)\(\)", inst, re.M)) == {"post_install", "post_upgrade", "pre_remove", "post_remove"}
    cmds = [l for l in _scriptlet_lines(inst) if not re.fullmatch(r"\w+\(\) \{|\}|post_install", l)]
    assert cmds and set(cmds) <= allowed, cmds


def test_uninstall_reset_writes_no_bytecode_as_root():
    script = (ROOT / "scripts/uninstall-reset.sh").read_text()
    assert re.search(r"^export PYTHONDONTWRITEBYTECODE=1$", script, re.M)
    assert script.index("PYTHONDONTWRITEBYTECODE") < script.index('"$PY"')
