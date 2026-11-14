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
  # first put every setting back the way it was before PowerDeck (needs the helper still present)
  export PYTHONPATH="$(cd "$(dirname "$0")/.." && pwd)${PYTHONPATH:+:$PYTHONPATH}"
  sh "$(dirname "$0")/uninstall-reset.sh"
  rm -f "$HELPER" "$POLICY" /usr/libexec/powerdeck-suspend /etc/systemd/logind.conf.d/99-powerdeck-lid.conf
  systemctl kill -s HUP systemd-logind 2>/dev/null || true
  echo "Settings restored; removed helper, policy and lid drop-in."
  exit 0
fi

cd "$(dirname "$0")/.."
# root:root 0755 – a user-writable helper run as root would be a privilege escalation
install -D -m 0755 -o root -g root powerdeck/helper.py "$HELPER"
install -D -m 0755 -o root -g root data/powerdeck-suspend /usr/libexec/powerdeck-suspend
install -D -m 0644 -o root -g root data/io.github.powerdeck.policy "$POLICY"
echo "Installed $HELPER and $POLICY"
