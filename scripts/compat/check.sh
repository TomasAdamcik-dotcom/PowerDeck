#!/bin/sh
# Runs inside a distribution container (as root) after its packages are installed; prints one
# line per check: "OK|WARN|FAIL <what>". Started by scripts/compat-matrix.sh.
cp -a /repo /tmp/src && cd /tmp/src || exit 1
id dev >/dev/null 2>&1 || useradd -m dev
chown -R dev /tmp/src
r() { echo "$1 $2"; }

. /etc/os-release && r INFO "distro: $PRETTY_NAME"
r INFO "$(python3 --version 2>&1)"
python3 - <<'PY' 2>&1
import gi
try:
    gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk
    g = (Gtk.get_major_version(), Gtk.get_minor_version(), Gtk.get_micro_version())
    a = (Adw.get_major_version(), Adw.get_minor_version(), Adw.get_micro_version())
    print(("OK" if g >= (4, 10, 0) else "FAIL"), "GTK %d.%d.%d (needs 4.10+)" % g)
    print(("OK" if a >= (1, 5, 0) else "FAIL"), "libadwaita %d.%d.%d (needs 1.5+)" % a)
except Exception as e:
    print("FAIL GTK4/libadwaita:", e)
try:
    gi.require_foreign("cairo"); print("OK PyGObject cairo support")
except Exception as e:
    print("FAIL PyGObject cairo support:", e)
PY
for t in systemctl loginctl pkexec nmcli powerprofilesctl gsettings gdbus rfkill ss notify-send brightnessctl; do
    if command -v "$t" >/dev/null; then r OK "tool $t"; else r WARN "tool $t missing"; fi
done
for sk in "org.gnome.settings-daemon.plugins.power sleep-inactive-battery-type" \
          "org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type" \
          "org.gnome.desktop.session idle-delay" "org.gnome.desktop.screensaver lock-enabled" \
          "org.gnome.desktop.screensaver lock-delay" "org.gnome.settings-daemon.plugins.color night-light-enabled" \
          "org.gnome.desktop.remote-desktop.rdp enable"; do
    # shellcheck disable=SC2086
    if gsettings get $sk >/dev/null 2>&1; then r OK "gsettings $sk"; else r WARN "gsettings $sk missing"; fi
done
if su dev -c "python3 -m pytest -q -p no:cacheprovider" >/tmp/pytest.log 2>&1; then
    r OK "tests: $(tail -1 /tmp/pytest.log)"
else
    r FAIL "tests: $(tail -1 /tmp/pytest.log)"; grep -E "^(FAILED|ERROR)" /tmp/pytest.log | head -15
fi
if su dev -c "python3 -m powerdeck.cli list >/dev/null && python3 -m powerdeck.cli plan Rucksack" >/tmp/plan.log 2>&1; then
    r OK "CLI list/plan"; sed 's/^/    /' /tmp/plan.log | head -20
else
    r FAIL "CLI list/plan"; tail -5 /tmp/plan.log
fi
if su dev -c "GTK_A11Y=none dbus-run-session -- xvfb-run -a -s '-screen 0 1100x1500x24' python3 scripts/screenshot.py /tmp/$DISTRO.png --active RemoteWork --select Rucksack --history" >/tmp/gui.log 2>&1; then
    cp /tmp/$DISTRO.png /out/ && r OK "GUI renders (dist/compat/$DISTRO.png)"
else
    r FAIL "GUI"; grep -v libEGL /tmp/gui.log | tail -5
fi
if command -v dpkg-deb >/dev/null && command -v apt-get >/dev/null; then
    scripts/build-deb.sh >/dev/null 2>&1
    if apt-get install -y -qq ./dist/powerdeck_*.deb >/tmp/deb.log 2>&1; then
        r OK ".deb installs (dependencies resolve)"
        su dev -c "cd / && powerdeck list >/dev/null" && r OK "installed powerdeck runs" || r FAIL "installed powerdeck runs"
        apt-get remove -y -qq powerdeck >/dev/null 2>&1 && r OK ".deb removes" || r FAIL ".deb removes"
    else
        r FAIL ".deb install"; tail -8 /tmp/deb.log
    fi
fi
