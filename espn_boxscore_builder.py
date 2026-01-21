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

PST_TZ = ZoneInfo("America/Los_Angeles")

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
    a, b = str(display).split("-", 1)
    return (_to_int(a, 0), _to_int(b, 0))

def _stat_map(stats_list):
    """
    ESPN can return team stats as a list of dicts with:
      - name: machine key (preferred)
      - label: human label (fallback)
      - displayValue: string value (e.g., "24-58")
      - value: numeric value
    We store both name and label (lowercased label) to maximize match rate.
    """
    out = {}
    if not isinstance(stats_list, list):
        return out
    for item in stats_list:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        label = item.get("label")
        dv = item.get("displayValue")
        val = item.get("value")

        # Prefer displayValue when present; else value
        stored = dv if dv not in (None, "") else val

        if name:
            out[str(name)] = stored
        if label:
            out[str(label).strip().lower()] = stored
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

    header = data.get("header", {}) if isinstance(data, dict) else {}
    comp = (header.get("competitions") or [{}])[0]

    game_date = comp.get("date")
    venue = (comp.get("venue") or {}).get("fullName")

    competitors = comp.get("competitors") or []
    home_id = next((str(c.get("team", {}).get("id")) for c in competitors if c.get("homeAway") == "home"), None)
    away_id = next((str(c.get("team", {}).get("id")) for c in competitors if c.get("homeAway") == "away"), None)

    teams = data.get("boxscore", {}).get("teams", [])
    if not isinstance(teams, list) or len(teams) < 2:
        raise ValueError("Missing boxscore.teams data")

    def parse_team(team_entry):
        team = team_entry.get("team", {}) or {}
        tid = str(team.get("id", "")) if team.get("id") is not None else ""
        name = team.get("displayName") or team.get("shortDisplayName") or team.get("name") or "Unknown"

        # ESPN varies key: teamStats vs statistics
        stats_list = team_entry.get("teamStats") or team_entry.get("statistics") or []
        smap = _stat_map(stats_list)

        def pick_stat(*names):
            for n in names:
                # Try direct key
                if n in smap and smap[n] not in (None, "", "0-0"):
                    return smap[n]
                # Try lowercased label key
                nl = str(n).strip().lower()
                if nl in smap and smap[nl] not in (None, "", "0-0"):
                    return smap[nl]
            return ""

        # Shooting (displayValue typically "made-attempted")
        fgm, fga = _parse_made_attempt(pick_stat("fieldGoals", "fg", "Field Goals"))
        tpm, tpa = _parse_made_attempt(pick_stat("threePointFieldGoals", "3ptFieldGoals", "3pt fg", "3pt", "Three Point Field Goals"))
        ftm, fta = _parse_made_attempt(pick_stat("freeThrows", "ft", "Free Throws"))

        # Other stats
        tov = _to_int(pick_stat("turnovers", "to", "Turnovers"), 0)
        orb = _to_int(pick_stat("reboundsOffensive", "offensiveRebounds", "oreb", "Offensive Rebounds"), 0)
        drb = _to_int(pick_stat("reboundsDefensive", "defensiveRebounds", "dreb", "Defensive Rebounds"), 0)
        reb = _to_int(pick_stat("rebounds", "totalRebounds", "reb", "Rebounds"), orb + drb)

        poss = float(_estimate_possessions(fga, fta, tov, orb))

        efg = _safe_div((fgm + 0.5 * tpm), fga, np.nan)
        ftr = _safe_div(fta, fga, np.nan)
        threepar = _safe_div(tpa, fga, np.nan)

        return {
            "team_id": tid,
            "team": name,
            "fgm": int(fgm), "fga": int(fga),
            "tpm": int(tpm), "tpa": int(tpa),
            "ftm": int(ftm), "fta": int(fta),
            "tov": int(tov),
            "orb": int(orb), "drb": int(drb), "reb": int(reb),
            "efg": float(efg) if pd.notna(efg) else np.nan,
            "ftr": float(ftr) if pd.notna(ftr) else np.nan,
            "3par": float(threepar) if pd.notna(threepar) else np.nan,
            "poss": poss,
        }

    parsed = [parse_team(t) for t in teams]

    # Map to home/away
    home = next((t for t in parsed if t["team_id"] and t["team_id"] == (home_id or "")), parsed[0])
    away = next((t for t in parsed if t["team_id"] and t["team_id"] == (away_id or "")), parsed[1])

    # Opponent-dependent metrics
    home["orb_pct"] = _safe_div(home["orb"], (home["orb"] + away["drb"]), np.nan)
    away["orb_pct"] = _safe_div(away["orb"], (away["orb"] + home["drb"]), np.nan)

    home["drb_pct"] = _safe_div(home["drb"], (home["drb"] + away["orb"]), np.nan)
    away["drb_pct"] = _safe_div(away["drb"], (away["drb"] + home["orb"]), np.nan)

    home["tov_pct"] = _safe_div(home["tov"], home["poss"], np.nan)
    away["tov_pct"] = _safe_div(away["tov"], away["poss"], np.nan)

    return {
        "event_id": str(event_id),
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
def fetch_scoreboard_games(date_yyyymmdd: str, timeout: int = 25):
    """
    Returns one row per game:
      date, game_id, home_team, away_team, home_score, away_score, home_win, completed
    """
    url = ESPN_SCOREBOARD_URL.format(date=date_yyyymmdd)
    r = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
    r.raise_for_status()
    data = r.json()

    rows = []
    for e in data.get("events", []) or []:
        game_id = e.get("id")
        competitions = e.get("competitions") or []
        comp = competitions[0] if competitions else {}
        competitors = comp.get("competitors") or []

        if not game_id or len(competitors) < 2:
            continue

        status = comp.get("status") or {}
        stype = status.get("type") or {}
        completed = bool(stype.get("completed", False))

        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        if not home or not away:
            continue

        home_team = (home.get("team") or {}).get("displayName")
        away_team = (away.get("team") or {}).get("displayName")

        home_score = _to_int(home.get("score"), 0)
        away_score = _to_int(away.get("score"), 0)
        home_win = home.get("winner")

        rows.append({
            "date": date_yyyymmdd,
            "game_id": str(game_id),
            "home_team": home_team,
            "away_team": away_team,
            "home_score": home_score,
            "away_score": away_score,
            "home_win": home_win,
            "completed": completed,
        })

    return rows

def build_espn_games_csv(days_back=7, out_csv="espn_games.csv", verbose=True):
    now_pst = datetime.now(PST_TZ)
    all_rows = []

    for i in range(days_back):
        d = (now_pst - timedelta(days=i)).strftime("%Y%m%d")
        rows = fetch_scoreboard_games(d)
        all_rows.extend(rows)
        if verbose:
            print(f"{d}: {len(rows)} games")

    df = pd.DataFrame(all_rows)
    if not df.empty:
        df = df.drop_duplicates(subset=["game_id"]).sort_values(["date", "game_id"])

    # Keep espn_games.csv schema stable (no 'completed' column)
    if "completed" in df.columns:
        df = df.drop(columns=["completed"])

    df.to_csv(out_csv, index=False)
    print(f"espn_games.csv written: {len(df)} rows")
    return df

# ---------- TEAM GAME LOGS CSV ----------
def build_team_game_logs_csv(days_back=7, out_csv="espn_team_game_logs.csv", verbose=True):
    """
    Builds espn_team_game_logs.csv for the last N days.
    One row per TEAM per GAME.
    Uses scoreboard to get game_ids, then summary endpoint for team stats.
    Filters to completed games and skips empty/placeholder boxscores.
    """
    now_pst = datetime.now(PST_TZ)
    rows = []
    failures = 0
    total_candidates = 0
    total_final = 0
    total_processed = 0
    total_skipped_empty = 0

    for i in range(days_back):
        d = (now_pst - timedelta(days=i)).strftime("%Y%m%d")
        games = fetch_scoreboard_games(d)
        total_candidates += len(games)

        final_games = [g for g in games if g.get("completed") is True]
        total_final += len(final_games)

        if verbose:
            print(f"📅 {d}: {len(games)} games, {len(final_games)} final")

        for g in final_games:
            try:
                parsed = fetch_and_parse_espn_summary(g["game_id"])
                home_row, away_row = summary_to_team_rows(parsed)

                # Safety gate: skip if ESPN returned no real attempts (common when payload missing)
                def has_real_boxscore(r):
                    return (r.get("fga", 0) or 0) > 0 or (r.get("fta", 0) or 0) > 0

                if not has_real_boxscore(home_row) and not has_real_boxscore(away_row):
                    total_skipped_empty += 1
                    continue

                rows.append(home_row)
                rows.append(away_row)
                total_processed += 1

            except Exception as e:
                failures += 1
                if verbose:
                    print("FAILED", g["game_id"], str(e)[:180])

    df = pd.DataFrame(rows)

    keep = [
        "team
