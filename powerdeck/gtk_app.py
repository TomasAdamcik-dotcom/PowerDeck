"""GTK4 / libadwaita front-end. All logic lives in the engine; this only shows it."""
from __future__ import annotations

import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from . import backends, engine  # noqa: E402
from .monitor import Monitor  # noqa: E402
from .i18n import _  # noqa: E402
from .describe import describe  # noqa: E402
from .logs import setup_logging  # noqa: E402
from .profile import LID_ACTIONS, POWER_MODES, Profile, ProfileError, load_profiles, save_profile  # noqa: E402
from .runner import system_runner  # noqa: E402
from .status import read_status, thermal_level  # noqa: E402

APP_ID = "io.github.powerdeck.PowerDeck"


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="PowerDeck", default_width=900, default_height=620)
        self.profiles = load_profiles()
        self.selected = None

        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title="PowerDeck",
                                                subtitle=_("PowerDeck — Linux workstation profiles")))
        view.add_top_bar(header)

        split = Adw.NavigationSplitView()
        # -- sidebar: profile list
        self.listbox = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.listbox.connect("row-selected", self._on_select)
        self._fill_list()
        new_btn = Gtk.Button(icon_name="list-add-symbolic", tooltip_text=_("New profile"))
        new_btn.connect("clicked", lambda *_a: self._edit(None))
        self.edit_btn = Gtk.Button(icon_name="document-edit-symbolic", tooltip_text=_("Edit"), sensitive=False)
        self.edit_btn.connect("clicked", lambda *_a: self._edit(self.selected))
        header.pack_start(new_btn)
        header.pack_start(self.edit_btn)
        side_page = Adw.NavigationPage(title=_("Profiles"),
                                       child=Gtk.ScrolledWindow(child=self.listbox))
        split.set_sidebar(side_page)

        # -- content (top to bottom): profile contents, actions, status, monitor
        content = Adw.PreferencesPage()
        # -- profile contents (first block): summary + bullets with command tooltips
        self.info_group = Adw.PreferencesGroup(title=_("Profile contents"),
                                               description=_("Select a profile on the left to see what it changes."))
        self.info_rows = []
        content.add(self.info_group)

        # -- actions right under the profile description
        actions = Adw.PreferencesGroup()
        box = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER, margin_top=6)
        self.apply_btn = Gtk.Button(label=_("Apply…"), css_classes=["suggested-action", "pill"],
                                    sensitive=False)
        self.apply_btn.connect("clicked", self._on_apply_clicked)
        remote = Gtk.Button(label=_("Enable Remote Work"), css_classes=["pill"])
        remote.connect("clicked", lambda *_a: self._confirm(self._find("RemoteWork")))
        restore = Gtk.Button(label=_("Restore previous"), css_classes=["pill"])
        restore.connect("clicked", self._on_restore)
        for b in (self.apply_btn, remote, restore):
            box.append(b)
        actions.add(box)
        content.add(actions)

        grp = Adw.PreferencesGroup(title=_("Status"))
        self.rows = {}
        for key in ("active", "battery", "temp", "conn", "mode", "wifips", "sessions"):
            r = Adw.ActionRow(title={"active": _("Active profile"), "battery": _("Battery"),
                                     "temp": _("CPU temperature"), "conn": _("Connection"),
                                     "mode": _("Power mode"), "wifips": _("Wi-Fi powersave"),
                                     "sessions": _("Sessions")}[key])
            r.add_css_class("property")
            self.rows[key] = r
            grp.add(r)
        content.add(grp)

        # -- live monitor (sysfs only, refreshed every 2 s)
        mgrp = Adw.PreferencesGroup(title=_("Monitor"))
        self.monitor = Monitor()
        self.mrows, self.mbars = {}, {}
        for key, title, bar in (("temp", _("CPU temperature"), True), ("freq", _("CPU frequency"), True),
                                ("load", _("CPU load"), True), ("power", _("Power draw"), False),
                                ("fan", _("Fan"), False)):
            r = Adw.ActionRow(title=title)
            r.add_css_class("property")
            if bar:
                lb = Gtk.LevelBar(min_value=0, max_value=100, valign=Gtk.Align.CENTER, width_request=120)
                r.add_suffix(lb)
                self.mbars[key] = lb
            self.mrows[key] = r
            mgrp.add(r)
        content.add(mgrp)

        split.set_content(Adw.NavigationPage(title="PowerDeck", child=content))

        self.toasts.set_child(split)
        view.set_content(self.toasts)
        self.set_content(view)
        self.refresh_status()
        self._select_active()
        GLib.timeout_add_seconds(10, self._tick)
        GLib.timeout_add_seconds(2, self._monitor_tick)

    # -- helpers
    def _fill_list(self):
        child = self.listbox.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.listbox.remove(child)
            child = nxt
        for p in self.profiles:
            row = Adw.ActionRow(title=p.name, subtitle=GLib.markup_escape_text(_(p.description)))
            row.profile = p
            self.listbox.append(row)

    def reload_profiles(self):
        self.profiles = load_profiles()
        self.selected = None
        self.apply_btn.set_sensitive(False)
        self.edit_btn.set_sensitive(False)
        self._fill_list()
        self._show_profile(None)

    def _edit(self, profile):
        ProfileEditor(self, profile).present()

    def _find(self, name):
        return next((p for p in self.profiles if p.name == name), None)

    def _tick(self):
        self.refresh_status()
        return True

    def _monitor_tick(self):
        s = self.monitor.sample()
        na = _("unknown")
        self.mrows["temp"].set_subtitle(na if s.temp_c is None else f"{s.temp_c:.0f} °C")
        self.mbars["temp"].set_value(max(0, min(100, s.temp_c or 0)))
        if s.freq_mhz is None:
            self.mrows["freq"].set_subtitle(na)
        else:
            lim = f" · {_('limit')} {s.limit_percent} %" if s.limit_percent else ""
            turbo = "" if s.turbo is None else f" · turbo {'on' if s.turbo else 'off'}"
            self.mrows["freq"].set_subtitle(f"{s.freq_mhz:.0f} MHz ({s.freq_percent:.0f} %){lim}{turbo}")
        self.mbars["freq"].set_value(s.freq_percent or 0)
        self.mrows["load"].set_subtitle(na if s.load_percent is None else f"{s.load_percent:.0f} %")
        self.mbars["load"].set_value(s.load_percent or 0)
        self.mrows["power"].set_subtitle(na if s.power_w is None else f"{s.power_w:+.1f} W")
        self.mrows["fan"].set_subtitle(na if s.fan_rpm is None else f"{s.fan_rpm} rpm")
        return True

    def refresh_status(self):
        s = read_status()
        self.rows["active"].set_subtitle(engine.active_profile() or _("none"))
        bat = _("unknown") if s.battery_percent is None else f"{s.battery_percent} % ({s.battery_state})"
        self.rows["battery"].set_subtitle(bat)
        if s.cpu_temp_c is None:
            temp = _("unknown")
        else:
            temp = f"{s.cpu_temp_c:.0f} °C — {_(thermal_level(s.cpu_temp_c))}"
        self.rows["temp"].set_subtitle(temp)
        self.rows["conn"].set_subtitle(s.active_connection or _("unknown"))
        self.rows["mode"].set_subtitle(s.power_mode or _("unknown"))
        self.rows["wifips"].set_subtitle(s.wifi_powersave or _("unknown"))
        self.rows["sessions"].set_subtitle(
            f"SSH: {s.ssh_connections} · RDP: {s.rdp_connections} · {_('Login sessions')}: {s.login_sessions}")

    def _select_active(self):
        active = engine.active_profile()
        row = self.listbox.get_first_child()
        while row is not None:
            if row.profile.name == active:
                self.listbox.select_row(row)
                return
            row = row.get_next_sibling()

    def _show_profile(self, profile):
        """Fill the top block: short description, then bullets; the info icon shows the command."""
        for r in self.info_rows:
            self.info_group.remove(r)
        self.info_rows = []
        if profile is None:
            self.info_group.set_title(_("Profile contents"))
            self.info_group.set_description(_("Select a profile on the left to see what it changes."))
            return
        desc = describe(profile)
        self.info_group.set_title(profile.name)
        self.info_group.set_description(GLib.markup_escape_text(desc.summary))
        for it in desc.items:
            row = Adw.ActionRow(title=GLib.markup_escape_text(("⚠ " if it.warning else "• ") + it.text))
            row.set_title_lines(0)
            if it.commands:
                btn = Gtk.Button(icon_name="dialog-information-symbolic", valign=Gtk.Align.CENTER,
                                 css_classes=["flat", "circular"], tooltip_text=_("Show command"))
                text = it.command_text()
                markup = "<tt>" + GLib.markup_escape_text(text) + "</tt>"
                if it.note:
                    markup += "\n\n" + GLib.markup_escape_text(it.note)
                btn.set_tooltip_markup(markup)
                btn.connect("clicked", self._copy_command, text)
                row.add_suffix(btn)
            self.info_group.add(row)
            self.info_rows.append(row)
        if any(i.warning for i in desc.items) and profile.thermal_guard:
            warn = Adw.ActionRow(title="⚠ " + _("Overheating risk – do not keep the laptop closed in a bag for long."))
            warn.set_title_lines(0)
            self.info_group.add(warn)
            self.info_rows.append(warn)

    def _copy_command(self, _btn, text):
        Gdk.Display.get_default().get_clipboard().set(text)
        self.toast(_("Copied to clipboard."))

    def _on_select(self, _lb, row):
        self.selected = row.profile if row else None
        self._show_profile(self.selected)
        self.apply_btn.set_sensitive(self.selected is not None)
        self.edit_btn.set_sensitive(self.selected is not None)

    def _on_apply_clicked(self, *_a):
        self._confirm(self.selected)

    def toast(self, msg):
        self.toasts.add_toast(Adw.Toast(title=msg))

    # -- apply flow: show exact diff, ask, then run off the UI thread
    def _confirm(self, profile):
        if profile is None:
            return
        plan = engine.make_plan(profile, previous=self._find(engine.active_profile() or ""))
        if plan.needs_admin and not backends.helper_installed():
            self.error(_("Admin helper is not installed."),
                       _("Install it once with:") + "\n\nsudo scripts/install-helper.sh")
            return
        if plan.empty:
            self.toast(_("No changes needed."))
            return
        body = _("The following changes will be made:") + "\n\n" + plan.diff_text()
        if plan.needs_admin:
            body += "\n\n" + _("Administrator permission will be requested.")
        if profile.thermal_guard:
            body += "\n\n⚠ " + _("Overheating risk: do not keep the laptop in a closed bag for long.")
        dlg = Adw.MessageDialog(transient_for=self, heading=_("Apply profile “{}”?").format(profile.name),
                                body=body)
        dlg.add_response("cancel", _("Cancel"))
        dlg.add_response("apply", _("Apply"))
        dlg.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)
        dlg.connect("response", lambda d, r: r == "apply" and self._run(
            lambda: engine.apply_plan(plan), _("Applied “{}”.").format(profile.name)))
        dlg.present()

    def _on_restore(self, *_a):
        self._run(engine.restore_last, _("Restored previous settings."))

    def _run(self, fn, ok_msg):
        def work():
            errs = fn()
            GLib.idle_add(self._done, errs, ok_msg)
        threading.Thread(target=work, daemon=True).start()

    def error(self, heading, body):
        dlg = Adw.MessageDialog(transient_for=self, heading=heading, body=body)
        dlg.add_response("ok", "OK")
        dlg.present()

    def _done(self, errs, ok_msg):
        if errs:
            self.error(_("Something went wrong"), "\n".join(errs))
        else:
            self.toast(ok_msg)
        self.refresh_status()
        return False


_NONE = "—"


def _combo(options, selected=0):
    r = Adw.ComboRow()
    r.set_model(Gtk.StringList.new(options))
    r.set_selected(selected)
    return r


class ProfileEditor(Adw.Window):
    """Create or edit a profile. Saved as JSON in ~/.config/powerdeck/profiles/."""

    def __init__(self, parent, profile):
        super().__init__(transient_for=parent, modal=True, title=_("Edit profile"),
                         default_width=520, default_height=640)
        self.parent_win = parent
        p = profile or Profile(name="")
        self.src = p
        self.name = Adw.EntryRow(title=_("Name"), text=p.name)
        self.desc = Adw.EntryRow(title=_("Description"), text=p.description)
        lid_opts = [_NONE, *LID_ACTIONS]
        self.lid = _combo(lid_opts, lid_opts.index(p.lid.get("battery", _NONE)) if p.lid else 0)
        self.lid.set_title(_("Lid close"))
        yn = [_NONE, _("Allowed"), _("Disabled")]
        cur = p.automatic_suspend.get("battery")
        self.auto = _combo(yn, 0 if cur is None else (1 if cur else 2))
        self.auto.set_title(_("Automatic suspend"))
        wf = [_NONE, "default", "off", "on"]
        self.wifi = _combo(wf, wf.index(p.wifi_powersave) if p.wifi_powersave else 0)
        self.wifi.set_title(_("Wi-Fi powersave"))
        pm = [_NONE, *POWER_MODES]
        self.mode = _combo(pm, pm.index(p.power_mode) if p.power_mode else 0)
        self.mode.set_title(_("Power mode"))
        self.nets = Adw.EntryRow(title=_("Wi-Fi networks (comma separated)"),
                                 text=", ".join(p.preferred_networks))
        self.teth = Adw.EntryRow(title=_("Tethering connection"), text=p.tethering.get("connection", ""))
        bt = [_NONE, "on", "off"]
        self.bt = _combo(bt, 0 if p.bluetooth is None else (1 if p.bluetooth else 2))
        self.bt.set_title("Bluetooth")

        tb = [_NONE, "on", "off"]
        t = p.cpu.get("turbo")
        self.turbo = _combo(tb, 0 if t is None else (1 if t else 2))
        self.turbo.set_title(_("CPU turbo"))
        self.fmax_opts = [_NONE, *[f"{v} %" for v in (100, 90, 80, 70, 60, 50, 40, 30)]]
        m = p.cpu.get("max_freq_percent")
        idx = self.fmax_opts.index(f"{m} %") if f"{m} %" in self.fmax_opts else 0
        self.fmax = _combo(self.fmax_opts, idx)
        self.fmax.set_title(_("CPU max frequency"))
        self._fmax_custom = m if m is not None and idx == 0 else None  # keep unusual values untouched

        grp = Adw.PreferencesGroup()
        for w in (self.name, self.desc, self.lid, self.auto, self.wifi, self.mode, self.nets, self.teth,
                  self.bt, self.turbo, self.fmax):
            grp.add(w)
        page = Adw.PreferencesPage()
        page.add(grp)
        self.msg = Gtk.Label(wrap=True, css_classes=["error"])
        save = Gtk.Button(label=_("Save"), css_classes=["suggested-action", "pill"], halign=Gtk.Align.CENTER)
        save.connect("clicked", self._save)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_bottom=12)
        box.append(self.msg)
        box.append(save)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(page)
        view.add_bottom_bar(box)
        self.set_content(view)

    def _sel(self, combo):
        return None if combo.get_selected() == 0 else combo.get_selected_item().get_string()

    def build(self) -> Profile:
        old = self.src
        d = old.to_dict()
        d["name"] = self.name.get_text().strip()
        d["description"] = self.desc.get_text().strip()
        lid = self._sel(self.lid)
        d["lid"] = {k: lid for k in ("battery", "ac", "docked")} if lid else {}
        a = self.auto.get_selected()
        d["automatic_suspend"] = {} if a == 0 else {"battery": a == 1, "ac": a == 1}
        d["wifi_powersave"] = self._sel(self.wifi)
        d["power_mode"] = self._sel(self.mode)
        d["preferred_networks"] = [n.strip() for n in self.nets.get_text().split(",") if n.strip()]
        t = self.teth.get_text().strip()
        d["tethering"] = {"connection": t, "priority": old.tethering.get("priority", 100)} if t else {}
        b = self.bt.get_selected()
        d["bluetooth"] = None if b == 0 else b == 1
        cpu = {}
        tb = self.turbo.get_selected()
        if tb:
            cpu["turbo"] = tb == 1
        fm = self.fmax.get_selected()
        if fm:
            cpu["max_freq_percent"] = int(self.fmax_opts[fm].split()[0])
        elif self._fmax_custom is not None:
            cpu["max_freq_percent"] = self._fmax_custom
        d["cpu"] = cpu
        return Profile.from_dict(d)

    def _save(self, *_a):
        try:
            save_profile(self.build())
        except (ProfileError, OSError, TypeError) as e:
            self.msg.set_text(str(e))
            return
        self.parent_win.reload_profiles()
        self.close()


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_startup(self):
        Adw.Application.do_startup(self)
        # Follow the system light/dark preference (like GNOME Calculator / Text Editor)
        # and switch live when the user changes it. No hardcoded colours anywhere.
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.DEFAULT)

    def do_activate(self):
        (self.props.active_window or MainWindow(self)).present()


def main() -> int:
    setup_logging()
    return App().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
