"""Tiny SK/EN translation layer (replace with gettext when strings stabilise)."""
import os

SK = {
    "PowerDeck — Linux workstation profiles": "PowerDeck — profily pre Linux notebooky",
    "Profiles": "Profily",
    "Status": "Stav",
    "Active profile": "Aktívny profil",
    "none": "žiadny",
    "Battery": "Batéria",
    "CPU temperature": "Teplota CPU",
    "Connection": "Pripojenie",
    "Power mode": "Režim napájania",
    "Apply…": "Aplikovať…",
    "Enable Remote Work": "Zapnúť Remote Work",
    "Restore previous": "Obnoviť predošlý stav",
    "Apply profile “{}”?": "Aplikovať profil „{}“?",
    "The following changes will be made:": "Vykonajú sa tieto zmeny:",
    "Administrator permission will be requested.": "Bude vyžiadané oprávnenie správcu.",
    "Overheating risk: do not keep the laptop in a closed bag for long.":
        "Riziko prehriatia: nenechávaj notebook dlho v uzavretej taške.",
    "Cancel": "Zrušiť",
    "Apply": "Aplikovať",
    "Applied “{}”.": "Profil „{}“ bol aplikovaný.",
    "Restored previous settings.": "Pôvodné nastavenia boli obnovené.",
    "No changes needed.": "Netreba nič meniť.",
    "unknown": "neznáme",
    "Wi-Fi powersave": "Wi-Fi powersave",
    "Sessions": "Relácie",
    "Login sessions": "Prihlásenia",
    "New profile": "Nový profil",
    "Edit": "Upraviť",
    "Edit profile": "Upraviť profil",
    "Name": "Názov",
    "Description": "Popis",
    "Lid close": "Zavretie veka",
    "Automatic suspend": "Automatické uspanie",
    "Allowed": "Povolené",
    "Disabled": "Vypnuté",
    "Wi-Fi networks (comma separated)": "Wi-Fi siete (oddelené čiarkou)",
    "Tethering connection": "Tethering pripojenie",
    "Save": "Uložiť",
    "Monitor": "Monitoring",
    "CPU frequency": "Frekvencia CPU",
    "CPU load": "Záťaž CPU",
    "Power draw": "Príkon",
    "Fan": "Ventilátor",
    "limit": "limit",
    "CPU turbo": "Turbo CPU",
    "CPU max frequency": "Max. frekvencia CPU",
    "Admin helper is not installed.": "Admin pomocník nie je nainštalovaný.",
    "Install it once with:": "Nainštaluj ho jednorazovo príkazom:",
    "Something went wrong": "Niečo sa pokazilo",
    "ok": "v poriadku",
    "warn": "varovanie",
    "critical": "kritické",
}


def _lang() -> str:
    for v in ("LC_ALL", "LC_MESSAGES", "LANG"):
        if os.environ.get(v):
            return os.environ[v][:2]
    return "en"


def _(s: str) -> str:
    return SK.get(s, s) if _lang() == "sk" else s
