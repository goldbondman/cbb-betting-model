
import requests
import pandas as pd
import numpy as np

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
    if not display or not isinstance(display, str) or "-" not in display:
        return (0, 0)
    a, b = display.split("-", 1)
    return (_to_int(a, 0), _to_int(b, 0))

def _stat_map(team_stats_list):
    out = {}
    if not isinstance(team_stats_list, list):
        return out
    for item in team_stats_list:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        dv = item.get("displayValue")
        if name:
            out[str(name)] = dv
    return out

def _estimate_possessions(fga, fta, tov, orb):
    # poss ≈ FGA + 0.44*FTA - ORB + TOV
    return fga + 0.44 * fta - orb + tov

def _safe_div(num, den, default=np.nan):
    return default if den in (0, 0.0, None) else num / den

def _extract_players(summary_json, team_id: str):
    """
    Lightweight player minutes + usage proxy.
    Useful later for injuries/breakouts.
    """
    players = []
    box = summary_json.get("boxscore", {}) if isinstance(summary_json, dict) else {}
    team_groups = box.get("players", [])
    if not isinstance(team_groups, list):
        return players

    for tg in team_groups:
        t = tg.get("team", {})
        if not isinstance(t, dict):
            continue
        if str(t.get("id", "")) != str(team_id):
            continue

        stat_tables = tg.get("statistics", [])
        if not isinstance(stat_tables, list):
            continue

        for table in stat_tables:
            athletes = table.get("athletes", [])
            if not isinstance(athletes, list):
                continue

            labels = table.get("labels", [])
            if not isinstance(labels, list):
                labels = []

            for a in athletes:
                athlete = a.get("athlete", {}) or {}
                name = athlete.get("displayName") or athlete.get("shortName") or athlete.get("fullName") or "Unknown"

                stats = a.get("stats", [])
                if not isinstance(stats, list) or (labels and len(labels) != len(stats)):
                    continue

                lmap = {str(labels[i]).lower(): stats[i] for i in range(len(labels))} if labels else {}

                def pick(*keys):
                    for k in keys:
                        if k in lmap:
                            return lmap[k]
                    return None

                row = {"player": name}
                row["minutes"] = _to_float(pick("min", "minutes"), np.nan)
                row["points"] = _to_int(pick("pts", "points"), 0)

                fg = pick("fg", "field goals")
                ft = pick("ft", "free throws")
                to = pick("to", "tov", "turnovers")
                reb = pick("reb", "rebs", "rebounds")
                ast = pick("ast", "assists")

                fgm, fga = _parse_made_attempt(fg) if isinstance(fg, str) else (0, 0)
                ftm, fta = _parse_made_attempt(ft) if isinstance(ft, str) else (0, 0)

                row["fgm"] = fgm
                row["fga"] = fga
                row["ftm"] = ftm
                row["fta"] = fta
                row["tov"] = _to_int(to, 0)
                row["reb"] = _to_int(reb, 0)
                row["ast"] = _to_int(ast, 0)
                row["usage_proxy"] = row["fga"] + 0.44 * row["fta"] + row["tov"]

                players.append(row)

    return players

# ---------- core fetch/parse ----------
def fetch_and_parse_espn_summary(event_id: str, timeout: int = 25):
    """
    Fetch ESPN summary JSON for a game, parse team box score metrics for both teams.
    Returns a dict with home/away rows + optional player lists.
    """
    url = ESPN_SUMMARY_URL.format(event_id=event_id)
    r = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
    r.raise_for_status()
    data = r.json()

    header = data.get("header", {}) if isinstance(data, dict) else {}
    competitions = header.get("competitions", []) if isinstance(header, dict) else []
    comp0 = competitions[0] if isinstance(competitions, list) and competitions else {}

    game_date = comp0.get("date")  # ISO datetime string
    venue = None
    try:
        venue = comp0.get("venue", {}).get("fullName")
    except Exception:
        venue = None

    box = data.get("boxscore", {}) if isinstance(data, dict) else {}
    teams = box.get("teams", [])
    if not isinstance(teams, list) or len(teams) < 2:
        raise ValueError("Unexpected ESPN summary format: boxscore.teams missing or too short")

    # Determine home/away by team id via header competitors (most reliable)
    home_team_id = None
    away_team_id = None
    competitors = comp0.get("competitors", []) if isinstance(comp0, dict) else []
    if isinstance(competitors, list) and len(competitors) >= 2:
        for c in competitors:
            if c.get("homeAway") == "home":
                home_team_id = str(c.get("team", {}).get("id", ""))
            elif c.get("homeAway") == "away":
                away_team_id = str(c.get("team", {}).get("id", ""))

    def parse_team(team_entry):
        t = team_entry.get("team", {}) or {}
        tid = str(t.get("id", ""))
        name = t.get("displayName") or t.get("shortDisplayName") or t.get("name") or "Unknown"

        stats_list = team_entry.get("teamStats", [])
        smap = _stat_map(stats_list)

        # Shooting lines
        fgm, fga = _parse_made_attempt(smap.get("fieldGoals", ""))
        if fga == 0:
            fga = _to_int(smap.get("fieldGoalsAttempted"), 0)
            fgm = _to_int(smap.get("fieldGoalsMade"), 0)

        tpm, tpa = _parse_made_attempt(smap.get("threePointFieldGoals", ""))
        if tpa == 0:
            tpa = _to_int(smap.get("threePointFieldGoalsAttempted"), 0)
            tpm = _to_int(smap.get("threePointFieldGoalsMade"), 0)

        ftm, fta = _parse_made_attempt(smap.get("freeThrows", ""))
        if fta == 0:
            fta = _to_int(smap.get("freeThrowsAttempted"), 0)
            ftm = _to_int(smap.get("freeThrowsMade"), 0)

        # Other stats
        tov = _to_int(smap.get("turnovers"), 0)
        orb = _to_int(smap.get("reboundsOffensive"), 0)
        drb = _to_int(smap.get("reboundsDefensive"), 0)
        reb = _to_int(smap.get("rebounds"), orb + drb)

        # Derived metrics
        efg = _safe_div((fgm + 0.5 * tpm), fga, np.nan)
        ftr = _safe_div(fta, fga, np.nan)
        threepar = _safe_div(tpa, fga, np.nan)
        poss = _estimate_possessions(fga, fta, tov, orb)

        return {
            "team_id": tid,
            "team": name,
            "fgm": fgm, "fga": fga,
            "tpm": tpm, "tpa": tpa,
            "ftm": ftm, "fta": fta,
            "tov": tov,
            "orb": orb, "drb": drb, "reb": reb,
            "efg": float(efg) if pd.notna(efg) else np.nan,
            "ftr": float(ftr) if pd.notna(ftr) else np.nan,
            "3par": float(threepar) if pd.notna(threepar) else np.nan,
            "poss": float(poss),
        }

    parsed = [parse_team(te) for te in teams]

    # Map to home/away
    if home_team_id and away_team_id:
        home_row = next((x for x in parsed if x["team_id"] == home_team_id), None)
        away_row = next((x for x in parsed if x["team_id"] == away_team_id), None)
        if home_row is None or away_row is None:
            home_row, away_row = parsed[0], parsed[1]
    else:
        home_row, away_row = parsed[0], parsed[1]

    # Compute opponent-dependent metrics
    home_row["orb_pct"] = _safe_div(home_row["orb"], (home_row["orb"] + away_row["drb"]), np.nan)
    away_row["orb_pct"] = _safe_div(away_row["orb"], (away_row["orb"] + home_row["drb"]), np.nan)

    home_row["drb_pct"] = _safe_div(home_row["drb"], (home_row["drb"] + away_row["orb"]), np.nan)
    away_row["drb_pct"] = _safe_div(away_row["drb"], (away_row["drb"] + home_row["orb"]), np.nan)

    home_row["tov_pct"] = _safe_div(home_row["tov"], home_row["poss"], np.nan)
    away_row["tov_pct"] = _safe_div(away_row["tov"], away_row["poss"], np.nan)

    # Player minutes (optional)
    players_home = _extract_players(data, home_row["team_id"])
    players_away = _extract_players(data, away_row["team_id"])

    return {
        "event_id": str(event_id),
        "game_date": game_date,
        "venue": venue,
        "home": home_row,
        "away": away_row,
        "players_home": players_home,
        "players_away": players_away,
    }

def summary_to_team_rows(parsed_summary: dict):
    """
    Flatten parsed_summary into 2 per-team rows for CSV storage.
    """
    event_id = parsed_summary.get("event_id")
    game_date = parsed_summary.get("game_date")
    venue = parsed_summary.get("venue")

    home = parsed_summary["home"].copy()
    away = parsed_summary["away"].copy()

    for row in (home, away):
        row["event_id"] = event_id
        row["game_date"] = game_date
        row["venue"] = venue

    home["opponent"] = away["team"]
    away["opponent"] = home["team"]

    home["home_away"] = "home"
    away["home_away"] = "away"

    return home, away

# ---------- scoreboard event ids ----------
def fetch_scoreboard_event_ids(date_yyyymmdd: str, timeout: int = 25):
    """
    Pulls event IDs for a given date from ESPN scoreboard API.
    """
    url = ESPN_SCOREBOARD_URL.format(date=date_yyyymmdd)
    r = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
    r.raise_for_status()
    data = r.json()

    events = data.get("events", [])
    out = []
    for e in events:
        eid = e.get("id")
        comp = (e.get("competitions") or [{}])[0]
        competitors = comp.get("competitors") or []
        if len(competitors) < 2 or not eid:
            continue

        home = None
        away = None
        for c in competitors:
            ha = c.get("homeAway")
            name = c.get("team", {}).get("displayName")
            if ha == "home":
                home = name
            elif ha == "away":
                away = name

        out.append({
            "event_id": str(eid),
            "game_date": date_yyyymmdd,
            "home_team": home,
            "away_team": away
        })
    return out

# ---------- runner ----------
def build_team_game_logs_csv(days_back=7, out_csv="espn_team_game_logs.csv", verbose=True):
    """
    Builds espn_team_game_logs.csv for the last N days.
    One row per TEAM per GAME.
    """
    from datetime import datetime, timedelta

    rows = []
    today = datetime.now()

    total_events = 0
    failures = 0

    for i in range(days_back):
        d = (today - timedelta(days=i)).strftime("%Y%m%d")
        events = fetch_scoreboard_event_ids(d)
        total_events += len(events)

        if verbose:
            print(f"📅 {d}: {len(events)} events")

        for ev in events:
            try:
                parsed = fetch_and_parse_espn_summary(ev["event_id"])
                home_row, away_row = summary_to_team_rows(parsed)
                rows.append(home_row)
                rows.append(away_row)
            except Exception as e:
                failures += 1
                if verbose:
                    print("FAILED", ev["event_id"], str(e)[:160])

    df = pd.DataFrame(rows)

    keep = [
        "team","opponent","home_away","event_id","game_date","venue",
        "fgm","fga","tpm","tpa","ftm","fta","tov","orb","drb","reb",
        "efg","3par","ftr","orb_pct","drb_pct","tov_pct","poss"
    ]
    df = df[[c for c in keep if c in df.columns]]

    df.to_csv(out_csv, index=False)

    if verbose:
        print("\n✅ DONE")
        print("Total events found:", total_events)
        print("Rows written:", len(df))
        print("Failures:", failures)
        print("Output:", out_csv)

    return df
