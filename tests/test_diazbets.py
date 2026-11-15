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
