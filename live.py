"""Élő állás, események és statisztika.

Az API-t a szerver kérdezi, nem a böngésző, és minden választ rövid ideig tárol (TTL),
így akárhány felhasználó / frissítés mellett legfeljebb ~1 kérés/45 mp/meccs megy az API felé.
Csak Pro csomaggal működik (az ingyenes csomag sebességkorlátja ehhez kevés).
"""
import json
import time
from datetime import datetime, timedelta, timezone

from apifootball import DAILY_BUDGET, PRO, ApiError, BudgetExceeded, Client, used_today

LIVE_STATUSES = {"1H", "HT", "2H", "ET", "BT", "P", "SUSP", "INT", "LIVE"}
PLAYED_FINAL = {"FT", "AET", "PEN"}
FINAL_STATUSES = PLAYED_FINAL | {"PST", "CANC", "ABD", "AWD", "WO"}
TTL = 45               # másodperc: ennyi ideig használjuk újra egy élő lekérés eredményét
LIVE_RESERVE = min(300, DAILY_BUDGET // 4)  # az élő funkció ennyi kérést mindig meghagy a napi gyűjtésnek

STAT_LABELS = [
    ("Ball Possession", "Labdabirtoklás"),
    ("expected_goals", "Várható gólok (xG)"),
    ("Total Shots", "Lövések"),
    ("Shots on Goal", "Kapura lövés"),
    ("Corner Kicks", "Szögletek"),
    ("Fouls", "Szabálytalanságok"),
    ("Offsides", "Les"),
    ("Goalkeeper Saves", "Védések"),
    ("Yellow Cards", "Sárga lap"),
    ("Red Cards", "Piros lap"),
]


# ---------------------------------------------------------------- gyorsítótár

def cache_get(conn, key, ttl):
    row = conn.execute("SELECT ts, json FROM cache WHERE key = ?", (key,)).fetchone()
    if row and time.time() - row["ts"] < ttl:
        return json.loads(row["json"])
    return None


def cache_stale(conn, key):
    row = conn.execute("SELECT json FROM cache WHERE key = ?", (key,)).fetchone()
    return json.loads(row["json"]) if row else None


def cache_put(conn, key, data):
    conn.execute("INSERT OR REPLACE INTO cache(key, ts, json) VALUES (?,?,?)", (key, time.time(), json.dumps(data)))
    conn.commit()


def _fetch(conn, key, ttl, path, **params):
    """Friss gyorsítótárból, különben az API-ból. Hibánál/kerethiánynál a régi adatot adja."""
    data = cache_get(conn, key, ttl)
    if data is not None:
        return data
    if used_today(conn) >= DAILY_BUDGET - LIVE_RESERVE:
        return cache_stale(conn, key)
    try:
        data = Client(conn).get(path, **params)
    except (ApiError, BudgetExceeded):
        return cache_stale(conn, key)
    cache_put(conn, key, data)
    return data


# ---------------------------------------------------------------- segédek

def in_window(f, now=None):
    """Van értelme élőben kérdezni? A kezdés előtt 20 perctől a kezdés után 4 óráig."""
    if f["status"] in LIVE_STATUSES:
        return True
    now = now or datetime.now(timezone.utc)
    try:
        ko = datetime.fromisoformat(f["kickoff"])
    except (TypeError, ValueError):
        return False
    return ko - timedelta(minutes=20) <= now <= ko + timedelta(hours=4)


def apply_fixture(conn, item):
    fx, g = item["fixture"], item["goals"]
    conn.execute("UPDATE fixtures SET status = ?, home_goals = ?, away_goals = ? WHERE id = ?",
                 (fx["status"]["short"], g.get("home"), g.get("away"), fx["id"]))


def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    try:
        return float(str(v).replace("%", "").strip())
    except ValueError:
        return None


def tip_state(market, pick, hg, ag, corners, status):
    """won / lost / on (jó úton) / off / void / open. Nem kezdődött meccsre None."""
    if status in ("NS", "TBD", "PST", "CANC") or hg is None or ag is None:
        return None
    final = status in PLAYED_FINAL
    both = hg > 0 and ag > 0
    total = hg + ag
    if market == "BTTS":
        if pick == "Yes":
            return "won" if both else ("lost" if final else ("on" if (hg or ag) else "off"))
        return "lost" if both else ("won" if final else "on")
    if market == "OU2.5":
        if pick.startswith("Over"):
            return "won" if total >= 3 else ("lost" if final else ("on" if total == 2 else "off"))
        return "lost" if total >= 3 else ("won" if final else "on")
    if market == "1X2":
        cur = "Home" if hg > ag else "Away" if ag > hg else "Draw"
        if final:
            return "won" if cur == pick else "lost"
        return "on" if cur == pick else "off"
    if market == "CORNERS":
        if corners is None:
            return "open"
        side, line = pick.split()
        line = float(line)
        if final:
            if corners == line:
                return "void"
            return "won" if (side == "Over") == (corners > line) else "lost"
        if side == "Over":
            return "won" if corners > line else "open"
        return "lost" if corners > line else "on"
    return None


def _tip_states(conn, fid, hg, ag, corners, status):
    out = {}
    for t in conn.execute("SELECT market, pick FROM tips WHERE fixture_id = ?", (fid,)):
        s = tip_state(t["market"], t["pick"], hg, ag, corners, status)
        if s:
            out[t["market"]] = s
    return out


# ---------------------------------------------------------------- lista (összes mai meccs)

def refresh_live(conn):
    if not PRO:
        return {"enabled": False, "fixtures": {}}
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    rows = conn.execute("SELECT * FROM fixtures WHERE day >= ?", (since,)).fetchall()
    cands = [f for f in rows if f["status"] not in FINAL_STATUSES and in_window(f, now)]
    live_items = {}
    if cands:
        data = _fetch(conn, "live_all", TTL, "fixtures", live="all")
        live_items = {it["fixture"]["id"]: it for it in (data or {}).get("response", [])}
        for f in cands:
            it = live_items.get(f["id"])
            if it:
                apply_fixture(conn, it)
        conn.commit()
        # Ami már nincs az élők között, de élőnek/elkezdettnek kellene lennie: véglegesítjük
        for f in cands:
            if f["id"] in live_items:
                continue
            try:
                started = datetime.fromisoformat(f["kickoff"]) + timedelta(minutes=3) <= now
            except (TypeError, ValueError):
                started = False
            if f["status"] in LIVE_STATUSES or started:
                match_item(conn, f)

    out = {}
    for f in conn.execute("SELECT * FROM fixtures WHERE day >= ?", (since,)).fetchall():
        if f["status"] in ("NS", "TBD"):
            continue
        it = live_items.get(f["id"])
        st = it["fixture"]["status"] if it else {}
        out[str(f["id"])] = {
            "status": f["status"], "elapsed": st.get("elapsed"), "extra": st.get("extra"),
            "hg": f["home_goals"], "ag": f["away_goals"],
            "live": f["status"] in LIVE_STATUSES,
            "tips": _tip_states(conn, f["id"], f["home_goals"], f["away_goals"], None, f["status"]),
        }
    return {"enabled": True, "fixtures": out, "updated": int(time.time())}


# ---------------------------------------------------------------- egy meccs részletei

def match_item(conn, f):
    ttl = TTL if in_window(f) else 3600
    data = _fetch(conn, f"fx_{f['id']}", ttl, "fixtures", id=f["id"])
    items = (data or {}).get("response") or []
    if not items:
        return None
    item = items[0]
    apply_fixture(conn, item)
    if item["fixture"]["status"]["short"] == "FT" and item.get("statistics"):
        import collect
        collect.store_stats(conn, f["id"], item["statistics"])
        collect.settle_local(conn)  # azonnali elszámolás, nem kell az éjszakai futásra várni
    conn.commit()
    return item


def normalize(conn, item, f):
    home_id = f["home_id"]
    st = item["fixture"]["status"]
    hg, ag = item["goals"].get("home"), item["goals"].get("away")
    events = []
    for e in item.get("events") or []:
        typ, det = e.get("type"), e.get("detail") or ""
        if typ == "Goal":
            kind = "pen_miss" if "Missed" in det else "owngoal" if "Own" in det else "goal"
        elif typ == "Card":
            kind = "red" if ("Red" in det or "Second" in det) else "yellow"
        elif typ == "subst":
            kind = "sub"
        elif typ == "Var":
            kind = "var"
        else:
            continue
        events.append({
            "min": (e.get("time") or {}).get("elapsed"), "extra": (e.get("time") or {}).get("extra"),
            "side": "home" if (e.get("team") or {}).get("id") == home_id else "away",
            "kind": kind, "player": (e.get("player") or {}).get("name"),
            "assist": (e.get("assist") or {}).get("name"), "detail": det,
        })
    sides = {}
    for s in item.get("statistics") or []:
        side = "home" if s["team"]["id"] == home_id else "away"
        sides[side] = {x["type"]: x["value"] for x in s.get("statistics", [])}
    stats = []
    for key, label in STAT_LABELS:
        h, a = _num(sides.get("home", {}).get(key)), _num(sides.get("away", {}).get(key))
        if h is None and a is None:
            continue
        stats.append({"key": key, "label": label, "home": h or 0, "away": a or 0,
                      "pct": key == "Ball Possession"})
    ch, ca = _num(sides.get("home", {}).get("Corner Kicks")), _num(sides.get("away", {}).get("Corner Kicks"))
    corners = int(ch + ca) if ch is not None and ca is not None else None
    status = st["short"]
    return {
        "status": status, "elapsed": st.get("elapsed"), "extra": st.get("extra"),
        "hg": hg, "ag": ag, "events": events, "stats": stats, "corners": corners,
        "tips": _tip_states(conn, f["id"], hg, ag, corners, status),
    }


def match_payload(conn, fid):
    f = conn.execute("SELECT * FROM fixtures WHERE id = ?", (fid,)).fetchone()
    if not f:
        return None
    payload = None
    if PRO and (f["status"] in LIVE_STATUSES or in_window(f) or f["status"] in PLAYED_FINAL):
        item = match_item(conn, f)
        if item:
            f = conn.execute("SELECT * FROM fixtures WHERE id = ?", (fid,)).fetchone()
            payload = normalize(conn, item, f)
    if payload is None:
        payload = {"status": f["status"], "elapsed": None, "extra": None,
                   "hg": f["home_goals"], "ag": f["away_goals"], "events": [], "stats": [], "corners": None,
                   "tips": _tip_states(conn, fid, f["home_goals"], f["away_goals"], None, f["status"])}
    payload.update(enabled=PRO, kickoff=f["kickoff"], live=payload["status"] in LIVE_STATUSES,
                   finished=payload["status"] in FINAL_STATUSES, updated=int(time.time()))
    return payload
