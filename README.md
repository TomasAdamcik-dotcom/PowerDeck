# PowerDeck

Linux workstation profiles for Ubuntu/GNOME. See [SPECIFICATION.md](SPECIFICATION.md).

## Status (v0.1 – core + GTK4 app)

- `powerdeck/` – core (profiles, plan/diff, snapshot, apply, rollback), CLI, GTK4/libadwaita app
- Backends: logind lid drop-in (via `pkexec` helper), GNOME auto-suspend, NetworkManager Wi‑Fi powersave, power-profiles-daemon
- Not yet implemented: see [TODO.md](TODO.md) (untested on real hardware, polkit policy, more backends, profile editor…)

## Install admin helper (needed for lid settings)

```bash
sudo scripts/install-helper.sh            # remove with: sudo scripts/install-helper.sh --uninstall
```

Installs a root-owned `/usr/libexec/powerdeck-helper` and a polkit policy. The helper can only write/remove one logind drop-in with whitelisted values.

## Run

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
python3 -m powerdeck.gtk_app        # GUI
python3 -m powerdeck.cli list       # CLI: list | status | plan NAME | apply NAME | restore
pytest                              # tests (fake system, no root needed)
```

Nothing is changed without showing the exact diff and asking for confirmation; every apply writes a backup to `~/.local/state/powerdeck/backups/` first, and `restore` undoes it.
