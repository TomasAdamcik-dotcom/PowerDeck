#!/bin/sh
# Test PowerDeck's system side on other distributions in throw-away headless VMs (QEMU/KVM):
# the native package, pkexec + polkit, the root helper, logind and the lid, the power-mode daemon,
# the guard as a user service and the uninstall reset. No GNOME (scripts/dev-vm.sh is the Ubuntu
# GNOME VM); no display, so logind acts on the lid itself (no external monitor = not docked).
#
#   scripts/distro-vm.sh debian|fedora create   download + verify the cloud image, install packages
#   scripts/distro-vm.sh debian|fedora test     sync this tree, run the whole test, print OK/FAIL
#   scripts/distro-vm.sh debian|fedora start|stop|ssh [cmd]|destroy
#
# Inside the VM the user "dev" has passwordless sudo and polkit (test VM only).
# Files: $POWERDECK_VM_DIR-<distro> (default ~/.local/share/powerdeck-vm-<distro>).
set -eu
cd "$(dirname "$0")/.."
SRC="$(pwd)"
D=${1:-}; shift || true
case "$D" in
  debian)
    IMG_DIR=https://cloud.debian.org/images/cloud/trixie/latest
    IMG=debian-13-genericcloud-amd64.qcow2; SUMS=SHA512SUMS; SUM=sha512sum
    SSH_PORT=2227; SEED_PORT=8801; ADMIN=sudo
    VGA="-vga std"  # Debian's GRUB resets the VM without a video card; the guest blacklists its DRM driver
    PKGS="python3 python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 python3-pytest network-manager pkexec polkitd power-profiles-daemon dbus-user-session gsettings-desktop-schemas gnome-settings-daemon-common dconf-gsettings-backend dpkg-dev rfkill iproute2 libnotify-bin"
    INSTALL_PKGS="apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $PKGS linux-image-amd64 \
        && apt-get purge -y -qq linux-image-cloud-amd64 'linux-image-*-cloud-amd64'" ;;  # the cloud kernel has no uinput (fake lid)
  fedora)
    IMG_DIR=https://download.fedoraproject.org/pub/fedora/linux/releases/45/Cloud/x86_64/images
    IMG=Fedora-Cloud-Base-Generic-45-1.4.x86_64.qcow2; SUMS=Fedora-Cloud-45-1.4-x86_64-CHECKSUM; SUM=sha256sum
    SSH_PORT=2228; SEED_PORT=8802; ADMIN=wheel
    VGA="-vga none"
    PKGS="python3 python3-gobject python3-cairo gtk4 libadwaita python3-pytest NetworkManager polkit tuned-ppd gsettings-desktop-schemas gnome-settings-daemon dconf rpm-build python3-devel util-linux iproute libnotify kernel-modules"
    INSTALL_PKGS="dnf -y -q install $PKGS" ;;
  *) sed -n '2,13p' "$0"; exit 2 ;;
esac
DIR=${POWERDECK_VM_DIR:-$HOME/.local/share/powerdeck-vm}-$D
KEY="$DIR/id_ed25519"
PID="$DIR/qemu.pid"

vssh() {
    ssh -q -i "$KEY" -p "$SSH_PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile="$DIR/known_hosts" \
        -o ConnectTimeout=5 -o BatchMode=yes dev@127.0.0.1 "$@"
}
running() { [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; }
wait_ssh() {
    i=0
    until vssh true 2>/dev/null; do
        i=$((i + 5)); [ "$i" -le "$1" ] || { echo "VM: no SSH after $1 s" >&2; return 1; }
        running || { echo "VM: qemu exited, see $DIR/serial.log" >&2; return 1; }
        sleep 5
    done
}
boot() {
    running && return 0
    # no display connector (no video card, or its DRM driver blacklisted), so logind never counts
    # the VM as docked (an external monitor) and acts on the lid
    # shellcheck disable=SC2086
    qemu-system-x86_64 -name "powerdeck-$D" -machine q35,accel=kvm -cpu host -smp 2 -m 2048 \
        -drive file="$DIR/disk.qcow2",if=virtio,discard=unmap \
        -nic user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:$SSH_PORT-:22 \
        $VGA -display none -serial file:"$DIR/serial.log" \
        -qmp unix:"$DIR/qmp.sock",server=on,wait=off -daemonize -pidfile "$PID" "$@"
}
qmp() {
    python3 - "$DIR/qmp.sock" "$1" <<'EOF'
import json, socket, sys
s = socket.socket(socket.AF_UNIX); s.settimeout(5); s.connect(sys.argv[1]); f = s.makefile("rw")
f.readline()
for cmd in ("qmp_capabilities", sys.argv[2]):
    f.write(json.dumps({"execute": cmd}) + "\n"); f.flush()
    while "return" not in (r := json.loads(f.readline())) and "error" not in r:
        pass
print(json.dumps(r.get("return", r)))
EOF
}
vm_state() { qmp query-status | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status", "?"))'; }

cmd_create() {
    [ -w /dev/kvm ] || { echo "/dev/kvm not writable" >&2; exit 1; }
    [ -e "$DIR/disk.qcow2" ] && { echo "VM exists; 'destroy' first" >&2; exit 1; }
    mkdir -p "$DIR"
    if [ ! -f "$DIR/base.img" ]; then
        curl -fsSL -o "$DIR/sums" "$IMG_DIR/$SUMS"
        echo "Downloading $IMG_DIR/$IMG"
        curl -fL -o "$DIR/$IMG" "$IMG_DIR/$IMG"
        (cd "$DIR" && grep -F "$IMG" sums | grep -v '^#' > sums.mine && $SUM -c --quiet sums.mine)
        mv "$DIR/$IMG" "$DIR/base.img"
    fi
    [ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N "" -C "powerdeck-$D" -f "$KEY"
    qemu-img create -q -f qcow2 -b "$DIR/base.img" -F qcow2 "$DIR/disk.qcow2" 12G
    rm -rf "$DIR/seed" && mkdir -p "$DIR/seed"
    cat > "$DIR/seed/user-data" <<EOF
#cloud-config
hostname: powerdeck-$D
users:
  - name: dev
    groups: [$ADMIN, input]
    shell: /bin/bash
    sudo: "ALL=(ALL) NOPASSWD:ALL"
    lock_passwd: true
    ssh_authorized_keys: ["$(cat "$KEY.pub")"]
ssh_pwauth: false
write_files:
  - path: /etc/modprobe.d/powerdeck-vm-no-drm.conf
    content: |
      # test VM: no DRM display connector, so logind does not treat the VM as docked
      blacklist bochs
      blacklist bochs_drm
      blacklist simpledrm
  - path: /etc/polkit-1/rules.d/49-powerdeck-vm-dev.rules
    content: |
      // test VM only: "dev" may do anything without a password (pkexec included)
      polkit.addRule(function (action, subject) {
          if (subject.user == "dev") return polkit.Result.YES;
      });
runcmd:
  - [sh, -c, "$INSTALL_PKGS >/var/log/powerdeck-vm-pkgs.log 2>&1"]
  - [loginctl, enable-linger, dev]
EOF
    printf 'instance-id: powerdeck-%s-%s\nlocal-hostname: powerdeck-%s\n' "$D" "$(date +%s)" "$D" > "$DIR/seed/meta-data"
    : > "$DIR/seed/vendor-data"
    printf 'version: 2\nethernets:\n  nic:\n    match: {name: "en*"}\n    dhcp4: true\n' > "$DIR/seed/network-config"
    python3 -m http.server -b 127.0.0.1 -d "$DIR/seed" "$SEED_PORT" >"$DIR/seed.log" 2>&1 &
    seed=$!
    trap 'kill $seed 2>/dev/null || true' EXIT
    boot -smbios "type=1,serial=ds=nocloud;s=http://10.0.2.2:$SEED_PORT/"
    echo "VM booting; cloud-init installs packages (log: $DIR/serial.log)…"
    wait_ssh 600
    vssh 'sudo cloud-init status --wait >/dev/null; cloud-init status; tail -3 /var/log/powerdeck-vm-pkgs.log'
    provision
    vssh 'sudo systemctl reboot' || true   # the DRM blacklist and new kernel modules take effect
    sleep 10; wait_ssh 300
    echo "VM ready: scripts/distro-vm.sh $D test"
}

provision() {  # fake lid (uinput SW_LID), the same as in scripts/dev-vm.sh
    vssh 'sudo tee /usr/local/sbin/powerdeck-fakelid >/dev/null && sudo chmod 755 /usr/local/sbin/powerdeck-fakelid' \
        < scripts/vm/fakelid.py
    vssh 'sudo tee /etc/systemd/system/powerdeck-fakelid.service >/dev/null && sudo systemctl daemon-reload \
          && sudo systemctl enable -q --now powerdeck-fakelid.service' <<'EOF'
[Unit]
Description=PowerDeck test VM: fake lid switch (uinput SW_LID)
Before=systemd-logind.service

[Service]
ExecStartPre=/sbin/modprobe uinput
ExecStart=/usr/local/sbin/powerdeck-fakelid

[Install]
WantedBy=multi-user.target
EOF
}

cmd_start() { boot; wait_ssh 300; echo "VM up"; }
cmd_stop() {
    running || { echo "VM not running"; return 0; }
    vssh 'sudo systemctl poweroff' 2>/dev/null || true
    i=0; while running && [ $i -lt 60 ]; do sleep 2; i=$((i + 2)); done
    running && kill "$(cat "$PID")"
    rm -f "$PID"; echo "VM stopped"
}
cmd_destroy() { cmd_stop; rm -f "$DIR/disk.qcow2" "$DIR/known_hosts" "$DIR/serial.log"; echo "VM disk deleted"; }

r() { echo "$1 $2"; }
lid() {  # lid close|open -> "suspended" or "running"; restarts a suspended VM and opens the lid again
    # logind ignores the lid for 30 s after boot/resume (HoldoffTimeoutSec), then acts: watch 40 s.
    # A suspended VM is powered off and booted again, not woken: these guests reset themselves
    # ~25 s after an S3 wake-up (QEMU/KVM, no kernel message; a QMP system_reset is a wake-up
    # too), which would break the next check at a random point.
    vssh "echo $1 | sudo tee /run/fakelid >/dev/null"
    st=running; i=0
    while [ $i -lt 40 ]; do
        sleep 2; i=$((i + 2))
        [ "$(vm_state)" = suspended ] && { st=suspended; break; }
    done
    if [ "$st" = suspended ]; then
        qmp quit >/dev/null 2>&1
        while running; do sleep 1; done
        rm -f "$PID"; boot; wait_ssh 300; vssh 'systemctl is-system-running --wait' >/dev/null
    fi
    vssh "echo open | sudo tee /run/fakelid >/dev/null"
    sleep 2
    echo "$st"
}
mode() {  # the active power mode, without PowerDeck (also after its removal)
    vssh "powerprofilesctl get 2>/dev/null || gdbus call --system --dest org.freedesktop.UPower.PowerProfiles \
          --object-path /org/freedesktop/UPower/PowerProfiles --method org.freedesktop.DBus.Properties.Get \
          org.freedesktop.UPower.PowerProfiles ActiveProfile | grep -oE '[a-z-]{4,}'"
}

cmd_test() {
    set +e   # report every check, do not stop at the first failing one
    running || cmd_start
    tar -C "$SRC" --exclude=.git --exclude=dist --exclude=__pycache__ --exclude=.pytest_cache -cf - . \
        | vssh 'rm -rf ~/PowerDeck && mkdir ~/PowerDeck && tar -C ~/PowerDeck -xf -'
    provision
    vssh '. /etc/os-release; echo "INFO $PRETTY_NAME, kernel $(uname -r), display connectors: $(ls /sys/class/drm | grep -c -- -) "'
    out=$(vssh 'cd ~/PowerDeck && python3 -m pytest -q -p no:cacheprovider 2>&1 | tail -1')
    case "$out" in *failed*|*error*) r FAIL "tests: $out" ;; *) r OK "tests: $out" ;; esac
    if [ "$D" = debian ]; then
        build='scripts/build-deb.sh >/dev/null && sudo apt-get install -y -qq ./dist/powerdeck_*.deb >/dev/null 2>&1'
        remove='sudo apt-get purge -y -qq powerdeck >/dev/null 2>&1'
    else
        build='scripts/build-rpm.sh >/dev/null 2>&1 && sudo dnf -y -q install ./dist/powerdeck-*.noarch.rpm >/dev/null 2>&1'
        remove='sudo dnf -y -q remove powerdeck >/dev/null 2>&1'
    fi
    vssh "$remove; cd ~/PowerDeck && rm -rf dist && $build" && r OK "native package builds and installs" \
        || { r FAIL "native package build/install"; return 1; }
    vssh 'cd / && powerdeck doctor' | sed 's/^/    /'
    vssh 'sudo systemctl reset-failed; rm -rf ~/.local/state/powerdeck ~/.config/powerdeck'

    # the lid without PowerDeck: logind suspends
    [ "$(lid close)" = suspended ] && r OK "lid closed without PowerDeck: suspends" || r FAIL "lid closed without PowerDeck: no suspend"
    m0=$(mode)
    # RemoteWork: lid ignored (root helper via pkexec, logind reloaded), power mode balanced
    vssh 'cd / && powerdeck apply RemoteWork -y >/tmp/apply.log 2>&1' && r OK "apply RemoteWork (pkexec + helper)" \
        || { r FAIL "apply RemoteWork"; vssh 'tail -5 /tmp/apply.log'; }
    vssh 'grep -q "HandleLidSwitch=ignore" /etc/systemd/logind.conf.d/99-powerdeck-lid.conf' \
        && r OK "lid drop-in written by the helper" || r FAIL "no lid drop-in"
    [ "$(lid close)" = running ] && r OK "RemoteWork: lid closed, VM keeps running" || r FAIL "RemoteWork: lid closed, VM suspended"
    # Travel: power-saver through the distribution's daemon (Fedora: tuned-ppd D-Bus API)
    vssh 'cd / && powerdeck apply Travel -y >/tmp/apply.log 2>&1' || { r FAIL "apply Travel"; vssh 'tail -5 /tmp/apply.log'; }
    m1=$(mode)
    [ "$m1" = power-saver ] && r OK "power mode $m0 -> $m1" || r FAIL "power mode after Travel: $m1 (before: $m0)"
    vssh 'cd / && powerdeck restore >/dev/null 2>&1' || r FAIL "powerdeck restore"
    m2=$(mode)
    [ "$m2" = "$m0" ] && r OK "restore: power mode back to $m2" || r FAIL "restore: power mode $m2, was $m0"
    vssh 'test ! -e /etc/systemd/logind.conf.d/99-powerdeck-lid.conf' && r OK "restore: lid drop-in removed" || r FAIL "restore: lid drop-in still there"
    [ "$(lid close)" = suspended ] && r OK "after restore: lid suspends again" || r FAIL "after restore: lid does not suspend"

    # the guard as the user's service (no graphical session here: started by hand)
    vssh 'systemctl --user daemon-reload; systemctl --user start powerdeck-guard; sleep 25; systemctl --user is-active powerdeck-guard' >/dev/null \
        && r OK "guard runs as a user service" || r FAIL "guard service"
    vssh 'test "$(wc -l < ~/.local/state/powerdeck/history.csv)" -ge 2' && r OK "guard records history" || r FAIL "guard records no history"

    # removal while a profile is active: the package's uninstall reset puts everything back
    vssh 'cd / && powerdeck apply Travel -y >/dev/null 2>&1'
    vssh "$remove" && r OK "package removes" || r FAIL "package removal"
    m3=$(mode)
    [ "$m3" = "$m0" ] && r OK "removal restored power mode ($m3)" || r FAIL "removal: power mode $m3, was $m0"
    vssh 'test ! -e /etc/systemd/logind.conf.d/99-powerdeck-lid.conf' && r OK "removal removed the lid drop-in" || r FAIL "removal left the lid drop-in"
    left=$(vssh 'sudo find /usr /etc -path /usr/share/doc -prune -o \( -name "*powerdeck*" -o -name "io.github.powerdeck*" \) -print | grep -vE "fakelid|powerdeck-vm"')
    [ -z "$left" ] && r OK "removal leaves no files" || r FAIL "removal left: $left"
    vssh 'systemctl --user is-active powerdeck-guard >/dev/null' && r FAIL "guard still running after removal" || r OK "guard stopped by removal"
}

case "${1:-}" in
    create|start|stop|test|destroy) "cmd_$1" ;;
    ssh) shift; vssh -t "$@" ;;
    *) sed -n '2,13p' "$0"; exit 2 ;;
esac
