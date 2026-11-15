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
