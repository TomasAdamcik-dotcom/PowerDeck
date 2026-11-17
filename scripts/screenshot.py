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
ap.add_argument("--history", action="store_true", help="open the history graph with made-up data")
ap.add_argument("--unfold", action="store_true", help="show the whole profile contents (folded by default)")
a = ap.parse_args()

tmp = tempfile.mkdtemp()
os.environ["XDG_CONFIG_HOME"] = os.path.join(tmp, "config")
os.environ["XDG_STATE_HOME"] = os.path.join(tmp, "state")
if a.active:
    os.makedirs(os.path.join(tmp, "state", "powerdeck"))
    with open(os.path.join(tmp, "state", "powerdeck", "active.json"), "w") as f:
        json.dump({"profile": a.active}, f)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from powerdeck import gtk_app  # noqa: E402  (selects the GTK 4 / Adw 1 versions first)
if a.history:
    import math
    import time
    from powerdeck import history
    now = time.time()
    for i in range(6 * 180):  # 6 h every 20 s, a gap (suspend) and three profile switches
        ts = now - 6 * 3600 + i * 20
        if 400 <= i < 460:
            continue
        prof = "Default" if i < 300 else "Rucksack" if i < 700 else "RemoteWork"
        base = 48 if prof == "Default" else 64 if prof == "Rucksack" else 55
        history.append(history.Point(ts, round(base + 6 * math.sin(i / 25) + (i % 7) * 0.4, 1),
                                     round((1300 if prof == "Rucksack" else 2400) + 700 * math.sin(i / 11)),
                                     prof))
from gi.repository import GLib, Gtk  # noqa: E402

app = gtk_app.App()


def select():
    w = app.props.active_window
    w.set_default_size(a.width, a.height)
    row = w.listbox.get_first_child()
    while row is not None and a.select and row.profile.name != a.select:
        row = row.get_next_sibling()
    if row is not None and a.select:
        w.listbox.select_row(row)
    if a.unfold and a.select:
        w.fold_btn.set_active(True)
    if a.history:
        w.history_btn.set_active(True)
        w.history.graph.range_dd.set_selected(1)  # 6 hours: shows the switches and the gap
    GLib.timeout_add(2500, snap, w)  # let the 2 s monitor tick fill in values
    return False


def snap(w):
    snapshot = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(w).snapshot(snapshot, w.get_width(), w.get_height())
    global failed
    failed = not w.get_native().get_renderer().render_texture(snapshot.to_node(), None).save_to_png(a.out)
    print("could not write" if failed else "saved", a.out, file=sys.stderr if failed else sys.stdout)
    app.quit()
    return False


failed = True  # until a PNG is written
GLib.timeout_add(800, select)
app.run([])
sys.exit(1 if failed else 0)
