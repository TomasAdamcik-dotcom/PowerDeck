# PowerDeck — čo chýba / ďalšie kroky

Stav: v0.1 (jadro + CLI + GTK4 appka). **Zatiaľ netestované na reálnom notebooku** – testy bežia len proti falošnému systému (`tests/test_core.py`).

## 0. Najprv: overenie na reálnom stroji (Ubuntu 26.04)

Bezpečné (nič nemenia):
- [ ] `python3 -m powerdeck.cli list` a `status` – vypíše sa niečo rozumné?
- [ ] `python3 -m powerdeck.cli plan RemoteWork` – zodpovedá diff skutočnému stavu?
- [ ] `python3 -m powerdeck.gtk_app` – otvorí sa okno, stav sa zobrazuje, diff dialóg vyzerá dobre
- [ ] `gsettings list-keys org.gnome.settings-daemon.plugins.power` – existujú `sleep-inactive-*-type`? (kľúče `lid-close-*` appka zámerne nepoužíva)
- [ ] `man nm-settings-nmcli` – potvrdiť hodnoty `802-11-wireless.powersave` (0 default, 1 ignore, 2 off, 3 on)

Zmeny (až po záložke vyššie):
- [ ] `apply RemoteWork` → `pkexec` sa spýta na heslo, vznikne `/etc/systemd/logind.conf.d/99-powerdeck-lid.conf`
- [ ] Overiť, že reload `logind` cez `systemctl kill -s HUP systemd-logind` naozaj prečíta drop-in (inak treba reštart/odhlásenie)
- [ ] Test zavretia veka 3–5 min + ping/SSH z telefónu
- [ ] `restore` vráti pôvodný stav (drop-in zmizne, gsettings/nmcli späť)
- [ ] Zlyhanie `pkexec` (zrušený dialóg) → appka zobrazí chybu a stav ostane konzistentný

## 1. Polkit politika (bezpečnosť/UX)

Bez nej `pkexec` funguje, ale ukáže generický dialóg a vyžaduje cestu k interpreteru.
- [ ] `data/io.github.powerdeck.policy` – akcia `io.github.powerdeck.set-lid` s popisom po slovensky/anglicky, `auth_admin_keep`
- [ ] Nainštalovať helper na pevnú cestu (napr. `/usr/libexec/powerdeck-helper`, root-owned) a v policy použiť `org.freedesktop.policykit.exec.path` na túto cestu – **nie** na `python3`
- [ ] `backends._helper_argv` prepnúť zo `sys.executable -m powerdeck.helper` na túto pevnú cestu (teraz by pkexec spustil modul z používateľom zapisovateľného miesta = riziko eskalácie)
- [ ] Inštalačný skript / balík (`.deb`) ktorý to všetko nainštaluje

## 2. Chýbajúce funkcie zo špecifikácie

- [ ] Autoconnect + `autoconnect-priority` pre tethering profil (NetworkManager)
- [ ] Jas obrazovky, nočný režim
- [ ] Bluetooth (rfkill), USB autosuspend (root, závislé na HW)
- [ ] Úlohy `on_activate` / `on_deactivate` (`systemctl --user …`); profily s úlohami = spúšťanie príkazov → zobraziť v diffe, importované profily nikdy automaticky
- [ ] `display.blank_after_minutes` / `lock_after_minutes` (gsettings `idle-delay`, `lock-delay`)
- [ ] Thermal guard: dnes len zobrazenie teploty; chýba notifikácia pri `warn_celsius` a reakcia pri `critical_celsius` (napr. prepnutie na power-saver / návrat na Default)
- [ ] Ochrana pri nízkej batérii s ignorovaným vekom (upozornenie / návrat na bezpečný profil)
- [ ] Zobrazenie aktívnych session (SSH/RDP/Claude) v stave – `loginctl`, `ss`
- [ ] Profily `Travel` a `Presentation`
- [ ] Gaming: „po skončení návrat na predchádzajúci profil“
- [ ] Obnova do `Default` ako **snapshot systému** (špecifikácia, sekcia 3 bod 6), nie len „suspend“ natvrdo; teraz `Default` predpokladá štandardné hodnoty GNOME
- [ ] Zachytenie/obnova viacerých krokov späť (dnes len posledná záloha)

## 3. GUI

- [ ] Editor/tvorba vlastných profilov (dnes len čítanie vstavaných + JSON v `~/.config/powerdeck/profiles/`)
- [ ] Zobrazenie `wifi_powersave` skutočného stavu (`iw dev … get power_save`)
- [ ] Voľba preferovaných sietí zo zoznamu uložených NM profilov
- [ ] Správne hlásenia chýb (dialóg namiesto toastu pri zlyhaní)
- [ ] Ikona, `.desktop` súbor, AppStream metadata
- [ ] Voliteľne: GNOME Shell extension / indikátor v paneli (až po v1)
- [ ] Preklady cez gettext (dnes jednoduchý slovník v `i18n.py`)

## 4. Kvalita a infraštruktúra

- [ ] Logy do `~/.local/state/powerdeck/logs/` (adresár je len v špecifikácii)
- [ ] Testy GUI (headless cez `xvfb-run`), integračné testy helpera v kontajneri
- [ ] CI (GitHub Actions: pytest)
- [ ] Zvážiť odstránenie hardkódovaných predpokladov: `thermal_zone*` ≠ vždy CPU, `BAT*` – viac batérií
- [ ] Balíčkovanie (`.deb`/Flatpak – pozor: Flatpak sandbox bráni `nmcli`/`pkexec`, pravdepodobne nevhodný)
- [ ] Licencia, verejné/súkromné repo

## 5. Otvorené otázky (zo SPECIFICATION.md)

1. Verzia GNOME na Ubuntu 26.04 a dostupné gsettings kľúče
2. HUP vs. reštart `systemd-logind` – čo je bezpečné?
3. RDP (`gnome-remote-desktop`) so zavretým vekom a zamknutou obrazovkou
4. HW používateľa: senzory teploty, `power-profiles-daemon` vs. TLP
5. GTK appka vs. extension (rozhodnuté pre v1: GTK appka + CLI)
