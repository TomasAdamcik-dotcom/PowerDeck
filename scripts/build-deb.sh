#!/bin/sh
# Build a .deb:  scripts/build-deb.sh  → dist/powerdeck_<version>_all.deb
# Installs the app, the root-owned helper, thermal service, polkit policy, desktop entry and guard service.
set -eu
umask 022
cd "$(dirname "$0")/.."
VERSION=$(python3 -c "import re;print(re.search(r'version = \"(.+?)\"', open('pyproject.toml').read()).group(1))")
OUT=dist/pkg
rm -rf "$OUT" && mkdir -p "$OUT/DEBIAN"
scripts/stage.sh "$OUT" /usr/lib/python3/dist-packages
echo /etc/xdg/autostart/io.github.powerdeck.PowerDeck.desktop > "$OUT/DEBIAN/conffiles"
# Debian policy: licence of the package in /usr/share/doc/<package>/copyright
install -d "$OUT/usr/share/doc/powerdeck"
cat > "$OUT/usr/share/doc/powerdeck/copyright" <<'COPYRIGHT'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: PowerDeck
Source: https://github.com/TomasAdamcik-dotcom/PowerDeck

Files: *
Copyright: 2026 Tomas Adamcik
License: GPL-3+
 This program is free software: you can redistribute it and/or modify
 it under the terms of the GNU General Public License as published by
 the Free Software Foundation, either version 3 of the License, or
 (at your option) any later version.
 .
 On Debian systems, the full text of the GNU General Public License
 version 3 can be found in the file /usr/share/common-licenses/GPL-3.
COPYRIGHT
chmod 0644 "$OUT/usr/share/doc/powerdeck/copyright"
cat > "$OUT/DEBIAN/control" <<CTL
Package: powerdeck
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.11), python3-gi, python3-gi-cairo, gir1.2-gtk-4.0, gir1.2-adw-1, pkexec | policykit-1, network-manager
Recommends: power-profiles-daemon, libnotify-bin, brightnessctl, dbus-bin
Maintainer: Tomas Adamcik <82325837+TomasAdamcik-dotcom@users.noreply.github.com>
Homepage: https://github.com/TomasAdamcik-dotcom/PowerDeck
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
