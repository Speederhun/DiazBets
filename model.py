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


def _venue(t, side, avg_venue, avg_mean, for_goals):
    """Egy csapat támadó/védekező erőszorzója a hazai vagy idegenbeli meccsei alapján.
    A kevés pályaadatot a csapat összesített (hazai+idegen) teljesítménye felé húzza, nem a liga átlaga felé."""
    w = "w_" if "w_hp" in t else ""
    pre, vg = ("h", "hgf" if for_goals else "hga") if side == "home" else ("a", "agf" if for_goals else "aga")
    og = ("agf" if for_goals else "aga") if side == "home" else ("hgf" if for_goals else "hga")
    vgames, vgoals = t[w + pre + "p"], t[w + vg]
    ogames = t[w + ("a" if side == "home" else "h") + "p"]
    ogoals = t[w + og]
    tg = vgames + ogames
    # a csapat összesített rátája a ligaátlaghoz viszonyítva (liga-szint zsugorítással)
    overall = (vgoals + ogoals + SHRINK_GAMES * avg_mean) / (tg + SHRINK_GAMES) / avg_mean if avg_mean else 1.0
    prior = avg_venue * overall
    return (vgoals + SHRINK_GAMES * prior) / (vgames + SHRINK_GAMES) / avg_venue


def fatigue_factor(rest_days):
    """Kevés pihenőnap kicsit visszaveti a csapat támadását (kis, óvatos korrekció)."""
    if rest_days is None:
        return 1.0
    if rest_days <= 2:
        return 0.95
    if rest_days == 3:
        return 0.975
    if rest_days == 4:
        return 0.99
    return 1.0


def injury_factors(n_missing):
    """(saját támadás szorzó, ellenfél támadás szorzó) a hiányzók száma alapján; alacsony plafonnal."""
    n = max(0, n_missing or 0)
    own = 1 - min(0.06, 0.01 * max(0, n - 1))
    opp = 1 + min(0.04, 0.007 * max(0, n - 1))
    return own, opp


def expected_goals(teams, avg_home, avg_away, home_id, away_id, adj=None):
    """adj (nem kötelező): {rest_h, rest_a, inj_h, inj_a} pihenőnapok és hiányzók száma."""
    h, a = teams.get(home_id), teams.get(away_id)
    if not h or not a or not avg_home or not avg_away:
        return None
    mean = (avg_home + avg_away) / 2
    att_h = _venue(h, "home", avg_home, mean, True)
    def_h = _venue(h, "home", avg_away, mean, False)
    att_a = _venue(a, "away", avg_away, mean, True)
    def_a = _venue(a, "away", avg_home, mean, False)
    lam_h = avg_home * att_h * def_a
    lam_a = avg_away * att_a * def_h
    if adj:
        own_h, opp_h = injury_factors(adj.get("inj_h"))
        own_a, opp_a = injury_factors(adj.get("inj_a"))
        lam_h *= fatigue_factor(adj.get("rest_h")) * own_h * opp_a
        lam_a *= fatigue_factor(adj.get("rest_a")) * own_a * opp_h
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


HALF_LIFE_DAYS = 70  # ennyi nap után számít a meccs fele annyit: a friss forma többet nyom


def _decay(day, now_day, half_life=HALF_LIFE_DAYS):
    if not day or not now_day:
        return 1.0
    from datetime import datetime
    try:
        age = (datetime.strptime(now_day, "%Y-%m-%d") - datetime.strptime(day, "%Y-%m-%d")).days
    except ValueError:
        return 1.0
    return 0.5 ** (max(age, 0) / half_life)


def team_table_from_results(rows, now_day=None):
    """rows: lejátszott meccsek időrendben (home_id, home, away_id, away, hg, ag[, day]).
    Ugyanazt a szerkezetet adja, mint a team_table, de a saját eredményadatbázisból.
    Ha now_day adott, a w_* mezők az idővel fakuló (friss meccset többre értékelő) összegek."""
    teams = {}
    tot_h = tot_a = n = 0

    def t(tid, name):
        return teams.setdefault(tid, {"name": name, "form": "", "rank": None, "points": None,
                                      "hp": 0, "hgf": 0, "hga": 0, "ap": 0, "agf": 0, "aga": 0,
                                      "w_hp": 0.0, "w_hgf": 0.0, "w_hga": 0.0,
                                      "w_ap": 0.0, "w_agf": 0.0, "w_aga": 0.0})

    for r in rows:
        h, a = t(r["home_id"], r["home"]), t(r["away_id"], r["away"])
        hg, ag = r["hg"], r["ag"]
        w = _decay(r["day"] if "day" in r.keys() else None, now_day)
        h["hp"] += 1; h["hgf"] += hg; h["hga"] += ag
        a["ap"] += 1; a["agf"] += ag; a["aga"] += hg
        h["w_hp"] += w; h["w_hgf"] += w * hg; h["w_hga"] += w * ag
        a["w_ap"] += w; a["w_agf"] += w * ag; a["w_aga"] += w * hg
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
        """SELECT home_id, home, away_id, away, home_goals AS hg, away_goals AS ag, day
           FROM fixtures WHERE status = 'FT' AND home_goals IS NOT NULL AND away_goals IS NOT NULL
             AND day >= ? AND day <= ? ORDER BY kickoff""", (cutoff, upto_day)).fetchall()
    return team_table_from_results(rows, now_day=upto_day)


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


# ---------------------------------------------------------------- hőtérkép, élő esély

def score_matrix(lam_h, lam_a, n=6):
    """n x n mátrix: [hazai gólok][vendég gólok] = valószínűség."""
    ph = [_pois(i, lam_h) for i in range(n)]
    pa = [_pois(j, lam_a) for j in range(n)]
    return [[ph[i] * pa[j] for j in range(n)] for i in range(n)]


def top_scores(matrix, k=3):
    cells = [(matrix[i][j], i, j) for i in range(len(matrix)) for j in range(len(matrix[0]))]
    cells.sort(reverse=True)
    return [{"h": i, "a": j, "p": p} for p, i, j in cells[:k]]


def live_1x2(lam_h, lam_a, hg, ag, elapsed):
    """1X2 esély az aktuális állás és a hátralévő idő alapján (a várható gólok az idővel arányosan fogynak)."""
    left = max(0.0, 90 - min(max(elapsed or 0, 0), 90)) / 90
    lh, la = lam_h * left, lam_a * left
    home = draw = away = 0.0
    for i in range(MAX_GOALS + 1):
        pi = _pois(i, lh) if lh > 0 else (1.0 if i == 0 else 0.0)
        for j in range(MAX_GOALS + 1):
            pj = _pois(j, la) if la > 0 else (1.0 if j == 0 else 0.0)
            p = pi * pj
            d = (hg - ag) + (i - j)
            if d > 0:
                home += p
            elif d == 0:
                draw += p
            else:
                away += p
    total = home + draw + away
    return {"Home": home / total, "Draw": draw / total, "Away": away / total}


# ---------------------------------------------------------------- önjavító kalibráció

CAL_BINS = (0.0, 0.45, 0.55, 0.65, 0.75, 1.01)
CAL_SHRINK = 15       # ennyi "hibátlan" tipp húzza vissza a kevés mintás sávot az átlóra
CAL_MIN_MARKET = 40   # piaconkénti saját görbéhez ennyi elszámolt tipp kell
CAL_MIN_POOLED = 60   # ennyi alatt egyáltalán nincs igazítás


def _curve(rows):
    """rows: (esély, 1 ha nyert / 0 ha nem). Visszaad: [(átlagos esély, delta)] a sávonként, shrinkage-dzsel."""
    pts = []
    for lo, hi in zip(CAL_BINS, CAL_BINS[1:]):
        b = [(p, y) for p, y in rows if lo <= p < hi]
        if not b:
            continue
        n = len(b)
        mean_p = sum(p for p, _ in b) / n
        hits = sum(y for _, y in b)
        adj = (hits + CAL_SHRINK * mean_p) / (n + CAL_SHRINK)
        pts.append((mean_p, adj - mean_p))
    return pts


def fit_calibration(rows):
    """rows: (market, esély, nyert 0/1) a lezárt tippekből (nyers modell-esély). Visszaad: {market: görbe, '*': közös görbe}."""
    rows = list(rows)
    cal = {}
    if len(rows) < CAL_MIN_POOLED:
        return cal
    cal["*"] = _curve([(p, y) for _, p, y in rows])
    by = {}
    for m, p, y in rows:
        by.setdefault(m, []).append((p, y))
    for m, r in by.items():
        if len(r) >= CAL_MIN_MARKET:
            cal[m] = _curve(r)
    return cal


def apply_calibration(cal, market, p):
    """A nyers esélyt a tanult görbe szerint igazítja (lineáris interpoláció a sávok között, a szélén állandó delta)."""
    pts = cal.get(market) or cal.get("*")
    if not pts:
        return p
    if p <= pts[0][0]:
        d = pts[0][1]
    elif p >= pts[-1][0]:
        d = pts[-1][1]
    else:
        d = pts[-1][1]
        for (x0, d0), (x1, d1) in zip(pts, pts[1:]):
            if x0 <= p <= x1:
                d = d0 + (d1 - d0) * ((p - x0) / (x1 - x0) if x1 > x0 else 0)
                break
    return min(0.98, max(0.02, p + d))
