#!/bin/sh
# Return the system to its pre-PowerDeck state. Run as root BEFORE PowerDeck's files are removed
# (Debian prerm "remove", or scripts/install-helper.sh --uninstall). Never fails the uninstall.
#
# 1. per user: revert every applied profile – system parts as root (NetworkManager, power mode,
#    Bluetooth, brightness, lid, CPU), then gsettings/tasks as that user (their own dconf);
# 2. stop and disable the guard service;
# 3. safety net: remove the lid drop-in, turbo on and no CPU frequency cap (kernel defaults).
PY=${POWERDECK_PYTHON:-/usr/bin/python3}
# Python runs as root here: never import from the current directory (a planted powerdeck/ or
# json.py would run as root). `-P` keeps the cwd off sys.path, `cd /` makes sure of it.
# (PYTHONPATH from the caller is kept, also for runuser, so a source checkout works too)
cd / || exit 0
HELPER=${POWERDECK_HELPER:-/usr/libexec/powerdeck-helper}
PASSWD=${POWERDECK_PASSWD:-/etc/passwd}

getent_users() { awk -F: '$3 >= 1000 && $3 < 65534 { print $1 ":" $3 ":" $6 }' "$PASSWD"; }

getent_users | while IFS=: read -r user uid home; do
  backups="$home/.local/state/powerdeck/backups"
  [ -d "$home/.local/state/powerdeck" ] || continue
  echo "PowerDeck: restoring settings of $user"
  if ls "$backups"/*.json >/dev/null 2>&1; then
    # system part (root): pkexec is dropped, the helper runs directly
    "$PY" -P -m powerdeck.cli reset-all --only system --no-mark --backups "$backups" || true
    # user part: gsettings need the user's D-Bus; use the running session or a private one
    if [ -S "/run/user/$uid/bus" ]; then
      runuser -u "$user" -- env HOME="$home" XDG_RUNTIME_DIR="/run/user/$uid" \
        DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" \
        "$PY" -P -m powerdeck.cli reset-all --only user --backups "$backups" || true
    else
      runuser -u "$user" -- env HOME="$home" dbus-run-session -- \
        "$PY" -P -m powerdeck.cli reset-all --only user --backups "$backups" || true
    fi
  fi
  # guard service: stop it in a running session, and remove the enable link in any case
  systemctl --user -M "$user@" disable --now powerdeck-guard.service >/dev/null 2>&1 || true
  rm -f "$home/.config/systemd/user/default.target.wants/powerdeck-guard.service"
  pkill -u "$uid" -f 'powerdeck(\.cli)? guard' >/dev/null 2>&1 || true
done

if [ -x "$HELPER" ]; then
  "$HELPER" remove-lid || true
  "$HELPER" set-cpu-turbo on >/dev/null 2>&1 || true
  "$HELPER" set-cpu-max-percent 100 >/dev/null 2>&1 || true
fi
exit 0
