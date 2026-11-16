#!/bin/sh
# Build a .deb:  scripts/build-deb.sh  → dist/powerdeck_<version>_all.deb
# Installs the app, the root-owned helper, thermal service, polkit policy, desktop entry and guard service.
set -eu
cd "$(dirname "$0")/.."
VERSION=$(python3 -c "import re;print(re.search(r'version = \"(.+?)\"', open('pyproject.toml').read()).group(1))")
OUT=dist/pkg
rm -rf "$OUT" && mkdir -p "$OUT/DEBIAN" "$OUT/usr/bin" "$OUT/usr/libexec" \
  "$OUT/usr/lib/python3/dist-packages/powerdeck" "$OUT/usr/share/polkit-1/actions" \
  "$OUT/usr/share/applications" "$OUT/usr/share/metainfo" \
  "$OUT/usr/share/icons/hicolor/scalable/apps" "$OUT/usr/lib/systemd/user" "$OUT/usr/lib/systemd/system"

install -m 0644 powerdeck/*.py "$OUT/usr/lib/python3/dist-packages/powerdeck/"
install -m 0755 powerdeck/helper.py "$OUT/usr/libexec/powerdeck-helper"
install -m 0755 data/powerdeck-suspend "$OUT/usr/libexec/powerdeck-suspend"
install -m 0755 powerdeck/thermal_limit.py "$OUT/usr/libexec/powerdeck-thermal"
install -m 0644 data/powerdeck-thermal@.service "$OUT/usr/lib/systemd/system/"
install -m 0755 scripts/uninstall-reset.sh "$OUT/usr/libexec/powerdeck-uninstall-reset"
install -m 0644 data/io.github.powerdeck.policy "$OUT/usr/share/polkit-1/actions/"
install -m 0644 data/io.github.powerdeck.PowerDeck.desktop "$OUT/usr/share/applications/"
install -m 0644 data/io.github.powerdeck.PowerDeck.metainfo.xml "$OUT/usr/share/metainfo/"
install -m 0644 data/io.github.powerdeck.PowerDeck.svg "$OUT/usr/share/icons/hicolor/scalable/apps/"
install -m 0644 data/powerdeck-guard.service "$OUT/usr/lib/systemd/user/"
printf '#!/usr/bin/python3\nimport sys\nfrom powerdeck.cli import main\nsys.exit(main())\n' > "$OUT/usr/bin/powerdeck"
printf '#!/usr/bin/python3\nimport sys\nfrom powerdeck.gtk_app import main\nsys.exit(main())\n' > "$OUT/usr/bin/powerdeck-gtk"
chmod 0755 "$OUT/usr/bin/powerdeck" "$OUT/usr/bin/powerdeck-gtk"

cat > "$OUT/DEBIAN/control" <<CTL
Package: powerdeck
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.11), python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1, pkexec | policykit-1, network-manager
Recommends: power-profiles-daemon, libnotify-bin, brightnessctl, dbus-bin
Maintainer: PowerDeck <noreply@example.invalid>
Description: Linux workstation profiles
 Switch laptop profiles (lid, suspend, Wi-Fi powersave, power mode) with
 confirmation, backup and rollback.
CTL
# postinst: systemd must see the (new) powerdeck-thermal@.service unit
cat > "$OUT/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e
if [ -d /run/systemd/system ]; then
  systemctl daemon-reload || true
fi
POSTINST
chmod 0755 "$OUT/DEBIAN/postinst"
# prerm runs while PowerDeck's files still exist: undo everything it changed (only on removal,
# not on upgrade). uninstall-reset.sh also stops the temperature regulation.
cat > "$OUT/DEBIAN/prerm" <<'PRERM'
#!/bin/sh
set -e
if [ "$1" = "remove" ]; then
  /usr/libexec/powerdeck-uninstall-reset || true
  # in any case, while its ExecStopPost (restores the CPU limits) still exists
  systemctl stop 'powerdeck-thermal@*.service' 2>/dev/null || true
fi
PRERM
chmod 0755 "$OUT/DEBIAN/prerm"
cat > "$OUT/DEBIAN/postrm" <<'RM'
#!/bin/sh
set -e
if [ "$1" = "purge" ] || [ "$1" = "remove" ]; then
  rm -f /etc/systemd/logind.conf.d/99-powerdeck-lid.conf
  systemctl daemon-reload 2>/dev/null || true
fi
RM
chmod 0755 "$OUT/DEBIAN/postrm"
mkdir -p dist
dpkg-deb --root-owner-group --build "$OUT" "dist/powerdeck_${VERSION}_all.deb"
