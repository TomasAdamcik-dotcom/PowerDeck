# PowerDeck — rules for agents

PowerDeck switches laptop profiles on Ubuntu 26.04 / GNOME (GTK4 app + CLI + a small root helper).
Spec and decision log: `SPECIFICATION.md` (section 0 is the current decision log — keep it up to date).
Open work: `TODO.md`. The owner writes in Slovak; answer in Slovak.

## Code that runs as root

Root code is: `powerdeck/helper.py` (→ `/usr/libexec/powerdeck-helper`), `data/powerdeck-suspend`,
`powerdeck/thermal_limit.py` (→ `/usr/libexec/powerdeck-thermal`, run by
`data/powerdeck-thermal@.service`), `data/io.github.powerdeck.policy`, `scripts/uninstall-reset.sh`, the root pass of
`powerdeck reset-all --only system` (`engine.reset_all`, `commands.allowed_as_root`), the Debian
maintainer scripts written by `scripts/build-deb.sh`, `scripts/install-helper.sh`, and
`scripts/install.sh` (with `scripts/stage.sh`, the installed file layout, and
`scripts/install-paths.sh`, the paths it may touch).

1. **Root never acts on anything a user can write without an allowlist.** Anything under `$HOME`
   (profiles, backups, `active.json`, logs), environment variables, the current directory and
   command-line arguments are untrusted. Root may only run commands that match an exact shape in
   `helper.valid_args` / `commands.allowed_as_root`; everything else is refused, never "sanitised".
2. **No generic "run as root".** The helper and each polkit action do one narrow thing. A new
   privileged operation = a new helper subcommand with its own validation and tests, never a
   pass-through of argv, a shell string or a path chosen by the user.
3. **Root's Python never imports from places a user controls:** `python3 -I` for the helper and
   the thermal daemon (both stdlib only),
   `-P` plus `cd /` in shell scripts; no `sys.path` tricks (checked by `tests/test_root_rules.py`).
4. **Paths from the user side:** refuse symlinks a non-root user could have planted
   (`engine._symlinked_dir`); write system files atomically (`tmp` + `os.replace`), mode 0644.
5. **Every change touching the files above gets a security review before merging**
   (`/security-review` or an independent agent) and a test in `tests/test_reset_security.py`
   (or next to it) for both an allowed and a refused case.

## Workflow

- Every change goes on its own worktree off `develop` (`../PowerDeck-worktrees/<topic>`), and is
  merged into `develop` only after it is tested. Never merge into `main`; the owner promotes
  develop → main. Push to GitHub only when asked.
- Tests: `python3 -m pytest -q`; without root or the real machine: `scripts/dev-container.sh all`
  (rootless podman: tests, `.deb` install/uninstall, headless GUI screenshot). For systemd,
  logind, pkexec, the guard service and the lid: `scripts/dev-vm.sh start|test|lid close|ssh`
  (QEMU/KVM GNOME VM, see README) before asking the owner to test on the laptop.
- Never type the owner's `sudo`/`pkexec` passwords; the owner runs `sudo …` and confirms pkexec.
- Before testing on the real laptop: `diff powerdeck/helper.py /usr/libexec/powerdeck-helper`
  (and `powerdeck/thermal_limit.py` against `/usr/libexec/powerdeck-thermal`)
  — if they differ, the owner must run `sudo scripts/install-helper.sh` first.
  After a test `apply`, always `restore`.
- For a feature, write 2–3 acceptance criteria and what must *not* happen; turn them into tests.
  Record decisions in the decision log of `SPECIFICATION.md`.
