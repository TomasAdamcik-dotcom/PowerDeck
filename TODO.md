# PowerDeck — stav a čo ostáva

Stav: v0.3. Všetko je implementované a pokryté testami (35 testov proti falošnému systému, GUI smoke test v headless Xvfb, `.deb` sa zbuilduje a nainštaluje v kontajneri). **Nič nebolo vyskúšané na reálnom notebooku** – to je hlavný zostávajúci krok.

## 0. Overenie na reálnom stroji (Ubuntu 26.04) — vyžaduje teba

Bezpečné (nič nemenia):
- [ ] `python3 -m powerdeck.cli status` – batéria, teplota, SSH/RDP, Wi‑Fi powersave: zmysluplné hodnoty? (správne `thermal_zone` pre CPU?)
- [ ] `python3 -m powerdeck.cli plan RemoteWork` / `plan Travel` – zodpovedá diff skutočnému stavu?
- [ ] `python3 -m powerdeck.gtk_app` – okno, stav, diff dialóg, editor profilov vyzerajú dobre; prepni Svetlý/Tmavý štýl v Nastaveniach → Vzhľad a over, že sa appka prepne hneď (bez reštartu)
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

## 0b. Test limitu CPU a monitoringu (nové, treba reálny HW)

Najprv bez zmien:
- [ ] `cat /sys/devices/system/cpu/intel_pstate/no_turbo` (Intel) alebo `cat /sys/devices/system/cpu/cpufreq/boost` (AMD) – existuje aspoň jeden? Ak nie, turbo sa preskočí (stav `n/a`)
- [ ] `ls /sys/devices/system/cpu/cpu0/cpufreq/` – sú `scaling_max_freq`, `cpuinfo_max_freq`, `scaling_cur_freq`? (pozor: pri `intel_pstate` v aktívnom režime sa `scaling_max_freq` správa trochu inak)
- [ ] `powerdeck monitor --duration 10` – zmysluplné hodnoty? Je `power`/`fan` k dispozícii (závisí od modelu; `n/a` je v poriadku)

Meranie vplyvu (odporúčaný postup, pri rovnakej záťaži, napr. `stress-ng --cpu 0 --timeout 120s` alebo reálna Claude/dev úloha):
1. `powerdeck monitor --interval 2 --duration 150 --csv /tmp/default.csv` počas záťaže v profile **Default**
2. `powerdeck apply Travel` (turbo off + max 70 %) a to isté: `--csv /tmp/travel.csv`; potom to isté pre `Rucksack` (max 50 %)
3. Porovnať teplotu, frekvenciu, príkon (CSV stĺpce `temp_c`, `freq_mhz`, `power_w`, `fan_rpm`) → aká je strata výkonu vs. zníženie teploty? Podľa toho doladiť 70 % / turbo v profiloch.
- [ ] `powerdeck restore` vráti turbo a max. frekvenciu (over cez monitor: `limit 100%`, `turbo on`)
- [ ] Po reštarte sú limity späť na predvolené (sysfs sa nezachováva) – to je zámer; ak ich chceš trvalé, treba systemd službu pri štarte
- [ ] Prvý `pkexec` dialóg ukáže nový text „Change laptop power settings (lid behaviour, CPU limits)“; po zmene politiky treba znova `sudo scripts/install-helper.sh` (alebo nový `.deb`)
- [ ] GUI: skupina „Monitoring“ sa obnovuje každé 2 s, level bary fungujú, nespomaľuje to appku

## 1. Hotové (od v0.1)

- Polkit politika + root-owned helper `/usr/libexec/powerdeck-helper`, inštalačný skript, GUI hlási chýbajúci helper
- Tethering autoconnect + priorita (`tethering` v profile)
- Jas, nočný režim, blank/lock timery (`display`), Bluetooth (`bluetooth`)
- Úlohy `on_activate`/`on_deactivate` – **iba** `systemctl --user start|stop|restart UNIT` (whitelist; validované pri načítaní profilu)
- Thermal guard (`powerdeck guard`): notifikácia pri `warn`, pri `critical` prepne na power-saver + notifikácia; upozornenie pri nízkej batérii s ignorovaným vekom; udalosti sa spúšťajú len pri zhoršení (bez spamu)
- Stav: SSH/RDP spojenia, login sessions, skutočný Wi‑Fi powersave (`iw`), priemer cez viac batérií, výber CPU senzora podľa typu zóny
- Profily `Travel`, `Rucksack`, `Presentation`; Gaming „návrat“ = `restore`
- `snapshot` – Default zo skutočného stavu systému
- Viacnásobný rollback (každé `restore` vráti ďalšiu zálohu)
- Editor/tvorba profilov v GUI, chybové dialógy, ikona, `.desktop`, AppStream
- Logy: `~/.local/state/powerdeck/logs/powerdeck.log`
- Limit CPU v profile (`cpu`: `turbo`, `max_freq_percent`) cez root helper (`set-cpu-turbo`, `set-cpu-max-percent`, len whitelistované hodnoty 10–100 %); Travel = turbo off + 70 %, **Rucksack = najprísnejší (turbo off, max 50 %, power-saver, BT off, thermal 70/82 °C)**, Default/Gaming ho vracajú na 100 %/turbo on; `snapshot` ho zachytáva
- Monitoring: `powerdeck monitor [--csv FILE] [--duration N]` + živá skupina v GUI (teplota, frekvencia, limit, turbo, záťaž, príkon, ventilátor)
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
