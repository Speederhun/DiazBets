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
