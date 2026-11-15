import json
import os
import time
from datetime import datetime
from functools import wraps
from zoneinfo import ZoneInfo

from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from apifootball import DAILY_BUDGET
import live
import model
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

MARKET_LABELS = live.MARKET_HU
PICK_LABELS = live.PICK_HU
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


@app.template_filter("pick_hu")
def pick_hu(pick):
    return live.pick_hu(pick)


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


HEAT_STOPS = ["#0d366b", "#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4"]  # egyszínű (kék) szekvenciális skála


def _hex_rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def heat_color(t):
    """t: 0..1 -> (háttér, szövegszín). Világos cellán sötét, sötét cellán világos szöveg."""
    t = max(0.0, min(1.0, t))
    pos = t * (len(HEAT_STOPS) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(HEAT_STOPS) - 1)
    f = pos - lo
    c1, c2 = _hex_rgb(HEAT_STOPS[lo]), _hex_rgb(HEAT_STOPS[hi])
    rgb = tuple(round(c1[k] + (c2[k] - c1[k]) * f) for k in range(3))
    return "#%02x%02x%02x" % rgb, ("#0b0b0b" if t >= 0.72 else "#ffffff")


def build_heatmap(lam_h, lam_a, n=6):
    mat = model.score_matrix(lam_h, lam_a, n)
    pmax = max(max(r) for r in mat)
    top = model.top_scores(mat, 3)
    top_cells = {(c["h"], c["a"]) for c in top[:1]}
    rows = []
    for i in range(n):
        row = []
        for j in range(n):
            bg, fg = heat_color(mat[i][j] / pmax)
            p = mat[i][j]
            row.append({"h": i, "a": j, "p": p, "txt": "<1" if p < 0.005 else f"{p * 100:.0f}",
                        "bg": bg, "fg": fg, "top": (i, j) in top_cells})
        rows.append(row)
    return {"rows": rows, "top": top, "coverage": sum(sum(r) for r in mat)}


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
    preds = {r["fixture_id"]: r for r in conn.execute(
        "SELECT p.* FROM predictions p JOIN fixtures f ON f.id = p.fixture_id WHERE f.day = ?", (day,))}
    used = conn.execute("SELECT count FROM api_usage ORDER BY day DESC LIMIT 1").fetchone()
    moves = odds_moves(conn, [f["id"] for f in fixtures])
    api_preds = {r["fixture_id"]: r for r in conn.execute(
        "SELECT a.* FROM api_predictions a JOIN fixtures f ON f.id = a.fixture_id WHERE f.day = ?", (day,))}
    conn.close()
    diverge = {}
    for fid, pr in preds.items():
        lam = (pr["lam_h"], pr["lam_a"]) if pr["lam_h"] is not None else None
        o = odds.get(fid, {})
        dv = model.divergence(model.opinions(lam, api_preds.get(fid), {("1X2", k): v for k, v in o.items()}))
        if dv:
            diverge[fid] = dv
    for fid in api_preds:
        if fid not in diverge and fid not in preds:
            dv = model.divergence(model.opinions(None, api_preds[fid], {("1X2", k): v for k, v in odds.get(fid, {}).items()}))
            if dv:
                diverge[fid] = dv
    # a tippelt kimenet szorzómozgása, ha jelentős (>=4%)
    tip_moves = {}
    for fid, ts in tips.items():
        for mk, t in ts.items():
            mv = moves.get(fid, {}).get((mk, t["pick"]))
            if mv and abs(mv["pct"]) >= 4:
                tip_moves[(fid, mk)] = mv
    groups = {}
    for f in fixtures:
        groups.setdefault((f["league_id"], f["league_name"], f["country"]), []).append(f)
    return render_template("index.html", day=day, groups=groups, tips=tips, odds=odds, preds=preds, tip_moves=tip_moves, diverge=diverge,
                           prev_day=_shift(day, -1), next_day=_shift(day, 1), api_used=used["count"] if used else 0, api_budget=DAILY_BUDGET)


def odds_moves(conn, fixture_ids):
    """{fixture_id: {(market, selection): {'open': első ár, 'now': aktuális ár}}} - csak ahol volt változás."""
    out = {}
    if not fixture_ids:
        return out
    q = ",".join("?" * len(fixture_ids))
    rows = conn.execute(f"""SELECT fixture_id, market, selection, price FROM odds_history
                            WHERE fixture_id IN ({q}) ORDER BY ts""", list(fixture_ids)).fetchall()
    first, last = {}, {}
    for r in rows:
        k = (r["fixture_id"], r["market"], r["selection"])
        first.setdefault(k, r["price"])
        last[k] = r["price"]
    for k, o in first.items():
        n = last[k]
        if abs(n - o) > 1e-9:
            out.setdefault(k[0], {})[(k[1], k[2])] = {"open": o, "now": n, "pct": (n / o - 1) * 100}
    return out


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
    moves = odds_moves(conn, [fid]).get(fid, {})
    injuries = conn.execute("SELECT * FROM injuries WHERE fixture_id = ? ORDER BY team, player", (fid,)).fetchall()
    h = conn.execute("SELECT json FROM h2h WHERE fixture_id = ?", (fid,)).fetchone()
    h2h = json.loads(h["json"]) if h else None
    pred = conn.execute("SELECT * FROM predictions WHERE fixture_id = ?", (fid,)).fetchone()
    heat = build_heatmap(pred["lam_h"], pred["lam_a"]) if pred and pred["lam_h"] is not None else None
    teams, _, _ = model.load_results_table(conn, f["day"])
    form = {tid: teams[tid] for tid in (f["home_id"], f["away_id"]) if tid in teams}
    api = conn.execute("SELECT * FROM api_predictions WHERE fixture_id = ?", (fid,)).fetchone()
    st = conn.execute("SELECT json FROM standings WHERE league_id = ? AND season = ?", (f["league_id"], f["season"])).fetchone()
    conn.close()
    lam = (pred["lam_h"], pred["lam_a"]) if pred and pred["lam_h"] is not None else None
    ops = model.opinions(lam, api, {("1X2", s["selection"]): s["price"] for s in odds.get("1X2", [])})
    diverge = model.divergence(ops)
    table, rank = None, {}
    if st:
        rows = json.loads(st["json"])
        rank = {r["team_id"]: r for r in rows if r["team_id"] in (f["home_id"], f["away_id"])}
        groups = {r["group"] for r in rank.values()}
        table = [r for r in rows if not groups or r["group"] in groups] if groups else rows
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
    return render_template("match.html", f=f, tips=tips, odds=odds, moves=moves, injuries=injuries, pred=pred, heat=heat, ops=ops, diverge=diverge, api=api, table=table, rank=rank,
                           h2h=h2h, h2h_summary=h2h_summary, form=form)


@app.route("/api/live")
@login_required
def api_live():
    conn = connect()
    try:
        return jsonify(live.refresh_live(conn))
    finally:
        conn.close()


@app.route("/api/match/<int:fid>/live")
@login_required
def api_match_live(fid):
    conn = connect()
    try:
        data = live.match_payload(conn, fid)
    finally:
        conn.close()
    if data is None:
        abort(404)
    return jsonify(data)


@app.route("/history")
@login_required
def history():
    conn = connect()
    rows = conn.execute(
        """SELECT t.*, f.home, f.away, f.day, f.home_goals, f.away_goals, f.league_name
           FROM tips t JOIN fixtures f ON f.id = t.fixture_id
           WHERE t.result IS NOT NULL ORDER BY f.day DESC, f.kickoff DESC LIMIT 5000""").fetchall()
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
    # Liga szerinti bontás: liga összesen és piaconként
    lg = {}
    for r in rows:
        if r["result"] not in ("win", "loss"):
            continue
        name = r["league_name"] or "?"
        for key in ((name, None), (name, r["market"])):
            s = lg.setdefault(key, {"n": 0, "win": 0, "profit": 0.0})
            s["n"] += 1
            s["win"] += r["result"] == "win"
            if r["odds"]:
                s["profit"] += (r["odds"] - 1) if r["result"] == "win" else -1
    leagues = []
    for name in sorted({k[0] for k in lg}, key=lambda n: -lg[(n, None)]["n"]):
        tot = lg[(name, None)]
        leagues.append({"name": name, **tot, "markets": [
            {"market": m, **lg[(name, m)]} for m in ("1X2", "BTTS", "OU2.5", "CORNERS") if (name, m) in lg]})
    # Kalibráció: a megadott esély és a tényleges találati arány összevetése sávonként
    bands = [(0.0, 0.5), (0.5, 0.55), (0.55, 0.6), (0.6, 0.65), (0.65, 0.7), (0.7, 1.01)]
    calib = []
    for lo, hi in bands:
        sel = [r for r in rows if r["result"] in ("win", "loss") and lo <= r["prob"] < hi]
        if sel:
            calib.append({"label": f"{int(lo*100)}–{min(int(hi*100), 100)}%", "n": len(sel),
                          "predicted": sum(r["prob"] for r in sel) / len(sel),
                          "actual": sum(r["result"] == "win" for r in sel) / len(sel)})
    # Diagramok: időrendben (a lekérdezés újabbtól régebbi felé rendez)
    chrono = [r for r in reversed(rows) if r["result"] in ("win", "loss")]
    profit, hit, wins, pexp, cum = [], [], 0, 0.0, 0.0
    for i, r in enumerate(chrono, 1):
        title = f'{r["day"]} · {r["home"]} – {r["away"]} ({MARKET_LABELS.get(r["market"], r["market"])}: {live.pick_hu(r["pick"])})'
        wins += r["result"] == "win"
        pexp += r["prob"]
        hit.append({"x": i, "label": title, "actual": wins / i * 100, "expected": pexp / i * 100})
        if r["odds"]:
            cum += (r["odds"] - 1) if r["result"] == "win" else -1
            profit.append({"x": len(profit) + 1, "label": title, "y": round(cum, 2)})
    chart_data = {"profit": profit, "hit": hit,
                  "calib": [{"label": c["label"], "n": c["n"], "predicted": c["predicted"] * 100,
                             "actual": c["actual"] * 100} for c in calib]}
    return render_template("history.html", rows=rows[:300], stats=stats, calib=calib, chart_data=chart_data, leagues=leagues)


if __name__ == "__main__":
    app.run(debug=False, port=int(os.environ.get("PORT", "8085")))
