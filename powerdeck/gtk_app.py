"""GTK4 / libadwaita front-end. All logic lives in the engine; this only shows it.

Anything that runs commands (status read, planning, applying) happens in a worker thread;
results come back to the GTK main loop with GLib.idle_add.
"""
from __future__ import annotations

import logging
import os
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import backends, engine  # noqa: E402
from .describe import Item, describe  # noqa: E402
from .gtk_editor import ProfileEditor  # noqa: E402
from .history_view import HistoryPanel  # noqa: E402
from .gui_texts import (COMPACT_CSS, FADED, HIDDEN, MONITOR_HELP, MONITOR_ROWS, STATUS_HELP,  # noqa: E402
                        STATUS_ROWS, fold_states)
from .i18n import _  # noqa: E402
from .logs import setup_logging  # noqa: E402
from .monitor import Monitor  # noqa: E402
from .profile import load_profiles  # noqa: E402
from .status import Status, profile_thermal_level, read_status  # noqa: E402
from .tray import Tray  # noqa: E402

APP_ID = "io.github.powerdeck.PowerDeck"
STATUS_INTERVAL_S = 10
MONITOR_INTERVAL_S = 2

log = logging.getLogger("powerdeck")


def _help_markup(text: str, command: str) -> str:
    """Tooltip: what a value means, then the command it comes from."""
    return (GLib.markup_escape_text(_(text)) + "\n\n" + GLib.markup_escape_text(_("Source:"))
            + "\n<tt>" + GLib.markup_escape_text(command) + "</tt>")


def _in_background(work, done, failed=None) -> None:
    """Run ``work()`` in a worker thread, then ``done(result)`` (or ``failed(exc)``) on the main loop."""
    def call(fn, arg):
        fn(arg)
        return False  # run once

    def target():
        try:
            result = work()
        except Exception as e:  # never lose the error in a dead thread
            log.exception("background task failed")
            if failed is not None:
                GLib.idle_add(call, failed, e)
            return
        GLib.idle_add(call, done, result)
    threading.Thread(target=target, daemon=True).start()


def _rows(listbox: Gtk.ListBox):
    row = listbox.get_first_child()
    while row is not None:
        yield row
        row = row.get_next_sibling()


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="PowerDeck", default_width=900, default_height=620)
        self.profiles = load_profiles()
        self.selected = None
        self.active_name: str | None = None
        self._status_pending = False
        self._running = False
        self._expanded = False  # profile contents folded (the owner's choice)

        header = Adw.HeaderBar()
        split = Adw.NavigationSplitView()
        split.set_sidebar(self._build_sidebar(header))
        split.set_content(Adw.NavigationPage(title="PowerDeck", child=self._build_content()))
        self.toasts = Adw.ToastOverlay(child=split)
        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(self.toasts)
        self.set_content(view)

        # with the top-bar icon, closing the window only hides it (the app keeps running)
        self.set_hide_on_close(getattr(app, "tray", None) is not None)
        self.connect("close-request", self._on_close)
        self.connect("notify::visible", lambda *_a: self.get_visible() and self.refresh_status())

        self.refresh_status()
        self._select_active()
        GLib.timeout_add_seconds(STATUS_INTERVAL_S, self._tick)
        GLib.timeout_add_seconds(MONITOR_INTERVAL_S, self._monitor_tick)

    def _on_close(self, _win) -> bool:
        if self.get_hide_on_close() or engine.active_profile() in (None, "Default"):
            return False
        self.get_application().quit_to_default()  # quits once Default is back
        return True

    # -- layout ---------------------------------------------------------------
    def _build_sidebar(self, header: Adw.HeaderBar) -> Adw.NavigationPage:
        """Title bar (active profile, new/edit buttons) and the profile list."""
        self.title = Adw.WindowTitle(title="PowerDeck")
        self.title.set_tooltip_markup(_help_markup(*STATUS_HELP["active"]))
        header.set_title_widget(self.title)
        self.listbox = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.listbox.connect("row-selected", self._on_select)
        self._fill_list()
        new_btn = Gtk.Button(icon_name="list-add-symbolic", tooltip_text=_("New profile"))
        new_btn.connect("clicked", lambda *_a: self._edit(None))
        self.edit_btn = Gtk.Button(icon_name="document-edit-symbolic", tooltip_text=_("Edit"), sensitive=False)
        self.edit_btn.connect("clicked", lambda *_a: self._edit(self.selected))
        self.history_btn = Gtk.ToggleButton(icon_name="powerdeck-history-symbolic",
                                            tooltip_text=_("Temperature and frequency history"))
        self.history_btn.connect("toggled", lambda b: self.history.toggle(b.get_active()))
        header.pack_start(new_btn)
        header.pack_start(self.edit_btn)
        header.pack_start(self.history_btn)
        return Adw.NavigationPage(title=_("Profiles"), child=Gtk.ScrolledWindow(child=self.listbox))

    def _build_content(self) -> Adw.PreferencesPage:
        """Top to bottom: profile contents, actions, status, monitor."""
        content = Adw.PreferencesPage()
        self.history = HistoryPanel()
        content.add(self.history)
        self.info_group = Adw.PreferencesGroup(title=_("Profile contents"),
                                               description=_("Select a profile on the left to see what it changes."))
        self.info_rows = []
        content.add(self.info_group)
        content.add(self._build_actions())
        content.add(self._build_status_group())
        content.add(self._build_monitor_group())
        return content

    def _build_actions(self) -> Adw.PreferencesGroup:
        """Buttons right under the profile description."""
        actions = Adw.PreferencesGroup()
        box = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER, margin_top=6)
        self.apply_btn = Gtk.Button(label=_("Apply"), css_classes=["suggested-action", "pill"],
                                    sensitive=False)
        self.apply_btn.connect("clicked", lambda *_a: self.selected and
                               self.get_application().apply_profile(self.selected.name))
        box.append(self.apply_btn)
        actions.add(box)
        return actions

    @staticmethod
    def _property_row(title: str, help_: tuple[str, str]) -> Adw.ActionRow:
        row = Adw.ActionRow(title=_(title))
        row.add_css_class("property")
        row.set_tooltip_markup(_help_markup(*help_))
        return row

    def _build_status_group(self) -> Adw.PreferencesGroup:
        grp = Adw.PreferencesGroup(title=_("Status"))
        self.rows = {}
        for key, title in STATUS_ROWS:
            self.rows[key] = self._property_row(title, STATUS_HELP[key])
            grp.add(self.rows[key])
        return grp

    def _build_monitor_group(self) -> Adw.PreferencesGroup:
        """Live monitor (sysfs only, cheap enough for the main loop)."""
        grp = Adw.PreferencesGroup(title=_("Monitor"))
        self.monitor = Monitor()
        self.mrows, self.mbars = {}, {}
        for key, title, bar in MONITOR_ROWS:
            row = self._property_row(title, MONITOR_HELP[key])
            if bar:
                self.mbars[key] = Gtk.LevelBar(min_value=0, max_value=100, valign=Gtk.Align.CENTER,
                                               width_request=120)
                row.add_suffix(self.mbars[key])
            self.mrows[key] = row
            grp.add(row)
        return grp

    # -- profile list -----------------------------------------------------------
    def _fill_list(self):
        while (child := self.listbox.get_first_child()) is not None:
            self.listbox.remove(child)
        for p in self.profiles:
            row = Adw.ActionRow(title=p.name, subtitle=GLib.markup_escape_text(_(p.description)))
            row.profile = p
            self.listbox.append(row)
        self._mark_active()

    def _mark_active(self):
        """Title bar subtitle + framed tile for the profile PowerDeck applied last."""
        self.active_name = active = engine.active_profile()
        self.title.set_subtitle(_("Active profile: {}").format(active) if active
                                else _("No profile activated"))
        for row in _rows(self.listbox):
            if row.profile.name == active:
                row.add_css_class("pd-active")
                row.set_tooltip_text(_("Active profile"))
            else:
                row.remove_css_class("pd-active")
                row.set_tooltip_text(None)

    def _select_active(self):
        active = engine.active_profile()
        for row in _rows(self.listbox):
            if row.profile.name == active:
                self.listbox.select_row(row)
                return

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

    def _on_select(self, _lb, row):
        self.selected = row.profile if row else None
        self._show_profile(self.selected)
        self.apply_btn.set_sensitive(self.selected is not None)
        self.edit_btn.set_sensitive(self.selected is not None)

    # -- profile contents -------------------------------------------------------
    def _add_info_row(self, row: Adw.ActionRow):
        self.info_group.add(row)
        self.info_rows.append(row)

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
        self.info_group.set_description(None)
        first = Adw.ActionRow(title=GLib.markup_escape_text(desc.summary))
        first.add_css_class("pd-compact")
        self.fold_btn = Gtk.ToggleButton(active=self._expanded, valign=Gtk.Align.CENTER,
                                         css_classes=["flat", "circular", "pd-info"])
        self.fold_btn.connect("toggled", self._on_fold)
        first.add_suffix(self.fold_btn)
        self._add_info_row(first)
        for it in desc.items:
            self._add_info_row(self._bullet_row(it))
        if any(i.warning for i in desc.items) and profile.thermal_guard:
            warn = Adw.ActionRow(title="⚠ " + _("Overheating risk – do not keep the laptop closed in a bag for long."))
            warn.set_title_lines(0)
            warn.add_css_class("pd-compact")
            self._add_info_row(warn)
        self._show_fold()

    def _on_fold(self, btn):
        self._expanded = btn.get_active()
        self._show_fold()

    def _show_fold(self):
        """Folded: the first line, the next one faded, the rest hidden; the arrow unfolds all."""
        for row, state in zip(self.info_rows, fold_states(len(self.info_rows), self._expanded)):
            row.set_visible(state != HIDDEN)
            (row.add_css_class if state == FADED else row.remove_css_class)("pd-faded")
        self.info_rows[0].set_title_lines(0 if self._expanded else 1)
        self.fold_btn.set_visible(len(self.info_rows) > 1)
        self.fold_btn.set_icon_name("pan-up-symbolic" if self._expanded else "pan-down-symbolic")
        self.fold_btn.set_tooltip_text(_("Show less") if self._expanded else _("Show all"))

    def _bullet_row(self, it: Item) -> Adw.ActionRow:
        """Compact bullet row; its info icon shows the command (and a note) and copies it on click."""
        row = Adw.ActionRow(title=GLib.markup_escape_text(("⚠ " if it.warning else "• ") + it.text))
        row.set_title_lines(0)
        row.add_css_class("pd-compact")
        if it.commands:
            text = it.command_text()
            markup = "<tt>" + GLib.markup_escape_text(text) + "</tt>"
            if it.note:
                markup += "\n\n" + GLib.markup_escape_text(it.note)
            btn = Gtk.Button(icon_name="dialog-information-symbolic", valign=Gtk.Align.CENTER,
                             css_classes=["flat", "circular", "pd-info"], tooltip_text=_("Show command"))
            btn.set_tooltip_markup(markup)
            btn.connect("clicked", self._copy_command, text)
            row.add_suffix(btn)
        return row

    def _copy_command(self, _btn, text):
        Gdk.Display.get_default().get_clipboard().set(text)
        self.toast(_("Copied to clipboard."))

    # -- status and monitor -----------------------------------------------------
    def _tick(self):
        if self.get_visible():  # hidden in the top bar: read nothing, save power
            self.refresh_status()
        return True

    def refresh_status(self):
        """Active profile now; the status rows once read_status (subprocesses) is done."""
        self._mark_active()
        if self._status_pending:
            return
        self._status_pending = True
        _in_background(read_status, self._show_status, lambda _e: self._show_status(None))

    def _show_status(self, s: Status | None):
        self._status_pending = False
        if s is None:
            return
        na = _("unknown")
        self.rows["battery"].set_subtitle(
            na if s.battery_percent is None else f"{s.battery_percent} % ({s.battery_state})")
        self.rows["conn"].set_subtitle(s.active_connection or na)
        self.rows["mode"].set_subtitle(s.power_mode or na)
        self.rows["wifips"].set_subtitle(s.wifi_powersave or na)
        self.rows["sessions"].set_subtitle(
            f"SSH: {s.ssh_connections} · RDP: {s.rdp_connections} · {_('Login sessions')}: {s.login_sessions}")

    def _set_monitor(self, key: str, text: str, bar: float | None = None):
        self.mrows[key].set_subtitle(text)
        if key in self.mbars:
            self.mbars[key].set_value(max(0, min(100, bar or 0)))

    def _monitor_tick(self):
        if not self.get_visible():
            return True
        s = self.monitor.sample()
        na = _("unknown")
        # thermal level against the active profile's limits (defaults without a thermal guard)
        level = profile_thermal_level(s.temp_c, self._find(self.active_name or ""))
        self._set_monitor("temp", na if s.temp_c is None else f"{s.temp_c:.0f} °C — {_(level)}", s.temp_c)
        if s.freq_mhz is None:
            freq = na
        else:
            lim = f" · {_('limit')} {s.limit_percent} %" if s.limit_percent else ""
            turbo = "" if s.turbo is None else f" · turbo {'on' if s.turbo else 'off'}"
            reg = f" · {_('held at')} {s.regulation}" if s.regulation else ""
            freq = f"{s.freq_mhz:.0f} MHz ({s.freq_percent:.0f} %){lim}{turbo}{reg}"
        self._set_monitor("freq", freq, s.freq_percent)
        self._set_monitor("load", na if s.load_percent is None else f"{s.load_percent:.0f} %", s.load_percent)
        self._set_monitor("power", na if s.power_w is None else f"{s.power_w:+.1f} W")
        self._set_monitor("fan", na if s.fan_rpm is None else f"{s.fan_rpm} rpm")
        return True

    # -- apply/restore run at once (no confirmation, the owner's choice), off the UI thread
    def _on_restore(self, *_a):
        self._run(engine.restore_last, _("Restored previous settings."))

    def _run(self, fn, ok_msg):
        if self._running:  # e.g. a double click or several tray clicks: one apply/restore at a time
            return
        self._running = True
        _in_background(fn, lambda errs: self._done(errs, ok_msg),
                       lambda e: self._done([str(e)], ok_msg))

    def _done(self, errs, ok_msg):
        self._running = False
        if errs:
            self.present()  # e.g. a restore from the top-bar icon, with the window hidden
            self.error(_("Something went wrong"), "\n".join(errs))
        else:
            self.toast(ok_msg)
        self.refresh_status()
        tray = getattr(self.get_application(), "tray", None)
        if tray is not None:
            tray.refresh()

    def toast(self, msg):
        self.toasts.add_toast(Adw.Toast(title=msg))

    def error(self, heading, body):
        dlg = Adw.AlertDialog(heading=heading, body=body)
        dlg.add_response("ok", "OK")
        dlg.present(self)


class App(Adw.Application):
    """One instance per session. ``--background`` (login autostart) shows only the top-bar
    icon; starting PowerDeck again, or "Open" in the icon's menu, shows the window."""

    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.tray: Tray | None = None
        self._tray_active: str | None = None
        self._quitting = False
        self._switching = False

    def do_startup(self):
        Adw.Application.do_startup(self)
        # Follow the system light/dark preference (like GNOME Calculator / Text Editor)
        # and switch live when the user changes it. No hardcoded colours anywhere.
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.DEFAULT)
        css = Gtk.CssProvider()
        if hasattr(css, "load_from_string"):  # GTK >= 4.12; load_from_data is deprecated there
            css.load_from_string(COMPACT_CSS)
        else:
            css.load_from_data(COMPACT_CSS, -1)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        data = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
        if os.path.isdir(data):  # running from the source tree: the .deb installs icons to hicolor
            Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(data)
        self._start_tray()

    def _start_tray(self):
        data = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
        tray = Tray(self._tray_state, self.activate, self._tray_profile, self._tray_restore, self.quit_to_default,
                    icon_theme_path=data if os.path.isdir(data) else "")
        if tray.start():
            self.tray = tray
            self.hold()  # keep running without a window
            self._tray_active = engine.active_profile()
            GLib.timeout_add_seconds(STATUS_INTERVAL_S, self._watch_active)

    @staticmethod
    def _tray_state():
        return [p.name for p in load_profiles()], engine.active_profile()

    def _watch_active(self):
        """The CLI or the guard may switch profiles too: keep the icon's menu current."""
        active = engine.active_profile()
        if active != self._tray_active:
            self._tray_active = active
            self.tray.refresh()
        return True

    def _open_window(self) -> MainWindow | None:
        return next((w for w in self.get_windows() if isinstance(w, MainWindow)), None)

    def _window(self) -> MainWindow:
        win = self._open_window() or MainWindow(self)
        win.present()
        return win

    def apply_profile(self, name: str):
        """Switch to ``name`` at once, without asking (Apply button, top-bar icon). From the
        icon no window opens, unless something goes wrong."""
        profiles = load_profiles()
        profile = next((p for p in profiles if p.name == name), None)
        if profile is None or self._switching:  # one switch at a time (double clicks, icon + button)
            return
        self._switching = True
        active = engine.active_profile()
        previous = next((p for p in profiles if p.name == active), None)
        _in_background(lambda: engine.quick_switch(profile, previous=previous),
                       lambda r: self._switched(profile.name, r),
                       lambda e: self._switched(profile.name, [str(e)]))

    def _switched(self, name: str, result):
        self._switching = False
        win = self._open_window()
        if result == engine.NEEDS_HELPER:
            self._window().error(_("Admin helper is not installed."),
                                 _("Install it once with:") + "\n\nsudo scripts/install-helper.sh")
            return
        if result and result != engine.NOTHING_TO_DO:
            self._window().error(_("Something went wrong"), "\n".join(result))
        elif win is not None:
            win.toast(_("No changes needed.") if result == engine.NOTHING_TO_DO
                      else _("Applied “{}”.").format(name))
        if win is not None:
            win.refresh_status()
        if self.tray is not None:
            self._tray_active = engine.active_profile()
            self.tray.refresh()

    def _tray_profile(self, name: str):
        self.apply_profile(name)

    def _tray_restore(self):
        win = self._open_window() or MainWindow(self)  # the window only shows up on an error
        win._on_restore()

    def quit_to_default(self):
        """Quitting PowerDeck puts the Default profile back (the owner's choice); without the
        top-bar icon closing the window quits. Hiding the window to the icon changes nothing."""
        if self._quitting:
            return
        active = engine.active_profile()
        profiles = load_profiles()
        default = next((p for p in profiles if p.name == "Default"), None)
        if default is None or active in (None, "Default"):
            self.quit()
            return
        self._quitting = True
        previous = next((p for p in profiles if p.name == active), None)
        _in_background(lambda: engine.switch_to(default, previous=previous),
                       self._quit_done, lambda e: self._quit_done([str(e)]))

    def _quit_done(self, errs):
        self._quitting = False
        if not errs:
            self.quit()
            return
        win = self._window()  # stay running and say why, rather than quit half-restored
        win.error(_("Default profile could not be restored"), "\n".join(errs))
        win.refresh_status()

    def do_command_line(self, cmd):
        if "--background" not in cmd.get_arguments()[1:]:
            self.activate()
        return 0

    def do_activate(self):
        self._window()


def main() -> int:
    setup_logging()
    return App().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
