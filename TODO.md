# PowerDeck — stav a čo ostáva

Stav: v0.3. Overenie na reálnom HW (ThinkPad T14s Gen 4, i7 intel_pstate, Ubuntu 26.04) prebieha – výsledky sú pri položkách nižšie. Všetko je implementované a pokryté testami (35 testov proti falošnému systému, GUI smoke test v headless Xvfb, `.deb` sa zbuilduje a nainštaluje v kontajneri). Ostáva najmä test veka a GUI.

## 0. Overenie na reálnom stroji (Ubuntu 26.04) — vyžaduje teba

Bezpečné (nič nemenia):
- [x] `python3 -m powerdeck.cli status` – batéria, teplota, SSH/RDP, Wi‑Fi powersave: zmysluplné hodnoty? (správne `thermal_zone` pre CPU?)
- [x] `python3 -m powerdeck.cli plan RemoteWork` / `plan Travel` – zodpovedá diff skutočnému stavu?
- [ ] `python3 -m powerdeck.gtk_app` – okno, stav, diff dialóg, editor profilov vyzerajú dobre; prepni Svetlý/Tmavý štýl v Nastaveniach → Vzhľad a over, že sa appka prepne hneď (bez reštartu)
- [x] `gsettings list-keys org.gnome.settings-daemon.plugins.power` a `…desktop.session`/`…screensaver` – existujú kľúče, ktoré appka používa (`sleep-inactive-*-type`, `idle-delay`, `lock-enabled`, `lock-delay`, `night-light-enabled`)?
- [ ] `man nm-settings-nmcli` – potvrdiť hodnoty `802-11-wireless.powersave` (0 default, 1 ignore, 2 off, 3 on)
- [x] `brightnessctl -m` (ak je nainštalovaný) – formát výstupu zodpovedá parseru (`dev,class,cur,pct%,max`)? ✓ `intel_backlight,backlight,9793,50%,19393`
- [x] `rfkill list bluetooth` – obsahuje „Soft blocked“?

Zmeny (až po záložke vyššie):
- [x] Inštalácia: `scripts/build-deb.sh && sudo apt install ./dist/powerdeck_*.deb` (alebo `sudo scripts/install-helper.sh`)
- [x] `apply RemoteWork` → `pkexec` ukáže náš SK/EN text; vznikne `/etc/systemd/logind.conf.d/99-powerdeck-lid.conf`
- [x] Veko vo VM end-to-end (simulovaný SW_LID): s drop-inom RemoteWork sa nezaspí, po `restore` zatvorenie veka uspí ✓
- [x] Overiť, že reload `logind` cez HUP (`systemctl kill -s HUP systemd-logind`) drop-in naozaj prečíta (inak treba reštart/odhlásenie – potom zmeniť helper) ✓ `busctl` hneď hlási `HandleLidSwitch=ignore`
- [ ] Test zavretia veka 3–5 min + ping/SSH z telefónu – systém nezaspí ✓; RDP (desktop sharing) padal: gsd-power pri zatvorenom veku a zablokovanom suspende (Claude Desktop inhibitor) zapne šetrič a gnome-shell v režime `unlock-dialog` zakáže remote desktop. Riešenie: `remote_access` profily + guard zruší šetrič (overiť s vekom), Remote Login ako robustná cesta. **VM (2026-11-15):** guard ako systemd služba zruší šetrič do 0,1 s a nová RDP session (Mutter RemoteDesktop) je hneď možná; bez guardu „Session creation inhibited“. Cestu veko → šetrič vo VM nejde zopakovať (QEMU displej = externý monitor), preto treba ešte test na notebooku
- [x] Test veka s RDP na notebooku (2026-11-16): pripojenie pri zatvorenom veku funguje a drží ✓; bežiace spojenie pri zatvorení veka raz spadne (gnome-shell ho zruší ~2 s pred signálom ActiveChanged, guard ho teda nevie predísť), guard zruší šetrič a opätovné pripojenie ide ✓. Admin heslo, ktoré pri tom vyskočilo, nebolo od RDP, ale od `powerdeck status` (`grdctl --system status` ide cez pkexec) – opravené, stav Remote Login sa číta cez `systemctl is-enabled gnome-remote-desktop.service`
- [ ] Diakritika cez RDP chýba (á, í, ž…): gnome-remote-desktop hlási „No keycode found for keyval“, keď aktívne rozloženie na notebooku je `gb` – prepnúť na `sk` alebo nastaviť rozloženie v RDP klientovi; nie je to chyba PowerDecku
- [x] GNOME Remote Login otestovaný (2026-11-16, Windows App z telefónu): **nefunguje** – po RDP prihlásení čierna obrazovka, lebo služba handover na prihlasovacej obrazovke GDM štartuje 32–35 s a systémový daemon to po 30 s vzdá („Aborting handover“; Launchpad #2141992). Každý pokus nechá bežať prihlasovaciu obrazovku (~0,8 GB RAM) až do vypnutia Remote Login. Zatiaľ platí zdieľanie plochy + guard; znova vyskúšať po aktualizácii gnome-remote-desktop
- [x] `restore` vráti pôvodný stav (opakované `restore` ide ďalej späť)
- [ ] Zrušený `pkexec` dialóg → appka ukáže chybu a stav ostane konzistentný
- [ ] `powerdeck guard --once` a systemd služba: `systemctl --user enable --now powerdeck-guard` – prídu notifikácie pri prekročení prahu? (VM: služba sa zapne a beží, notifikácie pri teplote neoverené – VM nemá senzor)
- [ ] `powerdeck snapshot` – uloží aktuálne nastavenia ako profil Default (prepíše vstavaný)

## 0b. Test limitu CPU a monitoringu (nové, treba reálny HW)

Najprv bez zmien:
- [x] `cat /sys/devices/system/cpu/intel_pstate/no_turbo` (Intel) alebo `cat /sys/devices/system/cpu/cpufreq/boost` (AMD) – existuje aspoň jeden? Ak nie, turbo sa preskočí (stav `n/a`)
- [x] `ls /sys/devices/system/cpu/cpu0/cpufreq/` – sú `scaling_max_freq`, `cpuinfo_max_freq`, `scaling_cur_freq`? (pozor: pri `intel_pstate` v aktívnom režime sa `scaling_max_freq` správa trochu inak)
- [x] `powerdeck monitor --duration 10` – zmysluplné hodnoty? Je `power`/`fan` k dispozícii (závisí od modelu; `n/a` je v poriadku)

Meranie vplyvu (odporúčaný postup, pri rovnakej záťaži, napr. `stress-ng --cpu 0 --timeout 120s` alebo reálna Claude/dev úloha):
1. `powerdeck monitor --interval 2 --duration 150 --csv /tmp/default.csv` počas záťaže v profile **Default**
2. `powerdeck apply Travel` (turbo off, bez % limitu) a to isté: `--csv /tmp/travel.csv`; potom to isté pre `Rucksack` (max 50 %)
3. Porovnať teplotu, frekvenciu, príkon (CSV stĺpce `temp_c`, `freq_mhz`, `power_w`, `fan_rpm`) → aká je strata výkonu vs. zníženie teploty? Podľa toho doladiť turbo / % limit v profiloch. Pozor: s vypnutým turbom je `cpuinfo_max_freq` len základný takt (T14s: 1,1 GHz), takže % sa počíta z neho (Rucksack 50 % ≈ 550 MHz).
- [x] `powerdeck restore` vráti turbo a max. frekvenciu (over cez monitor: `limit 100%`, `turbo on`) ✓ opravené: 100 % sa zapisovalo ako `cpuinfo_max_freq` počas vypnutého turba (1,1 GHz) a po zapnutí turba ostal strop; teraz helper zapíše 100 GHz, ktoré kernel oreže
- [ ] Po reštarte sú limity späť na predvolené (sysfs sa nezachováva) – to je zámer; ak ich chceš trvalé, treba systemd službu pri štarte
- [ ] Prvý `pkexec` dialóg ukáže nový text „Change laptop power settings (lid behaviour, CPU limits)“; po zmene politiky treba znova `sudo scripts/install-helper.sh` (alebo nový `.deb`)
- [ ] GUI: po kliknutí na profil sa hore vpravo ukáže „Obsah profilu“ (popis + odrážky); info ikona pri odrážke ukáže príkaz po nabehnutí myšou, kliknutie ho skopíruje – skontroluj vzhľad, čitateľnosť tooltipu a že texty sedia v SK aj EN
- [ ] GUI: skupina „Monitoring“ sa obnovuje každé 2 s, level bary fungujú, nespomaľuje to appku

## 0c. Uspanie pri kritickej teplote a odinštalovanie (nové, treba reálny HW)

- [ ] Uspanie pri kritickej teplote bez čakania na skutočné prehriatie: dočasne si v editore daj vlastný profil s nízkym prahom (napr. warn 40 / critical 45 °C), aplikuj ho, spusti `powerdeck guard --once` → má prísť notifikácia „PowerDeck: uspávam – dosiahnutá maximálna teplota“ a o 5 s sa notebook uspí
- [ ] To isté so spustenou aplikáciou, ktorá blokuje uspanie (napr. Claude Desktop): `systemctl suspend` zlyhá a guard použije `pkexec /usr/libexec/powerdeck-suspend` – **overiť, že polkit to pustí bez hesla aj zo systemd user služby** (akcia `io.github.powerdeck.thermal-suspend`, `allow_active=yes`; služba nie je v logind relácii, polkit ju má priradiť ku grafickej relácii používateľa)
- [ ] Odinštalovanie: aplikuj napr. Rucksack, potom `sudo apt remove powerdeck` → nastavenia ako pred PowerDeckom (veko, gsettings, Wi‑Fi powersave, power mode, Bluetooth, turbo/CPU limit), guard služba zastavená. V kontajneri overené len pre gsettings + drop-in veka (bez systemd/NetworkManager). **VM (2026-11-15) ✓** veko, power mode, zámok, idle, auto-suspend, guard služba zastavená; Wi‑Fi/BT/turbo vo VM nie sú – ostáva overiť na notebooku
- [ ] `apt` upgrade (nová verzia `.deb`) nastavenia **nesmie** vrátiť – prerm robí reset len pri „remove“

## 1. Hotové (od v0.1)

- Polkit politika + root-owned helper `/usr/libexec/powerdeck-helper`, inštalačný skript, GUI hlási chýbajúci helper
- Tethering autoconnect + priorita (`tethering` v profile)
- Jas, nočný režim, blank/lock timery (`display`), Bluetooth (`bluetooth`)
- Úlohy `on_activate`/`on_deactivate` – **iba** `systemctl --user start|stop|restart UNIT` (whitelist; validované pri načítaní profilu)
- Thermal guard (`powerdeck guard`): notifikácia pri `warn`, pri `critical` prepne na power-saver + notifikácia; upozornenie pri nízkej batérii s ignorovaným vekom; udalosti sa spúšťajú len pri zhoršení (bez spamu)
- Stav: SSH/RDP spojenia, login sessions, skutočný Wi‑Fi powersave (`iw`), priemer cez viac batérií, výber CPU senzora podľa typu zóny
- Profily `Travel`, `Rucksack`, `Presentation`; Max Performance „návrat“ = `restore`
- `snapshot` – Default zo skutočného stavu systému
- Viacnásobný rollback (každé `restore` vráti ďalšiu zálohu)
- Obsah profilu v GUI (`describe.py`): stručný popis + odrážky v ľudskej reči, info ikona s presným príkazom (test zaručuje, že tooltip = príkazy, ktoré engine naozaj spustí)
- Editor/tvorba profilov v GUI, chybové dialógy, ikona, `.desktop`, AppStream
- Logy: `~/.local/state/powerdeck/logs/powerdeck.log`
- Limit CPU v profile (`cpu`: `turbo`, `max_freq_percent`) cez root helper (`set-cpu-turbo`, `set-cpu-max-percent`, len whitelistované hodnoty 10–100 %); Travel = turbo off (bez % limitu; 70 % s vypnutým turbom = 770 MHz, príliš málo), **Rucksack = najprísnejší (turbo off, max 50 %, power-saver, BT off, thermal 70/82 °C)**, Default/Max Performance ho vracajú na 100 %/turbo on; `snapshot` ho zachytáva
- Monitoring: `powerdeck monitor [--csv FILE] [--duration N]` + živá skupina v GUI (teplota, frekvencia, limit, turbo, záťaž, príkon, ventilátor)
- Kritická teplota → notifikácia „uspávam – dosiahnutá maximálna teplota“, 5 s, potom `systemctl suspend`; ak uspanie blokuje aplikácia, malý root helper `/usr/libexec/powerdeck-suspend` (vlastná polkit akcia, vie len uspať); ak nejde ani to, druhá notifikácia „uspanie zlyhalo“
- Odinštalovanie (`apt remove`, `install-helper.sh --uninstall`) vráti všetky nastavenia do stavu pred PowerDeckom: `powerdeck reset-all` vráti všetky zálohy od najnovšej (systémové časti ako root, gsettings ako daný používateľ), zastaví guard, odstráni drop-in veka, turbo on + bez limitu CPU
- CI (`.github/workflows/tests.yml`), `.deb` (`scripts/build-deb.sh`)

## 2. Ostáva — potrebuje tvoje rozhodnutie alebo reálny HW

- [ ] **Licencia** (AppStream momentálne `LicenseRef-proprietary`) a verejné/súkromné repo – rozhodnutie vlastníka
- [ ] **Maintainer v `.deb`** je placeholder (`noreply@example.invalid`)
- [ ] **USB autosuspend** – závislé na HW (sysfs/udev/TLP, root); netýka sa bezpečne bez konkrétneho zariadenia
- [ ] **Voľba sietí zo zoznamu uložených NM profilov** v editore (dnes text oddelený čiarkou) – dá sa, ale je to UX rozhodnutie
- [ ] **Mazanie vlastných profilov** z GUI
- [ ] **Max Performance**: automatický návrat po skončení náročnej úlohy/hry (detekcia procesu) – dnes ručne cez Restore
- [ ] **GUI testy v CI** (headless GTK vyžaduje balíky na runneri); smoke test je len ručný (`xvfb-run`)
- [ ] **gettext** namiesto slovníka v `i18n.py` (až keď sa texty ustália)
- [ ] **Edit sloty špecifické pre dock/AC** v editore (dnes jedna hodnota pre všetky tri stavy veka)
- [ ] Voliteľne: GNOME Shell extension/indikátor v paneli (po v1)
- [ ] Odmietnuť/zvážiť Flatpak: sandbox bráni `nmcli`/`pkexec`

## 2b. GUI – redizajn (podľa inštrukcií vlastníka, prebieha)

- [x] Obsah profilu hore vpravo (popis + odrážky + info ikona s príkazom)
- [x] Aktívny profil v hornej lište („Aktívny profil: …“ / „Žiadny profil nie je aktivovaný“) a orámovaná dlaždica v zozname; riadok „Aktívny profil“ v Stave odstránený (bol by duplicitný); screenshoty v `docs/` + `scripts/screenshot.py`
- [x] Poradie vpravo: obsah profilu → tlačidlá → Stav → Monitoring; popisy profilov v zozname vľavo sa prekladajú
- [ ] Info ikona: v cloude (iná téma ikon) vyzerá ako žiarovka – na Ubuntu (Yaru) by mala byť „i“, overiť
- [x] Pri redizajne nahradiť zastaraný `Adw.MessageDialog` za `Adw.AlertDialog` (libadwaita 1.9 na Ubuntu 26.04 hlási DeprecationWarning); vyžaduje libadwaita ≥ 1.5
- [x] Tooltipy pri riadkoch v „Stave“ a „Monitoringu“ (čo hodnota znamená + príkaz/súbor, odkiaľ sa berie); teplota CPU je už len v Monitoringu (bola duplicitne aj v Stave)
- [x] Odrážky obsahu profilu kompaktnejšie (riadok 50 → 33 px, polovica voľného miesta hore aj dole)
- [x] Jazyk podľa prostredia (`LANGUAGE`, potom `LC_ALL`/`LC_MESSAGES`/`LANG`); jazyk bez prekladu → angličtina
- [x] Odstránené rozpory v textoch aj akciách: strážca pri kritickej teplote prepína na power-saver len ak ešte nebeží (inak len naliehavo varuje, a tak to aj píše); Wi‑Fi powersave bez zoznamu sietí sa teraz naozaj aplikuje na všetky uložené Wi‑Fi siete (predtým text sľuboval zmenu, ale nič sa nestalo); Travel „turbo off + 100 %“ už nepíše „bez limitu“
- [ ] Vizuálna kontrola GUI na reálnej ploche (screenshot sa v cloude nerobil)

## 2c. Možné vylepšenia (navrhnuté, zatiaľ neschválené)

- [ ] **Rucksack s reguláciou podľa teploty** namiesto pevného limitu 50 % (s vypnutým turbom ≈ 550 MHz). Profil by mal cieľovú teplotu, napr. `"thermal_target_celsius": 72`, a výkon by sa podľa nej sám uberal a pridával.
  - **Implementované 2026-11-16** (vetva `thermal-regulation`, návrh v [docs/thermal-regulation.md](docs/thermal-regulation.md)): Rucksack = turbo on, 100 %, cieľ 65 °C. Testy, kontajner aj VM (jednotka so sprísnením štartuje, bez RAPL skončí s kódom 3 bez reštartov) ✓. **Notebook 2026-11-16 ✓** (profil len s cieľom 65 °C, na sieti, 12 jadier na 100 % počas 3 min): po štarte prestrelí dole (82 °C → limit 6 W na ~20 s), potom drží **60–64 °C pri ~15–17 W a ~2,5 GHz** (predtým Rucksack ≈ 550 MHz). Firmvér raz vrátil MMIO PL1 na 30 W, regulácia to zvládla. `restore` vrátil 55/55 W (MSR) a 30/55 W (MMIO), služba zastavená. Prestrel na 6 W: obmedzený krok to zhoršil (94 °C, aj tak 5 W). **PI regulátor (2026-11-16) ✓:** najnižšie 7,1 W na ~5 s, od 35. s drží 65 ± 1 °C pri ~15–17 W a 2,3–2,4 GHz, `restore` ✓. **Taška na batérii 2026-11-16:** regulácia držala 61–66 °C pri ~11 W (power-saver), ale strážca po jednom meraní 84 °C (55 W PL2 náraz na štarte záťaže) notebook uspal, takže vzdialená plocha nešla. Thermald alternatíva sa nerobila.
  - **Oprava (vetva `bag-remote-work`):** PL2 ≤ PL1, strážca uspí až po potvrdení druhým meraním o 10 s, pri ≤ 15 % batérie bez nabíjania uspí (RemoteWork, Travel, Rucksack). Testy ✓.
    - [x] Notebook 2026-11-16: cieľ 65 °C + power-saver → MMIO PL1/PL2 11/11 W (predtým 11/55). 12 jadier 40 s na stole: najviac 54 °C, žiadny skok na štarte (predtým 82–94 °C). `restore` → 30/55 W, služba zastavená ✓.
    - [x] Taška 2026-11-16 12:33–12:45, batéria, 12 jadier 10 min: 46 → 65 °C bez skoku, potom 65–66 °C pri 9,4–11 W, strážca ani raz nezasiahol, RDP pripojené od 12:39 do konca, `restore` ✓.
    - [x] **RDP port:** používateľský gnome-remote-desktop („Negotiate port: yes“) od 12. 11. počúval na **3390**, lebo 3389 bol pri štarte obsadený; telefón sa pripájal na 3389 a dostal RST („nenájde PC“, aj v prvom teste v taške). Pomohol `systemctl --user restart gnome-remote-desktop`. Hotovo: `apply` upozorní, keď na 3389 nič nepočúva; na T14s `grdctl rdp disable-port-negotiation`. Strážca navyše RDP sám reštartuje (overené na T14s: po zastavení služby počúva znova o ~10 s).
    - [ ] Uspanie pri 15 %: dočasne vyskúšať s vyšším prahom alebo počkať na vybitie.
  - **Slučka:** každých 5–10 s prečíta teplotu CPU.
    - Nad cieľom výkon o krok zníži, pod cieľom mínus hysterézia (napr. 65 °C) ho zvýši.
    - Výkon sa drží v medziach, napr. 5–15 W alebo 400 MHz – plný výkon.
    - Kroky musia byť pomalé, lebo notebook v taške mení teplotu pomaly a výkon by inak kmital.
  - **Páka:** prednostne limit príkonu cez Intel RAPL (`/sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw`), lebo teplota závisí priamo od wattov a turbo môže ostať zapnuté. Záloha je `scaling_max_freq` so zapnutým turbom (rozsah 400 MHz – 5 GHz, takže zmizne problém s 550 MHz). Prepínanie power-saver/balanced je príliš hrubé.
  - **Root:** slučka zapisuje často, takže `pkexec` nepripadá do úvahy. Potrebná je malá systémová služba `powerdeck-thermal` (root):
    - od používateľa prijme len cieľovú teplotu cez helper (`set-thermal-target 55–90`, whitelist),
    - nič z `$HOME` nečíta (pravidlá v `CLAUDE.md`),
    - pri `restore`, odinštalovaní alebo páde vráti limity na predvolené.
    - Pred mergom ide na security review a slučka dostane testy na simulovaných teplotách.
  - **Alternatíva: thermald** s konfiguráciou pre profil. Je to menej kódu, ale na ThinkPadoch môže bežať v adaptívnom režime (DPTF) a konfigurácia sa s ním môže biť. Treba overiť na T14s.
  - **Strážca ostáva ako poistka:** varovanie a pri kritickej teplote uspanie.
  - **Pred kódom:**
    1. Na notebooku overiť, že RAPL je zapisovateľný a firmvér ho neprepisuje.
    2. Zmerať (`powerdeck monitor --csv` pri záťaži), koľko wattov v taške drží danú teplotu.
    3. Potom návrh v plan mode a odsúhlasenie.

## 3. Otvorené otázky

1. Verzia GNOME na Ubuntu 26.04 a dostupné gsettings kľúče
2. ~~HUP vs. reštart `systemd-logind`~~ – HUP stačí (overené)
3. RDP (`gnome-remote-desktop`) so zavretým vekom a zamknutou obrazovkou
4. HW: ThinkPad T14s Gen 4, `x86_pkg_temp`/`TCPU` zóny, `power-profiles-daemon` (intel_pstate + platform_profile), ventilátor cez `thinkpad` hwmon
5. ~~Thermal guard pri critical~~ – rozhodnuté: notifikácia + uspanie
