# DiazBets

Napi focimeccs-elemző két felhasználónak: meccsek, szorzók, sérültek, egymás elleni mérleg és tippek
(győztes, mindkét csapat szerez gólt, gólok 2.5 felett/alatt), előzmény-statisztikával.

- Adatforrás: [API-Football](https://www.api-football.com/) ingyenes csomag (100 kérés/nap), a kód 90-nél megáll.
- Tippek: Poisson-modell a csapatok hazai/vendég gólmutatóiból (saját eredményadatbázis), összevetve a szorzókkal ("érték").
- Technika: Flask + SQLite, gunicorn, Caddy fordított proxy.

A tippek statisztikai becslések, nem biztos nyerők. Szöglettippet ez a verzió nem ad (a szorzót megmutatja, ha van).

## Telepítés (Ubuntu, a meglévő Caddy mellett)

```bash
sudo adduser --system --group --home /opt/diazbets diazbets
sudo git clone https://github.com/Speederhun/DiazBets /opt/diazbets
sudo chown -R diazbets:diazbets /opt/diazbets
cd /opt/diazbets
sudo -u diazbets python3 -m venv .venv
sudo -u diazbets .venv/bin/pip install -r requirements.txt
sudo -u diazbets cp .env.example .env && sudo -u diazbets nano .env   # API kulcs + DIAZBETS_SECRET
sudo chmod 600 .env

# felhasználók (jelszót bekéri)
sudo -u diazbets .venv/bin/python manage.py create_user <nev1>
sudo -u diazbets .venv/bin/python manage.py create_user <nev2>
```

A 8085-ös portnak szabadnak kell lennie: `ss -ltn | grep 8085`. Ha foglalt, írd át a `deploy/diazbets.service`
és a Caddyfile-blokk portját is.

```bash
sudo cp deploy/diazbets.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now diazbets

# Caddy: a deploy/Caddyfile.snippet blokkot told be a /etc/caddy/Caddyfile végére
sudo systemctl reload caddy

# időzített futás
sudo -u diazbets crontab -e     # a deploy/crontab.example tartalmával
```

DNS: az `diazbets.ugyvivo.hu` A rekordja a DigitalOcean gép IP-jére mutasson, a Caddy intézi a HTTPS tanúsítványt.

Az első gyűjtés kézzel: `sudo -u diazbets bash -c 'set -a; . .env; .venv/bin/python collect.py collect'`

## Fejlesztés

```bash
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt pytest
python manage.py demo              # kitalált adatok
DIAZBETS_SECURE_COOKIE=0 python app.py
pytest
```

## Élő eredmények (csak Pro)

A listában élő perc és állás látszik, a meccsoldalon eredményjelző, eseményvonal (gólok, lapok, cserék), statisztikasávok
(labdabirtoklás, lövések, szögletek, xG) és a tippek élő állása. A szerver kérdezi az API-t, és 45 másodpercig tárolja a választ,
ezért a keretet alig terheli. A befejezett meccsek tippjeit azonnal elszámolja.

## Pro csomag (API-Football Pro, 7500 kérés/nap)

A böngészőből „Telepítés" (PWA) is elérhető: a `static/manifest.webmanifest`, az ikonok és a `/sw.js` (átengedő service worker, nem tárol adatot) adják.

Az `.env`-ben legyen `DIAZBETS_PLAN=pro`. Ilyenkor a keret 7300 kérés/nap (a valódi korlát 7500; az élő oldal a teljes keretet használhatja, a gyűjtés korábban megáll), és a lekérések gyorsak.

Első beállítás (egyszer, a gyűjtés előtt):

```bash
cd /opt/diazbets
sudo -u diazbets bash -c 'set -a; . ./.env; set +a; .venv/bin/python collect.py backfill 365'   # egy év eredményei
sudo -u diazbets bash -c 'set -a; . ./.env; set +a; .venv/bin/python collect.py stats 3000'     # szögletstatisztika
```

A `stats` meccsenként 1 kérés, a keret miatt több napra széttörhet: a kiírás jelzi, mennyi van hátra, és ugyanazt a parancsot kell újra futtatni.
Az éjszakai `settle` a friss meccsek szögletstatisztikáját magától letölti.

- Tippek: Poisson-modell a saját eredményadatbázisból, a fogadóirodai szorzókból számolt eséllyel vegyítve (`DIAZBETS_MARKET_WEIGHT`, alap 0,5).
- Szöglet: a csapatok szögletmutatóiból várható összes szöglet, a szorzóvonalak közül a legközelebbit választja.
- Az előzményeknél a találati arány mellett kalibrációs tábla mutatja, stimmel-e a megadott esély a valósággal.

## Ingyenes csomag korlátai

- A tabella-, szorzó- és sérült-végpontok csak a 2022–2024-es szezonokat engedik; egymás elleni lekérésnél a `last` paraméter nem használható.
- Napi 100 kérés, percenként 10 (`DIAZBETS_PLAN=free`).
- A szöglet-statisztika letöltése meccsenként 1 kérés, ingyen nem praktikus.
- Kevés saját adatú csapatoknál (kevesebb mint 3 meccs) szorzó alapú tartalék tipp készül.


## Önjavító modell és finomítások

- **Kalibráció:** a lezárt tippek alapján piaconként tanulja, mennyire túl- vagy alulbiztos a modell, és ennyivel igazítja az új tippek esélyét (legalább 60 elszámolt tipp kell hozzá, kevés mintánál erősen visszafogott). A nyers modell-esély külön (`tips.raw_prob`) megmarad, hogy a tanulás ne hajtson végtelen kört. Kikapcsolás: `DIAZBETS_CALIBRATE=0`.
- **Fáradtság és hiányzók:** kevés pihenőnap (<=4) és sok hiányzó kis, plafonozott korrekciót ad a várható gólokra. Kikapcsolás: `DIAZBETS_ADJUST=0`.
- **Hazai/idegen erő és forma:** külön hazai és idegenbeli erő, kevés pályaadatnál a csapat összesített teljesítménye felé húzva; az idősebb meccsek súlya 70 naponként felére csökken.
- **Szorzómozgás:** `python collect.py odds` frissíti a szorzókat és eltárolja a változásokat (`odds_history`). A listán és a meccsoldalon nyíl jelzi az elmozdulást.
- **Ligák szerint:** az Előzmények oldalon bajnokságonként és piaconként a találati arány és a profit.
- **API-előrejelzés a vegyítésben:** az 1X2 esélybe az API-Football előrejelzése is beleszámít (alap súly 15%, kevés meccsű csapatnál akár ~40%). Kikapcsolás: `DIAZBETS_API_WEIGHT=0`.
- **xG:** a modell a csapatok erejét az xG és a tényleges gól keverékéből (65% xG) számolja. A régi meccsek xG-jét a `collect.py stats N` tölti be (naponta futtasd, amíg a „még hátravan” 0 nem lesz).
- **Hosszabb előzmény:** 730 napnyi eredmény, 100 napos felezési idővel. Betöltés: `collect.py backfill 730`.
- **Kulcsjátékos-hiány:** az API góllövő- és gólpassz-listájából (hetente frissítve) számolja, mennyit ér a csapatnak a kiesett játékos (legfeljebb -15% a várható gólokból).
- **Éles iroda:** `DIAZBETS_SHARP_BOOKMAKER` (alap: 4). A `collect.py bookmakers` kiírja az API fogadóirodáit, ellenőrizd az azonosítót. 0 = kikapcsolva. A piaci esélyben az éles iroda 65%-ot számít.
- **Saját Elo:** a teljes adatbázis eredményeiből számolt Elo (`DIAZBETS_ELO_WEIGHT`, alap 0.10, kevés meccsű csapatnál nagyobb), a meccsoldalon 4. véleményként is látszik. Külső Elo-forrást (clubelo.com, eloratings.net) nem építettem be: nem hivatalos, a csapatnevek párosítása törékeny.

## Lista és élő nézet

- A Meccsek oldalon a ligák lenyithatók (a választás megmarad), a kártyák széles képernyőn 2–3 oszlopban állnak, a fejlécben az Élő jelvény a most zajló meccsek számát mutatja. A szűrő: Mind / Élő / Értékes tipp.
- `/live`: az élő és hamarosan induló meccsek egy rácsban, ligától függetlenül; a zajló meccsek elöl.
