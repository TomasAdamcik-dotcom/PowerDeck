# PowerDeck — stav a čo ostáva

Stav: v0.2. Všetko je implementované a pokryté testami (21 testov proti falošnému systému, GUI smoke test v headless Xvfb, `.deb` sa zbuilduje a nainštaluje v kontajneri). **Nič nebolo vyskúšané na reálnom notebooku** – to je hlavný zostávajúci krok.

## 0. Overenie na reálnom stroji (Ubuntu 26.04) — vyžaduje teba

Bezpečné (nič nemenia):
- [ ] `python3 -m powerdeck.cli status` – batéria, teplota, SSH/RDP, Wi‑Fi powersave: zmysluplné hodnoty? (správne `thermal_zone` pre CPU?)
- [ ] `python3 -m powerdeck.cli plan RemoteWork` / `plan Travel` – zodpovedá diff skutočnému stavu?
- [ ] `python3 -m powerdeck.gtk_app` – okno, stav, diff dialóg, editor profilov vyzerajú dobre
- [ ] `gsettings list-keys org.gnome.settings-daemon.plugins.power` a `…desktop.session`/`…screensaver` – existujú kľúče, ktoré appka používa (`sleep-inactive-*-type`, `idle-delay`, `lock-enabled`, `lock-delay`, `night-light-enabled`)?
- [ ] `man nm-settings-nmcli` – potvrdiť hodnoty `802-11-wireless.powersave` (0 default, 1 ignore, 2 off, 3 on)
- [ ] `brightnessctl -m` (ak je nainštalovaný) – formát výstupu zodpovedá parseru (`dev,class,cur,pct%,max`)?
- [ ] `rfkill list bluetooth` – obsahuje „Soft blocked“?

Zmeny (až po záložke vyššie):
- [ ] Inštalácia: `scripts/build-deb.sh && sudo apt install ./dist/powerdeck_*.deb` (alebo `sudo scripts/install-helper.sh`)
- [ ] `apply RemoteWork` → `pkexec` ukáže náš SK/EN text; vznikne `/etc/systemd/logind.conf.d/99-powerdeck-lid.conf`
- [ ] Overiť, že reload `logind` cez HUP (`systemctl kill -s HUP systemd-logind`) drop-in naozaj prečíta (inak treba reštart/odhlásenie – potom zmeniť helper)
- [ ] Test zavretia veka 3–5 min + ping/SSH z telefónu
- [ ] `restore` vráti pôvodný stav (opakované `restore` ide ďalej späť)
- [ ] Zrušený `pkexec` dialóg → appka ukáže chybu a stav ostane konzistentný
- [ ] `powerdeck guard --once` a systemd služba: `systemctl --user enable --now powerdeck-guard` – prídu notifikácie pri prekročení prahu?
- [ ] `powerdeck snapshot` – uloží aktuálne nastavenia ako profil Default (prepíše vstavaný)

## 1. Hotové (od v0.1)

- Polkit politika + root-owned helper `/usr/libexec/powerdeck-helper`, inštalačný skript, GUI hlási chýbajúci helper
- Tethering autoconnect + priorita (`tethering` v profile)
- Jas, nočný režim, blank/lock timery (`display`), Bluetooth (`bluetooth`)
- Úlohy `on_activate`/`on_deactivate` – **iba** `systemctl --user start|stop|restart UNIT` (whitelist; validované pri načítaní profilu)
- Thermal guard (`powerdeck guard`): notifikácia pri `warn`, pri `critical` prepne na power-saver + notifikácia; upozornenie pri nízkej batérii s ignorovaným vekom; udalosti sa spúšťajú len pri zhoršení (bez spamu)
- Stav: SSH/RDP spojenia, login sessions, skutočný Wi‑Fi powersave (`iw`), priemer cez viac batérií, výber CPU senzora podľa typu zóny
- Profily `Travel`, `Presentation`; Gaming „návrat“ = `restore`
- `snapshot` – Default zo skutočného stavu systému
- Viacnásobný rollback (každé `restore` vráti ďalšiu zálohu)
- Editor/tvorba profilov v GUI, chybové dialógy, ikona, `.desktop`, AppStream
- Logy: `~/.local/state/powerdeck/logs/powerdeck.log`
- CI (`.github/workflows/tests.yml`), `.deb` (`scripts/build-deb.sh`)

## 2. Ostáva — potrebuje tvoje rozhodnutie alebo reálny HW

- [ ] **Licencia** (AppStream momentálne `LicenseRef-proprietary`) a verejné/súkromné repo – rozhodnutie vlastníka
- [ ] **Maintainer v `.deb`** je placeholder (`noreply@example.invalid`)
- [ ] **USB autosuspend** – závislé na HW (sysfs/udev/TLP, root); netýka sa bezpečne bez konkrétneho zariadenia
- [ ] **Voľba sietí zo zoznamu uložených NM profilov** v editore (dnes text oddelený čiarkou) – dá sa, ale je to UX rozhodnutie
- [ ] **Mazanie vlastných profilov** z GUI
- [ ] **Gaming**: automatický návrat po skončení hry (detekcia procesu) – dnes ručne cez Restore
- [ ] **GUI testy v CI** (headless GTK vyžaduje balíky na runneri); smoke test je len ručný (`xvfb-run`)
- [ ] **gettext** namiesto slovníka v `i18n.py` (až keď sa texty ustália)
- [ ] **Edit sloty špecifické pre dock/AC** v editore (dnes jedna hodnota pre všetky tri stavy veka)
- [ ] Voliteľne: GNOME Shell extension/indikátor v paneli (po v1)
- [ ] Odmietnuť/zvážiť Flatpak: sandbox bráni `nmcli`/`pkexec`

## 3. Otvorené otázky

1. Verzia GNOME na Ubuntu 26.04 a dostupné gsettings kľúče
2. HUP vs. reštart `systemd-logind` – čo je bezpečné?
3. RDP (`gnome-remote-desktop`) so zavretým vekom a zamknutou obrazovkou
4. HW používateľa: senzory teploty, `power-profiles-daemon` vs. TLP
5. Thermal guard reaguje len prepnutím na power-saver – stačí, alebo má pri critical aj aplikovať Default?
