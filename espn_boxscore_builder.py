import os
import requests
import pandas as pd
import numpy as np

from zoneinfo import ZoneInfo
from datetime import datetime, timedelta

ESPN_SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/summary?event={event_id}"
# groups=50 is critical: this is the Division I group, not rankings/top-25
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?dates={date}&groups=50"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json,text/plain,*/*",
}

# ---------------- helpers ----------------
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

def _as_bool(x):
    if isinstance(x, bool):
        return x
    if x is None:
        return False
    if isinstance(x, str):
        s = x.strip().lower()
        return s in ("true", "t", "1", "yes", "y")
    return bool(x)

# ---------------- scoreboard (D1) ----------------
def fetch_scoreboard_games(date_yyyymmdd: str, timeout: int = 25):
    """
    Returns one row per GAME from ESPN scoreboard with status fields.
    Uses groups=50 to include all Division I games (not top-25 filtered).
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
        status = comp.get("status") or {}
        stype = status.get("type") or {}

        if not game_id or len(competitors) < 2:
            continue

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
            "status_name": stype.get("name"),
            "status_detail": stype.get("detail"),
            "status_completed": _as_bool(stype.get("completed")),
        })

    return rows

def build_espn_games_csv(days_back=7, out_csv="espn_games.csv", verbose=True):
    """
    Append-forever season-to-date games file.
    Dedup by game_id.
    """
    now_pst = datetime.now(ZoneInfo("America/Los_Angeles"))
    all_rows = []

    for i in range(days_back):
        d = (now_pst - timedelta(days=i)).strftime("%Y%m%d")
        rows = fetch_scoreboard_games(d)
        all_rows.extend(rows)
        if verbose:
            print(f"{d}: {len(rows)} games")

    df_new = pd.DataFrame(all_rows)

    if os.path.exists(out_csv):
        df_old = pd.read_csv(out_csv, dtype={"date": str, "game_id": str})
        df = pd.concat([df_old, df_new], ignore_index=True)
    else:
        df = df_new

    if not df.empty:
        df["date"] = df["date"].astype(str)
        df["game_id"] = df["game_id"].astype(str)
        df = df.drop_duplicates(subset=["game_id"]).sort_values(["date", "game_id"])

    df.to_csv(out_csv, index=False)
    print(f"{out_csv} written: {len(df)} rows")
    return df

# ---------------- summary parse ----------------
def fetch_and_parse_espn_summary(event_id: str, timeout: int = 25):
    """
    Fetch ESPN summary JSON for a game, parse team box score metrics for both teams.
    Returns dict with home/away rows.
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

    # Determine home/away team ids and points via header competitors (reliable)
    home_team_id = None
    away_team_id = None
    home_points = None
    away_points = None

    competitors = comp0.get("competitors", []) if isinstance(comp0, dict) else []
    if isinstance(competitors, list) and len(competitors) >= 2:
        for c in competitors:
            ha = c.get("homeAway")
            tid = str((c.get("team") or {}).get("id", ""))
            pts = _to_int(c.get("score"), 0)
            if ha == "home":
                home_team_id = tid
                home_points = pts
            elif ha == "away":
                away_team_id = tid
                away_points = pts

    box = data.get("boxscore", {}) if isinstance(data, dict) else {}
    teams = box.get("teams", [])
    if not isinstance(teams, list) or len(teams) < 2:
        raise ValueError("Unexpected ESPN summary format: boxscore.teams missing or too short")

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
            "points": np.nan,  # filled later from header competitors
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

    # Map to home/away using ids
    if home_team_id and away_team_id:
        home_row = next((x for x in parsed if x["team_id"] == home_team_id), None)
        away_row = next((x for x in parsed if x["team_id"] == away_team_id), None)
        if home_row is None or away_row is None:
            home_row, away_row = parsed[0], parsed[1]
    else:
        home_row, away_row = parsed[0], parsed[1]

    # Fill points
    if home_points is None:
        home_points = np.nan
    if away_points is None:
        away_points = np.nan

    home_row["points"] = home_points
    away_row["points"] = away_points

    # Opponent-dependent metrics
    home_row["orb_pct"] = _safe_div(home_row["orb"], (home_row["orb"] + away_row["drb"]), np.nan)
    away_row["orb_pct"] = _safe_div(away_row["orb"], (away_row["orb"] + home_row["drb"]), np.nan)

    home_row["drb_pct"] = _safe_div(home_row["drb"], (home_row["drb"] + away_row["orb"]), np.nan)
    away_row["drb_pct"] = _safe_div(away_row["drb"], (away_row["drb"] + home_row["orb"]), np.nan)

    home_row["tov_pct"] = _safe_div(home_row["tov"], home_row["poss"], np.nan)
    away_row["tov_pct"] = _safe_div(away_row["tov"], away_row["poss"], np.nan)

    # Pace and efficiency (use shared game possessions estimate)
    game_poss = np.nan
    if pd.notna(home_row["poss"]) and pd.notna(away_row["poss"]):
        game_poss = (float(home_row["poss"]) + float(away_row["poss"])) / 2.0

    home_row["pace"] = game_poss
    away_row["pace"] = game_poss

    home_row["ortg"] = _safe_div(home_row["points"] * 100.0, game_poss, np.nan)
    away_row["ortg"] = _safe_div(away_row["points"] * 100.0, game_poss, np.nan)

    home_row["drtg"] = _safe_div(away_row["points"] * 100.0, game_poss, np.nan)
    away_row["drtg"] = _safe_div(home_row["points"] * 100.0, game_poss, np.nan)

    home_row["netrtg"] = home_row["ortg"] - home_row["drtg"] if pd.notna(home_row["ortg"]) and pd.notna(home_row["drtg"]) else np.nan
    away_row["netrtg"] = away_row["ortg"] - away_row["drtg"] if pd.notna(away_row["ortg"]) and pd.notna(away_row["drtg"]) else np.nan

    return {
        "event_id": str(event_id),
        "game_date": game_date,
        "venue": venue,
        "home": home_row,
        "away": away_row,
    }

def summary_to_team_rows(parsed_summary: dict, status_name=None, status_completed=None):
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
        row["status_name"] = status_name
        row["status_completed"] = status_completed

    home["opponent"] = away["team"]
    away["opponent"] = home["team"]

    home["opponent_points"] = away.get("points", np.nan)
    away["opponent_points"] = home.get("points", np.nan)

    home["home_away"] = "home"
    away["home_away"] = "away"

    return home, away

# ---------------- team logs (append forever) ----------------
def build_team_game_logs_csv(days_back=7, out_csv="espn_team_game_logs.csv", verbose=True):
    """
    Append-forever team game logs.
    Only processes FINAL games (status_completed == True).
    Dedup by (event_id, team_id).
    """
    now_pst = datetime.now(ZoneInfo("America/Los_Angeles"))

    total_games = 0
    final_games = 0
    processed_games = 0
    failures = 0
    skipped_empty_box = 0

    rows = []
    for i in range(days_back):
        d = (now_pst - timedelta(days=i)).strftime("%Y%m%d")
        games = fetch_scoreboard_games(d)
        total_games += len(games)

        finals = [g for g in games if _as_bool(g.get("status_completed"))]
        final_games += len(finals)

        if verbose:
            print(f"📅 {d}: {len(games)} games, {len(finals)} final")

        for g in finals:
            try:
                parsed = fetch_and_parse_espn_summary(g["game_id"])
                home_row, away_row = summary_to_team_rows(
                    parsed,
                    status_name=g.get("status_name"),
                    status_completed=g.get("status_completed"),
                )

                # Guardrail: if ESPN returns an empty boxscore payload for a "final", skip
                if _to_int(home_row.get("fga"), 0) == 0 and _to_int(away_row.get("fga"), 0) == 0:
                    skipped_empty_box += 1
                    continue

                rows.append(home_row)
                rows.append(away_row)
                processed_games += 1
            except Exception as e:
                failures += 1
                if verbose:
                    print("FAILED", g.get("game_id"), str(e)[:180])

    df_new = pd.DataFrame(rows)

    keep = [
        "team_id","team","opponent","home_away","event_id","game_date","venue",
        "status_name","status_completed",
        "points","opponent_points",
        "fgm","fga","tpm","tpa","ftm","fta","tov","orb","drb","reb",
        "efg","3par","ftr","orb_pct","drb_pct","tov_pct","poss",
        "pace","ortg","drtg","netrtg",
    ]
    if not df_new.empty:
        df_new = df_new[[c for c in keep if c in df_new.columns]]

    if os.path.exists(out_csv):
        df_old = pd.read_csv(out_csv, dtype={"event_id": str, "team_id": str})
        df = pd.concat([df_old, df_new], ignore_index=True)
    else:
        df = df_new

    if not df.empty:
        df["event_id"] = df["event_id"].astype(str)
        df["team_id"] = df["team_id"].astype(str)
        df = df.drop_duplicates(subset=["event_id", "team_id"]).sort_values(["game_date", "event_id", "home_away"])

    df.to_csv(out_csv, index=False)

    if verbose:
        print("\n✅ DONE")
        print("Games checked:", total_games)
        print("Final games:", final_games)
        print("Games processed:", processed_games)
        print("Skipped empty boxscores:", skipped_empty_box)
        print("Rows written:", len(df))
        print("Failures:", failures)
        print("Output:", out_csv)

    return df

# ---------------- model-ready snapshot ----------------
def build_model_ready_snapshot(
    in_team_logs_csv="espn_team_game_logs.csv",
    out_csv="espn_team_model_ready.csv",
    verbose=True,
):
    """
    Creates a model-ready snapshot that is separate from raw logs.

    Output rows are still "team-game" rows, but they contain:
    - Rolling averages for the team (overall, home-only, away-only): L3, L7, season-to-date
    - Opponent rolling features merged in (opp_*)

    Rolling features are shifted by 1 game so they represent "pregame form".
    """
    if not os.path.exists(in_team_logs_csv):
        raise FileNotFoundError(f"Missing input file: {in_team_logs_csv}")

    df = pd.read_csv(in_team_logs_csv, dtype={"event_id": str, "team_id": str})
    if df.empty:
        pd.DataFrame().to_csv(out_csv, index=False)
        if verbose:
            print(f"{out_csv} written: 0 rows (empty input)")
        return df

    # Normalize and sort
    df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce", utc=True)
    df = df.sort_values(["team", "game_date", "event_id"]).reset_index(drop=True)

    # Metrics to roll (keep this tight and stable)
    roll_metrics = [
        "ortg","drtg","netrtg","pace",
        "efg","tov_pct","orb_pct","ftr","3par",
        "points","opponent_points",
    ]
    roll_metrics = [c for c in roll_metrics if c in df.columns]

    def _add_rolls(group_df, prefix):
        # L3, L7 rolling means, shifted by 1 (pregame)
        g = group_df.copy()
        for m in roll_metrics:
            g[f"{prefix}{m}_l3"] = g[m].rolling(3, min_periods=1).mean().shift(1)
            g[f"{prefix}{m}_l7"] = g[m].rolling(7, min_periods=1).mean().shift(1)
            g[f"{prefix}{m}_season"] = g[m].expanding(min_periods=1).mean().shift(1)
        return g

    # Overall rolls by team
    df = df.groupby("team", group_keys=False).apply(lambda g: _add_rolls(g, prefix="team_"))

    # Home-only and away-only splits
    df_home = df[df["home_away"] == "home"].copy()
    df_away = df[df["home_away"] == "away"].copy()

    if not df_home.empty:
        df_home = df_home.groupby("team", group_keys=False).apply(lambda g: _add_rolls(g, prefix="home_"))
        home_cols = [c for c in df_home.columns if c.startswith("home_")]
        df = df.merge(df_home[["event_id", "team_id"] + home_cols], on=["event_id", "team_id"], how="left")

    if not df_away.empty:
        df_away = df_away.groupby("team", group_keys=False).apply(lambda g: _add_rolls(g, prefix="away_"))
        away_cols = [c for c in df_away.columns if c.startswith("away_")]
        df = df.merge(df_away[["event_id", "team_id"] + away_cols], on=["event_id", "team_id"], how="left")

    # Opponent rolling features: build a join table keyed by (event_id, opponent team name)
    # Then merge onto each team row as opp_*
    feature_cols = [c for c in df.columns if c.startswith("team_") or c.startswith("home_") or c.startswith("away_")]

    opp_src = df[["event_id", "team", "team_id"] + feature_cols].copy()
    opp_src = opp_src.rename(columns={"team": "opponent"})
    opp_src = opp_src.rename(columns={c: f"opp_{c}" for c in feature_cols})

    df = df.merge(opp_src, on=["event_id", "opponent"], how="left")

    # Final column order (keep core ids first)
    core = [
        "event_id","game_date","team_id","team","opponent","home_away",
        "status_name","status_completed",
        "points","opponent_points",
        "pace","ortg","drtg","netrtg",
        "efg","tov_pct","orb_pct","ftr","3par",
    ]
    core = [c for c in core if c in df.columns]
    rest = [c for c in df.columns if c not in core]
    df_out = df[core + rest].copy()

    # Write snapshot
    df_out.to_csv(out_csv, index=False)
    if verbose:
        print(f"{out_csv} written: {len(df_out)} rows")

    return df_out

# ---------------- main ----------------
def _env_int(name, default):
    v = os.environ.get(name)
    if v is None or str(v).strip() == "":
        return default
    try:
        return int(str(v).strip())
    except Exception:
        return default

if __name__ == "__main__":
    days_back = _env_int("DAYS_BACK", 3)

    build_espn_games_csv(days_back=days_back, out_csv="espn_games.csv", verbose=True)
    build_team_game_logs_csv(days_back=days_back, out_csv="espn_team_game_logs.csv", verbose=True)

    # Always refresh model-ready snapshot from the full raw file
    build_model_ready_snapshot(
        in_team_logs_csv="espn_team_game_logs.csv",
        out_csv="espn_team_model_ready.csv",
        verbose=True,
    )
