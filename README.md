# PowerDeck

Linux workstation profiles for Ubuntu/GNOME. See [SPECIFICATION.md](SPECIFICATION.md).

## Status (v0.3 – untested on real hardware)

Profiles for lid, auto-suspend, Wi‑Fi powersave, tethering priority, power mode, display timers/brightness/night light, Bluetooth and whitelisted user-service tasks; thermal/battery guard; GTK4 app with profile editor; CLI; `.deb` build. See [TODO.md](TODO.md) for what is left and the real-hardware checklist.

## Install admin helper (needed for lid settings)

```bash
sudo scripts/install-helper.sh            # remove with: sudo scripts/install-helper.sh --uninstall
```

Installs a root-owned `/usr/libexec/powerdeck-helper` and a polkit policy. The helper can only write/remove one logind drop-in with whitelisted values.

## Build a .deb

```bash
scripts/build-deb.sh && sudo apt install ./dist/powerdeck_*.deb   # installs app, helper, polkit policy, desktop entry
```

## Run

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
python3 -m powerdeck.gtk_app        # GUI
python3 -m powerdeck.cli monitor --csv out.csv --duration 60   # temperature/frequency/load/power log
python3 -m powerdeck.cli list       # CLI: list | status | plan NAME | apply NAME | restore | snapshot | guard
pytest                              # tests (fake system, no root needed)
```

Nothing is changed without showing the exact diff and asking for confirmation; every apply writes a backup to `~/.local/state/powerdeck/backups/` first, and `restore` undoes it.
