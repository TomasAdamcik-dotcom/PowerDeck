# Ako postupovať lepšie a efektívnejšie

Spätný pohľad na doterajší vývoj PowerDecku (od zlúčenia dvoch .md dokumentov po code review). Body sú zoradené približne podľa toho, koľko času by ušetrili.

## 1. Testovanie na reálnom stroji skôr a priebežne

- **Čo sa stalo:** takmer všetko sa vyvíjalo v cloude bez systemd, NetworkManagera, senzorov a polkit agenta. Kľúčové veci (pkexec dialóg, HUP pre logind, uspanie zo služby strážcu, RDP pri zavretom veku) sa dali overiť len u teba a zostávali dlho ako „treba overiť“. Problém s RDP a šetričom obrazovky sa objavil až pri reálnom teste a vyžiadal si dodatočný návrh (`remote_access`).
- **Lepšie:**
  - Hneď na začiatku pripojiť notebook do session (Remote Control / priečinok z počítača). Agent by tak mohol spúšťať `plan`, `status` a `monitor` priamo na tvojom HW.
  - Prípadne mať testovací VM s Ubuntu 26.04 na overenie systemd, polkitu a `.deb`.
  - Po každej väčšej funkcii hneď prejsť bezpečné body z `TODO.md` (sekcia 0), nie až na konci.

## 2. Rozhodnúť dopredu, čo je „hotové“ a ako sa to rozhoduje

- **Čo sa stalo:** niektoré správania sa menili viackrát.
  - Reakcia strážcu pri kritickej teplote prešla tromi verziami: power-saver, potom podmienený power-saver, nakoniec uspanie.
  - Travel mal najprv 70 % limit, potom len vypnuté turbo.
  - Riadok „Aktívny profil“ v Stave pribudol a neskôr zmizol, keď sa aktívny profil presunul do hornej lišty.
- **Lepšie:**
  - Pri každej funkcii napísať 2–3 akceptačné kritériá, napr. „pri kritickej teplote notifikácia + uspanie do 10 s, aj keď uspanie blokuje aplikácia“.
  - Viesť krátky **zoznam rozhodnutí** (decision log) v `SPECIFICATION.md` a aktualizovať ho. Špecifikácia je dnes čiastočne zastaraná, aktuálny stav je roztrúsený v `TODO.md` a commitoch.

## 3. Vetvy a paralelná práca dohodnúť na začiatku

- **Čo sa stalo:** prvé kolá išli na vetvu `ccr-…`, potom vznikla `develop` (aj s prácou z inej session). Posledný commit z `ccr-…` v nej chýbal, takže ho bolo treba prenášať a riešiť konflikt v `TODO.md`. `TODO.md` sa menil súbežne na viacerých miestach.
- **Lepšie:**
  - Od prvého commitu `main` + `develop` + krátke feature vetvy (worktree), tak ako teraz.
  - Ak beží viac sessions naraz, každá má vlastnú tému a pred začatím si stiahne `develop`. `TODO.md` upravuje vždy len tá, ktorá mergeuje.
  - Zvážiť **PR namiesto priameho merge do `develop`**: CI by bežalo pred zlúčením a ostala by história review.

## 4. Bezpečnostné review hneď pri kóde, ktorý beží ako root

- **Čo sa stalo:** pri návrate nastavení pri odinštalovaní vznikla vážna chyba (root spúšťal príkazy zo záloh, ktoré môže zapísať používateľ). Odhalilo ju až code review o krok neskôr.
- **Lepšie:**
  - Každú zmenu, ktorá sa dotýka `pkexec`, helpera, polkitu, `prerm` alebo `postrm`, hneď nechať skontrolovať (`/security-review` alebo nezávislý agent), ešte pred mergom.
  - Pravidlo „root nikdy nečíta nič, čo môže zapísať používateľ, bez whitelistu“ zapísať do `CLAUDE.md` alebo špecifikácie, aby platilo aj pre ďalšie sessions.

## 5. Presnejšie zadania (hlavne pri GUI)

- **Čo sa stalo:**
  - „Keď klikne na status“ v skutočnosti znamenalo profil v zozname, takže to bolo treba vyjasňovať.
  - GUI úpravy prichádzali po jednej v samostatných kolách: poradie blokov, kompaktnosť, tooltipy, aktívny profil, rámik.
- **Lepšie:**
  - Pomenovať presný prvok („dlaždica profilu v ľavom zozname“, „blok Stav“) alebo poslať screenshot s nakreslenými šípkami.
  - Pripomienky ku GUI zbierať do jedného zoznamu a poslať naraz: jedna iterácia, jeden screenshot, jedna kontrola.
  - Na väčšie zmeny dizajnu dať najprv mockup, alebo poprosiť o 2 varianty na výber.

## 6. Spätná väzba na GUI automaticky

- **Čo sa stalo:** screenshoty sa robili ručne na požiadanie a skript `scripts/screenshot.py` vznikol až neskoro.
- **Lepšie:**
  - Mať screenshot skript od prvej verzie GUI.
  - Po každej GUI zmene automaticky priložiť SK aj EN screenshot.
  - Pridať do CI headless smoke test GUI (Xvfb) a screenshot ako artefakt, aby chyby ako nepreložený text v zozname profilov boli vidieť hneď.

## 7. Plánovanie väčších úloh pred písaním kódu

- **Čo sa stalo:** väčšie celky (CPU limity, odinštalovanie, refaktoring) sa písali rovno. Architektonické rozhodnutia (kde beží root, čo je user a čo system pass) sa riešili počas implementácie.
- **Lepšie:**
  - Pri úlohách nad ~1 hodinu práce najprv **plan mode**: návrh, otázky, odsúhlasenie, až potom kód.
  - Silnejší model nasadiť od začiatku pri návrhu a review (model sa menil až v polovici). Na rutinné úpravy textov a CSS stačí rýchlejší.

## 8. Otvorené rozhodnutia vybaviť naraz

- Niektoré otázky ostali dlho otvorené: licencia, maintainer v `.deb`, Flatpak áno/nie, editor sietí, mazanie profilov.
- **Lepšie:** raz za čas prejsť sekciu „potrebuje tvoje rozhodnutie“ v `TODO.md` a rozhodnúť všetko naraz. Nedokončené rozhodnutia sa potom netiahnu do každej iterácie.

## 9. Testy ako súčasť zadania

- **Čo sa stalo:** testy vždy vznikli, ale niektoré rozpory (duplicitné texty, text sľubuje inú akciu, než kód robí) zachytil test až potom, čo si si ich všimol ty.
- **Lepšie:** ku každej požiadavke napísať aj „čo sa nesmie stať“. Z toho sa rovno stane test, napr. „text v profile nesmie sľubovať akciu, ktorú kód nespraví“, „žiadne nastavenie sa nezapisuje dvakrát“.

## 10. Prostredie a nástroje vyjasniť na začiatku

- Otázky „kde to vyvíjaš“ (cloud vs. mobil/Termux) a „aký jazyk“ (Python vs. C#) prišli až počas práce.
- **Lepšie:** na začiatku projektu krátky „kick-off“:
  - kde beží vývoj a kde testovanie,
  - jazyk a toolkit,
  - cieľová verzia Ubuntu,
  - spôsob distribúcie (`.deb`),
  - vetvy.

Potom sa k tomu už netreba vracať.
