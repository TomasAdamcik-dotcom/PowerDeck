# PowerDeck — stav a čo ostáva

Stav: **v1.0.0** (2026-11-16, prvé verejné vydanie). Beží na ThinkPade T14s Gen 4 (Ubuntu 26.04) ako nainštalovaný `.deb`:
strážca ako služba po prihlásení, ikona v hornej lište, Rucksack s reguláciou teploty overený v
batohu s vzdialenou plochou. Rozhodnutia a ich dôvody sú v `SPECIFICATION.md`, sekcia 0.

## Ďalší postup (poradie)

1. **Testy na notebooku** (sekcia A, ~15–20 min), aby bol strážca ako stála služba overený.
2. **Rozhodnutia vlastníka** (sekcia B).
3. **Revízia GUI** (sekcia C).
4. **Vylepšenia** (sekcia D), podľa záujmu.

## A. Overiť na notebooku — vyžaduje teba

Najdôležitejšie (strážca teraz beží stále):
- [ ] **Kritická teplota, keď uspanie blokuje aplikácia** (napr. Claude Desktop): dočasný profil s nízkym prahom (napr. warn 40 / critical 45 °C) → notifikácia, druhé meranie o 10 s, `systemctl suspend` zlyhá a strážca použije `pkexec /usr/libexec/powerdeck-suspend`. **Overiť, že polkit to pustí bez hesla aj zo systemd user služby** (akcia `io.github.powerdeck.thermal-suspend`, `allow_active=yes`).
- [ ] **Uspanie pri 15 % batérie** (RemoteWork, Travel, Rucksack; bez nabíjačky): dočasne so zvýšeným prahom, napr. 80 %, alebo pri vybití. Po prebudení ho znova neuspí, kým nepripojíš nabíjačku alebo batéria nestúpne nad 18 %.
- [ ] **Po prihlásení / reštarte** beží strážca (`systemctl --user is-active powerdeck-guard`) a ikona je v hornej lište bez okna.
- [ ] **Odinštalovanie:** aplikuj Rucksack, potom `sudo apt remove powerdeck` → všetko ako pred PowerDeckom (veko, gsettings, Wi‑Fi powersave, power mode, Bluetooth, CPU limity a regulácia), strážca a ikona zastavené. VM ✓ pre veko, power mode, zámok, idle, auto-suspend; Wi‑Fi, BT, turbo a RAPL ešte nie.
- [ ] **Aktualizácia** na novší `.deb` aktívne nastavenia **nesmie** vrátiť (prerm robí reset len pri „remove“).

Drobnosti:
- [ ] Zrušený `pkexec` dialóg (GUI aj ikona v lište) → chyba sa ukáže a stav ostane konzistentný.
- [ ] `powerdeck snapshot` uloží aktuálne nastavenia ako profil Default.
- [ ] `man nm-settings-nmcli`: hodnoty `802-11-wireless.powersave` (0 default, 1 ignore, 2 off, 3 on).
- [ ] Po reštarte sú CPU limity a regulácia preč (sysfs sa nezachováva) – je to zámer, profil treba aplikovať znova.

## B. Rozhodnutia vlastníka

- [x] **Licencia:** GPL-3.0-or-later (2026-11-16). Repo sa zverejní.
- [x] **Maintainer v `.deb`:** GitHub noreply adresa vlastníka.

## C. Revízia GUI

- [ ] Vizuálna kontrola na reálnej ploche: okno, stav, dialóg so zmenami, editor; prepnutie Svetlý/Tmavý štýl za behu.
- [ ] Obsah profilu: info ikona (na Yaru má byť „i“, nie žiarovka), čitateľnosť tooltipu, texty v SK aj EN.
- [ ] Monitoring sa obnovuje každé 2 s, level bary fungujú a nespomaľujú appku.
- [ ] Menu ikony v lište: preklad položiek, poradie, čo ešte chýba (napr. teplota v popise ikony).

## D. Vylepšenia (navrhnuté, neschválené)

- [ ] Mazanie vlastných profilov z GUI.
- [ ] Výber Wi‑Fi sietí zo zoznamu uložených NetworkManager profilov v editore (dnes text oddelený čiarkou).
- [ ] Max Performance: automatický návrat po skončení náročnej úlohy (detekcia procesu); dnes ručne cez Restore.
- [ ] Editor: samostatné nastavenie veka pre batériu, nabíjačku a dok (dnes jedna hodnota pre všetky).
- [ ] GUI testy v CI (headless GTK potrebuje balíky na runneri); dnes `scripts/dev-container.sh gui`.
- [ ] Pri `apt remove` zastaviť bežiacich strážcov používateľov (dnes skončia až pri odhlásení; bez unit súboru `Restart=` zlyháva do limitu).
- [ ] gettext namiesto slovníka v `i18n.py` (až keď sa texty ustália).
- [ ] USB autosuspend (závisí od HW, root).
- Zamietnuté / odložené: Flatpak (sandbox bráni `nmcli`/`pkexec`); thermald namiesto vlastnej regulácie; GNOME Remote Login (čierna obrazovka po 30 s timeoute handoveru, Launchpad #2141992 – skúsiť znova po aktualizácii gnome-remote-desktop).

## Mimo PowerDecku

- Diakritika cez RDP (á, í, ž…) chýba, keď je na notebooku aktívne rozloženie `gb`: gnome-remote-desktop hlási „No keycode found for keyval“. Prepnúť na `sk` alebo nastaviť rozloženie v RDP klientovi.

## Hotové

**v1.0.0 (2026-11-16)** – prvé verejné vydanie
- Prepnutie profilu na jeden klik (najprv sa vrátia zmeny predošlého profilu), „Ukončiť PowerDeck“ vráti Default; tlačidlá „Zapnúť Remote Work“ a „Obnoviť predošlý stav“ z okna odstránené.
- Graf histórie teploty a frekvencie CPU (strážca zapisuje každých 20 s, 7 dní), prepnutia profilov ako zvislé čiary.
- Náhľad už nehlási „Wi-Fi powersave: disable → 2“ pri sieťach, ktoré sú už nastavené.
- Licencia GPL-3.0-or-later, skutočný maintainer v `.deb`, požiadavky a kompatibilita v README.

**v0.4 (2026-11-16)**
- **Rucksack s reguláciou teploty** (cieľ 65 °C) namiesto pevného limitu: root služba `powerdeck-thermal@<°C>`, PI regulátor nad RAPL PL1/PL2 (záloha `scaling_max_freq`), PL2 nikdy nad PL1. Návrh v [docs/thermal-regulation.md](docs/thermal-regulation.md). Notebook: na stole 65 ± 1 °C pri ~15–17 W; v batohu na batérii (power-saver) 65–66 °C pri 9,4–11 W, bez skoku na štarte záťaže.
- **Strážca:** kritická teplota sa potvrdzuje druhým meraním o 10 s (hneď len pri ≥ kritická + 8 °C); uspanie pri ≤ 15 % batérie v profiloch so zavretým vekom; obnoví vzdialenú plochu, keď na 3389 nič nepočúva (najviac 3×, 10 min odstup).
- **Práca na diaľku v batohu overená** (2026-11-16 12:33–12:45): RDP pripojené počas záťaže, strážca nezasiahol.
- `apply` upozorní, keď na RDP porte 3389 nič nepočúva (GNOME sa pri obsadenom porte ticho presunie na 3390).
- **Ikona v hornej lište** (StatusNotifierItem cez Gio, bez novej závislosti): otvoriť okno, aplikovať profil (s potvrdením), obnoviť, ukončiť. Zavretím okna aplikácia beží ďalej; skryté okno nič nemeria.
- `.deb`: strážca sa spúšťa s grafickou reláciou pre každého používateľa (`systemctl --user mask powerdeck-guard` ho vypne), ikona sa spúšťa pri prihlásení (`/etc/xdg/autostart`).
- Testy nezávisia od jazyka plochy.

**v0.1–v0.3**
- Profily: veko, auto-suspend, Wi‑Fi powersave, tethering, power mode, displej (jas, nočný režim, blank/lock, zámok), Bluetooth, CPU (turbo, % limit), úlohy `systemctl --user start|stop|restart UNIT` (whitelist).
- Vstavané profily Default, RemoteWork, Travel, Rucksack, Presentation, BatterySaver, Max Performance; vlastné profily a editor v GUI; `snapshot`; viacnásobný `restore`.
- Root helper `/usr/libexec/powerdeck-helper` (len whitelistované príkazy) + polkit; `powerdeck-suspend` (vie len uspať).
- RDP so zavretým vekom: `remote_access` profily vypnú zámok, strážca hneď zruší šetrič (overené na notebooku; spojenie pri zavretí veka raz spadne, opätovné pripojenie ide).
- Stav a monitoring (CLI aj GUI, `monitor --csv`), obsah profilu v ľudskej reči s presnými príkazmi, tooltipy, jazyk podľa prostredia.
- Odinštalovanie vráti všetky nastavenia do stavu pred PowerDeckom (`reset-all`).
- CI, `.deb`, rootless podman kontajner a QEMU/KVM VM na testy bez hesla.
