import json
import os
import time
from datetime import datetime
from functools import wraps
from zoneinfo import ZoneInfo

from flask import Flask, abort, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from db import connect, init_db

TZ = ZoneInfo("Europe/Budapest")

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("DIAZBETS_SECRET") or "dev-only-change-me",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("DIAZBETS_SECURE_COOKIE", "1") == "1",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
)
init_db()

MARKET_LABELS = {"1X2": "Győztes", "BTTS": "Mindkét csapat szerez gólt", "OU2.5": "Gólok (2.5)"}
PICK_LABELS = {
    "Home": "Hazai", "Draw": "Döntetlen", "Away": "Vendég",
    "Yes": "Igen", "No": "Nem", "Over 2.5": "Felett", "Under 2.5": "Alatt",
}
RESULT_LABELS = {"win": "Nyert", "loss": "Vesztett", "void": "Érvénytelen"}


def login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if "uid" not in session:
            return redirect(url_for("login", next=request.path))
        return view(*a, **kw)
    return wrapped


@app.template_filter("hhmm")
def hhmm(iso):
    try:
        return datetime.fromisoformat(iso).astimezone(TZ).strftime("%H:%M")
    except (TypeError, ValueError):
        return "--:--"


@app.template_filter("pct")
def pct(v):
    return f"{v * 100:.0f}%"


@app.context_processor
def helpers():
    return {"MARKET_LABELS": MARKET_LABELS, "PICK_LABELS": PICK_LABELS, "RESULT_LABELS": RESULT_LABELS}


@app.after_request
def security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        conn = connect()
        user = conn.execute("SELECT * FROM users WHERE username = ?",
                            (request.form.get("username", "").strip().lower(),)).fetchone()
        conn.close()
        if user and check_password_hash(user["pw_hash"], request.form.get("password", "")):
            session.clear()
            session.permanent = True
            session["uid"] = user["id"]
            session["name"] = user["username"]
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("index"))
        time.sleep(1)  # lassítja a találgatást
        error = "Hibás felhasználónév vagy jelszó."
    return render_template("login.html", error=error)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    day = request.args.get("d") or datetime.now(TZ).strftime("%Y-%m-%d")
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        abort(400)
    conn = connect()
    fixtures = conn.execute("SELECT * FROM fixtures WHERE day = ? ORDER BY league_id, kickoff", (day,)).fetchall()
    tips = {}
    for t in conn.execute("SELECT t.* FROM tips t JOIN fixtures f ON f.id = t.fixture_id WHERE f.day = ?", (day,)):
        tips.setdefault(t["fixture_id"], {})[t["market"]] = t
    odds = {}
    for o in conn.execute("""SELECT o.* FROM odds o JOIN fixtures f ON f.id = o.fixture_id
                             WHERE f.day = ? AND o.market = '1X2'""", (day,)):
        odds.setdefault(o["fixture_id"], {})[o["selection"]] = o["price"]
    used = conn.execute("SELECT count FROM api_usage ORDER BY day DESC LIMIT 1").fetchone()
    conn.close()
    groups = {}
    for f in fixtures:
        groups.setdefault((f["league_id"], f["league_name"], f["country"]), []).append(f)
    return render_template("index.html", day=day, groups=groups, tips=tips, odds=odds,
                           prev_day=_shift(day, -1), next_day=_shift(day, 1), api_used=used["count"] if used else 0)


def _shift(day, delta):
    from datetime import timedelta
    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=delta)).strftime("%Y-%m-%d")


@app.route("/match/<int:fid>")
@login_required
def match(fid):
    conn = connect()
    f = conn.execute("SELECT * FROM fixtures WHERE id = ?", (fid,)).fetchone()
    if not f:
        abort(404)
    tips = conn.execute("SELECT * FROM tips WHERE fixture_id = ? ORDER BY market", (fid,)).fetchall()
    odds = {}
    for o in conn.execute("SELECT * FROM odds WHERE fixture_id = ? ORDER BY market, selection", (fid,)):
        odds.setdefault(o["market"], []).append(o)
    injuries = conn.execute("SELECT * FROM injuries WHERE fixture_id = ? ORDER BY team, player", (fid,)).fetchall()
    h = conn.execute("SELECT json FROM h2h WHERE fixture_id = ?", (fid,)).fetchone()
    h2h = json.loads(h["json"]) if h else None
    st = conn.execute("SELECT json FROM standings WHERE league_id = ? AND season = ?",
                      (f["league_id"], f["season"])).fetchone()
    form = {}
    if st:
        import model
        teams, _, _ = model.team_table(model.flatten_standings(json.loads(st["json"])))
        for tid in (f["home_id"], f["away_id"]):
            if tid in teams:
                form[tid] = teams[tid]
    conn.close()
    h2h_summary = None
    if h2h:
        hw = aw = dr = 0
        for m in h2h:
            if m["hg"] is None or m["ag"] is None:
                continue
            if m["hg"] == m["ag"]:
                dr += 1
            else:
                winner = m["home"] if m["hg"] > m["ag"] else m["away"]
                if winner == f["home"]:
                    hw += 1
                else:
                    aw += 1
        h2h_summary = {"home": hw, "draw": dr, "away": aw}
    return render_template("match.html", f=f, tips=tips, odds=odds, injuries=injuries,
                           h2h=h2h, h2h_summary=h2h_summary, form=form)


@app.route("/history")
@login_required
def history():
    conn = connect()
    rows = conn.execute(
        """SELECT t.*, f.home, f.away, f.day, f.home_goals, f.away_goals, f.league_name
           FROM tips t JOIN fixtures f ON f.id = t.fixture_id
           WHERE t.result IS NOT NULL ORDER BY f.day DESC, f.kickoff DESC LIMIT 300""").fetchall()
    conn.close()
    stats = {}
    for scope in ("all", "value"):
        stats[scope] = {}
    for r in rows:
        if r["result"] == "void":
            continue
        for scope, ok in (("all", True), ("value", r["value"] is not None and r["value"] > 0)):
            if not ok:
                continue
            s = stats[scope].setdefault(r["market"], {"n": 0, "win": 0, "staked": 0, "profit": 0.0})
            s["n"] += 1
            s["win"] += r["result"] == "win"
            if r["odds"]:
                s["staked"] += 1
                s["profit"] += (r["odds"] - 1) if r["result"] == "win" else -1
    return render_template("history.html", rows=rows, stats=stats)


if __name__ == "__main__":
    app.run(debug=False, port=int(os.environ.get("PORT", "8085")))
