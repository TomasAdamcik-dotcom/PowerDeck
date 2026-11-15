#!/bin/sh
# Test PowerDeck in a throw-away Ubuntu 26.04 GNOME VM (QEMU/KVM) – no admin password on the host.
# Inside the VM the user "dev" has passwordless sudo and polkit, so pkexec/systemd/logind/the lid
# can be tested for real. Needs: qemu-system-x86_64, qemu-img, membership in the "kvm" group.
#
#   scripts/dev-vm.sh create      download + verify the cloud image, install GNOME (once, ~15 min)
#   scripts/dev-vm.sh start|stop  boot (headless, background) / shut down
#   scripts/dev-vm.sh status      running? lid state, logind/upower view
#   scripts/dev-vm.sh ssh [cmd]   shell (or a command) in the VM as "dev"
#   scripts/dev-vm.sh sync        copy the working tree to ~/PowerDeck in the VM (no .git, no dist)
#   scripts/dev-vm.sh test        sync, pytest, build + install the .deb inside the VM
#   scripts/dev-vm.sh lid close|open   flip the simulated lid switch (uinput SW_LID)
#   scripts/dev-vm.sh destroy     delete the VM disk (keeps the downloaded base image)
#
# Files live in $POWERDECK_VM_DIR (default ~/.local/share/powerdeck-vm). Only 127.0.0.1 ports are
# forwarded: SSH 2226, RDP 13389 (Remote Login) and 13390 (desktop sharing).
#
# Limits: the QEMU display is a "Virtual" connector, i.e. an external monitor for GNOME, so
# gsd-power blocks the lid switch while GNOME runs (test logind alone in multi-user.target).
# After suspend/resume the virtio GPU can hang the guest when GNOME restarts: kill QEMU, start.
set -eu
cd "$(dirname "$0")/.."
SRC="$(pwd)"
DIR=${POWERDECK_VM_DIR:-$HOME/.local/share/powerdeck-vm}
IMG_URL=https://cloud-images.ubuntu.com/releases/26.04/release/ubuntu-26.04-server-cloudimg-amd64.img
IMG_SHA=7b90593f5532b49b7e3fe3d2743f0d1f0d822e5b07cebcd8e00c0df8069ecab9
SSH_PORT=2226
SEED_PORT=8799
MEM=${POWERDECK_VM_MEM:-4096}
CPUS=${POWERDECK_VM_CPUS:-4}
KEY="$DIR/id_ed25519"
PID="$DIR/qemu.pid"

vssh() {
    ssh -q -i "$KEY" -p "$SSH_PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile="$DIR/known_hosts" \
        -o ConnectTimeout=5 -o BatchMode=yes dev@127.0.0.1 "$@"
}

running() { [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; }

wait_ssh() {  # wait_ssh SECONDS
    i=0
    until vssh true 2>/dev/null; do
        i=$((i + 5)); [ "$i" -le "$1" ] || { echo "VM: no SSH after $1 s" >&2; return 1; }
        running || { echo "VM: qemu exited, see $DIR/serial.log" >&2; return 1; }
        sleep 5
    done
}

boot() {  # boot [extra qemu args...]
    running && { echo "VM already running"; return 0; }
    # shellcheck disable=SC2086
    qemu-system-x86_64 -name powerdeck-vm -machine q35,accel=kvm -cpu host -smp "$CPUS" -m "$MEM" \
        -drive file="$DIR/disk.qcow2",if=virtio,discard=unmap \
        -nic user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:$SSH_PORT-:22,hostfwd=tcp:127.0.0.1:13389-:3389,hostfwd=tcp:127.0.0.1:13390-:3390 \
        -device virtio-vga -display none -device qemu-xhci -device usb-tablet \
        -serial file:"$DIR/serial.log" -qmp unix:"$DIR/qmp.sock",server=on,wait=off \
        -daemonize -pidfile "$PID" "$@"
}

qmp() {  # qmp COMMAND  -> prints the "return" JSON (e.g. qmp query-status, qmp system_wakeup)
    python3 - "$DIR/qmp.sock" "$1" <<'EOF'
import json, socket, sys
s = socket.socket(socket.AF_UNIX); s.settimeout(5); s.connect(sys.argv[1]); f = s.makefile("rw")
f.readline()  # greeting
for cmd in ("qmp_capabilities", sys.argv[2]):
    f.write(json.dumps({"execute": cmd}) + "\n"); f.flush()
    while "return" not in (r := json.loads(f.readline())) and "error" not in r:
        pass  # skip async events
print(json.dumps(r.get("return", r)))
EOF
}

vm_state() { qmp query-status | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status", "?"))'; }

user_data() {
    cat <<EOF
#cloud-config
hostname: powerdeck-vm
users:
  - name: dev
    groups: [sudo, adm, input]
    shell: /bin/bash
    sudo: "ALL=(ALL) NOPASSWD:ALL"
    lock_passwd: true
    ssh_authorized_keys: ["$(cat "$KEY.pub")"]
ssh_pwauth: false
package_update: true
write_files:
  - path: /etc/polkit-1/rules.d/49-powerdeck-vm-dev.rules
    content: |
      // test VM only: "dev" may do anything without a password (pkexec included)
      polkit.addRule(function (action, subject) {
          if (subject.user == "dev") return polkit.Result.YES;
      });
  # written after gdm3 is installed (runcmd): a custom.conf present before makes dpkg ask about it
  - path: /etc/powerdeck-vm/gdm-custom.conf
    content: |
      [daemon]
      AutomaticLoginEnable=true
      AutomaticLogin=dev
runcmd:
  - [sh, -c, "DEBIAN_FRONTEND=noninteractive apt-get install -y -o Dpkg::Options::=--force-confold --no-install-recommends ubuntu-desktop-minimal gdm3 gnome-shell gnome-session ubuntu-session gnome-terminal gnome-remote-desktop gnome-settings-daemon upower power-profiles-daemon network-manager python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-pytest libnotify-bin pkexec polkitd brightnessctl iw rfkill dbus-x11 dpkg-dev openssl freerdp3-x11 >/var/log/powerdeck-vm-apt.log 2>&1"]
  - [cp, /etc/powerdeck-vm/gdm-custom.conf, /etc/gdm3/custom.conf]
  - [systemctl, set-default, graphical.target]
EOF
}

cmd_create() {
    command -v qemu-system-x86_64 >/dev/null && command -v qemu-img >/dev/null \
        || { echo "needs qemu-system-x86_64 and qemu-img" >&2; exit 1; }
    [ -w /dev/kvm ] || { echo "/dev/kvm not writable (are you in the kvm group?)" >&2; exit 1; }
    [ -e "$DIR/disk.qcow2" ] && { echo "VM exists ($DIR/disk.qcow2); 'destroy' first" >&2; exit 1; }
    mkdir -p "$DIR"
    if [ ! -f "$DIR/base.img" ]; then
        echo "Downloading $IMG_URL (~860 MB)"
        curl -fL -o "$DIR/base.img.part" "$IMG_URL"
        echo "$IMG_SHA  $DIR/base.img.part" | sha256sum -c --quiet
        mv "$DIR/base.img.part" "$DIR/base.img"
    fi
    [ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N "" -C powerdeck-vm -f "$KEY"
    qemu-img create -q -f qcow2 -b "$DIR/base.img" -F qcow2 "$DIR/disk.qcow2" 16G
    # cloud-init NoCloud over HTTP (10.0.2.2 = the host in QEMU user networking): no ISO tools needed
    rm -rf "$DIR/seed" && mkdir -p "$DIR/seed"
    user_data > "$DIR/seed/user-data"
    printf 'instance-id: powerdeck-vm-%s\nlocal-hostname: powerdeck-vm\n' "$(date +%s)" > "$DIR/seed/meta-data"
    : > "$DIR/seed/vendor-data"
    # without network-config the NoCloud source retries it for 10 s
    printf 'version: 2\nethernets:\n  nic:\n    match: {name: "en*"}\n    dhcp4: true\n' > "$DIR/seed/network-config"
    python3 -m http.server -b 127.0.0.1 -d "$DIR/seed" "$SEED_PORT" >"$DIR/seed.log" 2>&1 &
    seed=$!
    trap 'kill $seed 2>/dev/null || true' EXIT
    boot -smbios "type=1,serial=ds=nocloud;s=http://10.0.2.2:$SEED_PORT/"
    echo "VM booting; installing GNOME via cloud-init (log: $DIR/serial.log)…"
    wait_ssh 600
    vssh 'cloud-init status --wait >/dev/null; cloud-init status'
    kill $seed 2>/dev/null || true
    provision
    echo "Rebooting into GNOME…"
    vssh 'sudo systemctl reboot' || true
    sleep 10
    wait_ssh 300
    cmd_status
}

provision() {  # fake lid service; idempotent, also run by 'sync'
    vssh 'sudo tee /usr/local/sbin/powerdeck-fakelid >/dev/null && sudo chmod 755 /usr/local/sbin/powerdeck-fakelid' \
        < scripts/vm/fakelid.py
    vssh 'sudo tee /etc/systemd/system/powerdeck-fakelid.service >/dev/null && sudo systemctl daemon-reload \
          && sudo systemctl enable -q --now powerdeck-fakelid.service' <<'EOF'
[Unit]
Description=PowerDeck test VM: fake lid switch (uinput SW_LID)
Before=systemd-logind.service upower.service

[Service]
ExecStartPre=/sbin/modprobe uinput
ExecStart=/usr/local/sbin/powerdeck-fakelid

[Install]
WantedBy=multi-user.target
EOF
}

cmd_start() { boot; wait_ssh 300; echo "VM up: scripts/dev-vm.sh ssh"; }

cmd_stop() {
    running || { echo "VM not running"; return 0; }
    vssh 'sudo systemctl poweroff' 2>/dev/null || true
    i=0; while running && [ $i -lt 60 ]; do sleep 2; i=$((i + 2)); done
    running && kill "$(cat "$PID")"
    rm -f "$PID"; echo "VM stopped"
}

cmd_status() {
    running || { echo "VM not running"; return 0; }
    st=$(vm_state); [ "$st" = running ] || { echo "VM state: $st (wake: scripts/dev-vm.sh lid open)"; return 0; }
    vssh 'echo "VM up: $(uptime -p)"; systemctl is-active powerdeck-fakelid >/dev/null && echo "fake lid: active";
          printf "logind LidClosed: "; busctl get-property org.freedesktop.login1 /org/freedesktop/login1 org.freedesktop.login1.Manager LidClosed;
          printf "upower LidIsClosed: "; busctl get-property org.freedesktop.UPower /org/freedesktop/UPower org.freedesktop.UPower LidIsClosed;
          loginctl list-sessions --no-legend'
}

cmd_sync() {
    running || { echo "VM not running ('start' first)" >&2; exit 1; }
    tar -C "$SRC" --exclude=.git --exclude=dist --exclude=__pycache__ --exclude=.pytest_cache -cf - . \
        | vssh 'rm -rf ~/PowerDeck && mkdir ~/PowerDeck && tar -C ~/PowerDeck -xf -'
    provision
    echo "synced to ~/PowerDeck in the VM"
}

cmd_test() {
    cmd_sync
    vssh 'cd ~/PowerDeck && python3 -m pytest -q -p no:cacheprovider | tail -1 \
          && scripts/build-deb.sh >/dev/null && sudo apt-get install -y -qq ./dist/powerdeck_*.deb >/dev/null 2>&1 \
          && cd / && powerdeck list >/dev/null && echo "deb installed: $(dpkg-query -W powerdeck)"'
}

cmd_lid() {
    case "${1:-}" in close|open) ;; *) echo "usage: lid close|open" >&2; exit 2;; esac
    if [ "$(vm_state)" = suspended ]; then
        # a real lid wakes the laptop on open; here the guest's fake lid can't, so wake it first
        echo "VM is suspended (lid closed with HandleLidSwitch=suspend); waking it"
        qmp system_wakeup >/dev/null
        wait_ssh 60
    fi
    vssh "echo $1 | sudo tee /run/fakelid >/dev/null"
    sleep 3
    if [ "$(vm_state)" = suspended ]; then echo "lid $1: VM suspended"; return 0; fi
    printf 'lid %s: VM running, logind LidClosed=' "$1"
    vssh 'busctl get-property org.freedesktop.login1 /org/freedesktop/login1 org.freedesktop.login1.Manager LidClosed'
}

cmd_destroy() { cmd_stop; rm -f "$DIR/disk.qcow2" "$DIR/known_hosts" "$DIR/serial.log"; echo "VM disk deleted"; }

case "${1:-}" in
    create|start|stop|status|sync|test|destroy) "cmd_$1" ;;
    ssh) shift; vssh -t "$@" ;;
    lid) shift; cmd_lid "$@" ;;
    *) sed -n '2,15p' "$0"; exit 2 ;;
esac
