#!/bin/sh
# Runs inside a distribution container (as root) after its packages are installed; prints one
# line per check: "OK|WARN|FAIL <what>". Started by scripts/compat-matrix.sh with DISTRO and
# DESKTOP (the XDG_CURRENT_DESKTOP of that distribution's session).
cp -a /repo /tmp/src && cd /tmp/src || exit 1
id dev >/dev/null 2>&1 || useradd -m dev
chown -R dev /tmp/src
r() { echo "$1 $2"; }
# as the desktop user, in a D-Bus session of that desktop
as_dev() { su dev -c "cd /tmp/src && XDG_CURRENT_DESKTOP=$DESKTOP dbus-run-session -- sh -c '$1'"; }

. /etc/os-release && r INFO "distro: $PRETTY_NAME, desktop $DESKTOP"
if su dev -c "python3 -m pytest -q -p no:cacheprovider" >/tmp/pytest.log 2>&1; then
    r OK "tests: $(tail -1 /tmp/pytest.log)"
else
    r FAIL "tests: $(tail -1 /tmp/pytest.log)"; grep -E "^(FAILED|ERROR)" /tmp/pytest.log | head -15
fi
if as_dev "python3 -m powerdeck.cli list >/dev/null && python3 -m powerdeck.cli plan Rucksack" >/tmp/plan.log 2>&1; then
    r OK "CLI list/plan"; sed 's/^/    /' /tmp/plan.log | head -24
else
    r FAIL "CLI list/plan"; tail -5 /tmp/plan.log
fi
if [ "$DESKTOP" = X-Cinnamon ]; then
    grep -q "Cinnamon lid close (ac)" /tmp/plan.log && r OK "plan has Cinnamon's own lid setting" \
        || r FAIL "plan has no Cinnamon lid setting"
fi

# a real apply + restore of the desktop settings (no root parts) as the user
SCHEMA=org.gnome.settings-daemon.plugins.power
[ "$DESKTOP" = X-Cinnamon ] && SCHEMA=org.cinnamon.settings-daemon.plugins.power
cat > /tmp/apply.sh <<EOF
python3 -c "from powerdeck.profile import Profile, save_profile
save_profile(Profile(name=\"CompatTest\", automatic_suspend={\"battery\": False, \"ac\": False},
                     display={\"blank_after_minutes\": 7}))" &&
before=\$(gsettings get $SCHEMA sleep-inactive-ac-type) &&
python3 -m powerdeck.cli apply CompatTest -y >/dev/null &&
during=\$(gsettings get $SCHEMA sleep-inactive-ac-type) &&
python3 -m powerdeck.cli restore >/dev/null &&
after=\$(gsettings get $SCHEMA sleep-inactive-ac-type) &&
echo "\$before -> \$during -> \$after"
EOF
chmod 0644 /tmp/apply.sh
res=$(as_dev "sh /tmp/apply.sh" 2>&1 | grep -v "^dbus-daemon" | tail -3)
case "$res" in
    "'suspend' -> 'nothing' -> 'suspend'") r OK "apply/restore changes $SCHEMA ($res)" ;;
    *) r FAIL "apply/restore of $SCHEMA: $res" ;;
esac

if as_dev "GTK_A11Y=none xvfb-run -a -s \"-screen 0 1100x1500x24\" python3 scripts/screenshot.py /tmp/$DISTRO.png --active RemoteWork --select Rucksack --history" >/tmp/gui.log 2>&1; then
    cp /tmp/$DISTRO.png /out/ && r OK "GUI renders (dist/compat/$DISTRO.png)"
else
    r FAIL "GUI"; grep -v libEGL /tmp/gui.log | tail -5
fi

# install as a package (.deb) or with scripts/install.sh, check with doctor, remove again
doctor() {
    as_dev "cd / && powerdeck doctor" >/tmp/doctor.log 2>&1; rc=$?
    sed 's/^/    /' /tmp/doctor.log
    # what a container lacks: a running guard, a top-bar icon host
    bad=$(grep -E "^(WARN|FAIL)" /tmp/doctor.log | grep -vE "guard service not running|no top-bar icon host")
    if [ $rc -eq 0 ] && [ -z "$bad" ]; then r OK "powerdeck doctor: no problems"; else r FAIL "powerdeck doctor: $bad"; fi
}
leftovers() {
    left=$(find /usr /etc -path /usr/share/doc -prune -o \( -name '*powerdeck*' -o -name 'io.github.powerdeck*' \) -print 2>/dev/null)
    [ -z "$left" ] && r OK "$1 leaves no files" || r FAIL "$1 left: $left"
}
if command -v dpkg-deb >/dev/null && command -v apt-get >/dev/null; then
    scripts/build-deb.sh >/dev/null 2>&1
    if apt-get install -y -qq ./dist/powerdeck_*.deb >/tmp/deb.log 2>&1; then
        r OK ".deb installs (dependencies resolve)"
        as_dev "cd / && powerdeck list >/dev/null" && r OK "installed powerdeck runs" || r FAIL "installed powerdeck runs"
        doctor
        apt-get purge -y -qq powerdeck >/dev/null 2>&1 && r OK ".deb removes" || r FAIL ".deb removes"
        leftovers ".deb removal"
    else
        r FAIL ".deb install"; tail -8 /tmp/deb.log
    fi
else
    # the distribution's own package: .rpm (dnf) or Arch package (pacman)
    if command -v dnf >/dev/null; then
        build="scripts/build-rpm.sh"; inst="dnf -y -q install ./dist/powerdeck-*.rpm"; rm_="dnf -y -q remove powerdeck"; kind=.rpm
    else
        chown -R dev /tmp/src
        build="su dev -c scripts/build-arch.sh"; inst="pacman -U --noconfirm ./dist/powerdeck-*.pkg.tar.zst"; rm_="pacman -R --noconfirm powerdeck"; kind="Arch package"
    fi
    if $build >/tmp/build.log 2>&1 && $inst >/tmp/pkg.log 2>&1; then
        r OK "$kind builds and installs (dependencies resolve)"
        as_dev "cd / && powerdeck list >/dev/null" && r OK "installed powerdeck runs" || r FAIL "installed powerdeck runs"
        doctor
        $rm_ >/tmp/pkgrm.log 2>&1 && r OK "$kind removes" || { r FAIL "$kind removes"; tail -5 /tmp/pkgrm.log; }
        leftovers "$kind removal"
    else
        r FAIL "$kind build/install"; tail -8 /tmp/build.log /tmp/pkg.log 2>/dev/null
    fi
    if scripts/install.sh >/tmp/install.log 2>&1 && scripts/install.sh >>/tmp/install.log 2>&1; then
        r OK "install.sh installs (and updates): $(head -1 /tmp/install.log)"
        as_dev "cd / && powerdeck list >/dev/null" && r OK "installed powerdeck runs" || r FAIL "installed powerdeck runs"
        doctor
        scripts/install.sh --uninstall >/tmp/uninstall.log 2>&1 && r OK "install.sh --uninstall" \
            || { r FAIL "install.sh --uninstall"; tail -5 /tmp/uninstall.log; }
        leftovers "install.sh --uninstall"
    else
        r FAIL "install.sh"; tail -8 /tmp/install.log
    fi
fi
