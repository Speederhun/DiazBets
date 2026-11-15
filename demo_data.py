"""Kitalált demó adatok a felület kipróbálásához (nem valós meccsek)."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import collect
from db import connect, init_db

TZ = ZoneInfo("Europe/Budapest")


def load():
    init_db()
    conn = connect()
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    yesterday = (datetime.now(TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
    names = {1: "Demo FC", 2: "Példa United", 3: "Minta SC", 4: "Teszt Rovers"}
    past = [(1, 2, 2, 1), (3, 4, 1, 1), (2, 3, 2, 0), (4, 1, 0, 3), (1, 3, 3, 1), (2, 4, 2, 1),
            (3, 1, 1, 2), (4, 2, 1, 1), (1, 4, 4, 0), (2, 1, 1, 1), (3, 2, 0, 1), (4, 3, 2, 2)]
    for k, (h, a, hg, ag) in enumerate(past):
        day = (datetime.now(TZ) - timedelta(days=k + 3)).strftime("%Y-%m-%d")
        ko = datetime.fromisoformat(f"{day}T20:00:00").replace(tzinfo=TZ).isoformat()
        conn.execute("""INSERT OR REPLACE INTO fixtures(id, day, kickoff, league_id, league_name, country, season,
            home_id, home, home_logo, away_id, away, away_logo, status, home_goals, away_goals)
            VALUES (?,?,?,39,'Premier League','England',2026,?,?,NULL,?,?,NULL,'FT',?,?)""",
                     (8000 + k, day, ko, h, names[h], a, names[a], hg, ag))
    fx = [
        (9001, today, 39, "Premier League", "England", 1, "Demo FC", 4, "Teszt Rovers", "NS", None, None, 20),
        (9002, today, 39, "Premier League", "England", 2, "Példa United", 3, "Minta SC", "NS", None, None, 18),
        (9003, yesterday, 39, "Premier League", "England", 3, "Minta SC", 1, "Demo FC", "FT", 1, 2, 20),
    ]
    for fid, day, lid, ln, c, hid, h, aid, a, status, hg, ag, hour in fx:
        ko = datetime.fromisoformat(f"{day}T{hour:02d}:00:00").replace(tzinfo=TZ).isoformat()
        conn.execute("""INSERT OR REPLACE INTO fixtures(id, day, kickoff, league_id, league_name, country, season,
            home_id, home, home_logo, away_id, away, away_logo, status, home_goals, away_goals)
            VALUES (?,?,?,?,?,?,2026,?,?,NULL,?,?,NULL,?,?,?)""",
                     (fid, day, ko, lid, ln, c, hid, h, aid, a, status, hg, ag))
    for fid, sel, price in [(9001, "Home", 1.45), (9001, "Draw", 4.6), (9001, "Away", 7.0),
                            (9002, "Home", 2.05), (9002, "Draw", 3.5), (9002, "Away", 3.6)]:
        conn.execute("INSERT OR REPLACE INTO odds VALUES (?, '1X2', ?, ?)", (fid, sel, price))
    for fid in (9001, 9002):
        conn.execute("INSERT OR REPLACE INTO odds VALUES (?, 'BTTS', 'Yes', 1.8)", (fid,))
        conn.execute("INSERT OR REPLACE INTO odds VALUES (?, 'BTTS', 'No', 1.95)", (fid,))
        conn.execute("INSERT OR REPLACE INTO odds VALUES (?, 'OU2.5', 'Over 2.5', 1.85)", (fid,))
        conn.execute("INSERT OR REPLACE INTO odds VALUES (?, 'OU2.5', 'Under 2.5', 1.95)", (fid,))
    conn.execute("INSERT INTO injuries VALUES (9001, 4, 'Teszt Rovers', 'Kovács Péter', 'Missing Fixture', 'Térdsérülés')")
    conn.execute("INSERT OR REPLACE INTO h2h VALUES (9001, ?)", (json.dumps([
        {"date": "2026-03-01", "home": "Teszt Rovers", "away": "Demo FC", "hg": 0, "ag": 2, "league": "Premier League"},
        {"date": "2025-10-12", "home": "Demo FC", "away": "Teszt Rovers", "hg": 3, "ag": 1, "league": "Premier League"},
    ]),))
    # tipp a tegnapi meccsre, hogy az előzmények is látsszanak
    conn.execute("INSERT OR REPLACE INTO tips(fixture_id, market, pick, prob, odds, value, note, result) VALUES (9003, '1X2', 'Away', 0.55, 1.9, 0.045, 'demó', NULL)")
    conn.execute("INSERT OR REPLACE INTO tips(fixture_id, market, pick, prob, odds, value, note, result) VALUES (9003, 'BTTS', 'Yes', 0.6, 1.8, 0.08, 'demó', NULL)")
    conn.commit()
    collect.generate_tips(conn, today)
    collect.settle_local(conn)
    conn.close()
    print("Demó adatok betöltve.")
