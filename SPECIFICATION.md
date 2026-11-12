# PowerDeck — zlúčená špecifikácia

> Zlúčenie dokumentov **RemoteStation** (pôvodný návod + nápad) a **PowerDeck** (výrobná špecifikácia).
> Pracovný názov: RemoteStation · Výrobný názov: **PowerDeck** · Platforma: Ubuntu 26.04 / GNOME
> Stav: **koncept / návrh – zatiaľ žiadny kód.** Položky označené ⚠️ treba overiť na reálnom stroji.

## 1. Zámer

PowerDeck je nástroj pre Ubuntu na prepínanie **profilov notebooku** (veko, uspávanie, Wi‑Fi powersave, režim napájania, jas, úlohy…) jedným kliknutím.

Hlavný scenár (**RemoteWork / Travel**): používateľ zavrie notebook, dá ho do ruksaku, pripojí cez tethering z telefónu a notebook ostane zapnutý a dostupný – beží dlhá Claude session, SSH, RDP, dev servery, VPN.

Krátky popis: *PowerDeck lets you switch laptop profiles for work, travel, battery and gaming.*

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
| **RemoteWork** | cestovanie/práca mimo domova | ignore | vypnutý | off (pre vybrané siete) | balanced |
| **Travel** | (variant RemoteWork) zameraný na ruksak: prísnejší thermal guard, USB tethering | ignore | vypnutý | off | balanced / power-saver pri prehriatí |
| **BatterySaver** | max. výdrž | suspend | zapnutý | on | power-saver, nižší jas, voliteľne vypnutý BT |
| **Gaming** | výkon, po skončení návrat na predošlý profil | suspend | zapnutý | default | performance |
| **Presentation** | obrazovka nezhasína, bez uspania | – | vypnutý | default | balanced |
| *vlastné* | používateľom vytvorené | | | | |

**Prvá verzia (MVP):** `Default`, `RemoteWork`, `BatterySaver`, `Gaming` + vlastné profily. `Travel` a `Presentation` neskôr.

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
- Pri nízkej batérii s ignorovaným vekom: upozornenie / automatický návrat na bezpečný profil (nastaviteľné).

## 9. Zobrazenie stavu (GUI)

Aktuálny profil · Wi‑Fi (SSID, powersave) · tethering/aktívne spojenie · batéria (%, nabíjanie) · teplota CPU (farebné prahy) · aktívne session (SSH/RDP/Claude) · tlačidlá **Enable Remote Work** / **Restore Default** · vytvorenie vlastného profilu.

## 10. Verzia 1 – rozsah

- GUI na tvorbu a prepínanie profilov, CLI.
- Predpripravené: `Default`, `RemoteWork`, `BatterySaver`, `Gaming`.
- Veko, suspend, Wi‑Fi powersave, power mode.
- Zobrazenie aktuálneho profilu a stavu.
- Potvrdenie + diff pred systémovými zmenami, zálohy a obnovenie.
- Rozhranie SK + EN.

**Až neskôr (v2+):** Bluetooth/USB autosuspend, jas/nočný režim, úlohy pri aktivácii, GNOME extension, thermal guard s automatickou reakciou, profily `Travel`/`Presentation`.

## 11. Otvorené otázky

1. Presná verzia GNOME na Ubuntu 26.04 a existencia kľúčov `lid-close-*-action`.
2. Reštart vs. reload `systemd-logind` – čo je bezpečné pre bežiacu session?
3. Spoľahlivosť RDP (`gnome-remote-desktop`) so zavretým vekom a zamknutou obrazovkou.
4. HW používateľa (model notebooku): dostupnosť teplotných senzorov, `power-profiles-daemon` vs. TLP.
5. Samostatná GTK appka vs. GNOME extension – rozhodnutie pre v1 (odporúčanie: GTK appka + CLI).
6. Licencia a verejné/súkromné repo.

## 12. Pôvod

- `RemoteStation.md` – návod na nastavenie notebooku + prvý návrh extension.
- `PowerDeck.md` – výrobná špecifikácia a názvoslovie (PowerDeck ako hlavný názov, RemoteStation ako názov pôvodného projektu/repozitára; ďalšie navrhnuté názvy: StationKit, ProfileDeck, WorkMode, RemoteDesk, DeskPilot, ModeShift, TuxMode).
