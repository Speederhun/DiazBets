# DiazBets

Napi focimeccs-elemző két felhasználónak: meccsek, szorzók, sérültek, egymás elleni mérleg és tippek
(győztes, mindkét csapat szerez gólt, gólok 2.5 felett/alatt), előzmény-statisztikával.

- Adatforrás: [API-Football](https://www.api-football.com/) ingyenes csomag (100 kérés/nap), a kód 90-nél megáll.
- Tippek: Poisson-modell a tabella hazai/vendég gólmutatóiból, összevetve a szorzókkal ("érték").
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

## Korlátok (ingyenes csomag)

- Naponta kb. 100 kérés: sok meccses napon a kevésbé fontos ligáknál (a lista sorrendje a `collect.py`-ban) kimaradhat a H2H.
- A kieséses szakaszokra (nincs tabella) a modell nem ad tippet.
- A szorzók naponta egyszer frissülnek (Bet365).
