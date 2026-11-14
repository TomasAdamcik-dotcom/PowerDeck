#!/usr/bin/env python3
"""Render the GUI to a PNG for the docs (works headless):

    xvfb-run -a python3 scripts/screenshot.py docs/screenshot.png --active RemoteWork --select Rucksack

Uses a throw-away config/state dir, so your real profiles and active profile are not touched.
"""
import argparse
import json
import os
import sys
import tempfile

ap = argparse.ArgumentParser()
ap.add_argument("out")
ap.add_argument("--active", help="profile shown as active (framed, title bar)")
ap.add_argument("--select", help="profile selected in the list")
ap.add_argument("--width", type=int, default=1000)
ap.add_argument("--height", type=int, default=1400)
a = ap.parse_args()

tmp = tempfile.mkdtemp()
os.environ["XDG_CONFIG_HOME"] = os.path.join(tmp, "config")
os.environ["XDG_STATE_HOME"] = os.path.join(tmp, "state")
if a.active:
    os.makedirs(os.path.join(tmp, "state", "powerdeck"))
    with open(os.path.join(tmp, "state", "powerdeck", "active.json"), "w") as f:
        json.dump({"profile": a.active}, f)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from gi.repository import GLib, Gtk  # noqa: E402

from powerdeck import gtk_app  # noqa: E402

app = gtk_app.App()


def select():
    w = app.props.active_window
    w.set_default_size(a.width, a.height)
    row = w.listbox.get_first_child()
    while row is not None and a.select and row.profile.name != a.select:
        row = row.get_next_sibling()
    if row is not None and a.select:
        w.listbox.select_row(row)
    GLib.timeout_add(2500, snap, w)  # let the 2 s monitor tick fill in values
    return False


def snap(w):
    snapshot = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(w).snapshot(snapshot, w.get_width(), w.get_height())
    w.get_native().get_renderer().render_texture(snapshot.to_node(), None).save_to_png(a.out)
    print("saved", a.out)
    app.quit()
    return False


GLib.timeout_add(800, select)
app.run([])
