#!/bin/sh
# Install (or with --uninstall remove) the PowerDeck privileged helper + polkit policy.
# Run from the repository root:  sudo scripts/install-helper.sh
set -eu
HELPER=/usr/libexec/powerdeck-helper
POLICY=/usr/share/polkit-1/actions/io.github.powerdeck.policy

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo $0 $*" >&2
  exit 1
fi

if [ "${1:-}" = "--uninstall" ]; then
  rm -f "$HELPER" "$POLICY" /etc/systemd/logind.conf.d/99-powerdeck-lid.conf
  echo "Removed helper, policy and lid drop-in (logind picks up the change on next reload/boot)."
  exit 0
fi

cd "$(dirname "$0")/.."
# root:root 0755 – a user-writable helper run as root would be a privilege escalation
install -D -m 0755 -o root -g root powerdeck/helper.py "$HELPER"
install -D -m 0644 -o root -g root data/io.github.powerdeck.policy "$POLICY"
echo "Installed $HELPER and $POLICY"
