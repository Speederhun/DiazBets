import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DIAZBETS_DB"] = os.path.join(tempfile.mkdtemp(), "t.sqlite")
os.environ["DIAZBETS_SECURE_COOKIE"] = "0"

import collect
import model


def test_probabilities_sum_and_symmetry():
    p = model.probabilities(1.5, 1.5)
    assert abs(p["Home"] + p["Draw"] + p["Away"] - 1) < 1e-9
    assert abs(p["Home"] - p["Away"]) < 1e-9
    assert 0 < p["BTTS"] < 1 and 0 < p["Over25"] < 1


def test_stronger_home_team_favoured():
    p = model.probabilities(2.4, 0.7)
    assert p["Home"] > 0.6 and p["Home"] > p["Away"]


def test_judge():
    assert collect.judge("1X2", "Home", 2, 1) == "win"
    assert collect.judge("1X2", "Draw", 2, 1) == "loss"
    assert collect.judge("BTTS", "Yes", 1, 1) == "win"
    assert collect.judge("BTTS", "No", 0, 2) == "win"
    assert collect.judge("OU2.5", "Over 2.5", 2, 1) == "win"
    assert collect.judge("OU2.5", "Under 2.5", 2, 1) == "loss"


def test_build_tips_value():
    probs = model.probabilities(2.0, 1.0)
    tips = model.build_tips(probs, {("1X2", "Home"): 2.0}, 2.0, 1.0)
    t = {x["market"]: x for x in tips}
    assert t["1X2"]["pick"] == "Home"
    assert abs(t["1X2"]["value"] - (t["1X2"]["prob"] * 2.0 - 1)) < 1e-9
    assert t["BTTS"]["value"] is None  # nincs szorzó


def test_demo_and_web_flow():
    import demo_data
    from werkzeug.security import generate_password_hash
    from db import connect
    demo_data.load()
    conn = connect()
    conn.execute("INSERT INTO users(username, pw_hash) VALUES ('teszt', ?)", (generate_password_hash("jelszo1234"),))
    conn.commit()
    # a tegnapi meccs tippjei el lettek számolva (Demo FC nyert 2-1 idegenben -> 1X2 Away nyert, BTTS Yes nyert)
    res = {r["market"]: r["result"] for r in conn.execute("SELECT * FROM tips WHERE fixture_id = 9003")}
    assert res == {"1X2": "win", "BTTS": "win"}
    n_today = conn.execute("SELECT COUNT(*) c FROM tips WHERE fixture_id IN (9001, 9002)").fetchone()["c"]
    assert n_today == 6
    conn.close()

    from app import app
    c = app.test_client()
    assert c.get("/").status_code == 302  # belépés nélkül átirányít
    assert c.get("/match/9001").status_code == 302
    r = c.post("/login", data={"username": "teszt", "password": "rossz"})
    assert r.status_code == 200 and "Hibás" in r.get_data(as_text=True)
    r = c.post("/login", data={"username": "teszt", "password": "jelszo1234"})
    assert r.status_code == 302
    for url in ("/", "/match/9001", "/match/9002", "/history"):
        r = c.get(url)
        assert r.status_code == 200, url
    assert "Demo FC" in c.get("/").get_data(as_text=True)
    assert "Kovács Péter" in c.get("/match/9001").get_data(as_text=True)
    assert c.get("/match/1").status_code == 404
    assert c.get("/?d=nem-datum").status_code == 400
    assert c.post("/login?next=//evil.example", data={"username": "teszt", "password": "jelszo1234"}).headers["Location"] == "/"


def test_tips_from_odds_fallback():
    odds = {("1X2", "Home"): 1.5, ("1X2", "Draw"): 4.0, ("1X2", "Away"): 7.0,
            ("BTTS", "Yes"): 1.9, ("BTTS", "No"): 1.9}
    tips = {x["market"]: x for x in model.tips_from_odds(odds)}
    assert tips["1X2"]["pick"] == "Home" and 0.5 < tips["1X2"]["prob"] < 0.7
    assert tips["1X2"]["value"] is None
    assert "OU2.5" not in tips  # nincs hozzá szorzó
    assert model.tips_from_odds({}) == []


def test_results_table_and_fallback_threshold():
    rows = [{"home_id": 1, "home": "A", "away_id": 2, "away": "B", "hg": 2, "ag": 0}] * 3
    teams, ah, aa = model.team_table_from_results(rows)
    assert teams[1]["form"] == "WWW" and teams[2]["form"] == "LLL"
    assert ah == 2 and aa == 0
    assert model.enough_data(teams, 1, 2)
    assert not model.enough_data(teams, 1, 99)


class FakeClient:
    """Az ingyenes csomag viselkedését utánozza: dátum szerinti meccslista megy, a többi 'plan' hibát ad."""
    def __init__(self, conn, api_key=None):
        self.calls = []

    def get(self, path, **params):
        self.calls.append(path)
        if path == "fixtures":
            d = params["date"]
            item = lambda fid, h, a, st, hg, ag: {
                "fixture": {"id": fid, "date": f"{d}T15:00:00+01:00", "status": {"short": st}},
                "league": {"id": 5, "name": "UEFA Nations League", "country": "World", "season": 2026},
                "teams": {"home": {"id": h, "name": f"T{h}", "logo": None}, "away": {"id": a, "name": f"T{a}", "logo": None}},
                "goals": {"home": hg, "away": ag}}
            if d == collect.today():
                return {"response": [item(777, 1, 2, "NS", None, None),
                                     {**item(778, 3, 4, "NS", None, None), "league": {"id": 999, "name": "Ismeretlen", "country": "X", "season": 2026}}]}
            return {"response": [item(int(d[-2:]) * 10 + 1, 1, 2, "FT", 2, 1),
                                 item(int(d[-2:]) * 10 + 2, 2, 1, "FT", 0, 0)]}
        raise collect.ApiError(f"{path}: {{'plan': 'Free plans do not have access to this season, try from 2022 to 2024.'}}")


def test_collect_survives_plan_errors_and_builds_tips_from_results(monkeypatch):
    from db import connect, init_db
    init_db()
    monkeypatch.setattr(collect, "Client", FakeClient)
    collect.backfill(4)
    conn = connect()
    assert conn.execute("SELECT COUNT(*) FROM fixtures WHERE status='FT' AND home IN ('T1','T2')").fetchone()[0] == 8
    conn.close()
    collect.collect()
    conn = connect()
    tips = conn.execute("SELECT market FROM tips WHERE fixture_id = 777").fetchall()
    assert len(tips) == 3  # 4 nap x 2 meccs -> mindkét csapatnak van elég saját adata
    assert conn.execute("SELECT COUNT(*) FROM fixtures WHERE id = 778").fetchone()[0] == 0  # nem figyelt liga
    conn.close()


def test_corner_model_and_tip():
    rows = [{"home_id": 1, "away_id": 2, "hc": 7, "ac": 3}] * 4 + [{"home_id": 2, "away_id": 1, "hc": 5, "ac": 4}] * 4
    teams, ah, aa = model.corner_table(rows)
    lam = model.expected_corners(teams, ah, aa, 1, 2)
    assert lam and 3 < sum(lam) < 15
    assert model.expected_corners(teams, ah, aa, 1, 99) is None
    odds = {("CORNERS", "Over 9.5"): 1.9, ("CORNERS", "Under 9.5"): 1.9,
            ("CORNERS", "Over 11.5"): 3.0, ("CORNERS", "Under 11.5"): 1.3}
    tip = model.corner_tip(10.2, odds)
    assert tip["market"] == "CORNERS" and tip["pick"].endswith("9.5") and tip["odds"] == 1.9
    assert 0.5 <= tip["prob"] <= 1
    assert model.corner_tip(10.2, {})["odds"] is None  # szorzó nélkül is ad tippet


def test_judge_corners():
    assert collect.judge_corners("Over 9.5", 12) == "win"
    assert collect.judge_corners("Over 9.5", 9) == "loss"
    assert collect.judge_corners("Under 9.5", 9) == "win"
    assert collect.judge_corners("Under 9.0", 9) == "void"
    assert collect.judge_corners("Over 9.0", 10) == "win"


def test_blend_moves_toward_market():
    own = model.probabilities(2.0, 0.8)
    odds = {("1X2", "Home"): 3.0, ("1X2", "Draw"): 3.3, ("1X2", "Away"): 2.4}
    mixed = model.blend(own, odds, 0.5)
    mk = model.market_probs(odds)
    assert abs(mixed["Home"] + mixed["Draw"] + mixed["Away"] - 1) < 1e-9
    assert min(own["Home"], mk["Home"]) < mixed["Home"] < max(own["Home"], mk["Home"])
    assert model.blend(own, odds, 0) == own
    assert model.blend(own, {}, 0.5) == own  # szorzó nélkül nem változik


def test_settle_corners_and_stats_storage():
    from db import connect
    conn = connect()
    conn.execute("""INSERT OR REPLACE INTO fixtures(id, day, kickoff, league_id, season, home_id, home, away_id, away,
                    status, home_goals, away_goals) VALUES (5001,'2026-01-01','2026-01-01T15:00:00+01:00',39,2026,1,'A',2,'B','FT',1,0)""")
    conn.execute("INSERT OR REPLACE INTO tips(fixture_id, market, pick, prob, odds, value, note, result) VALUES (5001,'CORNERS','Over 9.5',0.6,1.9,0.14,'',NULL)")
    conn.commit()
    assert collect.settle_local(conn) >= 0
    assert conn.execute("SELECT result FROM tips WHERE fixture_id=5001").fetchone()["result"] is None  # még nincs statisztika
    collect.store_stats(conn, 5001, [
        {"team": {"id": 1}, "statistics": [{"type": "Corner Kicks", "value": 7}]},
        {"team": {"id": 2}, "statistics": [{"type": "Corner Kicks", "value": 4}]}])
    collect.settle_local(conn)
    assert conn.execute("SELECT result FROM tips WHERE fixture_id=5001").fetchone()["result"] == "win"
    conn.close()


def test_fetch_paged_without_page_param():
    class C:
        def __init__(self): self.calls = []
        def get(self, path, **p):
            self.calls.append(p)
            if "page" in p:
                raise AssertionError("page nem küldhető")
            return {"response": [1, 2], "paging": {"current": 1, "total": 3}}
    c = C()
    assert collect.fetch_paged(c, "injuries", paged=False, date="2026-01-01") == [1, 2]
    assert len(c.calls) == 1


def test_reserve_never_exceeds_budget():
    assert collect.RESERVE <= collect.DAILY_BUDGET // 4


def test_adaptive_weight():
    assert model.adaptive_weight(0.5, 15) == 0.5
    assert model.adaptive_weight(0.5, 40) == 0.5
    assert model.adaptive_weight(0.5, 3) > 0.85
    assert model.adaptive_weight(0.5, 0) == 1.0


def test_corner_tip_blend_and_integer_line():
    odds = {("CORNERS", "Over 9.0"): 1.9, ("CORNERS", "Under 9.0"): 1.9}
    solo = model.corner_tip(7.0, odds, 0)
    mixed = model.corner_tip(7.0, odds, 0.8)
    assert solo["pick"].startswith("Under 9.0") and mixed["pick"].startswith("Under 9.0")
    assert mixed["prob"] < solo["prob"]          # a piac (≈50%) lehúzza a túlzott magabiztosságot
    assert 0.5 <= mixed["prob"] <= 1


# ---------------------------------------------------------------- élő

import live


def test_tip_states():
    ts = live.tip_state
    assert ts("BTTS", "Yes", 1, 1, None, "2H") == "won"
    assert ts("BTTS", "Yes", 1, 0, None, "2H") == "on"
    assert ts("BTTS", "Yes", 1, 0, None, "FT") == "lost"
    assert ts("BTTS", "No", 1, 1, None, "1H") == "lost"
    assert ts("OU2.5", "Over 2.5", 2, 1, None, "2H") == "won"
    assert ts("OU2.5", "Under 2.5", 2, 1, None, "2H") == "lost"
    assert ts("OU2.5", "Under 2.5", 1, 1, None, "FT") == "won"
    assert ts("1X2", "Home", 1, 0, None, "2H") == "on"
    assert ts("1X2", "Home", 1, 0, None, "FT") == "won"
    assert ts("1X2", "Draw", 1, 0, None, "FT") == "lost"
    assert ts("CORNERS", "Over 9.5", 1, 0, 10, "2H") == "won"
    assert ts("CORNERS", "Under 9.5", 1, 0, 10, "2H") == "lost"
    assert ts("CORNERS", "Under 9.0", 1, 0, 9, "FT") == "void"
    assert ts("CORNERS", "Over 9.5", 1, 0, None, "2H") == "open"
    assert ts("1X2", "Home", None, None, None, "NS") is None


def _live_item(fid, status="2H", elapsed=63, hg=1, ag=1):
    return {
        "fixture": {"id": fid, "status": {"short": status, "elapsed": elapsed, "extra": None}},
        "goals": {"home": hg, "away": ag},
        "events": [
            {"time": {"elapsed": 12, "extra": None}, "team": {"id": 4}, "player": {"name": "Gólszerző"}, "assist": {"name": "Passzoló"}, "type": "Goal", "detail": "Normal Goal"},
            {"time": {"elapsed": 30, "extra": None}, "team": {"id": 1}, "player": {"name": "Sárga"}, "assist": {"name": None}, "type": "Card", "detail": "Yellow Card"},
            {"time": {"elapsed": 55, "extra": None}, "team": {"id": 1}, "player": {"name": "Egyenlítő"}, "assist": {"name": None}, "type": "Goal", "detail": "Penalty"},
        ],
        "statistics": [
            {"team": {"id": 1}, "statistics": [{"type": "Ball Possession", "value": "58%"}, {"type": "Corner Kicks", "value": 6}, {"type": "Total Shots", "value": 12}]},
            {"team": {"id": 4}, "statistics": [{"type": "Ball Possession", "value": "42%"}, {"type": "Corner Kicks", "value": 5}, {"type": "Total Shots", "value": None}]},
        ],
    }


class LiveFake:
    state = {"status": "2H", "hg": 1, "ag": 1}
    calls = []

    def __init__(self, conn, api_key=None):
        pass

    def get(self, path, **p):
        LiveFake.calls.append((path, p))
        s = LiveFake.state
        item = _live_item(9001, s["status"], 63 if s["status"] == "2H" else 90, s["hg"], s["ag"])
        if "live" in p:
            return {"response": [item] if s["status"] != "FT" else []}
        return {"response": [item]}


def test_live_flow(monkeypatch):
    import time as _t
    from datetime import datetime, timedelta, timezone
    from db import connect
    monkeypatch.setattr(live, "PRO", True)
    monkeypatch.setattr(live, "Client", LiveFake)
    LiveFake.calls.clear()
    LiveFake.state = {"status": "2H", "hg": 1, "ag": 1}
    conn = connect()
    ko = (datetime.now(timezone.utc) - timedelta(minutes=70)).isoformat()
    conn.execute("UPDATE fixtures SET kickoff=?, status='NS', home_goals=NULL, away_goals=NULL WHERE id=9001", (ko,))
    conn.execute("DELETE FROM cache")
    conn.commit()

    d = live.refresh_live(conn)
    assert d["enabled"] and d["fixtures"]["9001"]["live"] and d["fixtures"]["9001"]["hg"] == 1
    assert d["fixtures"]["9001"]["elapsed"] == 63
    n_calls = len(LiveFake.calls)
    live.refresh_live(conn)
    assert len(LiveFake.calls) == n_calls  # a gyorsítótár miatt nincs új API-hívás

    p = live.match_payload(conn, 9001)
    assert p["live"] and p["hg"] == 1 and p["ag"] == 1
    assert [e["kind"] for e in p["events"]] == ["goal", "yellow", "goal"]
    assert p["events"][0]["side"] == "away" and p["events"][1]["side"] == "home"
    labels = {s["key"]: s for s in p["stats"]}
    assert labels["Ball Possession"]["home"] == 58 and labels["Ball Possession"]["pct"]
    assert "Total Shots" in labels and labels["Total Shots"]["away"] == 0
    assert p["corners"] == 11
    assert p["tips"].get("BTTS") in ("won", "lost")  # van tipp a meccsre, az állás kiszámolva

    # befejeződik a meccs: azonnali elszámolás a szögletstatisztikával együtt
    LiveFake.state = {"status": "FT", "hg": 1, "ag": 1}
    conn.execute("DELETE FROM cache")
    conn.commit()
    d = live.refresh_live(conn)
    assert d["fixtures"]["9001"]["status"] == "FT"
    done = {r["market"]: r["result"] for r in conn.execute("SELECT * FROM tips WHERE fixture_id=9001")}
    assert all(v is not None for v in done.values()), done
    conn.close()


def test_live_disabled_on_free_plan(monkeypatch):
    from db import connect
    monkeypatch.setattr(live, "PRO", False)
    conn = connect()
    assert live.refresh_live(conn) == {"enabled": False, "fixtures": {}}
    conn.close()


def test_live_api_requires_login_and_works():
    from app import app
    c = app.test_client()
    assert c.get("/api/live").status_code == 302
    assert c.get("/api/match/9001/live").status_code == 302
    c.post("/login", data={"username": "teszt", "password": "jelszo1234"})
    assert c.get("/api/live").status_code == 200
    assert c.get("/api/match/9001/live").status_code == 200
    assert c.get("/api/match/1/live").status_code == 404


# ---------------------------------------------------------------- önjavító modell, fáradtság, ligák, szorzómozgás

def test_calibration_corrects_overconfidence_and_needs_data():
    over = [("1X2", 0.65, 1)] * 30 + [("1X2", 0.65, 0)] * 30 + [("BTTS", 0.65, 1)] * 5   # 65%-ot mondott, 50%-ban jött be
    cal = model.fit_calibration(over)
    adj = model.apply_calibration(cal, "1X2", 0.65)
    assert 0.5 < adj < 0.6                       # lejjebb húzza, de a shrinkage miatt nem teljesen 50%-ra
    assert model.apply_calibration(model.fit_calibration(over[:20]), "1X2", 0.65) == 0.65  # kevés adat: nincs igazítás
    ok = [("1X2", 0.6, 1)] * 36 + [("1X2", 0.6, 0)] * 24                                 # 60%, pontos
    assert abs(model.apply_calibration(model.fit_calibration(ok), "1X2", 0.6) - 0.6) < 0.01
    assert 0.02 <= model.apply_calibration(cal, "1X2", 0.99) <= 0.98


def test_fatigue_injury_and_venue_adjustments():
    assert model.fatigue_factor(2) < model.fatigue_factor(3) < model.fatigue_factor(4) < model.fatigue_factor(7) == 1.0
    assert model.fatigue_factor(None) == 1.0
    own, opp = model.injury_factors(20)
    assert own >= 0.94 and opp <= 1.04          # plafon
    assert model.injury_factors(0) == (1.0, 1.0)
    rows = [{"home_id": 1, "home": "A", "away_id": 2, "away": "B", "hg": 2, "ag": 1, "day": "2026-01-01"},
            {"home_id": 2, "home": "B", "away_id": 1, "away": "A", "hg": 0, "ag": 2, "day": "2026-01-08"},
            {"home_id": 1, "home": "A", "away_id": 3, "away": "C", "hg": 1, "ag": 1, "day": "2026-01-15"},
            {"home_id": 3, "home": "C", "away_id": 2, "away": "B", "hg": 1, "ag": 0, "day": "2026-01-22"}]
    teams, ah, aa = model.team_table_from_results(rows, now_day="2026-02-01")
    base = model.expected_goals(teams, ah, aa, 1, 2)
    tired = model.expected_goals(teams, ah, aa, 1, 2, {"rest_h": 2, "rest_a": 7, "inj_h": 5, "inj_a": 0})
    assert tired[0] < base[0] and tired[1] > base[1]
    # a friss meccs többet számít: a w_ összegek kisebbek az egyszerű összegeknél
    assert teams[1]["w_hp"] < teams[1]["hp"]


def test_odds_history_records_only_changes():
    from db import connect, init_db
    init_db()
    conn = connect()
    conn.execute("""INSERT OR REPLACE INTO fixtures(id, day, kickoff, league_id, season, home_id, home, away_id, away, status)
                    VALUES (6001,'2026-11-20','2026-11-20T20:00:00+01:00',39,2026,1,'A',2,'B','NS')""")
    conn.commit()

    def item(price):
        return [{"fixture": {"id": 6001}, "bookmakers": [{"bets": [{"id": 1, "values": [{"value": "Home", "odd": str(price)}]}]}]}]
    collect.store_odds(conn, item(2.0))
    collect.store_odds(conn, item(2.0))   # változatlan: nem kerül be újra
    collect.store_odds(conn, item(1.8))
    prices = [r["price"] for r in conn.execute("SELECT price FROM odds_history WHERE fixture_id=6001 ORDER BY ts, rowid")]
    assert prices == [2.0, 1.8]
    from app import odds_moves
    mv = odds_moves(conn, [6001])[6001][("1X2", "Home")]
    assert mv["open"] == 2.0 and mv["now"] == 1.8 and mv["pct"] < -9
    conn.close()


def test_generate_tips_stores_raw_prob_and_pages_show_new_sections():
    from db import connect
    conn = connect()
    r = conn.execute("SELECT raw_prob, prob FROM tips WHERE fixture_id IN (9001, 9002) AND raw_prob IS NOT NULL").fetchall()
    assert len(r) >= 1
    # ligák szerinti tábla és szorzómozgás-jelzés megjelenik
    conn.execute("INSERT INTO odds_history(fixture_id, market, selection, ts, price) VALUES (9001,'1X2','Home',1,2.4)")
    conn.execute("INSERT INTO odds_history(fixture_id, market, selection, ts, price) VALUES (9001,'1X2','Home',2,1.9)")
    conn.commit()
    conn.close()
    from app import app
    c = app.test_client()
    c.post("/login", data={"username": "teszt", "password": "jelszo1234"})
    assert "Ligák szerint" in c.get("/history").get_data(as_text=True)
    page = c.get("/match/9001").get_data(as_text=True)
    assert "2.40 → 1.90" in page


def test_opinions_divergence():
    ops = model.opinions((2.2, 0.8), {"p_home": 0.35, "p_draw": 0.30, "p_away": 0.35},
                         {("1X2", "Home"): 1.6, ("1X2", "Draw"): 4.0, ("1X2", "Away"): 5.5})
    assert [o["key"] for o in ops] == ["model", "api", "market"]
    dv = model.divergence(ops)
    assert dv and dv["outcome"] in ("Hazai", "Vendég") and dv["spread"] >= 0.15
    agree = model.opinions(None, {"p_home": 0.5, "p_draw": 0.25, "p_away": 0.25},
                           {("1X2", "Home"): 2.0, ("1X2", "Draw"): 4.0, ("1X2", "Away"): 4.0})
    assert model.divergence(agree) is None
    assert model.divergence(ops[:1]) is None  # egy forrás nem hasonlítható össze


def test_api_prediction_and_standings_storage_and_match_page():
    from db import connect
    conn = connect()
    collect.store_api_prediction(conn, 9001, {"predictions": {
        "winner": {"id": 4, "name": "Teszt Rovers"}, "advice": "Double chance Teszt Rovers or draw",
        "under_over": "-3.5", "goals": {"home": "-1.5", "away": "-2.5"},
        "percent": {"home": "10%", "draw": "20%", "away": "70%"}}})
    row = conn.execute("SELECT * FROM api_predictions WHERE fixture_id=9001").fetchone()
    assert abs(row["p_away"] - 0.7) < 1e-9 and row["winner"] == "Teszt Rovers"

    class SC:
        n = 0
        def get(self, path, **p):
            SC.n += 1
            return {"response": [{"league": {"standings": [[
                {"rank": 1, "team": {"id": 1, "name": "Demo FC"}, "points": 30, "goalsDiff": 12, "group": "Liga",
                 "all": {"played": 12, "win": 9, "draw": 3, "lose": 0, "goals": {"for": 25, "against": 13}}, "form": "WWDWW"},
                {"rank": 2, "team": {"id": 4, "name": "Teszt Rovers"}, "points": 20, "goalsDiff": 1, "group": "Liga",
                 "all": {"played": 12, "win": 6, "draw": 2, "lose": 4, "goals": {"for": 15, "against": 14}}, "form": "LWWLD"}]]}}]}
    f = conn.execute("SELECT * FROM fixtures WHERE id=9001").fetchone()
    collect.fetch_standings(conn, SC(), [f], "2026-11-15")
    collect.fetch_standings(conn, SC(), [f], "2026-11-15")   # ugyanazon a napon nem kér újra
    assert SC.n == 1
    conn.close()
    from app import app
    c = app.test_client()
    c.post("/login", data={"username": "teszt", "password": "jelszo1234"})
    page = c.get("/match/9001").get_data(as_text=True)
    assert "Vélemények összevetése" in page and "API-Football előrejelzés" in page
    assert "Tabella" in page and "1. hely" in page and "30 pont" in page
    assert "Eltérő vélemények" in c.get("/?d=" + f["day"]).get_data(as_text=True)
