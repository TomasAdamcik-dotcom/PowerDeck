"""GTK4 / libadwaita front-end. All logic lives in the engine; this only shows it."""
from __future__ import annotations

import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import backends, engine  # noqa: E402
from .i18n import _  # noqa: E402
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

        # -- content: status + actions
        content = Adw.PreferencesPage()
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
        split.set_content(Adw.NavigationPage(title="PowerDeck", child=content))

        self.toasts.set_child(split)
        view.set_content(self.toasts)
        self.set_content(view)
        self.refresh_status()
        GLib.timeout_add_seconds(10, self._tick)

    # -- helpers
    def _fill_list(self):
        child = self.listbox.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.listbox.remove(child)
            child = nxt
        for p in self.profiles:
            row = Adw.ActionRow(title=p.name, subtitle=p.description)
            row.profile = p
            self.listbox.append(row)

    def reload_profiles(self):
        self.profiles = load_profiles()
        self.selected = None
        self.apply_btn.set_sensitive(False)
        self.edit_btn.set_sensitive(False)
        self._fill_list()

    def _edit(self, profile):
        ProfileEditor(self, profile).present()

    def _find(self, name):
        return next((p for p in self.profiles if p.name == name), None)

    def _tick(self):
        self.refresh_status()
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

    def _on_select(self, _lb, row):
        self.selected = row.profile if row else None
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

        grp = Adw.PreferencesGroup()
        for w in (self.name, self.desc, self.lid, self.auto, self.wifi, self.mode, self.nets, self.teth, self.bt):
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
        p = Profile.from_dict(d)
        return p

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

    def do_activate(self):
        (self.props.active_window or MainWindow(self)).present()


def main() -> int:
    setup_logging()
    return App().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
