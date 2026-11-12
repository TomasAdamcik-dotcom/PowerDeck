"""GTK4 / libadwaita front-end. All logic lives in the engine; this only shows it."""
from __future__ import annotations

import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import engine  # noqa: E402
from .i18n import _  # noqa: E402
from .profile import load_profiles  # noqa: E402
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
        for p in self.profiles:
            row = Adw.ActionRow(title=p.name, subtitle=p.description)
            row.profile = p
            self.listbox.append(row)
        side_page = Adw.NavigationPage(title=_("Profiles"),
                                       child=Gtk.ScrolledWindow(child=self.listbox))
        split.set_sidebar(side_page)

        # -- content: status + actions
        content = Adw.PreferencesPage()
        grp = Adw.PreferencesGroup(title=_("Status"))
        self.rows = {}
        for key in ("active", "battery", "temp", "conn", "mode"):
            r = Adw.ActionRow(title={"active": _("Active profile"), "battery": _("Battery"),
                                     "temp": _("CPU temperature"), "conn": _("Connection"),
                                     "mode": _("Power mode")}[key])
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

    def _on_select(self, _lb, row):
        self.selected = row.profile if row else None
        self.apply_btn.set_sensitive(self.selected is not None)

    def _on_apply_clicked(self, *_a):
        self._confirm(self.selected)

    def toast(self, msg):
        self.toasts.add_toast(Adw.Toast(title=msg))

    # -- apply flow: show exact diff, ask, then run off the UI thread
    def _confirm(self, profile):
        if profile is None:
            return
        plan = engine.make_plan(profile)
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

    def _done(self, errs, ok_msg):
        self.toast("; ".join(errs) if errs else ok_msg)
        self.refresh_status()
        return False


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_activate(self):
        (self.props.active_window or MainWindow(self)).present()


def main() -> int:
    return App().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
