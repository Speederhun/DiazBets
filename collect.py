"""Napi adatgyűjtés, tippgenerálás és elszámolás.

Használat:
    python collect.py collect [YYYY-MM-DD]   # meccsek, szorzók, sérültek, H2H, tippek
    python collect.py settle                 # tegnapi és mai eredmények, tippek elszámolása
    python collect.py backfill [napok]       # múltbeli eredmények letöltése a modellhez (alap: 45 nap)
"""
import json
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import model
from apifootball import ApiError, BudgetExceeded, Client
from db import connect, init_db

TZ = ZoneInfo("Europe/Budapest")

# API-Football liga ID-k, fontossági sorrendben (ha elfogy a keret, a sor végéről maradnak ki)
LEAGUES = [
    (5, "Nemzetek Ligája"),
    (2, "Bajnokok Ligája"),
    (3, "Európa-liga"),
    (39, "Premier League"),
    (140, "La Liga"),
    (135, "Serie A"),
    (78, "Bundesliga"),
    (61, "Ligue 1"),
    (271, "NB I"),
    (848, "Konferencia-liga"),
    (88, "Eredivisie"),
    (94, "Primeira Liga"),
    (40, "Championship"),
]
LEAGUE_ORDER = {lid: i for i, (lid, _) in enumerate(LEAGUES)}

BOOKMAKER = 8  # Bet365
BET_IDS = {1: "1X2", 8: "BTTS", 5: "OU", 45: "CORNERS"}
FINISHED = {"FT", "AET", "PEN"}


def today():
    return datetime.now(TZ).strftime("%Y-%m-%d")


def log(msg):
    print(f"[{datetime.now(TZ):%H:%M:%S}] {msg}", flush=True)


# ---------------------------------------------------------------- gyűjtés

def store_fixtures(conn, items, day):
    wanted = {lid for lid, _ in LEAGUES}
    n = 0
    skipped = {}
    for it in items:
        if it["league"]["id"] not in wanted:
            key = f'{it["league"]["id"]} {it["league"]["name"]}'
            skipped[key] = skipped.get(key, 0) + 1
    if skipped:
        top = sorted(skipped.items(), key=lambda kv: -kv[1])[:8]
        log(f"Az API {len(items)} meccset adott; nem figyelt ligák: " + "; ".join(f"{k} ({v})" for k, v in top))
    else:
        log(f"Az API {len(items)} meccset adott")
    for it in items:
        lg = it["league"]
        if lg["id"] not in wanted:
            continue
        fx, t, g = it["fixture"], it["teams"], it["goals"]
        conn.execute(
            """INSERT INTO fixtures(id, day, kickoff, league_id, league_name, country, season,
                   home_id, home, home_logo, away_id, away, away_logo, status, home_goals, away_goals)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET kickoff=excluded.kickoff, status=excluded.status,
                   home_goals=excluded.home_goals, away_goals=excluded.away_goals""",
            (fx["id"], day, fx["date"], lg["id"], lg["name"], lg.get("country"), lg["season"],
             t["home"]["id"], t["home"]["name"], t["home"].get("logo"),
             t["away"]["id"], t["away"]["name"], t["away"].get("logo"),
             fx["status"]["short"], g.get("home"), g.get("away")),
        )
        n += 1
    conn.commit()
    return n


def known_ids(conn):
    return {r[0] for r in conn.execute("SELECT id FROM fixtures")}


def store_odds(conn, items):
    ids = known_ids(conn)
    for it in items:
        fid = it["fixture"]["id"]
        if fid not in ids:
            continue
        for bm in it.get("bookmakers", []):
            for bet in bm.get("bets", []):
                market = BET_IDS.get(bet["id"])
                if not market:
                    continue
                for v in bet["values"]:
                    sel = str(v["value"])
                    if market == "OU":
                        if sel not in ("Over 2.5", "Under 2.5"):
                            continue
                        market_name = "OU2.5"
                    elif market == "CORNERS":
                        market_name = "CORNERS"
                    else:
                        market_name = market
                    conn.execute(
                        "INSERT OR REPLACE INTO odds(fixture_id, market, selection, price) VALUES (?,?,?,?)",
                        (fid, market_name, sel, float(v["odd"])),
                    )
    conn.commit()


def store_injuries(conn, items):
    seen = set()
    ids = known_ids(conn)
    for it in items:
        fid = it["fixture"]["id"]
        if fid not in ids:
            continue
        if fid not in seen:
            conn.execute("DELETE FROM injuries WHERE fixture_id = ?", (fid,))
            seen.add(fid)
        conn.execute(
            "INSERT INTO injuries(fixture_id, team_id, team, player, type, reason) VALUES (?,?,?,?,?,?)",
            (fid, it["team"]["id"], it["team"]["name"], it["player"]["name"],
             it["player"].get("type"), it["player"].get("reason")),
        )
    conn.commit()


def store_h2h(conn, fixture_id, items):
    items = sorted((it for it in items if it["fixture"]["status"]["short"] == "FT"),
                   key=lambda it: it["fixture"]["date"], reverse=True)[:5]
    rows = [{
        "date": it["fixture"]["date"][:10],
        "home": it["teams"]["home"]["name"], "away": it["teams"]["away"]["name"],
        "hg": it["goals"]["home"], "ag": it["goals"]["away"],
        "league": it["league"]["name"],
    } for it in items]
    conn.execute("INSERT OR REPLACE INTO h2h(fixture_id, json) VALUES (?,?)", (fixture_id, json.dumps(rows)))
    conn.commit()


def fetch_paged(client, path, max_pages=8, **params):
    out, page = [], 1
    while True:
        data = client.get(path, page=page, **params)
        out.extend(data.get("response", []))
        paging = data.get("paging") or {}
        if page >= paging.get("total", 1) or page >= max_pages:
            return out
        page += 1


def _safe(label, fn):
    """Egy lépés hibája ne állítsa le a többit. A keret elfogyása viszont mindent leállít."""
    try:
        fn()
        log(f"{label}: kész")
        return True
    except BudgetExceeded:
        raise
    except Exception as e:  # noqa: BLE001 - a pontos okot ki akarjuk írni a naplóba
        log(f"{label}: HIBA – {type(e).__name__}: {e}")
        return False


def mark_day(conn, day):
    conn.execute("INSERT OR IGNORE INTO fetched_days(day) VALUES (?)", (day,))
    conn.commit()


def collect(day=None):
    init_db()
    conn = connect()
    day = day or today()
    client = Client(conn)
    try:
        data = client.get("fixtures", date=day, timezone="Europe/Budapest")
        n = store_fixtures(conn, data.get("response", []), day)
        mark_day(conn, day)
        log(f"{day}: {n} meccs a figyelt ligákból")

        fixtures = conn.execute("SELECT * FROM fixtures WHERE day = ? AND status = 'NS' ORDER BY kickoff",
                                (day,)).fetchall()
        ordered = sorted(fixtures, key=lambda f: LEAGUE_ORDER.get(f["league_id"], 99))
        if not ordered:
            return

        # Szorzók: előbb napra lekérve, ha az ingyenes csomag nem engedi, meccsenként próbáljuk
        def odds_by_date():
            items = fetch_paged(client, "odds", date=day, bookmaker=BOOKMAKER)
            log(f"  szorzó {len(items)} meccshez (dátum szerint)")
            store_odds(conn, items)
            if not items:
                raise ApiError("üres válasz")

        def odds_by_fixture():
            ok = 0
            for f in ordered[:12]:
                d = client.get("odds", fixture=f["id"], bookmaker=BOOKMAKER)
                store_odds(conn, d.get("response", []))
                ok += bool(d.get("response"))
            log(f"  szorzó {ok} meccshez (meccsenként)")

        if not _safe("szorzók (dátum szerint)", odds_by_date):
            _safe("szorzók (meccsenként)", odds_by_fixture)

        def injuries_by_date():
            items = fetch_paged(client, "injuries", date=day)
            log(f"  {len(items)} sérült/eltiltott (dátum szerint)")
            store_injuries(conn, items)

        def injuries_by_fixture():
            total = 0
            for f in ordered[:12]:
                d = client.get("injuries", fixture=f["id"])
                store_injuries(conn, d.get("response", []))
                total += len(d.get("response", []))
            log(f"  {total} sérült/eltiltott (meccsenként)")

        if not _safe("sérültek (dátum szerint)", injuries_by_date):
            _safe("sérültek (meccsenként)", injuries_by_fixture)

        for f in ordered:
            if conn.execute("SELECT 1 FROM h2h WHERE fixture_id=?", (f["id"],)).fetchone():
                continue

            def get_h2h(f=f):
                d = client.get("fixtures/headtohead", h2h=f"{f['home_id']}-{f['away_id']}")
                store_h2h(conn, f["id"], d.get("response", []))

            if not _safe(f"H2H {f['home']}–{f['away']}", get_h2h):
                break  # ha az első is elutasítva, a többit se kérjük
    except BudgetExceeded:
        log("Elfogyott a napi kéréskeret, a maradék kimarad.")
    except ApiError as e:
        log(f"API hiba: {e}")
    finally:
        generate_tips(conn, day)
        conn.close()


def backfill(days=45):
    """Múltbeli eredmények letöltése (napi 1 kérés), ebből épül a csapatok formája és a modell."""
    init_db()
    conn = connect()
    client = Client(conn)
    from apifootball import DAILY_BUDGET, used_today
    done = 0
    try:
        for i in range(1, days + 1):
            d = (datetime.now(TZ) - timedelta(days=i)).strftime("%Y-%m-%d")
            if conn.execute("SELECT 1 FROM fetched_days WHERE day=?", (d,)).fetchone():
                continue
            if used_today(conn) >= DAILY_BUDGET - 25:
                log("A keret nagy része elfogyott, a visszatöltést holnap folytasd ugyanezzel a paranccsal.")
                break
            data = client.get("fixtures", date=d, timezone="Europe/Budapest")
            n = store_fixtures(conn, data.get("response", []), d)
            mark_day(conn, d)
            done += 1
            log(f"{d}: {n} meccs")
    except BudgetExceeded:
        log("Elfogyott a napi kéréskeret.")
    except ApiError as e:
        log(f"API hiba: {e}")
    finally:
        total = conn.execute("SELECT COUNT(*) FROM fixtures WHERE status='FT'").fetchone()[0]
        log(f"{done} nap letöltve, összesen {total} lejátszott meccs az adatbázisban")
        conn.close()


# ---------------------------------------------------------------- tippek

def load_odds(conn, fixture_id):
    rows = conn.execute("SELECT market, selection, price FROM odds WHERE fixture_id = ?", (fixture_id,))
    return {(r["market"], r["selection"]): r["price"] for r in rows}


def generate_tips(conn, day):
    teams, avg_h, avg_a = model.load_results_table(conn, day)
    count = 0
    for f in conn.execute("SELECT * FROM fixtures WHERE day = ? AND status = 'NS'", (day,)).fetchall():
        odds = load_odds(conn, f["id"])
        xg = None
        if avg_h and model.enough_data(teams, f["home_id"], f["away_id"]):
            xg = model.expected_goals(teams, avg_h, avg_a, f["home_id"], f["away_id"])
        if xg:
            new_tips = model.build_tips(model.probabilities(*xg), odds, *xg)
        else:
            # Kevés saját adat: szorzó alapú tartalék tipp (ha van szorzó)
            new_tips = model.tips_from_odds(odds)
        for t in new_tips:
            conn.execute(
                """INSERT INTO tips(fixture_id, market, pick, prob, odds, value, note)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(fixture_id, market) DO UPDATE SET pick=excluded.pick, prob=excluded.prob,
                       odds=excluded.odds, value=excluded.value, note=excluded.note
                   WHERE tips.result IS NULL""",
                (f["id"], t["market"], t["pick"], t["prob"], t["odds"], t["value"], t["note"]),
            )
            count += 1
    conn.commit()
    log(f"{count} tipp generálva ({len(teams)} csapat adata alapján)")


# ---------------------------------------------------------------- elszámolás

def judge(market, pick, hg, ag):
    """Visszaad: 'win' / 'loss'."""
    if market == "1X2":
        actual = "Home" if hg > ag else "Away" if ag > hg else "Draw"
        return "win" if pick == actual else "loss"
    if market == "BTTS":
        actual = "Yes" if hg > 0 and ag > 0 else "No"
        return "win" if pick == actual else "loss"
    if market == "OU2.5":
        actual = "Over 2.5" if hg + ag >= 3 else "Under 2.5"
        return "win" if pick == actual else "loss"
    raise ValueError(market)


def settle_local(conn):
    n = 0
    rows = conn.execute(
        """SELECT t.fixture_id, t.market, t.pick, f.home_goals, f.away_goals, f.status
           FROM tips t JOIN fixtures f ON f.id = t.fixture_id
           WHERE t.result IS NULL AND f.status IN ('FT','AET','PEN')
             AND f.home_goals IS NOT NULL AND f.away_goals IS NOT NULL""").fetchall()
    for r in rows:
        # Hosszabbítás/tizenegyesek után a gólok nem a 90 perces állást mutatják: ezeket érvénytelenre tesszük.
        if r["status"] == "FT":
            res = judge(r["market"], r["pick"], r["home_goals"], r["away_goals"])
        else:
            res = "void"
        conn.execute("UPDATE tips SET result=? WHERE fixture_id=? AND market=?", (res, r["fixture_id"], r["market"]))
        n += 1
    conn.commit()
    return n


def settle():
    init_db()
    conn = connect()
    client = Client(conn)
    try:
        for d in ((datetime.now(TZ) - timedelta(days=1)).strftime("%Y-%m-%d"), today()):
            data = client.get("fixtures", date=d, timezone="Europe/Budapest")
            store_fixtures(conn, data.get("response", []), d)
    except (BudgetExceeded, ApiError) as e:
        log(f"Elszámolás részleges: {e}")
    finally:
        log(f"{settle_local(conn)} tipp elszámolva")
        conn.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "collect":
        collect(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "settle":
        settle()
    elif cmd == "backfill":
        backfill(int(sys.argv[2]) if len(sys.argv) > 2 else 45)
    else:
        print(__doc__)
        sys.exit(1)
