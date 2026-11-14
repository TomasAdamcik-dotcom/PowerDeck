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

## Uninstall

`sudo apt remove powerdeck` (or `sudo scripts/install-helper.sh --uninstall`) first puts every setting back the way it was before PowerDeck (`powerdeck reset-all` for each user), stops the guard, then removes the files.

## Run

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
python3 -m powerdeck.gtk_app        # GUI
python3 -m powerdeck.cli monitor --csv out.csv --duration 60   # temperature/frequency/load/power log
python3 -m powerdeck.cli list       # CLI: list | status | plan NAME | apply NAME | restore | snapshot | guard
pytest                              # tests (fake system, no root needed)
```

## Remote desktop with the lid closed

GNOME activates its screensaver when the lid closes (it does so instead of suspending, e.g. while an app such as Claude Desktop blocks suspend), and an active screensaver – locked or not – closes all **desktop sharing** RDP sessions and refuses new ones. Two ways around it:

- **Desktop sharing (your running session):** the `RemoteWork`, `Travel` and `Rucksack` profiles turn the screen lock and idle blanking off, and the guard dismisses the screensaver the moment it appears. The guard must run: `systemctl --user enable --now powerdeck-guard` (installed by the `.deb`), or `python3 -m powerdeck.cli guard` from the repository. The RDP connection still drops once when the lid closes, but you can reconnect right away. While one of these profiles is active, anyone who opens the lid gets an unlocked desktop.
- **Remote Login (recommended for the backpack):** a separate headless GNOME session that the lid and the lock screen do not affect. Enable it in *Settings → System → Remote Desktop → Remote Login* (set its own user name and password; it uses port 3389, so desktop sharing moves to another port – check it in the same dialog). `powerdeck status` shows `remote_login: enabled` once it is on.

Nothing is changed without showing the exact diff and asking for confirmation; every apply writes a backup to `~/.local/state/powerdeck/backups/` first, and `restore` undoes it.
