"""History graph: CPU temperature and CPU frequency over time, profile switches as vertical lines.

Two panels on one shared time axis (two units would need two y-scales in one panel, which
misleads), each with one line; a profile switch is a dashed line through both, with its name.
Hovering shows the values at that moment. Data comes from ``history`` (recorded by the guard).
"""
from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Adw, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from . import cpu, history  # noqa: E402
from .i18n import _  # noqa: E402

RANGES = ((3600, "1 hour"), (6 * 3600, "6 hours"), (24 * 3600, "24 hours"), (7 * 24 * 3600, "7 days"))
# (light, dark) line colours: orange = temperature, blue = frequency (validated pair)
TEMP_RGB = ((0xEB, 0x68, 0x34), (0xD9, 0x59, 0x26))
FREQ_RGB = ((0x2A, 0x78, 0xD6), (0x39, 0x87, 0xE5))
LEFT, RIGHT, TOP, BOTTOM, GAP = 44, 12, 20, 22, 30


def _rgb(c, alpha=1.0):
    return c[0] / 255, c[1] / 255, c[2] / 255, alpha


class HistoryGraph(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        top = Gtk.Box(spacing=8)
        top.append(Gtk.Label(label=_("Recorded every 20 s by the PowerDeck guard; kept for 7 days."),
                             css_classes=["dim-label"], xalign=0, hexpand=True, wrap=True))
        self.range_dd = Gtk.DropDown.new_from_strings([_(label) for _s, label in RANGES])
        self.range_dd.connect("notify::selected", lambda *_a: self.reload())
        top.append(self.range_dd)
        self.append(top)
        self.area = Gtk.DrawingArea(content_height=320, hexpand=True, has_tooltip=True)
        self.area.set_draw_func(self._draw)
        self.area.connect("query-tooltip", self._tooltip)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda _c, x, _y: self._hover(x))
        motion.connect("leave", lambda _c: self._hover(None))
        self.area.add_controller(motion)
        self.append(self.area)
        self.points: list[history.Point] = []
        self.end = time.time()
        self.hover_x: float | None = None
        self.hw_mhz = cpu.read_freqs()[1]

    @property
    def span(self) -> int:
        return RANGES[self.range_dd.get_selected()][0]

    def reload(self):
        self.end = time.time()
        self.points = history.read(since=self.end - self.span)
        self.area.queue_draw()

    # -- geometry ----------------------------------------------------------------
    def _panels(self, h: float) -> tuple[tuple[float, float], tuple[float, float]]:
        ph = max(20.0, (h - TOP - BOTTOM - GAP) / 2)
        return (TOP, ph), (TOP + ph + GAP, ph)

    def _x(self, ts: float, w: float) -> float:
        return LEFT + (ts - (self.end - self.span)) / self.span * (w - LEFT - RIGHT)

    def _nearest(self, x: float, w: float) -> history.Point | None:
        pw = w - LEFT - RIGHT
        if not self.points or pw <= 0 or not LEFT <= x <= w - RIGHT:
            return None
        ts = self.end - self.span + (x - LEFT) / pw * self.span
        p = min(self.points, key=lambda p: abs(p.ts - ts))
        return p if abs(p.ts - ts) <= max(2 * history.INTERVAL_S, self.span / pw * 4) else None

    def _hover(self, x):
        self.hover_x = x
        self.area.queue_draw()

    def _tooltip(self, area, x, _y, _kb, tip):
        p = self._nearest(x, area.get_width())
        if p is None:
            return False
        lines = [time.strftime("%a %H:%M:%S", time.localtime(p.ts)),
                 _("CPU temperature") + ": " + ("–" if p.temp_c is None else f"{p.temp_c:.0f} °C"),
                 _("CPU frequency") + ": " + ("–" if p.freq_mhz is None else f"{p.freq_mhz / 1000:.2f} GHz"),
                 _("Profile") + ": " + (p.profile or _("none"))]
        tip.set_text("\n".join(lines))
        return True

    # -- drawing -------------------------------------------------------------------
    def _text(self, cr, x, y, text, rgba, size=8.5, align=0.0):
        layout = self.area.create_pango_layout(text)
        fd = self.area.get_pango_context().get_font_description().copy()
        fd.set_size(int(size * Pango.SCALE))
        layout.set_font_description(fd)
        tw = layout.get_pixel_size()[0]
        cr.set_source_rgba(*rgba)
        cr.move_to(x - tw * align, y)
        PangoCairo.show_layout(cr, layout)

    def _draw(self, area, cr, w, h):
        dark = Adw.StyleManager.get_default().get_dark()
        fg = area.get_color()
        ink = (fg.red, fg.green, fg.blue)
        muted, grid = (*ink, 0.6), (*ink, 0.12)
        (t_y, ph), (f_y, _ph) = self._panels(h)
        start = self.end - self.span
        if not self.points:
            self._text(cr, w / 2, h / 2 - 8, _("No data yet: the PowerDeck guard records every 20 s."),
                       muted, 10, 0.5)
            return
        temps = [p.temp_c for p in self.points if p.temp_c is not None]
        freqs = [p.freq_mhz / 1000 for p in self.points if p.freq_mhz is not None]
        panels = (
            (t_y, _("CPU temperature (°C)"), history.axis_range(temps, 40, 80, 3), "{:g}°",
             lambda p: p.temp_c, TEMP_RGB[dark]),
            (f_y, _("CPU frequency (GHz)"), (0.0, max([*freqs, (self.hw_mhz or 0) / 1000, 1.0])), "{:g}",
             lambda p: None if p.freq_mhz is None else p.freq_mhz / 1000, FREQ_RGB[dark]),
        )
        cr.set_line_width(1)
        for y0, title, (lo, hi), fmt, value, rgb in panels:
            self._text(cr, LEFT, y0 - 17, title, muted, 9)
            y = lambda v, y0=y0, lo=lo, hi=hi: y0 + ph - (v - lo) / (hi - lo) * ph
            for tick in history.value_ticks(lo, hi, 4):
                cr.set_source_rgba(*grid)
                cr.move_to(LEFT, round(y(tick)) + 0.5)
                cr.line_to(w - RIGHT, round(y(tick)) + 0.5)
                cr.stroke()
                self._text(cr, LEFT - 6, y(tick) - 7, fmt.format(tick), muted, 8, 1.0)
            cr.save()
            cr.rectangle(LEFT, y0, w - LEFT - RIGHT, ph)
            cr.clip()
            cr.set_source_rgba(*_rgb(rgb))
            cr.set_line_width(2)
            for seg in history.segments(self.points):
                pts = [(self._x(p.ts, w), y(v)) for p in seg if (v := value(p)) is not None]
                if len(pts) == 1:  # a lone reading still shows
                    cr.arc(*pts[0], 2, 0, 6.3)
                    cr.fill()
                for i, (px, py) in enumerate(pts):
                    (cr.line_to if i else cr.move_to)(px, py)
                cr.stroke()
            cr.restore()
            cr.set_line_width(1)
        bottom = f_y + ph
        # profile switches: dashed line through both panels, name at the top
        cr.set_dash([4, 3])
        for ts, name in history.profile_marks(self.points):
            x = round(self._x(ts, w)) + 0.5
            cr.set_source_rgba(*ink, 0.55)
            cr.move_to(x, t_y)
            cr.line_to(x, bottom)
            cr.stroke()
            self._text(cr, x + 3, t_y + 1, name or _("none"), (*ink, 0.85), 8)
        cr.set_dash([])
        # time axis
        fmt = "%H:%M" if self.span <= 24 * 3600 else "%a %d."
        for tick in history.time_ticks(start, self.end):
            x = self._x(tick, w)
            cr.set_source_rgba(*grid)
            cr.move_to(round(x) + 0.5, bottom)
            cr.line_to(round(x) + 0.5, bottom + 4)
            cr.stroke()
            self._text(cr, x, bottom + 5, time.strftime(fmt, time.localtime(tick)), muted, 8, 0.5)
        # hover: crosshair + dots at the nearest reading
        p = self._nearest(self.hover_x, w) if self.hover_x is not None else None
        if p is not None:
            x = self._x(p.ts, w)
            cr.set_source_rgba(*ink, 0.35)
            cr.move_to(round(x) + 0.5, t_y)
            cr.line_to(round(x) + 0.5, bottom)
            cr.stroke()
            for y0, _t, (lo, hi), _f, value, rgb in panels:
                if (v := value(p)) is not None:
                    cr.set_source_rgba(*_rgb(rgb))
                    cr.arc(x, y0 + ph - (v - lo) / (hi - lo) * ph, 4, 0, 6.3)
                    cr.fill()


class HistoryPanel(Adw.PreferencesGroup):
    """The graph at the top of the window, shown by the header-bar button; it refreshes every
    20 s while shown and costs nothing while hidden."""

    def __init__(self):
        super().__init__(title=_("Temperature and frequency history"), visible=False)
        self.graph = HistoryGraph()
        self.add(self.graph)
        self._timer = 0

    def toggle(self, show: bool):
        self.set_visible(show)
        if show:
            self.graph.reload()
            if not self._timer:
                self._timer = GLib.timeout_add_seconds(history.INTERVAL_S, self._tick)
        elif self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _tick(self):
        if self.get_mapped():
            self.graph.reload()
        return True
