# PowerDeck — zlúčená špecifikácia

> Zlúčenie dokumentov **RemoteStation** (pôvodný návod + nápad) a **PowerDeck** (výrobná špecifikácia).
> Pracovný názov: RemoteStation · Výrobný názov: **PowerDeck** · Platforma: Ubuntu 26.04 / GNOME
> Stav: **v0.4, implementované a používané na T14s** (pôvodne koncept). Sekcie 1–12 sú pôvodný návrh. Kde sa s ním niečo rozchádza, platí **zoznam rozhodnutí v sekcii 0**. Otvorené úlohy sú v `TODO.md`, pravidlá pre agentov v `CLAUDE.md`.

## 0. Zoznam rozhodnutí (decision log)

Najnovšie hore. Každé rozhodnutie: dátum · čo platí · prečo · kde v kóde. Keď sa rozhodnutie zmení, starý riadok sa neprepisuje, ale označí ako ~~nahradené~~ a pribudne nový.

| Dátum | Rozhodnutie | Prečo | Kde |
|---|---|---|---|
| 2026-11-16 | **História teploty a frekvencie CPU.** Strážca (beží stále) každých 20 s zapíše do `~/.local/state/powerdeck/history.csv` teplotu CPU, priemernú frekvenciu a aktívny profil. Uchováva sa 7 dní, staršie riadky odstraňuje raz za 6 h. Tlačidlo s grafom vedľa „Upraviť“ zobrazí graf za 1 h, 6 h, 24 h alebo 7 dní: dva panely nad sebou so spoločnou časovou osou (teplota °C a frekvencia GHz, nie dve osi v jednom grafe), prepnutia profilov ako zvislé prerušované čiary s názvom profilu, prerušenie čiary pri výpadku záznamu nad 60 s (uspanie) a hodnoty pod kurzorom. Ako „výkon CPU“ sa zaznamenáva frekvencia, lebo príkon CPU (RAPL `energy_uj`) môže čítať len root. Nová závislosť: `python3-gi-cairo`. | Vlastník chce vidieť, ako sa teplota a výkon menia pri jednotlivých profiloch. | `powerdeck/history.py`, `powerdeck/history_view.py`, `guard.loop` |
| 2026-11-16 | **Prepnutie profilu na jeden klik, ukončenie = Default.** Použitie profilu (GUI, menu ikony, `powerdeck apply`) najprv vráti všetky čakajúce zálohy (najnovšiu prvú) a až potom naplánuje a použije nový profil na čistom stave; jeden potvrdzovací dialóg ukáže, čo sa vráti a čo nový profil nastaví. Nič z predošlého profilu neostane (napr. teplotný cieľ a vypnutý BT z Rucksacku po prepnutí na RemoteWork). Ak vrátenie zlyhá, nový profil sa nepoužije. Opätovné použitie aktívneho profilu nič nevracia. `restore` potom vráti stav spred PowerDecku. „Ukončiť PowerDeck“ v menu ikony (bez ikony zavretie okna) najprv prepne na Default; zavretie okna s ikonou ho len skryje a profil nemení. Tlačidlá „Zapnúť Remote Work“ a „Obnoviť predošlý stav“ v okne sú preč (RemoteWork je v zozname, obnova v menu ikony a CLI). | Vlastník nechcel pred každým prepnutím ručne robiť restore. | `engine.switch_to`, `engine.pending_undo`, `gtk_app`, `cli` |
| 2026-11-16 | **Strážca beží stále, PowerDeck má ikonu v hornej lište.** `.deb` povolí `powerdeck-guard` pre všetkých používateľov symlinkom v `graphical-session.target.wants` (bez kódu v maintainer skriptoch; `ConditionUser=!@system`, takže nie v GDM; odhlásiť sa dá `systemctl --user mask powerdeck-guard`). Pri prihlásení sa spustí `powerdeck-gtk --background` (`/etc/xdg/autostart`): len ikona (StatusNotifierItem + dbusmenu cez Gio, bez novej závislosti), zavretím okna sa okno skryje, „Ukončiť PowerDeck“ v menu ikony aplikáciu ukončí. Profil z menu ikony otvorí ten istý potvrdzovací dialóg. Skryté okno nič nemeria. Bez StatusNotifierWatcher sa aplikácia správa ako doteraz. | Vlastník chce prácu na diaľku v batohu bez ručného spúšťania strážcu a ikonu ako má Claude desktop. | `powerdeck/tray.py`, `gtk_app.App`, `data/powerdeck-guard.service`, `scripts/build-deb.sh` |
| 2026-11-16 | **Strážca obnoví RDP:** pri profile s `remote_access`, keď na 3389 nič nepočúva dve merania po sebe (~30 s) a zdieľanie plochy je v GNOME zapnuté, spustí `systemctl --user restart gnome-remote-desktop`. Najviac 3× s odstupom 10 min; po tom, čo port znova počúva, sa počítadlo vynuluje. | V batohu upozornenie pri `apply` nikto nevidí, telefón hlási len „nenájde PC“. | `guard.keep_rdp_listening` |
| 2026-11-16 | **RDP port 3389:** po `apply` profilu s `remote_access` PowerDeck upozorní, keď na 3389 nič nepočúva (`ss -Htln`), a poradí reštart `gnome-remote-desktop` a `grdctl rdp disable-port-negotiation`. Na T14s je vyjednávanie portu vypnuté (vlastník). | gnome-remote-desktop s „Negotiate port“ sa pri obsadenom 3389 ticho presunul na 3390 a telefón hlásil „nenájde PC“. | `guard.rdp_listening`, `guard.remote_access_notes` |
| 2026-11-16 | **Notebook v batohu má ostať dostupný na diaľku.** (1) Strážca uspí pri kritickej teplote až po **druhom meraní o 10 s** (alebo hneď, ak je teplota ≥ kritická + 8 °C). Prvé meranie len upozorní. (2) Služba regulácie drží **PL2 ≤ PL1** v každej RAPL doméne, takže pri štarte záťaže nie je krátky 55 W náraz. (3) V profiloch, kde zavretie veka na batérii nič nerobí (RemoteWork, Travel, Rucksack), strážca pri **≤ 15 % batérie** bez nabíjania notebook **uspí** (predtým len upozornil). Po prebudení pri ≤ 15 % ho znova neuspí, kým sa nepripojí napájanie alebo batéria nestúpne nad 18 % (jedno čudné meranie po prebudení stav nezruší). Použije tú istú polkit akciu `powerdeck-suspend`. | Test v batohu: jedno meranie 84 °C (turbo náraz pred reakciou regulácie) uspalo notebook a vzdialená plocha ani Claude nefungovali. Vlastník chce pri vybíjaní uspanie pri 15 %, len v týchto troch profiloch. | `guard.evaluate`, `guard.low_battery_reaction`, `thermal_limit.rapl_knobs`/`apply_level` |
| 2026-11-16 | **Rucksack reguluje teplotu** namiesto pevného limitu: turbo on, max 100 %, `cpu.thermal_target_celsius: 65` (55–90, `null` = vypnuté). Root služba `powerdeck-thermal@<°C>.service` (spúšťa ju len helper: `set-thermal-target`, `clear-thermal-target`) každé 2 s znižuje/zvyšuje PL1+PL2 v RAPL, bez RAPL `scaling_max_freq`. Po zastavení, páde aj odinštalovaní vráti pôvodné limity; po reštarte nebeží. Default, Travel a Max Performance ju vypínajú. Guard 70/82 °C ostáva ako poistka. | Pevný limit bol buď príliš pomalý (50 % z 1,1 GHz ≈ 550 MHz), alebo príliš horúci. | `thermal_limit.py`, `helper.py`, `data/powerdeck-thermal@.service`, `backends.plan_cpu`, `builtin.py`, docs/thermal-regulation.md |
| 2026-11-16 | **RDP so zavretým vekom = zdieľanie plochy + guard.** ~~Remote Login ako robustná cesta~~ sa nahrádza: na T14s (Ubuntu 26.04) končí čiernou obrazovkou, lebo handover na GDM štartuje dlhšie, než je 30 s timeout. Zdieľanie plochy overené: pripojenie pri zatvorenom veku drží a bežiace spojenie raz spadne, opätovné pripojenie funguje. Stav Remote Login sa číta cez `systemctl is-enabled` (nie `grdctl --system`, ktorý pýta heslo). | Test na notebooku 2026-11-16. | `guard.remote_access_notes`, `status.read_remote_login`, README |
| 2026-11-15 | Testovacie VM: QEMU/KVM, Ubuntu 26.04 + GNOME 50, simulované veko cez uinput `SW_LID`, bez admin hesla na hoste. Overené v ňom: pkexec, drop-in veka, guard služba a `apt remove`. Cesta veko → šetrič → RDP sa dá overiť len na notebooku. | Systemd, logind a polkit kontajner neotestuje a notebook nechceme pri každej zmene meniť. | `scripts/dev-vm.sh`, `scripts/vm/fakelid.py` |
| 2026-11-14 | **Root nikdy nekoná podľa ničoho, čo môže zapísať používateľ, bez whitelistu.** Odinštalovanie číta zálohy z `$HOME`, ale ako root spustí len príkazy presne v tvare `commands.allowed_as_root`; ostatné odmietne. Symlinky do adresára záloh od ne-roota sa odmietajú. Python ako root sa nespúšťa s cwd v `sys.path` (`-I`, `-P` + `cd /`). | Code review našlo, že root spúšťal príkazy zo záloh, ktoré môže prepísať ktorýkoľvek proces používateľa. | `helper.valid_args`, `commands.allowed_as_root`, `engine._symlinked_dir`, `scripts/uninstall-reset.sh`, `tests/test_reset_security.py`, `CLAUDE.md` |
| 2026-11-14 | Odinštalovanie (`apt remove`, `install-helper.sh --uninstall`) vráti **všetky** nastavenia do stavu pred PowerDeckom. `apt upgrade` ich nevracia (prerm len pri `remove`). | Po odinštalovaní nesmie ostať ignorované veko ani strop CPU. | `scripts/uninstall-reset.sh`, `powerdeck reset-all` |
| 2026-11-14 | Pri **kritickej teplote**: notifikácia, po 5 s uspanie. Ak uspanie blokuje aplikácia, použije sa `pkexec /usr/libexec/powerdeck-suspend` (vlastná polkit akcia, vie len uspať). Pri `warn`: len notifikácia. | Prechod na power-saver v ruksaku nestačil. Prešlo to tromi verziami: ~~power-saver~~, ~~podmienený power-saver~~ a teraz uspanie. | `guard.py`, `data/powerdeck-suspend`, polkit `io.github.powerdeck.thermal-suspend` |
| 2026-11-14 | Vývoj bez admin hesla: rootless podman kontajner (testy, `.deb`, GUI screenshot). Veko, RDP, CPU, teplota a batéria sa testujú vo VM alebo na notebooku. | Na overenie nechceme stále zadávať heslo. | `scripts/dev-container.sh`, `container/` |
| 2026-11-13 | **RDP so zavretým vekom:** profily RemoteWork, Travel a Rucksack majú `remote_access: true`, `lock_screen: false` a `blank_after_minutes: 0`. `powerdeck guard` hneď zruší šetrič obrazovky, ktorý GNOME zapne pri zavretí veka. Robustná cesta je **GNOME Remote Login** (systémový headless gnome-remote-desktop), zapína ho používateľ v Nastaveniach. (Voľba C.) | Keď je uspanie zablokované inhibítorom, gsd-power pri zavretí veka aktivuje šetrič. gnome-shell sa prepne do režimu `unlock-dialog` (`allowScreencast=false`) a zruší remote desktop. Samotné vypnutie zámku nepomáha. | `guard.watch_screensaver`, `profile.remote_access`, `builtin.py` |
| 2026-11-13 | Nový kľúč `display.lock_screen` (bool, `org.gnome.desktop.screensaver lock-enabled`). Nedá sa kombinovať s `lock_after_minutes`. Default, BatterySaver a Max Performance majú zámok zapnutý. | Vzdialené profily ho musia vypnúť a ostatné ho vracajú. | `profile.py`, `backends.plan_display` |
| 2026-11-13 | **Travel = len vypnuté turbo**, bez % limitu (`max_freq_percent: 100`). ~~Pôvodne 70 %.~~ | Pri vypnutom turbe kernel zníži `cpuinfo_max_freq` na základný takt (T14s: 1,1 GHz). % sa potom počíta z neho, takže 70 % by bolo 770 MHz. | `builtin.py` |
| 2026-11-13 | Limit CPU 100 % = helper zapíše 100 GHz a kernel ho oreže na aktuálne maximum. | Hodnota zapísaná pri vypnutom turbe ostala ako trvalý strop (CPU zaseknuté na 1,1 GHz aj po `restore`). | `helper.set_max_percent` |
| 2026-11-13 | Reload logind cez **HUP** stačí, reštart ani odhlásenie netreba (overené cez `busctl`). | Otvorená otázka 2 v sekcii 11. | `helper._reload_logind` |
| 2026-11-13 | Profil **Gaming** premenovaný na **Max Performance**. „Návrat“ po skončení = `restore`. | Rozhodnutie vlastníka. | `builtin.py` |
| 2026-11-13 | `restore` vráti aj aktívny profil na predošlý. Neúspešné `apply` (napr. zrušený alebo vypršaný pkexec) po sebe nenechá zálohu. pkexec má timeout 300 s. | Po `restore` ostával zastaraný `active.json` a po zlyhaní ostávala falošná záloha. | `engine.restore_last`, `runner.PKEXEC_TIMEOUT` |
| 2026-11-13 | Vetvy: práca na worktree z `develop`, merge do `develop` až po otestovaní. `develop` → `main` je rozhodnutie vlastníka. | Prvé opravy išli priamo do `develop`. | `CLAUDE.md` |
| 2026-11-12 | Pribudli profily **Travel**, **Rucksack** (najprísnejší: turbo off, max 50 %, power-saver, BT off, 70/82 °C) a **Presentation**. Sú už v1, nie v2+. | Hlavný scenár je notebook v taške. | `builtin.py` |
| 2026-11-12 | Limit CPU (`cpu.turbo`, `cpu.max_freq_percent` 10–100) ide len cez root helper, iba whitelistované hodnoty. | Zápis do sysfs vyžaduje root. | `helper.py`, `cpu.py` |
| 2026-11-12 | Úlohy profilu (`tasks`) sú **iba** `systemctl --user start\|stop\|restart UNIT`, validované pri načítaní. | Profil = kód. Žiadne ľubovoľné príkazy (pôvodný návrh v sekcii 6 ich mal). | `profile.py` |
| 2026-11-12 | GTK4 + libadwaita appka + CLI. GNOME Shell extension len voliteľne po v1. Flatpak nie (sandbox bráni `nmcli` a `pkexec`). | Otvorená otázka 5. | — |

**Čaká na rozhodnutie vlastníka:** redizajn GUI, licencia, maintainer v `.deb`. Celý zoznam je v sekcii 2 súboru `TODO.md`.

## 1. Zámer

PowerDeck je nástroj pre Ubuntu na prepínanie **profilov notebooku** (veko, uspávanie, Wi‑Fi powersave, režim napájania, jas, úlohy…) jedným kliknutím.

Hlavný scenár (**RemoteWork / Travel**): používateľ zavrie notebook, dá ho do ruksaku, pripojí cez tethering z telefónu a notebook ostane zapnutý a dostupný – beží dlhá Claude session, SSH, RDP, dev servery, VPN.

Krátky popis: *PowerDeck lets you switch laptop profiles for work, travel, battery life and maximum performance.*

## 2. Čo sa dá a čo nie (zhodnotenie uskutočniteľnosti)

### ✅ Dá sa (štandardné, overené mechanizmy)

| Funkcia | Ako | Root? |
|---|---|---|
| Zavretie veka nič nerobí | drop-in `/etc/systemd/logind.conf.d/*.conf` (`HandleLidSwitch*=ignore`) + reštart/reload `systemd-logind` | áno (pkexec/polkit) |
| Vypnutie automatického uspania | `gsettings` `sleep-inactive-{ac,battery}-type = 'nothing'` | nie |
| Wi‑Fi powersave per sieť | `nmcli connection modify <profil> 802-11-wireless.powersave 2` | áno / polkit NM |
| Autoconnect + priorita tetheringu | `nmcli … connection.autoconnect(-priority)` | zvyčajne nie |
| Režim napájania (saver/balanced/performance) | `powerprofilesctl` / D‑Bus `power-profiles-daemon` | nie |
| Jas obrazovky, nočný režim | `gsettings` (Night Light) + `brightnessctl` / D‑Bus | nie |
| Bluetooth zap/vyp | `rfkill` / `bluetoothctl` | nie |
| Stav: batéria, Wi‑Fi, teplota, session | `upower`, `nmcli`, `sensors` (lm-sensors), `loginctl`, `ss` | nie |
| Úlohy pri aktivácii/deaktivácii profilu | `systemctl --user start/stop …` | nie |
| Profily v JSON, zálohy, rollback | vlastná logika (snapshot pôvodných hodnôt pred aplikovaním) | nie |
| Notifikácie (prehrievanie…) | `notify-send` / libnotify | nie |
| SK + EN rozhranie | gettext | nie |

### ⚠️ Dá sa, ale s výhradami / treba overiť

- **GNOME `lid-close-*-action` kľúče** (sekcia 3 pôvodného návodu): v novších verziách GNOME ich `gnome-settings-daemon` nemusí mať – správanie veka rieši `logind`. ⚠️ Overiť `gsettings list-keys org.gnome.settings-daemon.plugins.power` na Ubuntu 26.04; ak kľúče chýbajú, vynechať.
- **GNOME Shell extension vs. samostatná appka:** extension beží v procese shellu (JavaScript/GJS), nemôže robiť privilegované zmeny a každá nová verzia GNOME môže rozbiť kompatibilitu (`shell-version`). Odporúčanie: **GTK4/libadwaita appka + malý privilegovaný helper (polkit)**, extension (ikonka v paneli) len voliteľne neskôr.
- **Priorita autoconnect:** funguje pre uložené profily; ak telefón sieť nevysiela (hotspot vypnutý), nič sa nepripojí – appka to nevie vyriešiť.
- **USB / Bluetooth tethering:** zariadenie sa objaví ako nové sieťové rozhranie (`usb0`/`enx…`, BT PAN). Appka môže nastaviť autoconnect/prioritu profilu, ale nevie zapnúť tethering na telefóne.
- **USB autosuspend:** nastavuje sa cez sysfs/udev/TLP – vyžaduje root a je zariadenie‑špecifické; vyššie riziko vedľajších efektov (zamŕzajúce periférie).
- **RDP pri zavretom veku:** ⚠️ závisí od `gnome-remote-desktop` (session musí existovať, správanie pri zamknutí obrazovky overiť). SSH/Tailscale je spoľahlivejšie.
- **„Spustiť Claude session“ ako úloha:** vyžaduje, aby používateľ sám vytvoril `claude-session.service`; PowerDeck ho len spustí.
- **Úlohy z profilu = spúšťanie ľubovoľných príkazov.** Profil (JSON) sa preto musí považovať za kód: nikdy nenačítavať cudzie profily bez zobrazenia príkazov a potvrdenia.

### ❌ Nedá sa / mimo rozsahu

- **Zaručiť, že sa notebook v zatvorenom ruksaku neprehreje.** Veko ignorované + záťaž + žiadne prúdenie vzduchu = fyzikálny problém. PowerDeck môže len **varovať** (prahy 85/92 °C), prípadne prepnúť na power-saver; „thermal guard“ nemôže spoľahlivo detegovať, že je notebook v taške.
- **Univerzálne riadenie ventilátorov / TDP** – závisí od HW a firmvéru (ThinkPad, Dell, … majú rôzne rozhrania).
- **Zachovať Wi‑Fi/procesy počas skutočného suspendu.** Ak sa notebook uspí, spojenie sa preruší; powersave nastavenie to nemení.
- **Ovládať telefón** (zapnúť hotspot, USB tethering) z notebooku.
- **Ticho meniť systémové nastavenia** – zámerne zakázané návrhom (viď bezpečnosť).
- Overovanie a obchádzanie obmedzení konkrétneho HW (napr. firmvérový suspend pri zavretí veka) – mimo kontroly OS.

## 3. Opravy a nezrovnalosti v pôvodných dokumentoch

1. **Hodnoty `802-11-wireless.powersave`:** pôvodný návod uvádza `1` = zapnúť, `3` = zapnúť. Správne: `0` = predvolené (default), `1` = ignorovať (ponechať ako je), `2` = vypnúť, `3` = zapnúť. ⚠️ Overiť v `man nm-settings-nmcli` na cieľovom systéme.
2. **Dve cesty konfigurácie:** RemoteStation `~/.config/remote-station/profiles/`, PowerDeck `~/.config/powerdeck/profiles/` → **používa sa `~/.config/powerdeck/profiles/`**.
3. **Dva formáty profilu** (RemoteStation: `lid.{battery,ac,docked}`, `wifi.powersave: "off"`; PowerDeck: `lid.action`, `network.wifi_powersave: false`) → zjednotené v sekcii 6 (rozlišuje veko pre batériu/AC/dock, ukladá `thermal_guard`).
4. **Zoznamy profilov sa líšia** (`Travel` vs. `Presentation`) → viď sekcia 5.
5. **`sudo` vs. `pkexec`:** v návode sa používa `sudo`; v appke sa používa výhradne `pkexec`/polkit s presným náhľadom zmien.
6. **`Default` profil nesmie predpokladať hodnoty:** predvolené hodnoty sa nesmú vymyslieť, ale zachytiť zo systému (snapshot), inak by „Restore Default“ mohol prepísať používateľovu pôvodnú konfiguráciu.

## 4. Manuálny postup (referenčný návod, na ktorom sa appka zakladá)

### 4.1 Zavretie veka = nič (hlavné nastavenie)

```bash
sudo mkdir -p /etc/systemd/logind.conf.d
sudo tee /etc/systemd/logind.conf.d/99-lid-ignore.conf >/dev/null <<'EOF'
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
EOF
sudo systemctl restart systemd-logind
```

Pozn.: reštart `systemd-logind` môže ukončiť grafickú session na niektorých systémoch ⚠️ – appka by mala radšej použiť `systemctl kill -s HUP systemd-logind` (logind znovu načíta konfiguráciu bez reštartu) alebo upozorniť na odhlásenie.

Overenie:

```bash
systemd-analyze cat-config systemd/logind.conf | grep -i HandleLidSwitch
```

### 4.2 Vypnutie automatického uspania

```bash
gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-battery-type 'nothing'
gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type 'nothing'
```

### 4.3 GNOME správanie pri zavretí veka (⚠️ overiť, či kľúče existujú)

```bash
gsettings set org.gnome.settings-daemon.plugins.power lid-close-battery-action 'nothing'
gsettings set org.gnome.settings-daemon.plugins.power lid-close-ac-action 'nothing'
```

### 4.4 Wi‑Fi powersave pre konkrétne siete

```bash
nmcli connection show                       # názvy profilov (NAME ≠ nutne SSID)
sudo nmcli connection modify "MojTelefon" 802-11-wireless.powersave 2   # 2 = vypnúť
nmcli connection show "MojTelefon" | grep -i powersave
iw dev wlp3s0 get power_save                # očakávané: Power save: off
```

### 4.5 Automatické pripojenie tetheringu

```bash
nmcli connection modify "MojTelefon" connection.autoconnect yes
nmcli connection modify "MojTelefon" connection.autoconnect-priority 100
nmcli connection up "MojTelefon"
```

Pre dlhú cestu je najstabilnejší **USB tethering** (kábel + zároveň nabíjanie).

### 4.6 Test

1. Nechaj bežať Claude session / dlhú úlohu.
2. Pripoj sa z telefónu cez SSH/Tailscale (alebo RDP).
3. Zavri veko na 2–5 minút.
4. Z telefónu `ping` na notebook / SSH. Ak odpovedá, funguje to.

Diagnostika pri neúspechu:

```bash
journalctl -b -1 -e | grep -iE 'suspend|sleep|lid|PM:|wifi' | tail -80
systemd-analyze cat-config systemd/logind.conf | grep -i HandleLidSwitch
```

### 4.7 Teplota

```bash
sensors
```

- CPU dlho okolo 90 °C+ → zníž záťaž alebo notebook vyber z ruksaku.
- Pri ceste používaj **Balanced**, nie **Performance**.
- Nenechávaj ho v úplne uzavretej taške bez prúdenia vzduchu (priedušné puzdro).

## 5. Profily

| Profil | Účel | Veko | Auto‑suspend | Wi‑Fi powersave | Power mode |
|---|---|---|---|---|---|
| **Default** | bezpečný denný režim, pôvodné hodnoty systému | suspend | zapnutý | default | balanced |
| **RemoteWork** | cestovanie/práca mimo domova, SSH/RDP so zavretým vekom (bez zámku) | ignore | vypnutý | off (pre vybrané siete) | balanced |
| **Travel** | (variant RemoteWork) zameraný na ruksak: turbo off, prísnejší thermal guard (80/88 °C) | ignore | vypnutý | off | power-saver |
| **Rucksack** | notebook zavretý v taške: CPU regulované na 65 °C (turbo on, 100 %), BT off, 70/82 °C | ignore | vypnutý | off | power-saver |
| **BatterySaver** | max. výdrž | suspend | zapnutý | on | power-saver, nižší jas, voliteľne vypnutý BT |
| **Max Performance** | výkon, po skončení návrat na predošlý profil | suspend | zapnutý | default | performance |
| **Presentation** | obrazovka nezhasína, bez uspania | – | vypnutý | default | balanced |
| *vlastné* | používateľom vytvorené | | | | |

**Prvá verzia (MVP):** `Default`, `RemoteWork`, `BatterySaver`, `Max Performance` + vlastné profily. `Travel` a `Presentation` neskôr. *(Nahradené: v0.3 má všetky, aj Rucksack. Presné hodnoty sú v `powerdeck/builtin.py`, viď sekcia 0.)*

## 6. Formát profilu (zjednotený návrh)

```json
{
  "name": "RemoteWork",
  "description": "Notebook zostáva aktívny pri cestovaní",
  "lid": { "battery": "ignore", "ac": "ignore", "docked": "ignore" },
  "power": {
    "automatic_suspend": { "battery": false, "ac": false },
    "mode": "balanced"
  },
  "network": {
    "wifi_powersave": "off",
    "preferred_networks": ["MojTelefon", "Doma", "Praca"]
  },
  "display": { "blank_after_minutes": 5, "lock_after_minutes": 10 },
  "thermal_guard": { "warn_celsius": 85, "critical_celsius": 92 },
  "notifications": true,
  "tasks": {
    "on_activate": ["systemctl --user start claude-session.service"],
    "on_deactivate": []
  }
}
```

- `wifi_powersave`: `"default" | "off" | "on"`.
- `lid.*`: `"ignore" | "suspend" | "lock" | "poweroff"` …
- `Default` profil sa **nevyrába ručne**, ale vznikne ako snapshot systému pri prvom spustení.

## 7. Architektúra

- **Jadro (core):** Python 3, čisté funkcie `plan(profile, current_state) → zoznam zmien (diff)` a `apply(diff)` / `revert(snapshot)`. Dá sa testovať bez GUI aj bez root.
- **Backendy:** `nmcli`, `gsettings`, `powerprofilesctl`, `systemd-logind` drop-in, `upower`, `lm-sensors`, `rfkill`.
- **Privilegovaný helper:** malý skript spúšťaný cez `pkexec`/polkit, ktorý smie zapísať **len** whitelistované súbory (napr. drop-in `99-powerdeck-lid.conf`) – nie ľubovoľné príkazy.
- **Frontend:** GTK4 + libadwaita (hlavná appka), voliteľne GNOME Shell extension/indikátor.
- **Úložisko:** `~/.config/powerdeck/profiles/*.json`; zálohy/snapshoty `~/.local/state/powerdeck/backups/`; logy `~/.local/state/powerdeck/logs/`.
- **CLI:** `powerdeck apply RemoteWork`, `powerdeck status`, `powerdeck restore` (užitočné aj pre skripty a testovanie bez GUI).

## 8. Bezpečnosť a spoľahlivosť

- Žiadne tiché systémové zmeny: **presný diff → potvrdenie → záloha → aplikovanie**.
- Rollback na predchádzajúci stav/`Default` vždy dostupný (aj pri páde aplikácie – snapshot na disku).
- Jasné varovanie pred rizikom prehriatia pri aktivácii RemoteWork/Travel.
- Úlohy v profile = spúšťanie príkazov: zobraziť v diffe, importované profily nikdy nespúšťať automaticky.
- Polkit akcie úzke a špecifické, žiadny generický „spusti ako root“.
- Root nikdy nekoná podľa ničoho, čo môže zapísať používateľ (profily, zálohy, `$HOME`, premenné prostredia, argumenty), bez presného whitelistu. Každú zmenu kódu, ktorý beží ako root, treba pred mergom dať na security review. Podrobnosti sú v `CLAUDE.md`.
- Pri nízkej batérii s ignorovaným vekom: upozornenie / automatický návrat na bezpečný profil (nastaviteľné).

## 9. Zobrazenie stavu (GUI)

Aktuálny profil · Wi‑Fi (SSID, powersave) · tethering/aktívne spojenie · batéria (%, nabíjanie) · teplota CPU (farebné prahy) · aktívne session (SSH/RDP/Claude) · tlačidlá **Enable Remote Work** / **Restore Default** · vytvorenie vlastného profilu.

## 10. Verzia 1 – rozsah

- GUI na tvorbu a prepínanie profilov, CLI.
- Predpripravené: `Default`, `RemoteWork`, `BatterySaver`, `Max Performance`.
- Veko, suspend, Wi‑Fi powersave, power mode.
- Zobrazenie aktuálneho profilu a stavu.
- Potvrdenie + diff pred systémovými zmenami, zálohy a obnovenie.
- Rozhranie SK + EN.

**Až neskôr (v2+):** Bluetooth/USB autosuspend, jas/nočný režim, úlohy pri aktivácii, GNOME extension, thermal guard s automatickou reakciou, profily `Travel`/`Presentation`.

## 11. Otvorené otázky

1. Presná verzia GNOME na Ubuntu 26.04 a existencia kľúčov `lid-close-*-action`. *(Kľúče, ktoré appka používa, overené na HW; správanie veka rieši len logind.)*
2. ~~Reštart vs. reload `systemd-logind`~~: HUP stačí (sekcia 0).
3. Spoľahlivosť RDP (`gnome-remote-desktop`) so zavretým vekom a zamknutou obrazovkou. *(Príčina nájdená, riešenie v sekcii 0. Čaká sa na test so skutočne zavretým vekom.)*
4. ~~HW používateľa~~: ThinkPad T14s Gen 4, intel_pstate, `power-profiles-daemon`, zóny `x86_pkg_temp`/`TCPU`, ventilátor cez `thinkpad` hwmon.
5. ~~GTK appka vs. extension~~: GTK appka + CLI (sekcia 0).
6. Licencia a verejné/súkromné repo. *(Otvorené.)*

## 12. Pôvod

- `RemoteStation.md` – návod na nastavenie notebooku + prvý návrh extension.
- `PowerDeck.md` – výrobná špecifikácia a názvoslovie (PowerDeck ako hlavný názov, RemoteStation ako názov pôvodného projektu/repozitára; ďalšie navrhnuté názvy: StationKit, ProfileDeck, WorkMode, RemoteDesk, DeskPilot, ModeShift, TuxMode).
