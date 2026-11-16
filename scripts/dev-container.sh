#!/bin/sh
# Develop and test PowerDeck in a rootless podman container – no admin password, host untouched.
#
#   scripts/dev-container.sh build   build the image (once, or after container/Containerfile changes)
#   scripts/dev-container.sh test    pytest as an unprivileged user
#   scripts/dev-container.sh deb     build the .deb, install it as (container) root, smoke-test, remove it
#   scripts/dev-container.sh gui     start the GTK app headless (Xvfb) and save a screenshot to dist/
#   scripts/dev-container.sh all     test + deb + gui
#   scripts/dev-container.sh shell   interactive shell (root inside the container)
#
# The repository is mounted read-only; builds happen in a copy inside the container, so no
# root-owned files end up in the working tree. Only dist/ is written (screenshot).
set -eu
cd "$(dirname "$0")/.."
IMAGE=localhost/powerdeck-dev
SRC="$(pwd)"

run() {  # run [podman options...] -- command...
    opts=""
    while [ "$1" != "--" ]; do opts="$opts $1"; shift; done
    shift
    # shellcheck disable=SC2086
    podman run --rm --network=none $opts -v "$SRC:/repo:ro,Z" "$IMAGE" sh -euc "$*"
}

copy='cp -a /repo /tmp/src && cd /tmp/src'

cmd_build() { podman build -t "$IMAGE" -f container/Containerfile container; }

cmd_test() {
    run --user dev -- "$copy && python3 -m pytest -q -p no:cacheprovider"
}

cmd_deb() {
    run -- "$copy && scripts/build-deb.sh >/dev/null && apt-get install -y -qq ./dist/powerdeck_*.deb >/dev/null 2>&1 \
        || dpkg -i ./dist/powerdeck_*.deb
        set -x
        test \"\$(stat -c %U:%a /usr/libexec/powerdeck-helper)\" = root:755
        test \"\$(stat -c %U:%a /usr/libexec/powerdeck-thermal)\" = root:755
        test -f /usr/lib/systemd/system/powerdeck-thermal@.service
        test \"\$(readlink -f /usr/lib/systemd/user/graphical-session.target.wants/powerdeck-guard.service)\" = /usr/lib/systemd/user/powerdeck-guard.service
        grep -qx 'Exec=powerdeck-gtk --background' /etc/xdg/autostart/io.github.powerdeck.PowerDeck.desktop
        python3 -c \"import xml.etree.ElementTree as E; p=[a.text for a in E.parse('/usr/share/polkit-1/actions/io.github.powerdeck.policy').iter('annotate') if a.get('key')=='org.freedesktop.policykit.exec.path']; assert '/usr/libexec/powerdeck-helper' in p, p\"
        su dev -c 'cd / && powerdeck list >/dev/null && powerdeck plan Default >/dev/null'
        /usr/libexec/powerdeck-helper set-lid ignore ignore ignore
        grep -q HandleLidSwitch=ignore /etc/systemd/logind.conf.d/99-powerdeck-lid.conf
        ! /usr/libexec/powerdeck-helper set-lid 'ignore; rm -rf /' ignore ignore
        apt-get remove -y -qq powerdeck >/dev/null 2>&1
        test ! -e /etc/systemd/logind.conf.d/99-powerdeck-lid.conf
        test ! -e /usr/libexec/powerdeck-helper
        test ! -e /usr/libexec/powerdeck-thermal
        test ! -e /usr/lib/systemd/user/graphical-session.target.wants/powerdeck-guard.service
        set +x
        echo 'deb: OK'"
}

cmd_gui() {
    mkdir -p dist
    # keep-id: the container's "dev" user is you on the host, so it can write dist/
    run --user dev --userns=keep-id:uid=1001,gid=1001 -e GTK_A11Y=none -v "$SRC/dist:/out:Z" -- "$copy && \
        dbus-run-session -- xvfb-run -a -s '-screen 0 1100x1500x24' \
        python3 scripts/screenshot.py /out/container-gui.png --active RemoteWork --select Travel \
        && echo 'gui: OK -> dist/container-gui.png'"
}

cmd_shell() { podman run --rm -it -v "$SRC:/repo:ro,Z" "$IMAGE" bash; }

case "${1:-all}" in
    build) cmd_build ;;
    test) cmd_test ;;
    deb) cmd_deb ;;
    gui) cmd_gui ;;
    shell) cmd_shell ;;
    all) cmd_test && cmd_deb && cmd_gui ;;
    *) sed -n '2,12p' "$0"; exit 2 ;;
esac
