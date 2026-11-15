"""Egyszerű Poisson-modell a tabella hazai/vendég gólmutatóiból."""
import math

MAX_GOALS = 8
SHRINK_GAMES = 6  # ennyi "átlagos" meccs húzza vissza a kevés adatú csapatokat az átlag felé


def flatten_standings(standings_json):
    """Az API standings válaszából lapos sorlista (csoportkörnél több tábla is lehet)."""
    rows = []
    for resp in standings_json.get("response", []):
        for table in resp.get("league", {}).get("standings", []):
            rows.extend(table)
    return rows


def team_table(rows):
    """Visszaadja: {team_id: {...}}, liga átlagos hazai és vendég gólok meccsenként."""
    home_games = home_goals = away_goals_total = 0
    teams = {}
    for r in rows:
        h, a = r.get("home", {}), r.get("away", {})
        hp, ap = h.get("played", 0), a.get("played", 0)
        hgf, hga = h.get("goals", {}).get("for", 0), h.get("goals", {}).get("against", 0)
        agf, aga = a.get("goals", {}).get("for", 0), a.get("goals", {}).get("against", 0)
        home_games += hp
        home_goals += hgf
        away_goals_total += agf
        teams[r["team"]["id"]] = {
            "name": r["team"]["name"],
            "form": r.get("form") or "",
            "rank": r.get("rank"),
            "points": r.get("points"),
            "hp": hp, "hgf": hgf, "hga": hga,
            "ap": ap, "agf": agf, "aga": aga,
        }
    if home_games == 0:
        return teams, None, None
    return teams, home_goals / home_games, away_goals_total / home_games


def _rate(goals, games, league_avg):
    # Zsugorított arány: (gólok + k*átlag) / (meccsek + k), majd átlaghoz viszonyítva.
    return (goals + SHRINK_GAMES * league_avg) / (games + SHRINK_GAMES) / league_avg


def expected_goals(teams, avg_home, avg_away, home_id, away_id):
    h, a = teams.get(home_id), teams.get(away_id)
    if not h or not a or not avg_home or not avg_away:
        return None
    att_h = _rate(h["hgf"], h["hp"], avg_home)
    def_h = _rate(h["hga"], h["hp"], avg_away)
    att_a = _rate(a["agf"], a["ap"], avg_away)
    def_a = _rate(a["aga"], a["ap"], avg_home)
    lam_h = avg_home * att_h * def_a
    lam_a = avg_away * att_a * def_h
    return lam_h, lam_a


def _pois(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def probabilities(lam_h, lam_a):
    home = draw = away = btts = over25 = 0.0
    total = 0.0
    for i in range(MAX_GOALS + 1):
        pi = _pois(i, lam_h)
        for j in range(MAX_GOALS + 1):
            p = pi * _pois(j, lam_a)
            total += p
            if i > j:
                home += p
            elif i == j:
                draw += p
            else:
                away += p
            if i > 0 and j > 0:
                btts += p
            if i + j >= 3:
                over25 += p
    return {
        "Home": home / total, "Draw": draw / total, "Away": away / total,
        "BTTS": btts / total, "Over25": over25 / total,
    }


def build_tips(probs, odds, lam_h, lam_a):
    """odds: {(market, selection): price}. Visszaad egy listát tippekkel piaconként."""
    tips = []
    xg = f"Várható gólok: {lam_h:.2f} – {lam_a:.2f}."

    # 1X2: a legvalószínűbb kimenetel
    pick = max(("Home", "Draw", "Away"), key=lambda k: probs[k])
    tips.append(_tip("1X2", pick, probs[pick], odds.get(("1X2", pick)), xg))

    # Mindkét csapat szerez gólt
    p_yes = probs["BTTS"]
    pick, p = ("Yes", p_yes) if p_yes >= 0.5 else ("No", 1 - p_yes)
    tips.append(_tip("BTTS", pick, p, odds.get(("BTTS", pick)), xg))

    # Gólok 2.5 felett/alatt
    p_over = probs["Over25"]
    pick, p = ("Over 2.5", p_over) if p_over >= 0.5 else ("Under 2.5", 1 - p_over)
    tips.append(_tip("OU2.5", pick, p, odds.get(("OU2.5", pick)), xg))
    return tips


def _tip(market, pick, prob, price, note):
    value = (prob * price - 1) if price else None
    return {"market": market, "pick": pick, "prob": prob, "odds": price, "value": value, "note": note}


def tips_from_odds(odds):
    """Tartalék: a szorzókból, a fogadóiroda árrésének levonásával számolt esélyek. Érték nincs, mert nincs saját becslés."""
    note = "Kevés saját adat van a csapatokról, ezért a tipp a szorzókból számolt esélyen alapul (nincs saját becslés)."
    tips = []
    for market, sels in (("1X2", ("Home", "Draw", "Away")), ("BTTS", ("Yes", "No")),
                         ("OU2.5", ("Over 2.5", "Under 2.5"))):
        prices = [odds.get((market, s)) for s in sels]
        if not all(prices):
            continue
        inv = [1 / p for p in prices]
        total = sum(inv)
        best = max(range(len(sels)), key=lambda i: inv[i])
        tips.append({"market": market, "pick": sels[best], "prob": inv[best] / total,
                     "odds": prices[best], "value": None, "note": note})
    return tips


MIN_GAMES = 3  # csapatonként ennyi lejátszott meccs kell a saját becsléshez


def team_table_from_results(rows):
    """rows: lejátszott meccsek időrendben (home_id, home, away_id, away, hg, ag).
    Ugyanazt a szerkezetet adja, mint a team_table, de a saját eredményadatbázisból."""
    teams = {}
    tot_h = tot_a = n = 0

    def t(tid, name):
        return teams.setdefault(tid, {"name": name, "form": "", "rank": None, "points": None,
                                      "hp": 0, "hgf": 0, "hga": 0, "ap": 0, "agf": 0, "aga": 0})

    for r in rows:
        h, a = t(r["home_id"], r["home"]), t(r["away_id"], r["away"])
        hg, ag = r["hg"], r["ag"]
        h["hp"] += 1; h["hgf"] += hg; h["hga"] += ag
        a["ap"] += 1; a["agf"] += ag; a["aga"] += hg
        h["form"] = (h["form"] + ("W" if hg > ag else "D" if hg == ag else "L"))[-5:]
        a["form"] = (a["form"] + ("W" if ag > hg else "D" if hg == ag else "L"))[-5:]
        tot_h += hg
        tot_a += ag
        n += 1
    if n == 0:
        return teams, None, None
    return teams, tot_h / n, tot_a / n


def load_results_table(conn, upto_day, days=150):
    from datetime import datetime, timedelta
    cutoff = (datetime.strptime(upto_day, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute(
        """SELECT home_id, home, away_id, away, home_goals AS hg, away_goals AS ag
           FROM fixtures WHERE status = 'FT' AND home_goals IS NOT NULL AND away_goals IS NOT NULL
             AND day >= ? AND day <= ? ORDER BY kickoff""", (cutoff, upto_day)).fetchall()
    return team_table_from_results(rows)


def enough_data(teams, home_id, away_id):
    def games(tid):
        t = teams.get(tid)
        return (t["hp"] + t["ap"]) if t else 0
    return games(home_id) >= MIN_GAMES and games(away_id) >= MIN_GAMES


# ---------------------------------------------------------------- szögletek

def corner_table(rows):
    """rows: home_id, away_id, hc (hazai szögletek), ac (vendég szögletek). Visszaad: teams, átlag hazai, átlag vendég."""
    teams = {}
    th = ta = n = 0

    def t(tid):
        return teams.setdefault(tid, {"hp": 0, "hf": 0, "ha": 0, "ap": 0, "af": 0, "aa": 0})

    for r in rows:
        h, a = t(r["home_id"]), t(r["away_id"])
        h["hp"] += 1; h["hf"] += r["hc"]; h["ha"] += r["ac"]
        a["ap"] += 1; a["af"] += r["ac"]; a["aa"] += r["hc"]
        th += r["hc"]; ta += r["ac"]; n += 1
    if n == 0:
        return teams, None, None
    return teams, th / n, ta / n


def expected_corners(teams, avg_h, avg_a, home_id, away_id):
    h, a = teams.get(home_id), teams.get(away_id)
    if not h or not a or not avg_h or not avg_a:
        return None
    if h["hp"] + h["ap"] < MIN_GAMES or a["hp"] + a["ap"] < MIN_GAMES:
        return None
    att_h = _rate(h["hf"], h["hp"], avg_h)
    def_h = _rate(h["ha"], h["hp"], avg_a)
    att_a = _rate(a["af"], a["ap"], avg_a)
    def_a = _rate(a["aa"], a["ap"], avg_h)
    return avg_h * att_h * def_a, avg_a * att_a * def_h


def poisson_cdf(k, lam):
    return sum(_pois(i, lam) for i in range(0, int(k) + 1))


def corner_tip(lam_total, odds, w=0.0):
    """A rendelkezésre álló szögletvonalak közül a várható értékhez legközelebbit választja.
    w: a piaci esély súlya (0..1). Kerek vonalnál a visszajáró tét (pontosan a vonal) kiesik a számításból."""
    lines = {}
    for (market, sel), price in odds.items():
        if market != "CORNERS":
            continue
        parts = sel.split()
        if len(parts) == 2 and parts[0] in ("Over", "Under"):
            try:
                lines.setdefault(float(parts[1]), {})[parts[0]] = price
            except ValueError:
                pass
    full = {x: v for x, v in lines.items() if "Over" in v and "Under" in v}
    if full:
        line = min(full, key=lambda x: abs(x - lam_total))
    else:
        line = int(lam_total) + 0.5
    if line == int(line):
        p_under = poisson_cdf(line - 1, lam_total)
        p_over = 1 - poisson_cdf(line, lam_total)
        p_over = p_over / (p_over + p_under)
    else:
        p_over = 1 - poisson_cdf(line, lam_total)
    sides = lines.get(line, {})
    if w and "Over" in sides and "Under" in sides:
        inv_o, inv_u = 1 / sides["Over"], 1 / sides["Under"]
        p_over = (1 - w) * p_over + w * inv_o / (inv_o + inv_u)
    side, p = ("Over", p_over) if p_over >= 0.5 else ("Under", 1 - p_over)
    price = sides.get(side)
    note = f"Várható szögletek: {lam_total:.1f} (vonal: {line:g})."
    if line == int(line):
        note += " Pontosan a vonalnyi szögletnél a tét visszajár."
    return _tip("CORNERS", f"{side} {line:g}" if line != int(line) else f"{side} {line:.1f}", p, price, note)


# ---------------------------------------------------------------- vegyítés a piaccal

def market_probs(odds):
    """A fogadóiroda szorzóiból, az árrés levonásával számolt esélyek (amit csak lehet)."""
    out = {}

    def norm(market, sels):
        prices = [odds.get((market, s)) for s in sels]
        if not all(prices):
            return None
        inv = [1 / p for p in prices]
        tot = sum(inv)
        return [x / tot for x in inv]

    r = norm("1X2", ("Home", "Draw", "Away"))
    if r:
        out["Home"], out["Draw"], out["Away"] = r
    r = norm("BTTS", ("Yes", "No"))
    if r:
        out["BTTS"] = r[0]
    r = norm("OU2.5", ("Over 2.5", "Under 2.5"))
    if r:
        out["Over25"] = r[0]
    return out


def blend(probs, odds, w):
    """Saját modell és piaci esély súlyozott átlaga. w = a piac súlya (0..1)."""
    if not w:
        return probs
    mk = market_probs(odds)
    out = dict(probs)
    for k, v in mk.items():
        out[k] = (1 - w) * probs[k] + w * v
    total = out["Home"] + out["Draw"] + out["Away"]
    for k in ("Home", "Draw", "Away"):
        out[k] /= total
    return out


def adaptive_weight(base, games):
    """Kevés meccses csapatoknál a piac súlya nő: 15 meccsnél már az alap, 0-nál 1 felé tart."""
    shortfall = max(0.0, 1 - games / 15)
    return base + (1 - base) * shortfall


def min_corner_games(teams, home_id, away_id):
    def g(tid):
        t = teams.get(tid)
        return (t["hp"] + t["ap"]) if t else 0
    return min(g(home_id), g(away_id))


def min_games(teams, home_id, away_id):
    def g(tid):
        t = teams.get(tid)
        return (t["hp"] + t["ap"]) if t else 0
    return min(g(home_id), g(away_id))
