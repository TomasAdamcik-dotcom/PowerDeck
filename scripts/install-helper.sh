#!/bin/sh
# Install (or with --uninstall remove) the PowerDeck privileged helper + polkit policy.
# Run from the repository root:  sudo scripts/install-helper.sh
set -eu
HELPER=/usr/libexec/powerdeck-helper
POLICY=/usr/share/polkit-1/actions/io.github.powerdeck.policy
THERMAL=/usr/libexec/powerdeck-thermal
UNIT=/usr/lib/systemd/system/powerdeck-thermal@.service

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo $0 $*" >&2
  exit 1
fi

if [ "${1:-}" = "--uninstall" ]; then
  # first put every setting back the way it was before PowerDeck (needs the helper still present)
  export PYTHONPATH="$(cd "$(dirname "$0")/.." && pwd)${PYTHONPATH:+:$PYTHONPATH}"
  sh "$(dirname "$0")/uninstall-reset.sh"
  systemctl stop 'powerdeck-thermal@*.service' 2>/dev/null || true
  rm -f "$HELPER" "$POLICY" "$THERMAL" "$UNIT" /usr/libexec/powerdeck-suspend \
    /etc/systemd/logind.conf.d/99-powerdeck-lid.conf
  systemctl daemon-reload 2>/dev/null || true
  systemctl kill -s HUP systemd-logind 2>/dev/null || true
  echo "Settings restored; removed helper, policy, thermal service and lid drop-in."
  exit 0
fi

cd "$(dirname "$0")/.."
# root:root 0755 – a user-writable helper run as root would be a privilege escalation
install -D -m 0755 -o root -g root powerdeck/helper.py "$HELPER"
install -D -m 0755 -o root -g root data/powerdeck-suspend /usr/libexec/powerdeck-suspend
install -D -m 0755 -o root -g root powerdeck/thermal_limit.py "$THERMAL"
install -D -m 0644 -o root -g root data/powerdeck-thermal@.service "$UNIT"
install -D -m 0644 -o root -g root data/io.github.powerdeck.policy "$POLICY"
systemctl daemon-reload 2>/dev/null || true
echo "Installed $HELPER, $THERMAL, $UNIT and $POLICY"
