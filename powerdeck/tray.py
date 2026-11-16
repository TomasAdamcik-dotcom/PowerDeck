"""Top-bar icon: a StatusNotifierItem with a small menu, over D-Bus with Gio only.

GTK4 has no tray API and the Ayatana AppIndicator library is GTK3-only, so the item and its
menu (com.canonical.dbusmenu) are exported by hand. GNOME shows them through the "AppIndicator
and KStatusNotifierItem Support" extension (enabled by default on Ubuntu). Without a
StatusNotifierWatcher on the bus ``Tray.start`` returns False and the app behaves like a
normal window that quits on close.
"""
from __future__ import annotations

import logging
import os

from gi.repository import Gio, GLib

from .i18n import _

log = logging.getLogger(__name__)

WATCHER = "org.kde.StatusNotifierWatcher"
ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"
ICON = "io.github.powerdeck.PowerDeck"

OPEN_ID, RESTORE_ID, QUIT_ID, PROFILE_BASE = 1, 2, 3, 100

ITEM_XML = """<node><interface name="org.kde.StatusNotifierItem">
<property name="Category" type="s" access="read"/><property name="Id" type="s" access="read"/>
<property name="Title" type="s" access="read"/><property name="Status" type="s" access="read"/>
<property name="WindowId" type="i" access="read"/><property name="IconName" type="s" access="read"/>
<property name="IconThemePath" type="s" access="read"/>
<property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
<property name="ItemIsMenu" type="b" access="read"/><property name="Menu" type="o" access="read"/>
<method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
<signal name="NewTitle"/><signal name="NewIcon"/><signal name="NewToolTip"/>
<signal name="NewStatus"><arg type="s"/></signal>
</interface></node>"""

MENU_XML = """<node><interface name="com.canonical.dbusmenu">
<property name="Version" type="u" access="read"/><property name="TextDirection" type="s" access="read"/>
<property name="Status" type="s" access="read"/><property name="IconThemePath" type="as" access="read"/>
<method name="GetLayout"><arg type="i" direction="in"/><arg type="i" direction="in"/>
<arg type="as" direction="in"/><arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/></method>
<method name="GetGroupProperties"><arg type="ai" direction="in"/><arg type="as" direction="in"/>
<arg type="a(ia{sv})" direction="out"/></method>
<method name="GetProperty"><arg type="i" direction="in"/><arg type="s" direction="in"/>
<arg type="v" direction="out"/></method>
<method name="Event"><arg type="i" direction="in"/><arg type="s" direction="in"/>
<arg type="v" direction="in"/><arg type="u" direction="in"/></method>
<method name="EventGroup"><arg type="a(isvu)" direction="in"/><arg type="ai" direction="out"/></method>
<method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
<method name="AboutToShowGroup"><arg type="ai" direction="in"/><arg type="ai" direction="out"/>
<arg type="ai" direction="out"/></method>
<signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
<signal name="ItemsPropertiesUpdated"><arg type="a(ia{sv})"/><arg type="a(ias)"/></signal>
</interface></node>"""


def menu_items(profiles: list[str], active: str | None) -> list[tuple[int, dict]]:
    """(id, properties) of the menu entries, top to bottom; values are plain Python."""
    items = [(OPEN_ID, {"label": _("Open PowerDeck")}), (-1, {"type": "separator"})]
    for i, name in enumerate(profiles):
        items.append((PROFILE_BASE + i, {"label": name, "toggle-type": "radio",
                                         "toggle-state": int(name == active)}))
    items += [(-2, {"type": "separator"}),
              (RESTORE_ID, {"label": _("Restore previous"), "enabled": active is not None}),
              (QUIT_ID, {"label": _("Quit PowerDeck (back to Default)")})]
    return items


def _variant_props(props: dict) -> dict:
    kinds = {bool: "b", int: "i", str: "s"}
    return {k: GLib.Variant(kinds[type(v)], v) for k, v in props.items()}


class Tray:
    """``on_open()``, ``on_profile(name)``, ``on_restore()``, ``on_quit()`` run on the main loop;
    ``state()`` returns (profile names, active profile name or None)."""

    def __init__(self, state, on_open, on_profile, on_restore, on_quit, icon_theme_path: str = ""):
        self.state, self.on_open, self.on_profile = state, on_open, on_profile
        self.on_restore, self.on_quit = on_restore, on_quit
        self.icon_theme_path = icon_theme_path
        self.revision = 1
        self.bus: Gio.DBusConnection | None = None
        self.name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self._ids: list[int] = []
        self._profiles: list[str] = []
        self._active: str | None = None

    # -- lifecycle -------------------------------------------------------------
    def start(self, bus: Gio.DBusConnection | None = None) -> bool:
        """Export the item and register it; False if there is no tray to show it in."""
        try:
            self.bus = bus or Gio.bus_get_sync(Gio.BusType.SESSION)
            if not self._watcher_present():
                log.info("no StatusNotifierWatcher on the session bus: no top-bar icon")
                return False
            item = Gio.DBusNodeInfo.new_for_xml(ITEM_XML).interfaces[0]
            menu = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
            self._ids = [
                self.bus.register_object(ITEM_PATH, item, self._item_call, self._item_prop, None),
                self.bus.register_object(MENU_PATH, menu, self._menu_call, self._menu_prop, None)]
            Gio.bus_own_name_on_connection(self.bus, self.name, Gio.BusNameOwnerFlags.NONE, None, None)
            self._register()
            # gnome-shell restarted (or the extension re-enabled): register again
            Gio.bus_watch_name_on_connection(self.bus, WATCHER, Gio.BusNameWatcherFlags.NONE,
                                             lambda *_a: self._register(), None)
            return True
        except GLib.Error as e:
            log.warning("top-bar icon unavailable: %s", e.message)
            return False

    def _watcher_present(self) -> bool:
        res = self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                 "NameHasOwner", GLib.Variant("(s)", (WATCHER,)), GLib.VariantType("(b)"),
                                 Gio.DBusCallFlags.NONE, 2000, None)
        return res.unpack()[0]

    def _register(self):
        self.bus.call(WATCHER, "/StatusNotifierWatcher", WATCHER, "RegisterStatusNotifierItem",
                      GLib.Variant("(s)", (self.name,)), None, Gio.DBusCallFlags.NONE, 2000, None, None)

    def refresh(self):
        """Profiles or the active profile changed: rebuild the menu and the tooltip."""
        if self.bus is None:
            return
        self.revision += 1
        self.bus.emit_signal(None, MENU_PATH, "com.canonical.dbusmenu", "LayoutUpdated",
                             GLib.Variant("(ui)", (self.revision, 0)))
        self.bus.emit_signal(None, ITEM_PATH, "org.kde.StatusNotifierItem", "NewToolTip", None)
        self.bus.emit_signal(None, ITEM_PATH, "org.kde.StatusNotifierItem", "NewTitle", None)

    # -- org.kde.StatusNotifierItem ---------------------------------------------
    def _title(self) -> str:
        active = self.state()[1]
        return "PowerDeck — " + (active or _("No profile activated"))

    def _item_prop(self, _bus, _sender, _path, _iface, prop):
        values = {
            "Category": ("s", "Hardware"), "Id": ("s", "powerdeck"), "Title": ("s", self._title()),
            "Status": ("s", "Active"), "WindowId": ("i", 0), "IconName": ("s", ICON),
            "IconThemePath": ("s", self.icon_theme_path),
            "ToolTip": ("(sa(iiay)ss)", (ICON, [], "PowerDeck", self._title())),
            "ItemIsMenu": ("b", False), "Menu": ("o", MENU_PATH),
        }
        return GLib.Variant(*values[prop]) if prop in values else None

    def _item_call(self, _bus, _sender, _path, _iface, method, _params, inv):
        if method in ("Activate", "SecondaryActivate"):
            GLib.idle_add(self._once, self.on_open)
        inv.return_value(None)

    # -- com.canonical.dbusmenu --------------------------------------------------
    def _menu_prop(self, _bus, _sender, _path, _iface, prop):
        values = {"Version": ("u", 3), "TextDirection": ("s", "ltr"), "Status": ("s", "normal"),
                  "IconThemePath": ("as", [self.icon_theme_path] if self.icon_theme_path else [])}
        return GLib.Variant(*values[prop]) if prop in values else None

    def _items(self) -> list[tuple[int, dict]]:
        self._profiles, self._active = self.state()
        items = menu_items(self._profiles, self._active)
        # separators need ids too; give them unique ones above the profiles
        return [(i if i > 0 else 10_000 - i, p) for i, p in items]

    def layout(self) -> GLib.Variant:
        children = [GLib.Variant("(ia{sv}av)", (i, _variant_props(p), [])) for i, p in self._items()]
        return GLib.Variant("(u(ia{sv}av))", (self.revision, (0, {"children-display":
                                                                 GLib.Variant("s", "submenu")}, children)))

    def _menu_call(self, _bus, _sender, _path, _iface, method, params, inv):
        if method == "GetLayout":
            inv.return_value(self.layout())
        elif method == "GetGroupProperties":
            ids = set(params.unpack()[0])
            props = [(i, _variant_props(p)) for i, p in self._items() if not ids or i in ids]
            inv.return_value(GLib.Variant("(a(ia{sv}))", (props,)))
        elif method == "GetProperty":
            item_id, name = params.unpack()
            props = dict(self._items()).get(item_id, {})
            if name in props:
                inv.return_value(GLib.Variant("(v)", (_variant_props({name: props[name]})[name],)))
            else:
                inv.return_dbus_error("com.canonical.dbusmenu.Error", "no such property")
        elif method == "Event":
            item_id, event = params.unpack()[:2]
            if event == "clicked":
                self.clicked(item_id)
            inv.return_value(None)
        elif method == "EventGroup":
            for item_id, event, _data, _ts in params.unpack()[0]:
                if event == "clicked":
                    self.clicked(item_id)
            inv.return_value(GLib.Variant("(ai)", ([],)))
        elif method == "AboutToShow":
            inv.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            inv.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            inv.return_value(None)

    def clicked(self, item_id: int) -> None:
        if item_id == OPEN_ID:
            GLib.idle_add(self._once, self.on_open)
        elif item_id == RESTORE_ID:
            GLib.idle_add(self._once, self.on_restore)
        elif item_id == QUIT_ID:
            GLib.idle_add(self._once, self.on_quit)
        elif PROFILE_BASE <= item_id < PROFILE_BASE + len(self._profiles):
            name = self._profiles[item_id - PROFILE_BASE]
            GLib.idle_add(self._once, lambda: self.on_profile(name))

    @staticmethod
    def _once(fn):
        fn()
        return False
