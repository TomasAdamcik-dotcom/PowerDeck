#!/bin/sh
# Compatibility check on other distributions in rootless podman containers (no VM, no admin
# password): library versions, tools, GNOME settings keys, tests, CLI, headless GUI and, on
# Debian-based systems, .deb install/remove. Results: dist/compat/<distro>.log and .png.
#   scripts/compat-matrix.sh [debian fedora mint manjaro]
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
esac; }
setup() { case $1 in
    # GNOME settings daemon schemas: GNOME desktops ship them; Mint's Cinnamon does not (see below)
    debian) echo "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $DEB_PKGS gnome-settings-daemon-common gnome-remote-desktop" ;;
    mint) echo "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $DEB_PKGS cinnamon-settings-daemon" ;;
    fedora) echo "dnf -y -q install python3 python3-pytest python3-gobject gtk4 libadwaita xorg-x11-server-Xvfb xauth dbus-daemon dbus-x11 glib2 dconf gsettings-desktop-schemas gnome-settings-daemon gnome-remote-desktop NetworkManager polkit util-linux iproute libnotify brightnessctl tuned-ppd systemd shadow-utils" ;;
    manjaro) echo "pacman -Syu --noconfirm -q python python-pytest python-gobject python-cairo gtk4 libadwaita xorg-server-xvfb xorg-xauth dbus glib2 dconf gsettings-desktop-schemas gnome-settings-daemon gnome-remote-desktop networkmanager polkit util-linux iproute2 libnotify brightnessctl power-profiles-daemon >/dev/null" ;;
esac; }
for d in ${*:-debian fedora mint manjaro}; do
    echo "== $d"
    podman run --rm -e DISTRO="$d" -v "$PWD:/repo:ro,Z" -v "$PWD/dist/compat:/out:Z" "$(image "$d")" \
        sh -c "$(setup "$d") >/tmp/setup.log 2>&1 || { echo 'FAIL package setup'; tail -15 /tmp/setup.log; exit 1; }; sh /repo/scripts/compat/check.sh" \
        2>&1 | tee "dist/compat/$d.log" | grep -vE "^(Trying|Getting|Copying|Writing)"
done
