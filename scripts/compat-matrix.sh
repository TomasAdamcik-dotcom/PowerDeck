#!/bin/sh
# Compatibility check on other distributions in rootless podman containers (no VM, no admin
# password): tests, CLI plan, a real apply/restore of the desktop settings, headless GUI, install
# (.deb or scripts/install.sh), `powerdeck doctor` and a clean removal.
# Results: dist/compat/<distro>.log and .png.  A distribution passes when no line is FAIL.
#   scripts/compat-matrix.sh [debian fedora mint manjaro zorin]
# Zorin OS has no container image: "zorin" is its base, Ubuntu 24.04 (Zorin OS 18), with the
# GNOME packages and Zorin's XDG_CURRENT_DESKTOP. Zorin OS 17 (Ubuntu 22.04: GTK 4.6,
# libadwaita 1.1) is too old for PowerDeck.
# Not covered here (needs a real session/VM): systemd/logind, pkexec dialogs, the lid, RDP.
set -u
cd "$(dirname "$0")/.." || exit 1
mkdir -p dist/compat
DEB_PKGS="python3 python3-pytest python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 xvfb xauth dbus dbus-x11 \
 libglib2.0-bin dconf-gsettings-backend gsettings-desktop-schemas network-manager pkexec rfkill iproute2 \
 libnotify-bin brightnessctl dpkg-dev power-profiles-daemon systemd"
image() { case $1 in
    debian) echo docker.io/library/debian:trixie ;;
    fedora) echo registry.fedoraproject.org/fedora:latest ;;
    mint) echo docker.io/linuxmintd/mint22.2-amd64 ;;
    manjaro) echo docker.io/manjarolinux/base:latest ;;
    zorin) echo docker.io/library/ubuntu:24.04 ;;
esac; }
desktop() { case $1 in mint) echo X-Cinnamon ;; zorin) echo zorin:GNOME ;; *) echo GNOME ;; esac; }
setup() { case $1 in
    # GNOME settings daemon schemas: GNOME desktops ship them; Mint's Cinnamon does not (see below)
    debian|zorin) echo "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $DEB_PKGS gnome-settings-daemon-common gnome-remote-desktop" ;;
    mint) echo "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $DEB_PKGS cinnamon-settings-daemon xrdp" ;;
    fedora) echo "dnf -y -q install python3 python3-pytest python3-gobject gtk4 libadwaita xorg-x11-server-Xvfb xauth dbus-daemon dbus-x11 glib2 dconf gsettings-desktop-schemas gnome-settings-daemon gnome-remote-desktop NetworkManager polkit util-linux iproute libnotify brightnessctl tuned-ppd systemd shadow-utils findutils which" ;;
    manjaro) echo "pacman -Syu --noconfirm -q python python-pytest python-gobject python-cairo gtk4 libadwaita xorg-server-xvfb xorg-xauth dbus glib2 dconf gsettings-desktop-schemas gnome-settings-daemon gnome-remote-desktop networkmanager polkit util-linux iproute2 libnotify brightnessctl power-profiles-daemon findutils >/dev/null" ;;
esac; }
for d in ${*:-debian fedora mint manjaro zorin}; do
    echo "== $d"
    podman run --rm -e DISTRO="$d" -e DESKTOP="$(desktop "$d")" -v "$PWD:/repo:ro,Z" -v "$PWD/dist/compat:/out:Z" "$(image "$d")" \
        sh -c "$(setup "$d") >/tmp/setup.log 2>&1 || { echo 'FAIL package setup'; tail -15 /tmp/setup.log; exit 1; }; sh /repo/scripts/compat/check.sh" \
        2>&1 | tee "dist/compat/$d.log" | grep -vE "^(Trying|Getting|Copying|Writing)"
done
echo "== summary"
for d in ${*:-debian fedora mint manjaro zorin}; do
    if grep -q "^FAIL" "dist/compat/$d.log"; then echo "FAIL $d"; else echo "OK   $d"; fi
done
