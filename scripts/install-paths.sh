# Sourced by scripts/install.sh (root): the only paths it may write or remove.
# ours PATH: true for one of PowerDeck's own installed files, judged by its exact directory and a
# file-name pattern (a name never contains '/', so '*' cannot reach into other directories).
ours() {
  case "$1" in *..*|*:*|*"
"*) return 1 ;; esac
  case "${1%/*}:${1##*/}" in
    /usr/bin:powerdeck|/usr/bin:powerdeck-gtk|/usr/libexec:powerdeck-*|\
    /usr/lib/systemd/system:powerdeck-*|/usr/lib/systemd/user:powerdeck-*|\
    /usr/lib/systemd/user/graphical-session.target.wants:powerdeck-*|\
    /usr/share/polkit-1/actions:io.github.powerdeck.*|/usr/share/applications:io.github.powerdeck.*|\
    /usr/share/metainfo:io.github.powerdeck.*|/usr/share/icons/hicolor/scalable/apps:io.github.powerdeck.*|\
    /usr/share/icons/hicolor/symbolic/apps:powerdeck-*|/etc/xdg/autostart:io.github.powerdeck.*|\
    /usr/lib/python3/dist-packages/powerdeck:*.py|\
    /usr/lib/python3.[0-9]/site-packages/powerdeck:*.py|/usr/lib/python3.[0-9][0-9]/site-packages/powerdeck:*.py|\
    /usr/lib64/python3.[0-9]/site-packages/powerdeck:*.py|/usr/lib64/python3.[0-9][0-9]/site-packages/powerdeck:*.py)
      return 0 ;;
  esac
  return 1
}
