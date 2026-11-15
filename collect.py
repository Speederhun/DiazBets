"""Napi adatgyűjtés, tippgenerálás és elszámolás.

Használat:
    python collect.py collect [YYYY-MM-DD]   # meccsek, szorzók, sérültek, H2H, tippek
    python collect.py settle                 # tegnapi és mai eredmények, tippek elszámolása
    python collect.py stats [db]             # szögletstatisztika letöltése a lejátszott meccsekhez (Pro)
    python collect.py backfill [napok]       # múltbeli eredmények letöltése a modellhez (alap: 45 nap)
    python collect.py odds [YYYY-MM-DD]      # csak a szorzók frissítése (mozgáskövetés), tippek újraszámolása
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import model
from apifootball import DAILY_BUDGET, PRO, ApiError, BudgetExceeded, Client, used_today
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
PER_FIXTURE_LIMIT = 60 if PRO else 12   # meccsenkénti tartalék lekérések száma
RESERVE = min(500 if PRO else 25, DAILY_BUDGET // 4)  # a visszatöltés/statisztika ennyi kérést mindig meghagy
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
                    price = float(v["odd"])
                    old = conn.execute("SELECT price FROM odds WHERE fixture_id=? AND market=? AND selection=?",
                                       (fid, market_name, sel)).fetchone()
                    if old is None or abs(old["price"] - price) > 1e-9:
                        conn.execute("INSERT INTO odds_history(fixture_id, market, selection, ts, price) VALUES (?,?,?,?,?)",
                                     (fid, market_name, sel, time.time(), price))
                    conn.execute(
                        "INSERT OR REPLACE INTO odds(fixture_id, market, selection, price) VALUES (?,?,?,?)",
                        (fid, market_name, sel, price),
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


def fetch_paged(client, path, max_pages=8, paged=True, **params):
    out, page = [], 1
    while True:
        # Egyes végpontok (pl. injuries) nem ismerik a page paramétert
        data = client.get(path, page=page, **params) if paged else client.get(path, **params)
        out.extend(data.get("response", []))
        paging = data.get("paging") or {}
        if not paged or page >= paging.get("total", 1) or page >= max_pages:
            return out
        page += 1


def store_stats(conn, fixture_id, items):
    """Szögletek tárolása. Üres válasznál 0-s csapatazonosítóval jelöljük, hogy ne kérjük újra."""
    n = 0
    for it in items:
        corners = None
        for st in it.get("statistics", []):
            if st.get("type") == "Corner Kicks":
                corners = st.get("value")
                break
        conn.execute("INSERT OR REPLACE INTO fixture_stats(fixture_id, team_id, corners) VALUES (?,?,?)",
                     (fixture_id, it["team"]["id"], int(corners) if corners is not None else None))
        n += 1
    if n == 0:
        conn.execute("INSERT OR REPLACE INTO fixture_stats(fixture_id, team_id, corners) VALUES (?,0,NULL)", (fixture_id,))
    conn.commit()


def collect_stats(conn, client, limit=500):
    rows = conn.execute(
        """SELECT id FROM fixtures f WHERE status = 'FT'
             AND NOT EXISTS (SELECT 1 FROM fixture_stats s WHERE s.fixture_id = f.id)
           ORDER BY kickoff DESC LIMIT ?""", (limit,)).fetchall()
    done = 0
    for r in rows:
        if used_today(conn) >= DAILY_BUDGET - RESERVE:
            log("A keret nagy része elfogyott, a statisztika-letöltést holnap folytasd.")
            break
        d = client.get("fixtures/statistics", fixture=r["id"])
        store_stats(conn, r["id"], d.get("response", []))
        done += 1
    left = conn.execute("""SELECT COUNT(*) FROM fixtures f WHERE status = 'FT'
        AND NOT EXISTS (SELECT 1 FROM fixture_stats s WHERE s.fixture_id = f.id)""").fetchone()[0]
    log(f"szögletstatisztika: {done} meccs letöltve, {left} még hátravan")


def stats_cmd(limit=500):
    init_db()
    conn = connect()
    client = Client(conn)
    try:
        collect_stats(conn, client, limit)
    except BudgetExceeded:
        log("Elfogyott a napi kéréskeret.")
    except ApiError as e:
        log(f"API hiba: {e}")
    finally:
        conn.close()


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
            for f in ordered[:PER_FIXTURE_LIMIT]:
                d = client.get("odds", fixture=f["id"], bookmaker=BOOKMAKER)
                store_odds(conn, d.get("response", []))
                ok += bool(d.get("response"))
            log(f"  szorzó {ok} meccshez (meccsenként)")

        if not _safe("szorzók (dátum szerint)", odds_by_date):
            _safe("szorzók (meccsenként)", odds_by_fixture)

        def injuries_by_date():
            items = fetch_paged(client, "injuries", paged=False, date=day)
            log(f"  {len(items)} sérült/eltiltott (dátum szerint)")
            store_injuries(conn, items)

        def injuries_by_fixture():
            total = 0
            for f in ordered[:PER_FIXTURE_LIMIT]:
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

        if PRO:
            _safe("API-előrejelzések", lambda: fetch_predictions(conn, client, ordered[:PER_FIXTURE_LIMIT]))
            _safe("tabellák", lambda: fetch_standings(conn, client, ordered, day))
    except BudgetExceeded:
        log("Elfogyott a napi kéréskeret, a maradék kimarad.")
    except ApiError as e:
        log(f"API hiba: {e}")
    finally:
        generate_tips(conn, day)
        conn.close()


def _pct(s):
    try:
        return float(str(s).strip().rstrip("%")) / 100
    except (TypeError, ValueError):
        return None


def store_api_prediction(conn, fid, item):
    p = item.get("predictions") or {}
    pc = p.get("percent") or {}
    goals = p.get("goals") or {}
    win = (p.get("winner") or {})
    conn.execute(
        """INSERT OR REPLACE INTO api_predictions(fixture_id, p_home, p_draw, p_away, advice, winner, under_over, goals_h, goals_a)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (fid, _pct(pc.get("home")), _pct(pc.get("draw")), _pct(pc.get("away")), p.get("advice"),
         win.get("name"), p.get("under_over"), goals.get("home"), goals.get("away")))
    conn.commit()


def fetch_predictions(conn, client, fixtures):
    n = 0
    for f in fixtures:
        if conn.execute("SELECT 1 FROM api_predictions WHERE fixture_id=?", (f["id"],)).fetchone():
            continue
        resp = client.get("predictions", fixture=f["id"]).get("response") or []
        if resp:
            store_api_prediction(conn, f["id"], resp[0])
            n += 1
    log(f"  {n} API-előrejelzés letöltve")


def compact_standings(data):
    """Az API standings válaszából lapos, kis sorlista (csoportkörnél a csoport neve is benne van)."""
    out = []
    for r in model.flatten_standings(data):
        al = r.get("all") or {}
        g = al.get("goals") or {}
        out.append({"rank": r.get("rank"), "team_id": r["team"]["id"], "team": r["team"]["name"],
                    "points": r.get("points"), "played": al.get("played"), "win": al.get("win"),
                    "draw": al.get("draw"), "lose": al.get("lose"), "gf": g.get("for"), "ga": g.get("against"),
                    "gd": r.get("goalsDiff"), "group": r.get("group") or "", "form": r.get("form") or ""})
    return out


def fetch_standings(conn, client, fixtures, day):
    n = 0
    seen = set()
    for f in fixtures:
        key = (f["league_id"], f["season"])
        if key in seen or not f["season"]:
            continue
        seen.add(key)
        row = conn.execute("SELECT fetched_day FROM standings WHERE league_id=? AND season=?", key).fetchone()
        if row and row["fetched_day"] == day:
            continue
        data = client.get("standings", league=key[0], season=key[1])
        rows = compact_standings(data)
        if rows:
            conn.execute("INSERT OR REPLACE INTO standings(league_id, season, fetched_day, json) VALUES (?,?,?,?)",
                         (key[0], key[1], day, json.dumps(rows)))
            conn.commit()
            n += 1
    log(f"  {n} tabella frissítve")


def refresh_odds(day=None):
    """Csak a szorzókat frissíti (a mozgás követéséhez), majd újraszámolja a még le nem zárt tippeket. Sérülteket/H2H-t nem kér."""
    init_db()
    conn = connect()
    day = day or today()
    client = Client(conn)
    try:
        pending = conn.execute("SELECT COUNT(*) FROM fixtures WHERE day=? AND status='NS'", (day,)).fetchone()[0]
        if not pending:
            log(f"{day}: nincs kezdés előtti meccs, nincs mit frissíteni")
            return
        items = fetch_paged(client, "odds", date=day, bookmaker=BOOKMAKER)
        store_odds(conn, items)
        changed = conn.execute("SELECT COUNT(*) FROM odds_history WHERE ts > ?", (time.time() - 300,)).fetchone()[0]
        log(f"  szorzó frissítve {len(items)} meccshez, {changed} változás")
    except BudgetExceeded:
        log("Elfogyott a napi kéréskeret.")
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
    done = 0
    try:
        for i in range(1, days + 1):
            d = (datetime.now(TZ) - timedelta(days=i)).strftime("%Y-%m-%d")
            if conn.execute("SELECT 1 FROM fetched_days WHERE day=?", (d,)).fetchone():
                continue
            if used_today(conn) >= DAILY_BUDGET - RESERVE:
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


def rest_days(conn, team_id, kickoff):
    """Hány napja játszott utoljára a csapat (a figyelt ligákban lejátszott meccsei alapján); None, ha nincs adat."""
    row = conn.execute(
        """SELECT MAX(kickoff) AS k FROM fixtures WHERE status IN ('FT','AET','PEN')
           AND (home_id = ? OR away_id = ?) AND kickoff < ?""", (team_id, team_id, kickoff)).fetchone()
    if not row or not row["k"]:
        return None
    try:
        a = datetime.fromisoformat(row["k"])
        b = datetime.fromisoformat(kickoff)
        return max(0, (b - a).days)
    except ValueError:
        return None


def missing_counts(conn, fixture_id):
    out = {}
    for r in conn.execute("SELECT team_id, COUNT(*) AS n FROM injuries WHERE fixture_id=? GROUP BY team_id", (fixture_id,)):
        out[r["team_id"]] = r["n"]
    return out


def load_calibration(conn):
    rows = conn.execute(
        """SELECT market, COALESCE(raw_prob, prob) AS p, CASE result WHEN 'win' THEN 1 ELSE 0 END AS y
           FROM tips WHERE result IN ('win','loss') ORDER BY fixture_id DESC LIMIT 3000""").fetchall()
    return model.fit_calibration((r["market"], r["p"], r["y"]) for r in rows)


def generate_tips(conn, day):
    teams, avg_h, avg_a = model.load_results_table(conn, day)
    cal = load_calibration(conn) if os.environ.get("DIAZBETS_CALIBRATE", "1") != "0" else {}
    adjust = os.environ.get("DIAZBETS_ADJUST", "1") != "0"
    api_base = float(os.environ.get("DIAZBETS_API_WEIGHT", "0.15"))  # 0 = az API előrejelzése nem számít bele
    corner_rows = conn.execute(
        """SELECT f.home_id, f.away_id, sh.corners AS hc, sa.corners AS ac
           FROM fixtures f
           JOIN fixture_stats sh ON sh.fixture_id = f.id AND sh.team_id = f.home_id
           JOIN fixture_stats sa ON sa.fixture_id = f.id AND sa.team_id = f.away_id
           WHERE f.status = 'FT' AND sh.corners IS NOT NULL AND sa.corners IS NOT NULL AND f.day <= ?""", (day,)).fetchall()
    c_teams, c_avg_h, c_avg_a = model.corner_table(corner_rows)
    weight = float(os.environ.get("DIAZBETS_MARKET_WEIGHT", "0.5"))
    count = 0
    for f in conn.execute("SELECT * FROM fixtures WHERE day = ? AND status = 'NS'", (day,)).fetchall():
        odds = load_odds(conn, f["id"])
        xg = None
        extra = []
        if avg_h and model.enough_data(teams, f["home_id"], f["away_id"]):
            adj = None
            if adjust:
                rh, ra = rest_days(conn, f["home_id"], f["kickoff"]), rest_days(conn, f["away_id"], f["kickoff"])
                mc = missing_counts(conn, f["id"])
                adj = {"rest_h": rh, "rest_a": ra, "inj_h": mc.get(f["home_id"], 0), "inj_a": mc.get(f["away_id"], 0)}
                if (rh is not None and rh <= 3) or (ra is not None and ra <= 3):
                    extra.append(f"Pihenőnapok: {f['home']} {rh if rh is not None else '?'}, {f['away']} {ra if ra is not None else '?'}.")
                if adj["inj_h"] > 1 or adj["inj_a"] > 1:
                    extra.append(f"Hiányzók: {f['home']} {adj['inj_h']}, {f['away']} {adj['inj_a']}.")
            xg = model.expected_goals(teams, avg_h, avg_a, f["home_id"], f["away_id"], adj)
        if xg:
            w = model.adaptive_weight(weight, model.min_games(teams, f["home_id"], f["away_id"]))
            probs = model.blend(model.probabilities(*xg), odds, w)
            api = conn.execute("SELECT * FROM api_predictions WHERE fixture_id=?", (f["id"],)).fetchone()
            wa = 0.0
            if api and api_base > 0:
                games = model.min_games(teams, f["home_id"], f["away_id"])
                wa = model.api_weight(api_base, games)
                probs = model.blend_api(probs, api, wa)
            new_tips = model.build_tips(probs, odds, *xg)
            conn.execute("INSERT OR REPLACE INTO predictions(fixture_id, p_home, p_draw, p_away, lam_h, lam_a) VALUES (?,?,?,?,?,?)",
                         (f["id"], probs["Home"], probs["Draw"], probs["Away"], xg[0], xg[1]))
            if w and model.market_probs(odds):
                for t in new_tips:
                    t["note"] += f" A tipp a saját modell és a szorzók átlaga (piac súlya {w:.0%}, kevés meccsnél nagyobb)."
            if wa:
                for t in new_tips:
                    if t["market"] == "1X2":
                        t["note"] += f" Az 1X2 esélyben az API-Football előrejelzése is benne van ({wa:.0%} súllyal)."
        else:
            # Kevés saját adat: szorzó alapú tartalék tipp (ha van szorzó)
            new_tips = model.tips_from_odds(odds)
            mk = model.market_probs(odds)
            if all(k in mk for k in ("Home", "Draw", "Away")):
                conn.execute("INSERT OR REPLACE INTO predictions(fixture_id, p_home, p_draw, p_away, lam_h, lam_a) VALUES (?,?,?,?,NULL,NULL)",
                             (f["id"], mk["Home"], mk["Draw"], mk["Away"]))
        cx = model.expected_corners(c_teams, c_avg_h, c_avg_a, f["home_id"], f["away_id"]) if c_avg_h else None
        if cx:
            wc = model.adaptive_weight(weight, model.min_corner_games(c_teams, f["home_id"], f["away_id"]))
            tip = model.corner_tip(cx[0] + cx[1], odds, wc)
            if wc and odds:
                tip["note"] += f" Piaci súly: {wc:.0%}."
            new_tips.append(tip)
        for t in new_tips:
            raw = t["prob"]
            cp = model.apply_calibration(cal, t["market"], raw)
            if abs(cp - raw) >= 0.005:
                t["prob"] = cp
                t["value"] = (cp * t["odds"] - 1) if t["odds"] else None
                t["note"] += f" Kalibrálva a korábbi tippek alapján: {raw:.0%} → {cp:.0%}."
            if extra and t["market"] != "CORNERS":
                t["note"] += " " + " ".join(extra)
            conn.execute(
                """INSERT INTO tips(fixture_id, market, pick, prob, odds, value, note, raw_prob)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(fixture_id, market) DO UPDATE SET pick=excluded.pick, prob=excluded.prob,
                       odds=excluded.odds, value=excluded.value, note=excluded.note, raw_prob=excluded.raw_prob
                   WHERE tips.result IS NULL""",
                (f["id"], t["market"], t["pick"], t["prob"], t["odds"], t["value"], t["note"], raw),
            )
            count += 1
    conn.commit()
    log(f"{count} tipp generálva ({len(teams)} csapat adata alapján" + (", kalibráció aktív" if cal else "") + ")")


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


def judge_corners(pick, total):
    side, line = pick.split()
    line = float(line)
    if total == line:
        return "void"  # kerek vonalnál a pontos találat visszajáró tét
    return "win" if (side == "Over") == (total > line) else "loss"


def settle_local(conn):
    n = 0
    rows = conn.execute(
        """SELECT t.fixture_id, t.market, t.pick, f.home_goals, f.away_goals, f.status
           FROM tips t JOIN fixtures f ON f.id = t.fixture_id
           WHERE t.result IS NULL AND f.status IN ('FT','AET','PEN')
             AND f.home_goals IS NOT NULL AND f.away_goals IS NOT NULL""").fetchall()
    for r in rows:
        if r["market"] == "CORNERS":
            tot = conn.execute("""SELECT SUM(corners) s, COUNT(corners) n FROM fixture_stats
                                  WHERE fixture_id = ? AND team_id != 0""", (r["fixture_id"],)).fetchone()
            if not tot or tot["n"] < 2:
                continue  # a szögletstatisztika még nem érkezett meg, később számoljuk el
            res = judge_corners(r["pick"], tot["s"]) if r["status"] == "FT" else "void"
            conn.execute("UPDATE tips SET result=? WHERE fixture_id=? AND market=?", (res, r["fixture_id"], r["market"]))
            n += 1
            continue
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
        if PRO:
            collect_stats(conn, client, limit=150)
    except (BudgetExceeded, ApiError) as e:
        log(f"Elszámolás részleges: {e}")
    finally:
        log(f"{settle_local(conn)} tipp elszámolva")
        conn.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "collect":
        collect(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "odds":
        refresh_odds(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "settle":
        settle()
    elif cmd == "stats":
        stats_cmd(int(sys.argv[2]) if len(sys.argv) > 2 else 500)
    elif cmd == "backfill":
        backfill(int(sys.argv[2]) if len(sys.argv) > 2 else 45)
    else:
        print(__doc__)
        sys.exit(1)
