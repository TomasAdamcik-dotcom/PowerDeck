"""Profile editor window of the GTK front-end."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .i18n import _  # noqa: E402
from .profile import LID_ACTIONS, POWER_MODES, Profile, ProfileError, save_profile  # noqa: E402

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
        if "thermal_target_celsius" in old.cpu:  # no editor field yet: keep it
            cpu["thermal_target_celsius"] = old.cpu["thermal_target_celsius"]
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
