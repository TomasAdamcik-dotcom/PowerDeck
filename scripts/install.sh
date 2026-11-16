#!/bin/sh
# Install PowerDeck system-wide where there is no .deb (Fedora, Arch, Manjaro, openSUSE, ...):
#   sudo scripts/install.sh               install or update from this source tree
#   sudo scripts/install.sh --uninstall   put every setting back, remove every installed file
# The same files as the .deb (scripts/stage.sh); the list of them is kept in $MANIFEST.
# On Debian, Ubuntu, Mint and Zorin use the .deb instead (scripts/build-deb.sh).
set -eu
umask 022
MANIFEST=/usr/share/powerdeck/installed-files
LIDDROPIN=/etc/systemd/logind.conf.d/99-powerdeck-lid.conf

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo $0 $*" >&2
  exit 1
fi
if command -v dpkg-query >/dev/null 2>&1 && dpkg-query -W -f '${Status}' powerdeck 2>/dev/null | grep -q installed; then
  echo "PowerDeck is installed as a .deb package; use apt to update or remove it." >&2
  exit 1
fi

# Only PowerDeck's own paths are ever written or removed, whatever the manifest says.
. "$(dirname "$0")/install-paths.sh"

remove_listed() {  # remove the files in list $1 (and the Python package's bytecode)
  while IFS= read -r f; do
    ours "$f" || { echo "skipping unexpected path in the file list: $f" >&2; continue; }
    case "$f" in */powerdeck/cli.py) rm -rf -- "${f%/cli.py}/__pycache__" ;; esac
    rm -f -- "$f"
    case "$f" in */powerdeck/*.py) rmdir -- "${f%/*}" 2>/dev/null || true ;; esac
  done < "$1"
}

if [ "${1:-}" = "--uninstall" ]; then
  [ -f "$MANIFEST" ] || { echo "PowerDeck is not installed by this script ($MANIFEST missing)." >&2; exit 1; }
  # first put every setting back the way it was before PowerDeck (needs the installed files);
  # the package's directory explicitly, in case Python was upgraded since (3.14 -> 3.15)
  cli=$(grep -m1 '/powerdeck/cli.py$' "$MANIFEST" || true)
  if [ -n "$cli" ] && ours "$cli"; then
    export PYTHONPATH="${cli%/powerdeck/cli.py}"
  fi
  [ -x /usr/libexec/powerdeck-uninstall-reset ] && /usr/libexec/powerdeck-uninstall-reset || true
  # in any case, while its ExecStopPost (restores the CPU limits) still exists
  systemctl stop 'powerdeck-thermal@*.service' 2>/dev/null || true
  remove_listed "$MANIFEST"
  rm -f -- "$MANIFEST" "$LIDDROPIN"
  rmdir /usr/share/powerdeck 2>/dev/null || true
  systemctl daemon-reload 2>/dev/null || true
  systemctl kill -s HUP systemd-logind 2>/dev/null || true
  echo "PowerDeck removed; settings restored."
  exit 0
fi

cd "$(dirname "$0")/.."
# The Python package goes where /usr/bin/python3 looks for it (Debian: dist-packages).
PYLIB=$(cd / && /usr/bin/python3 -I -c '
import sys, sysconfig
for p in ["/usr/lib/python3/dist-packages",
          sysconfig.get_path("purelib", "posix_prefix", {"base": "/usr", "platbase": "/usr"})]:
    if p in sys.path:
        print(p)
        break
')
[ -n "$PYLIB" ] || { echo "No system site-packages directory of /usr/bin/python3 found." >&2; exit 1; }

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
scripts/stage.sh "$STAGE/root" "$PYLIB"
(cd "$STAGE/root" && find . ! -type d | sed 's|^\.||' | sort) > "$STAGE/files"
while IFS= read -r f; do
  ours "$f" || { echo "internal error: unexpected staged path $f" >&2; exit 1; }
done < "$STAGE/files"

# copy: directories created as needed (existing ones untouched), files and links replaced atomically
while IFS= read -r f; do
  mkdir -p "${f%/*}"
  rm -f -- "$f.powerdeck-new"   # never write through a leftover (symlink)
  cp -P --preserve=mode "$STAGE/root$f" "$f.powerdeck-new"
  mv -f "$f.powerdeck-new" "$f"
done < "$STAGE/files"
# files of an older version that this one no longer has
if [ -f "$MANIFEST" ]; then
  sort "$MANIFEST" | comm -23 - "$STAGE/files" > "$STAGE/gone"
  remove_listed "$STAGE/gone"
fi
mkdir -p "${MANIFEST%/*}"
rm -f -- "$MANIFEST.powerdeck-new"
cp "$STAGE/files" "$MANIFEST.powerdeck-new" && chmod 0644 "$MANIFEST.powerdeck-new"
mv -f "$MANIFEST.powerdeck-new" "$MANIFEST"
systemctl daemon-reload 2>/dev/null || true
gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
echo "PowerDeck installed ($(wc -l < "$MANIFEST") files, Python package in $PYLIB)."
echo "Check what works on this system:  powerdeck doctor"
