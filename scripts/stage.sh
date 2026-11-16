#!/bin/sh
# Put PowerDeck's installed files under ROOT, Python package in PYLIB (an absolute path such as
# /usr/lib/python3/dist-packages):  scripts/stage.sh ROOT PYLIB
# Used by build-deb.sh (Debian, Ubuntu, Mint, Zorin) and install.sh (Fedora, Arch, Manjaro, ...).
set -eu
cd "$(dirname "$0")/.."
OUT=$1 PYLIB=$2
case "$PYLIB" in /usr/*) ;; *) echo "PYLIB must be under /usr: $PYLIB" >&2; exit 1 ;; esac
umask 022
for d in usr/bin usr/libexec "${PYLIB#/}/powerdeck" usr/share/polkit-1/actions usr/share/applications \
         usr/share/metainfo usr/share/icons/hicolor/scalable/apps usr/share/icons/hicolor/symbolic/apps \
         usr/lib/systemd/user/graphical-session.target.wants usr/lib/systemd/system etc/xdg/autostart; do
  mkdir -p "$OUT/$d"
done
install -m 0644 powerdeck/*.py "$OUT$PYLIB/powerdeck/"
install -m 0755 powerdeck/helper.py "$OUT/usr/libexec/powerdeck-helper"
install -m 0755 data/powerdeck-suspend "$OUT/usr/libexec/powerdeck-suspend"
install -m 0755 powerdeck/thermal_limit.py "$OUT/usr/libexec/powerdeck-thermal"
install -m 0644 data/powerdeck-thermal@.service "$OUT/usr/lib/systemd/system/"
install -m 0755 scripts/uninstall-reset.sh "$OUT/usr/libexec/powerdeck-uninstall-reset"
install -m 0644 data/io.github.powerdeck.policy "$OUT/usr/share/polkit-1/actions/"
install -m 0644 data/io.github.powerdeck.PowerDeck.desktop "$OUT/usr/share/applications/"
install -m 0644 data/io.github.powerdeck.PowerDeck.metainfo.xml "$OUT/usr/share/metainfo/"
install -m 0644 data/io.github.powerdeck.PowerDeck.svg "$OUT/usr/share/icons/hicolor/scalable/apps/"
install -m 0644 data/powerdeck-history-symbolic.svg "$OUT/usr/share/icons/hicolor/symbolic/apps/"
install -m 0644 data/powerdeck-guard.service "$OUT/usr/lib/systemd/user/"
# enabled for every user by default (like a vendor preset); a user can opt out with
# `systemctl --user mask powerdeck-guard`. Shipped as a symlink, so no maintainer-script code.
ln -sfn ../powerdeck-guard.service "$OUT/usr/lib/systemd/user/graphical-session.target.wants/powerdeck-guard.service"
# top-bar icon at login (GNOME "Startup Apps" can turn it off)
install -m 0644 data/io.github.powerdeck.PowerDeck-autostart.desktop "$OUT/etc/xdg/autostart/io.github.powerdeck.PowerDeck.desktop"
printf '#!/usr/bin/python3\nimport sys\nfrom powerdeck.cli import main\nsys.exit(main())\n' > "$OUT/usr/bin/powerdeck"
printf '#!/usr/bin/python3\nimport sys\nfrom powerdeck.gtk_app import main\nsys.exit(main())\n' > "$OUT/usr/bin/powerdeck-gtk"
chmod 0755 "$OUT/usr/bin/powerdeck" "$OUT/usr/bin/powerdeck-gtk"
