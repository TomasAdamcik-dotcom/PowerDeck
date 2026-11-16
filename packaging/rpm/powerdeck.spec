# Fedora / RHEL package. Build: scripts/build-rpm.sh (in a Fedora container if rpmbuild is missing)
Name:           powerdeck
Version:        1.1.0
Release:        1%{?dist}
Summary:        Laptop profiles for GNOME and Cinnamon
License:        GPL-3.0-or-later
URL:            https://github.com/TomasAdamcik-dotcom/PowerDeck
Source0:        %{url}/archive/refs/tags/v%{version}/PowerDeck-%{version}.tar.gz
BuildArch:      noarch
BuildRequires:  python3-devel
Requires:       python3 >= 3.11
Requires:       python3-gobject
Requires:       python3-cairo
Requires:       gtk4 >= 4.10
Requires:       libadwaita >= 1.5
Requires:       NetworkManager
Requires:       polkit
Requires:       systemd
# the uninstall reset: awk, runuser, and a D-Bus session for a user who is not logged in
Requires:       gawk
Requires:       util-linux
Requires:       dbus-daemon
Recommends:     (tuned-ppd or power-profiles-daemon)
Recommends:     libnotify
Recommends:     brightnessctl

%description
Switch laptop profiles (lid, suspend, Wi-Fi powersave, power mode, display,
Bluetooth, CPU limits) with confirmation, backup and rollback; a guard that
keeps remote desktop reachable with the lid closed and suspends on overheating.

%prep
%autosetup -n PowerDeck-%{version}

%build

%install
sh scripts/stage.sh %{buildroot} %{python3_sitelib}

%post
systemctl daemon-reload >/dev/null 2>&1 || :

%preun
# on removal only (not on upgrade): put every setting back while PowerDeck's files still exist
if [ "$1" -eq 0 ]; then
    /usr/libexec/powerdeck-uninstall-reset || :
    # in any case, while its ExecStopPost (restores the CPU limits) still exists
    systemctl stop 'powerdeck-thermal@*.service' >/dev/null 2>&1 || :
fi

%postun
if [ "$1" -eq 0 ]; then
    rm -f /etc/systemd/logind.conf.d/99-powerdeck-lid.conf
    systemctl daemon-reload >/dev/null 2>&1 || :
fi

%files
%license LICENSE
%doc README.md
/usr/bin/powerdeck
/usr/bin/powerdeck-gtk
/usr/libexec/powerdeck-helper
/usr/libexec/powerdeck-suspend
/usr/libexec/powerdeck-thermal
/usr/libexec/powerdeck-uninstall-reset
%{python3_sitelib}/powerdeck/
/usr/lib/systemd/system/powerdeck-thermal@.service
/usr/lib/systemd/user/powerdeck-guard.service
/usr/lib/systemd/user/graphical-session.target.wants/powerdeck-guard.service
%config(noreplace) /etc/xdg/autostart/io.github.powerdeck.PowerDeck.desktop
/usr/share/polkit-1/actions/io.github.powerdeck.policy
/usr/share/applications/io.github.powerdeck.PowerDeck.desktop
/usr/share/metainfo/io.github.powerdeck.PowerDeck.metainfo.xml
/usr/share/icons/hicolor/scalable/apps/io.github.powerdeck.PowerDeck.svg
/usr/share/icons/hicolor/symbolic/apps/powerdeck-history-symbolic.svg

%changelog
* Mon Nov 16 2026 Tomas Adamcik <82325837+TomasAdamcik-dotcom@users.noreply.github.com> - 1.1.0-1
- First RPM: Fedora support (power mode through tuned-ppd), Cinnamon, powerdeck doctor
