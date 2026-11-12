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
