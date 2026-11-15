"""Élő állás, események, statisztika, felállások.

Az API-t a szerver kérdezi, nem a böngésző, és minden választ rövid ideig tárol (TTL),
így akárhány felhasználó / frissítés mellett legfeljebb ~1 kérés/45 mp/meccs megy az API felé.
Csak Pro csomaggal működik (az ingyenes csomag sebességkorlátja ehhez kevés).
"""
import json
import time
from datetime import datetime, timedelta, timezone

import model
from apifootball import DAILY_BUDGET, PRO, ApiError, BudgetExceeded, Client, used_today

LIVE_STATUSES = {"1H", "HT", "2H", "ET", "BT", "P", "SUSP", "INT", "LIVE"}
PLAYED_FINAL = {"FT", "AET", "PEN"}
FINAL_STATUSES = PLAYED_FINAL | {"PST", "CANC", "ABD", "AWD", "WO"}
TTL = 45               # másodperc: ennyi ideig használjuk újra egy élő lekérés eredményét
LIVE_RESERVE = min(300, DAILY_BUDGET // 4)  # az élő funkció ennyi kérést mindig meghagy a napi gyűjtésnek
LINEUP_LEAD = 80       # perc: a kezdés előtt ennyivel már kérdezzük a felállást

MARKET_HU = {"1X2": "Győztes", "BTTS": "Mindkét csapat szerez gólt", "OU2.5": "Gólok (2.5)", "CORNERS": "Szögletek"}
PICK_HU = {"Home": "Hazai", "Draw": "Döntetlen", "Away": "Vendég", "Yes": "Igen", "No": "Nem",
           "Over 2.5": "Felett", "Under 2.5": "Alatt"}

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


def pick_hu(pick):
    if pick in PICK_HU:
        return PICK_HU[pick]
    side, _, line = pick.partition(" ")
    return f"{ {'Over': 'Felett', 'Under': 'Alatt'}.get(side, side) } {line}".strip()


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


def _fetch2(conn, key, ttl, path, **params):
    """(adat, friss?) – friss gyorsítótárból, különben az API-ból. Hibánál/kerethiánynál a régi adatot adja."""
    data = cache_get(conn, key, ttl)
    if data is not None:
        return data, False
    if used_today(conn) >= DAILY_BUDGET - LIVE_RESERVE:
        return cache_stale(conn, key), False
    try:
        data = Client(conn).get(path, **params)
    except (ApiError, BudgetExceeded):
        return cache_stale(conn, key), False
    cache_put(conn, key, data)
    return data, True


def _fetch(conn, key, ttl, path, **params):
    return _fetch2(conn, key, ttl, path, **params)[0]


# ---------------------------------------------------------------- segédek

def in_window(f, now=None):
    """Van értelme élőben kérdezni? A kezdés előtt ~80 perctől (felállások) a kezdés után 4 óráig."""
    if f["status"] in LIVE_STATUSES:
        return True
    now = now or datetime.now(timezone.utc)
    try:
        ko = datetime.fromisoformat(f["kickoff"])
    except (TypeError, ValueError):
        return False
    return ko - timedelta(minutes=LINEUP_LEAD) <= now <= ko + timedelta(hours=4)


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


def _tips_info(conn, fid, hg, ag, corners, status):
    states, names = {}, {}
    for t in conn.execute("SELECT market, pick FROM tips WHERE fixture_id = ?", (fid,)):
        names[t["market"]] = f"{MARKET_HU.get(t['market'], t['market'])}: {pick_hu(t['pick'])}"
        s = tip_state(t["market"], t["pick"], hg, ag, corners, status)
        if s:
            states[t["market"]] = s
    return states, names


def _elapsed_minutes(status, elapsed):
    if status == "HT":
        return 45
    return elapsed or 0


def live_probs(conn, fid, hg, ag, status, elapsed):
    """Élő 1X2 esély (csak ha van saját várható gól-becslés és a meccs folyamatban van)."""
    if status not in LIVE_STATUSES or hg is None or ag is None:
        return None
    row = conn.execute("SELECT lam_h, lam_a FROM predictions WHERE fixture_id = ?", (fid,)).fetchone()
    if not row or row["lam_h"] is None:
        return None
    p = model.live_1x2(row["lam_h"], row["lam_a"], hg, ag, _elapsed_minutes(status, elapsed))
    return {"h": p["Home"], "d": p["Draw"], "a": p["Away"]}


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
        states, names = _tips_info(conn, f["id"], f["home_goals"], f["away_goals"], None, f["status"])
        out[str(f["id"])] = {
            "home": f["home"], "away": f["away"],
            "status": f["status"], "elapsed": st.get("elapsed"), "extra": st.get("extra"),
            "hg": f["home_goals"], "ag": f["away_goals"],
            "live": f["status"] in LIVE_STATUSES,
            "tips": states, "tip_names": names,
            "probs": live_probs(conn, f["id"], f["home_goals"], f["away_goals"], f["status"], st.get("elapsed")),
        }
    return {"enabled": True, "fixtures": out, "updated": int(time.time())}


# ---------------------------------------------------------------- egy meccs részletei

def _stats_by_side(item, home_id):
    sides = {}
    for s in item.get("statistics") or []:
        side = "home" if s["team"]["id"] == home_id else "away"
        sides[side] = {x["type"]: x["value"] for x in s.get("statistics", [])}
    return sides


def _snapshot(conn, item, f):
    """Élő pillanatfelvétel a nyomás-grafikonhoz (csak friss lekérésnél, csak élő meccsnél)."""
    st = item["fixture"]["status"]
    if st["short"] not in LIVE_STATUSES or not item.get("statistics"):
        return
    sd = _stats_by_side(item, f["home_id"])

    def g(side, key):
        return _num(sd.get(side, {}).get(key)) or 0

    vals = (st.get("elapsed") or 0, item["goals"].get("home") or 0, item["goals"].get("away") or 0,
            g("home", "Total Shots"), g("away", "Total Shots"), g("home", "Shots on Goal"), g("away", "Shots on Goal"),
            g("home", "Corner Kicks"), g("away", "Corner Kicks"), g("home", "expected_goals"), g("away", "expected_goals"))
    last = conn.execute("""SELECT elapsed, hg, ag, h_shots, a_shots, h_sot, a_sot, h_corners, a_corners, h_xg, a_xg
                           FROM live_snapshots WHERE fixture_id = ? ORDER BY ts DESC LIMIT 1""", (f["id"],)).fetchone()
    if last and tuple(last) == vals:
        return
    conn.execute("""INSERT INTO live_snapshots(fixture_id, ts, elapsed, hg, ag, h_shots, a_shots, h_sot, a_sot,
                    h_corners, a_corners, h_xg, a_xg) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (f["id"], time.time()) + vals)


def series(conn, fid):
    rows = conn.execute("SELECT * FROM live_snapshots WHERE fixture_id = ? ORDER BY ts", (fid,)).fetchall()
    out = [{"m": 0, "hg": 0, "ag": 0, "hs": 0, "as": 0, "hsot": 0, "asot": 0, "hc": 0, "ac": 0}] if rows else []
    for r in rows:
        out.append({"m": r["elapsed"], "hg": r["hg"], "ag": r["ag"], "hs": r["h_shots"], "as": r["a_shots"],
                    "hsot": r["h_sot"], "asot": r["a_sot"], "hc": r["h_corners"], "ac": r["a_corners"]})
    return out


def match_item(conn, f):
    ttl = TTL if in_window(f) else 3600
    data, fresh = _fetch2(conn, f"fx_{f['id']}", ttl, "fixtures", id=f["id"])
    items = (data or {}).get("response") or []
    if not items:
        return None
    item = items[0]
    apply_fixture(conn, item)
    if fresh:
        _snapshot(conn, item, f)
    if item["fixture"]["status"]["short"] == "FT" and item.get("statistics"):
        import collect
        collect.store_stats(conn, f["id"], item["statistics"])
        collect.settle_local(conn)  # azonnali elszámolás, nem kell az éjszakai futásra várni
    conn.commit()
    return item


def _lineups(item, f):
    out = {}
    order = {"G": 1, "D": 2, "M": 3, "F": 4}
    for L in item.get("lineups") or []:
        side = "home" if (L.get("team") or {}).get("id") == f["home_id"] else "away"

        def pl(p):
            p = p.get("player") or {}
            return {"n": p.get("number"), "name": p.get("name"), "pos": p.get("pos"), "grid": p.get("grid")}

        xi = [pl(p) for p in L.get("startXI") or []]
        subs = [pl(p) for p in L.get("substitutes") or []]
        # sor:oszlop a rácsból; ha hiányzik, a poszt szerint osztjuk szét
        have_grid = xi and all(p["grid"] and ":" in p["grid"] for p in xi)
        if have_grid:
            for p in xi:
                r, c = p["grid"].split(":")
                p["row"], p["col"] = int(r), int(c)
        else:
            counters = {}
            for p in xi:
                row = order.get(p["pos"], 3)
                counters[row] = counters.get(row, 0) + 1
                p["row"], p["col"] = row, counters[row]
        for p in xi:
            p.pop("grid", None)
        for p in subs:
            p.pop("grid", None)
        out[side] = {"formation": L.get("formation"), "coach": (L.get("coach") or {}).get("name"), "xi": xi, "subs": subs}
    return out or None


def normalize(conn, item, f):
    home_id = f["home_id"]
    st = item["fixture"]["status"]
    hg, ag = item["goals"].get("home"), item["goals"].get("away")
    lineups = _lineups(item, f)
    starters = set()
    if lineups:
        for side in lineups.values():
            starters.update(p["name"] for p in side["xi"])
    events = []
    for e in item.get("events") or []:
        typ, det = e.get("type"), e.get("detail") or ""
        sub_in = sub_out = None
        if typ == "Goal":
            kind = "pen_miss" if "Missed" in det else "owngoal" if "Own" in det else "goal"
        elif typ == "Card":
            kind = "red" if ("Red" in det or "Second" in det) else "yellow"
        elif typ == "subst":
            kind = "sub"
            p, a = (e.get("player") or {}).get("name"), (e.get("assist") or {}).get("name")
            # Melyik megy ki és melyik be? A kezdőben szereplő megy ki; ha nem dönthető el, az első a kimenő.
            if p in starters and a not in starters:
                sub_out, sub_in = p, a
            elif a in starters and p not in starters:
                sub_out, sub_in = a, p
            else:
                sub_out, sub_in = p, a
        elif typ == "Var":
            kind = "var"
        else:
            continue
        events.append({
            "min": (e.get("time") or {}).get("elapsed"), "extra": (e.get("time") or {}).get("extra"),
            "side": "home" if (e.get("team") or {}).get("id") == home_id else "away",
            "kind": kind, "player": sub_in if kind == "sub" else (e.get("player") or {}).get("name"),
            "assist": sub_out if kind == "sub" else (e.get("assist") or {}).get("name"),
            "sub_in": sub_in, "sub_out": sub_out, "detail": det,
        })
    sides = _stats_by_side(item, home_id)
    stats = []
    for key, label in STAT_LABELS:
        h, a = _num(sides.get("home", {}).get(key)), _num(sides.get("away", {}).get(key))
        if h is None and a is None:
            continue
        stats.append({"key": key, "label": label, "home": h or 0, "away": a or 0, "pct": key == "Ball Possession"})
    ch, ca = _num(sides.get("home", {}).get("Corner Kicks")), _num(sides.get("away", {}).get("Corner Kicks"))
    corners = int(ch + ca) if ch is not None and ca is not None else None
    status = st["short"]
    states, names = _tips_info(conn, f["id"], hg, ag, corners, status)
    return {
        "status": status, "elapsed": st.get("elapsed"), "extra": st.get("extra"),
        "hg": hg, "ag": ag, "events": events, "stats": stats, "corners": corners,
        "tips": states, "tip_names": names, "lineups": lineups,
        "probs": live_probs(conn, f["id"], hg, ag, status, st.get("elapsed")),
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
        states, names = _tips_info(conn, fid, f["home_goals"], f["away_goals"], None, f["status"])
        payload = {"status": f["status"], "elapsed": None, "extra": None,
                   "hg": f["home_goals"], "ag": f["away_goals"], "events": [], "stats": [], "corners": None,
                   "tips": states, "tip_names": names, "lineups": None, "probs": None}
    payload.update(enabled=PRO, kickoff=f["kickoff"], home=f["home"], away=f["away"],
                   live=payload["status"] in LIVE_STATUSES, finished=payload["status"] in FINAL_STATUSES,
                   series=series(conn, fid), updated=int(time.time()))
    return payload
