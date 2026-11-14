"""Static texts and CSS of the GTK front-end (pure data: no GTK, testable).

Strings are English; the GUI passes them through ``i18n._`` when it builds the widgets.
"""

# Profile bullets: half the default vertical padding of an Adw.ActionRow (50 px row, ~17 px text).
COMPACT_CSS = """
list.boxed-list > row.pd-compact, row.pd-compact { min-height: 0; padding-top: 0; padding-bottom: 0; }
row.pd-compact > box.header { min-height: 0; padding-top: 2px; padding-bottom: 2px; }  /* + 6 px title padding = 8 px */
/* active profile tile in the sidebar: accent frame, no layout shift */
.navigation-sidebar > row.pd-active { box-shadow: inset 0 0 0 2px @accent_bg_color; }
row.pd-compact button.pd-info { min-height: 24px; min-width: 24px; padding: 0; margin-top: -4px; margin-bottom: -4px; }
"""

# "Status" rows, top to bottom: (key, title). CPU temperature lives only in "Monitor" (live), and
# the active profile is shown in the title bar and framed in the list, so neither is repeated here.
STATUS_ROWS = (
    ("battery", "Battery"),
    ("conn", "Connection"),
    ("mode", "Power mode"),
    ("wifips", "Wi-Fi powersave"),
    ("sessions", "Sessions"),
)

# "Monitor" rows, top to bottom: (key, title, with a level bar)
MONITOR_ROWS = (
    ("temp", "CPU temperature", True),
    ("freq", "CPU frequency", True),
    ("load", "CPU load", True),
    ("power", "Power draw", False),
    ("fan", "Fan", False),
)

# Tooltips of the Status / Monitor rows: (what it means, where the value comes from)
STATUS_HELP = {
    "active": ("Profile applied last by PowerDeck. Changes made outside PowerDeck are not detected.",
               "cat ~/.local/state/powerdeck/active.json"),
    "battery": ("Charge in % (average over all batteries) and charging state from the kernel.",
                "cat /sys/class/power_supply/BAT*/capacity /sys/class/power_supply/BAT*/status"),
    "conn": ("First active Wi-Fi, Ethernet or mobile connection in NetworkManager.",
             "nmcli -t -f NAME,TYPE connection show --active"),
    "mode": ("Current power-profiles-daemon mode: power-saver, balanced or performance.",
             "powerprofilesctl get"),
    "wifips": ("Real power-save state of the Wi-Fi adapter right now (not the saved profile value).",
               "iw dev <interface> get power_save"),
    "sessions": ("Established SSH (port 22) and RDP (port 3389) connections and login sessions.",
                 "ss -Htn state established sport = :22\nss -Htn state established sport = :3389\n"
                 "loginctl list-sessions --no-legend"),
}
MONITOR_HELP = {
    "temp": ("Hottest CPU thermal zone (x86_pkg_temp, coretemp, k10temp…); compared with the "
             "active profile's thermal limits.", "cat /sys/class/thermal/thermal_zone*/{type,temp}"),
    "freq": ("Average current clock of all cores, % of the hardware maximum, the configured cap "
             "and turbo state.", "cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq"),
    "load": ("Share of CPU time not idle since the previous sample (every 2 s).", "head -1 /proc/stat"),
    "power": ("Battery power: + discharging, − charging. Not reported by every laptop.",
              "cat /sys/class/power_supply/BAT*/power_now"),
    "fan": ("Fastest fan reported by hwmon. Not reported by every laptop.",
            "cat /sys/class/hwmon/hwmon*/fan*_input"),
}
