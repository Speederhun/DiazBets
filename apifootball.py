"""API-Football kliens napi kéréskeret-őrrel (az ingyenes csomag 100 kérés/nap)."""
import os
import time
from datetime import datetime, timezone

import requests

BASE_URL = "https://v3.football.api-sports.io"
# Biztonsági tartalék a 100-as napi limit alatt.
DAILY_BUDGET = int(os.environ.get("DIAZBETS_DAILY_BUDGET", "90"))


class BudgetExceeded(Exception):
    pass


class ApiError(Exception):
    pass


def _utc_day():
    # A kvóta 00:00 UTC-kor nullázódik.
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def used_today(conn):
    row = conn.execute("SELECT count FROM api_usage WHERE day = ?", (_utc_day(),)).fetchone()
    return row["count"] if row else 0


class Client:
    def __init__(self, conn, api_key=None):
        self.conn = conn
        self.api_key = api_key or os.environ.get("API_FOOTBALL_KEY")
        if not self.api_key:
            raise ApiError("Hiányzik az API_FOOTBALL_KEY (.env fájl).")
        self.session = requests.Session()
        self.session.headers["x-apisports-key"] = self.api_key

    def get(self, path, **params):
        if used_today(self.conn) >= DAILY_BUDGET:
            raise BudgetExceeded("Elfogyott a napi kéréskeret.")
        day = _utc_day()
        self.conn.execute(
            "INSERT INTO api_usage(day, count) VALUES (?, 1) "
            "ON CONFLICT(day) DO UPDATE SET count = count + 1",
            (day,),
        )
        self.conn.commit()
        resp = self.session.get(f"{BASE_URL}/{path}", params=params, timeout=30)
        if resp.status_code != 200:
            raise ApiError(f"HTTP {resp.status_code} a(z) {path} hívásnál")
        data = resp.json()
        errors = data.get("errors")
        if errors:
            # Az API üres listát vagy dictet ad hiba nélkül.
            raise ApiError(f"{path}: {errors}")
        # Percenkénti limit (ingyenes csomag: 10/perc) kímélése.
        time.sleep(float(os.environ.get("DIAZBETS_REQUEST_DELAY", "6.5")))
        return data
