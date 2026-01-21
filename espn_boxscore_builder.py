import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ESPN_SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/summary?event={event_id}"
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?dates={date}"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json,text/plain,*/*",
}

# ---------- helpers ----------
def _to_int(x, default=0):
    try:
        if x is None:
            return default
        if isinstance(x, str):
            x = x.strip()
            if x == "":
                return default
            return int(float(x))
        return int(x)
    except Exception:
        return default

def _to_float(x, default=np.nan):
    try:
        if x is None:
            return default
        if isinstance(x, str):
            x = x.strip()
            if x == "":
                return default
            return float(x)
        return float(x)
    except Exception:
        return default

def _parse_made_attempt(display: str):
    if not display or "-" not in str(display):
        return (0, 0)
    a, b = display.split("-", 1)
    return (_to_int(a, 0), _to_int(b, 0))

def _stat_map(team_stats_list):
    out = {}
    if not isinstance(team_stats_list, list):
        return out
    for item in team_stats_list:
        name = item.get("name")
        dv = item.get("displayValue")
        if name:
            out[str(name)] = dv
    return out

def _estimate_possessions(fga, fta, tov, orb):
    return fga + 0.44 * fta - orb + tov

def _safe_div(num, den, default=np.nan):
    return default if den in (0, 0.0, None) else num / den

# ---------- ESPN SUMMARY (TEAM STATS) ----------
def fetch_and_parse_espn_summary(event_id: str, timeout: int = 25):
    url = ESPN_SUMMARY_URL.format(event_id=event_id)
    r = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
    r.raise_for_status()
    data = r.json()

    header = data.get("header", {})
    comp = (header.get("competitions") or [{}])[0]

    game_date = comp.get("date")
    venue = (comp.get("venue") or {}).get("fullName")

    competitors = comp.get("competitors") or []
    home_id = next((str(c["team"]["id"]) for c in competitors if c.get("homeAway") == "home"), None)
    away_id = next((str(c["team"]["id"]) for c in competitors if c.get("homeAway") == "away"), None)

    teams = data.get("boxscore", {}).get("teams", [])
    if len(teams) < 2:
        raise ValueError("Missing team boxscore data")

    def parse_team(te):
        team = te.get("team", {})
        stats = _stat_map(te.get("teamStats", []))

        fgm, fga = _parse_made_attempt(stats.get("fieldGoals"))
        tpm, tpa = _parse_made_attempt(stats.get("threePointFieldGoals"))
        ftm, fta = _parse_made_attempt(stats.get("freeThrows"))

        tov = _to_int(stats.get("turnovers"))
        orb = _to_int(stats.get("reboundsOffensive"))
        drb = _to_int(stats.get("reboundsDefensive"))
        reb = _to_int(stats.get("rebounds"), orb + drb)

        poss = _estimate_possessions(fga, fta, tov, orb)

        return {
            "team_id": str(team.get("id")),
            "team": team.get("displayName"),
            "fgm": fgm, "fga": fga,
            "tpm": tpm, "tpa": tpa,
            "ftm": ftm, "fta": fta,
            "tov": tov,
            "orb": orb, "drb": drb, "reb": reb,
            "efg": _safe_div(fgm + 0.5 * tpm, fga),
            "ftr": _safe_div(fta, fga),
            "3par": _safe_div(tpa, fga),
            "poss": poss,
        }

    parsed = [parse_team(t) for t in teams]
    home = next((t for t in parsed if t["team_id"] == home_id), parsed[0])
    away = next((t for t in parsed if t["team_id"] == away_id), parsed[1])

    home["orb_pct"] = _safe_div(home["orb"], home["orb"] + away["drb"])
    away["orb_pct"] = _safe_div(away["orb"], away["orb"] + home["drb"])

    home["drb_pct"] = _safe_div(home["drb"], home["drb"] + away["orb"])
    away["drb_pct"] = _safe_div(away["drb"], away["drb"] + home["orb"])

    home["tov_pct"] = _safe_div(home["tov"], home["poss"])
    away["tov_pct"] = _safe_div(away["tov"], away["poss"])

    return {
        "event_id": event_id,
        "game_date": game_date,
        "venue": venue,
        "home": home,
        "away": away,
    }

def summary_to_team_rows(parsed):
    home = parsed["home"].copy()
    away = parsed["away"].copy()

    for r in (home, away):
        r["event_id"] = parsed["event_id"]
        r["game_date"] = parsed["game_date"]
        r["venue"] = parsed["venue"]

    home["opponent"] = away["team"]
    away["opponent"] = home["team"]

    home["home_away"] = "home"
    away["home_away"] = "away"

    return home, away

# ---------- ESPN SCOREBOARD (GAMES CSV) ----------
def fetch_scoreboard_games(date_yyyymmdd: str):
    url = ESPN_SCOREBOARD_URL.format(date=date_yyyymmdd)
    r = requests.get(url, headers=DEFAULT_HEADERS)
    r.raise_for_status()
    data = r.json()

    rows = []
    for e in data.get("events", []):
        comp = (e.get("competitions") or [{}])[0]
        competitors = comp.get("competitors") or []

        if len(competitors) < 2:
            continue

        home = next(c for c in competitors if c.get("homeAway") == "home")
        away = next(c for c in competitors if c.get("homeAway") == "away")

        rows.append({
            "date": date_yyyymmdd,
            "game_id": str(e.get("id")),
            "home_team": home["team"]["displayName"],
            "away_team": away["team"]["displayName"],
            "home_score": _to_int(home.get("score")),
            "away_score": _to_int(away.get("score")),
            "home_win": home.get("winner"),
        })

    return rows

def build_espn_games_csv(days_back=7, out_csv="espn_games.csv"):
    now_pst = datetime.now(ZoneInfo("America/Los_Angeles"))
    rows = []

    for i in range(days_back):
        d = (now_pst - timedelta(days=i)).strftime("%Y%m%d")
        rows.extend(fetch_scoreboard_games(d))
        print(f"{d}: games fetched")

    df = pd.DataFrame(rows).drop_duplicates(subset=["game_id"])
    df.to_csv(out_csv, index=False)
    print(f"espn_games.csv written ({len(df)} rows)")

# ---------- TEAM GAME LOGS CSV ----------
def build_team_game_logs_csv(days_back=7, out_csv="espn_team_game_logs.csv"):
    now_pst = datetime.now(ZoneInfo("America/Los_Angeles"))
    rows = []

    for i in range(days_back):
        d = (now_pst - timedelta(days=i)).strftime("%Y%m%d")
        scoreboard = fetch_scoreboard_games(d)

        for g in scoreboard:
            try:
                parsed = fetch_and_parse_espn_summary(g["game_id"])
                home, away = summary_to_team_rows(parsed)
                rows.extend([home, away])
            except Exception as e:
                print("FAILED", g["game_id"], str(e)[:120])

    keep = [
        "team","opponent","home_away","event_id","game_date","venue",
        "fgm","fga","tpm","tpa","ftm","fta","tov","orb","drb","reb",
        "efg","3par","ftr","orb_pct","drb_pct","tov_pct","poss"
    ]

    df = pd.DataFrame(rows)
    df = df[[c for c in keep if c in df.columns]]
    df.to_csv(out_csv, index=False)
    print(f"espn_team_game_logs.csv written ({len(df)} rows)")

# ---------- ENTRY POINT ----------
if __name__ == "__main__":
    build_espn_games_csv(days_back=7)
    build_team_game_logs_csv(days_back=7)
