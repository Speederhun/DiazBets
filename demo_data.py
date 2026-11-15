"""Kitalált demó adatok a felület kipróbálásához (nem valós meccsek)."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import collect
from db import connect, init_db

TZ = ZoneInfo("Europe/Budapest")


def _standing(tid, name, rank, form, hp, hgf, hga, ap, agf, aga):
    return {"rank": rank, "team": {"id": tid, "name": name}, "points": 20 + rank, "form": form,
            "home": {"played": hp, "goals": {"for": hgf, "against": hga}},
            "away": {"played": ap, "goals": {"for": agf, "against": aga}}}


def load():
    init_db()
    conn = connect()
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    yesterday = (datetime.now(TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
    rows = [
        _standing(1, "Demo FC", 1, "WWWDW", 6, 16, 4, 5, 10, 5),
        _standing(2, "Példa United", 2, "WDWLW", 6, 12, 6, 5, 8, 7),
        _standing(3, "Minta SC", 3, "LDWWL", 5, 9, 7, 6, 7, 9),
        _standing(4, "Teszt Rovers", 4, "LLDLW", 6, 6, 10, 5, 4, 11),
    ]
    st = {"response": [{"league": {"standings": [rows]}}]}
    conn.execute("INSERT OR REPLACE INTO standings VALUES (39, 2026, ?, ?)", (today, json.dumps(st)))
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
    conn.execute("INSERT OR REPLACE INTO tips VALUES (9003, '1X2', 'Away', 0.55, 1.9, 0.045, 'demó', NULL)")
    conn.execute("INSERT OR REPLACE INTO tips VALUES (9003, 'BTTS', 'Yes', 0.6, 1.8, 0.08, 'demó', NULL)")
    conn.commit()
    collect.generate_tips(conn, today)
    collect.settle_local(conn)
    conn.close()
    print("Demó adatok betöltve.")
