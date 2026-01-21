#!/usr/bin/env python3
"""
ESPN CBB Boxscore + Feature Builder (Season-to-date, append forever)

Outputs:
- espn_games.csv                     (scoreboard snapshot, one row per game, append+dedupe)
- espn_team_game_logs.csv            (raw-ish team-game rows + per-game metrics + audit, append+dedupe)
- espn_team_game_features.csv        (pregame rolling features + opponent-adjusted deltas + rest/volatility/style, append+dedupe)
- espn_matchups_model_ready.csv      (one row per game, home/away pregame features + labels, rebuild each run)

Notes:
- "pregame" features are computed WITHOUT leakage (shifted so current game is excluded).
- Default DAYS_BACK is 3 for daily scheduled runs (captures late-posted boxscores).
- For a big backfill run (like last 80 days), set env DAYS_BACK=80 in GitHub Actions.
"""

import os
import time
import hashlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from collections import defaultdict, deque

import requests
import pandas as pd
import numpy as np

# ---------------- config ----------------
ESPN_SUMMARY_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/summary?event={event_id}"
)
ESPN_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?dates={date}"
)

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json,text/plain,*/*",
}

PARSE_VERSION = "v1.3.1"
SOURCE_NAME = "espn"

TZ_PST = ZoneInfo("America/Los_Angeles")

DEFAULT_DAYS_BACK = int(os.getenv("DAYS_BACK", "3"))
REQUEST_TIMEOUT = int(os.getenv("ESPN_TIMEOUT", "25"))

OUT_GAMES = "espn_games.csv"
OUT_TEAM_LOGS = "espn_team_game_logs.csv"
OUT_TEAM_FEATURES = "espn_team_game_features.csv"
OUT_MATCHUPS = "espn_matchups_model_ready.csv"

# ---------------- helpers ----------------
def _utc_now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
    return float(fga + 0.44 * fta - orb + tov)


def _safe_div(num, den, default=np.nan):
    return default if den in (0, 0.0, None) else (num / den)


def _stable_row_hash(d: dict, keys):
    payload = "|".join([str(d.get(k, "")) for k in keys])
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _read_csv_if_exists(path: str) -> pd.DataFrame:
    if os.path.exists(path) and os.path.getsize(path) > 0:
        try:
            return pd.read_csv(path)
        except Exception:
            # if file exists but is malformed, treat as empty to avoid crashing scheduled runs
            return pd.DataFrame()
    return pd.DataFrame()


def _append_dedupe_write(existing_path: str, new_df: pd.DataFrame, subset_keys, sort_cols=None):
    old = _read_csv_if_exists(existing_path)
    if old.empty:
        combined = new_df.copy()
    else:
        combined = pd.concat([old, new_df], ignore_index=True)

    if subset_keys:
        combined = combined.drop_duplicates(subset=subset_keys, keep="last")

    if sort_cols:
        sort_cols_present = [c for c in sort_cols if c in combined.columns]
        if sort_cols_present:
            combined = combined.sort_values(sort_cols_present)

    combined.to_csv(existing_path, index=False)
    return combined


# ---------------- ESPN fetch: scoreboard ----------------
def fetch_scoreboard_games(date_yyyymmdd: str, timeout: int = REQUEST_TIMEOUT):
    url = ESPN_SCOREBOARD_URL.format(date=date_yyyymmdd)
    r = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
    r.raise_for_status()
    data = r.json()

    rows = []
    for e in (data.get("events") or []):
        game_id = e.get("id")
        competitions = e.get("competitions") or []
        comp = competitions[0] if competitions else {}
        competitors = comp.get("competitors") or []

        if not game_id or len(competitors) < 2:
            continue

        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        if not home or not away:
            continue

        status = comp.get("status") or {}
        stype = (status.get("type") or {})
        completed = bool(stype.get("completed"))
        state = stype.get("state") or ""
        detail = stype.get("detail") or ""
        short_detail = stype.get("shortDetail") or ""
        status_desc = stype.get("description") or ""

        game_dt = comp.get("date") or e.get("date")  # iso
        venue = (comp.get("venue") or {}).get("fullName") if isinstance(comp.get("venue"), dict) else None

        home_team = (home.get("team") or {}).get("displayName")
        away_team = (away.get("team") or {}).get("displayName")

        home_score = _to_int(home.get("score"), 0)
        away_score = _to_int(away.get("score"), 0)

        home_win = home.get("winner")
        away_win = away.get("winner")

        rows.append({
            "date": date_yyyymmdd,
            "game_id": str(game_id),
            "game_datetime_utc": game_dt,
            "venue": venue,
            "home_team": home_team,
            "away_team": away_team,
            "home_score": home_score,
            "away_score": away_score,
            "home_win": home_win,
            "away_win": away_win,
            "completed": completed,
            "state": state,
            "status_desc": status_desc,
            "status_detail": detail or short_detail,
            "pulled_at_utc": _utc_now_iso(),
            "source": SOURCE_NAME,
        })

    return rows


def build_espn_games_csv(days_back=DEFAULT_DAYS_BACK, out_csv=OUT_GAMES, verbose=True):
    now_pst = datetime.now(TZ_PST)
    all_rows = []
    for i in range(days_back):
        d = (now_pst - timedelta(days=i)).strftime("%Y%m%d")
        rows = fetch_scoreboard_games(d)
        all_rows.extend(rows)
        if verbose:
            total = len(rows)
            finals = sum(1 for r in rows if r.get("completed"))
            print(f"{d}: {total} games, {finals} final")

    df_new = pd.DataFrame(all_rows)
    if df_new.empty:
        if verbose:
            print("No games returned from scoreboard.")
        return df_new

    # append+dedupe forever
    df_all = _append_dedupe_write(
        out_csv,
        df_new,
        subset_keys=["game_id"],
        sort_cols=["date", "game_id"],
    )
    if verbose:
        print(f"{out_csv} total rows: {len(df_all)}")

    return df_all


# ---------------- ESPN fetch: summary / boxscore ----------------
def _extract_players(summary_json, team_id: str):
    """Lightweight player minutes + usage proxy."""
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


def fetch_and_parse_espn_summary(event_id: str, timeout: int = REQUEST_TIMEOUT):
    """
    Fetch ESPN summary JSON for a game, parse team box score metrics for both teams.
    Returns dict with home/away rows + game metadata.
    """
    url = ESPN_SUMMARY_URL.format(event_id=event_id)
    r = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
    r.raise_for_status()
    data = r.json()

    header = data.get("header", {}) if isinstance(data, dict) else {}
    competitions = header.get("competitions", []) if isinstance(header, dict) else []
    comp0 = competitions[0] if isinstance(competitions, list) and competitions else {}

    game_datetime_utc = comp0.get("date")  # ISO datetime string
    venue = None
    try:
        venue = (comp0.get("venue") or {}).get("fullName")
    except Exception:
        venue = None

    status = comp0.get("status") or {}
    stype = (status.get("type") or {})
    completed = bool(stype.get("completed"))
    state = stype.get("state") or ""
    status_desc = stype.get("description") or ""
    status_detail = stype.get("detail") or stype.get("shortDetail") or ""

    # Identify home/away ids + points via competitors
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
        if fgm == 0:
            fgm = _to_int(smap.get("fieldGoalsMade"), 0)

        tpm, tpa = _parse_made_attempt(smap.get("threePointFieldGoals", ""))
        if tpa == 0:
            tpa = _to_int(smap.get("threePointFieldGoalsAttempted"), 0)
        if tpm == 0:
            tpm = _to_int(smap.get("threePointFieldGoalsMade"), 0)

        ftm, fta = _parse_made_attempt(smap.get("freeThrows", ""))
        if fta == 0:
            fta = _to_int(smap.get("freeThrowsAttempted"), 0)
        if ftm == 0:
            ftm = _to_int(smap.get("freeThrowsMade"), 0)

        tov = _to_int(smap.get("turnovers"), 0)
        orb = _to_int(smap.get("reboundsOffensive"), 0)
        drb = _to_int(smap.get("reboundsDefensive"), 0)
        reb = _to_int(smap.get("rebounds"), orb + drb)

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

    if home_team_id and away_team_id:
        home_row = next((x for x in parsed if x["team_id"] == home_team_id), None)
        away_row = next((x for x in parsed if x["team_id"] == away_team_id), None)
        if home_row is None or away_row is None:
            home_row, away_row = parsed[0], parsed[1]
    else:
        home_row, away_row = parsed[0], parsed[1]

    # opponent-dependent metrics
    home_row["orb_pct"] = _safe_div(home_row["orb"], (home_row["orb"] + away_row["drb"]), np.nan)
    away_row["orb_pct"] = _safe_div(away_row["orb"], (away_row["orb"] + home_row["drb"]), np.nan)

    home_row["drb_pct"] = _safe_div(home_row["drb"], (home_row["drb"] + away_row["orb"]), np.nan)
    away_row["drb_pct"] = _safe_div(away_row["drb"], (away_row["drb"] + home_row["orb"]), np.nan)

    home_row["tov_pct"] = _safe_div(home_row["tov"], home_row["poss"], np.nan)
    away_row["tov_pct"] = _safe_div(away_row["tov"], away_row["poss"], np.nan)

    if home_points is None or away_points is None:
        try:
            home_points = _to_int((competitors[0] or {}).get("score"), 0)
            away_points = _to_int((competitors[1] or {}).get("score"), 0)
        except Exception:
            home_points, away_points = None, None

    home_row["points_for"] = _to_int(home_points, 0)
    away_row["points_for"] = _to_int(away_points, 0)
    home_row["points_against"] = away_row["points_for"]
    away_row["points_against"] = home_row["points_for"]
    home_row["margin"] = home_row["points_for"] - home_row["points_against"]
    away_row["margin"] = away_row["points_for"] - away_row["points_against"]

    return {
        "event_id": str(event_id),
        "game_datetime_utc": game_datetime_utc,
        "venue": venue,
        "completed": completed,
        "state": state,
        "status_desc": status_desc,
        "status_detail": status_detail,
        "home": home_row,
        "away": away_row,
        "players_home": _extract_players(data, home_row["team_id"]),
        "players_away": _extract_players(data, away_row["team_id"]),
    }


def summary_to_team_rows(parsed_summary: dict):
    """Flatten parsed_summary into 2 team-game rows."""
    event_id = parsed_summary.get("event_id")
    game_dt = parsed_summary.get("game_datetime_utc")
    venue = parsed_summary.get("venue")

    meta = {
        "event_id": event_id,
        "game_datetime_utc": game_dt,
        "venue": venue,
        "completed": parsed_summary.get("completed"),
        "state": parsed_summary.get("state"),
        "status_desc": parsed_summary.get("status_desc"),
        "status_detail": parsed_summary.get("status_detail"),
        "pulled_at_utc": _utc_now_iso(),
        "source": SOURCE_NAME,
        "parse_version": PARSE_VERSION,
    }

    home = parsed_summary["home"].copy()
    away = parsed_summary["away"].copy()

    for row in (home, away):
        row.update(meta)

    home["opponent"] = away["team"]
    away["opponent"] = home["team"]

    home["home_away"] = "home"
    away["home_away"] = "away"

    home["home_team"] = home["team"]
    home["away_team"] = away["team"]
    away["home_team"] = home["team"]
    away["away_team"] = away["team"]

    return home, away


# ---------------- rolling feature engineering ----------------
def _compute_per_game_advanced_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds ORtg/DRtg/Net/Pace (poss), blowout, and basic integrity checks.
    Assumes team-game rows include points_for, points_against, poss.
    """
    out = df.copy()

    for c in ["points_for", "points_against", "poss", "fga", "fta", "tov", "orb", "drb", "reb", "margin"]:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")

    out["pace"] = out["poss"]

    out["ortg"] = out.apply(lambda r: _safe_div(r.get("points_for", np.nan) * 100.0, r.get("poss", np.nan), np.nan), axis=1)
    out["drtg"] = out.apply(lambda r: _safe_div(r.get("points_against", np.nan) * 100.0, r.get("poss", np.nan), np.nan), axis=1)
    out["netrtg"] = out["ortg"] - out["drtg"]

    out["blowout"] = (out["margin"].abs() >= 18).astype(int)

    # integrity checks
    out["data_ok"] = True
    out.loc[out["poss"].fillna(0) <= 30, "data_ok"] = False
    out.loc[(out["completed"] == True) & (out["fga"].fillna(0) == 0), "data_ok"] = False

    out["row_hash"] = out.apply(
        lambda r: _stable_row_hash(
            r.to_dict(),
            keys=[
                "event_id", "team_id", "team", "home_away",
                "game_datetime_utc", "points_for", "points_against",
                "fga", "tov", "orb", "poss", "parse_version",
            ],
        ),
        axis=1,
    )
    return out


def _group_shift_rolling(s: pd.Series, window: int, fn: str):
    s2 = s.shift(1)
    if fn == "mean":
        return s2.rolling(window=window, min_periods=1).mean()
    if fn == "std":
        return s2.rolling(window=window, min_periods=2).std(ddof=0)
    raise ValueError("Unsupported fn")


def _group_shift_expanding_mean(s: pd.Series):
    return s.shift(1).expanding(min_periods=1).mean()


def _add_rolling_pack(df: pd.DataFrame, group_cols, prefix: str):
    """
    Adds L3/L7 means + L7 std + season (expanding) means for core metrics.
    """
    out = df.copy()

    core = {
        "ortg": "ortg",
        "drtg": "drtg",
        "netrtg": "netrtg",
        "pace": "pace",
        "efg": "efg",
        "tov_pct": "tov_pct",
        "orb_pct": "orb_pct",
        "drb_pct": "drb_pct",
        "ftr": "ftr",
        "3par": "3par",
    }

    g = out.groupby(group_cols, sort=False)

    for metric, col in core.items():
        if col not in out.columns:
            continue
        out[f"{prefix}{metric}_l3_pre"] = g[col].apply(lambda s: _group_shift_rolling(s, 3, "mean")).reset_index(level=group_cols, drop=True)
        out[f"{prefix}{metric}_l7_pre"] = g[col].apply(lambda s: _group_shift_rolling(s, 7, "mean")).reset_index(level=group_cols, drop=True)
        out[f"{prefix}{metric}_std_l7_pre"] = g[col].apply(lambda s: _group_shift_rolling(s, 7, "std")).reset_index(level=group_cols, drop=True)
        out[f"{prefix}{metric}_season_pre"] = g[col].apply(lambda s: _group_shift_expanding_mean(s)).reset_index(level=group_cols, drop=True)

    return out


def _add_noblow_rollups(df: pd.DataFrame, group_cols, prefix: str):
    """
    Adds L7 no-blowout rollups for ortg/drtg/netrtg.
    """
    out = df.copy()
    for metric in ["ortg", "drtg", "netrtg"]:
        if metric not in out.columns:
            continue
        tmp = out[metric].where(out["blowout"] == 0, np.nan)
        out[f"{prefix}{metric}_l7_noblow_pre"] = out.groupby(group_cols, sort=False)[tmp.name].apply(
            lambda s: s.shift(1).rolling(7, min_periods=1).mean()
        ).reset_index(level=group_cols, drop=True)
    return out


def _time_window_counts_per_team(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds rest and schedule density features:
    - days_since_last_game
    - days_rest (max(days_since_last_game - 1, 0))
    - games_last_7_days (prior games only)
    - back_to_back (days_since_last_game <= 1.5)
    - three_in_six (prior games in last 6 days >= 2)
    """
    out = df.copy()
    out["game_dt"] = pd.to_datetime(out["game_datetime_utc"], utc=True, errors="coerce")
    out = out.sort_values(["team", "game_dt", "event_id"])

    out["prev_game_dt"] = out.groupby("team")["game_dt"].shift(1)
    out["days_since_last_game"] = (out["game_dt"] - out["prev_game_dt"]).dt.total_seconds() / 86400.0
    out["days_rest"] = (out["days_since_last_game"] - 1.0).clip(lower=0)
    out["back_to_back"] = (out["days_since_last_game"].fillna(999) <= 1.5).astype(int)

    games_last_7 = []
    three_in_six = []

    by_team = defaultdict(deque)
    for _, r in out.iterrows():
        team = r["team"]
        dt = r["game_dt"]
        dq = by_team[team]

        cutoff7 = dt - pd.Timedelta(days=7)
        while dq and dq[0] < cutoff7:
            dq.popleft()
        games_last_7.append(len(dq))

        cutoff6 = dt - pd.Timedelta(days=6)
        cnt6 = sum(1 for x in dq if x >= cutoff6)
        three_in_six.append(1 if cnt6 >= 2 else 0)

        dq.append(dt)

    out["games_last_7_days"] = games_last_7
    out["three_in_six"] = three_in_six
    return out.drop(columns=["prev_game_dt"])

def _merge_opponent_rows(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds opponent per-game proxies and opponent pregame rolling fields.
    Requires team-game rows for both teams per event_id.
    """
    out = df.copy()

    out["_key"] = out["event_id"].astype(str) + "|" + out["home_away"].astype(str)
    opp_side = out["home_away"].map({"home": "away", "away": "home"})
    out["_opp_key"] = out["event_id"].astype(str) + "|" + opp_side.astype(str)

    cols = [
        "_key",
        "team", "team_id",
        "points_for", "points_against",
        "efg", "tov_pct", "orb_pct", "drb_pct", "ftr", "3par",
        "ortg", "drtg", "netrtg", "pace",

        # opponent pregame rolling cols
        "ortg_l7_pre", "drtg_l7_pre", "netrtg_l7_pre", "pace_l7_pre",
        "efg_l7_pre", "tov_pct_l7_pre", "orb_pct_l7_pre", "drb_pct_l7_pre", "ftr_l7_pre", "3par_l7_pre",
        "ortg_season_pre", "drtg_season_pre", "netrtg_season_pre", "pace_season_pre",
        "efg_season_pre", "tov_pct_season_pre", "orb_pct_season_pre", "drb_pct_season_pre", "ftr_season_pre", "3par_season_pre",

        # home/away split rollups
        "ha_ortg_l7_pre", "ha_drtg_l7_pre", "ha_netrtg_l7_pre", "ha_pace_l7_pre",
        "ha_efg_l7_pre", "ha_tov_pct_l7_pre", "ha_orb_pct_l7_pre", "ha_drb_pct_l7_pre", "ha_ftr_l7_pre", "ha_3par_l7_pre",
        "ha_ortg_season_pre", "ha_drtg_season_pre", "ha_netrtg_season_pre", "ha_pace_season_pre",
        "ha_efg_season_pre", "ha_tov_pct_season_pre", "ha_orb_pct_season_pre", "ha_drb_pct_season_pre", "ha_ftr_season_pre", "ha_3par_season_pre",

        # defensive allowed rollups (may not exist on first merge pass, that's fine)
        "ftr_allowed_l7_pre", "ftr_allowed_season_pre",
        "efg_allowed_l7_pre", "efg_allowed_season_pre",
    ]
    cols = [c for c in cols if c in out.columns]

    lookup = out[cols].copy()

    # prefix all lookup columns EXCEPT the join key "_key"
    lookup = lookup.rename(columns={c: f"opp_{c}" for c in lookup.columns if c != "_key"})

    # merge opponent row onto each team row
    out = out.merge(lookup, left_on="_opp_key", right_on="_key", how="left")

    # Defensive "allowed" proxies per game (opponent offense)
    out["efg_allowed_game"] = out.get("opp_efg")
    out["ftr_allowed_game"] = out.get("opp_ftr")
    out["tov_forced_game"] = out.get("opp_tov_pct")  # proxy

    return out.drop(columns=["_key_x", "_key_y", "_key", "_opp_key"], errors="ignore")

def _add_allowed_rollups(df: pd.DataFrame) -> pd.DataFrame:
    """
    Builds rolling defensive "allowed" metrics from per-game opponent offense.
    Uses shift so it's pregame.
    """
    out = df.copy()
    if "game_dt" not in out.columns:
        out["game_dt"] = pd.to_datetime(out["game_datetime_utc"], utc=True, errors="coerce")

    out = out.sort_values(["team", "game_dt", "event_id"])
    g = out.groupby("team", sort=False)

    out["ftr_allowed_l7_pre"] = g["ftr_allowed_game"].apply(lambda s: s.shift(1).rolling(7, min_periods=1).mean()).reset_index(level=0, drop=True)
    out["ftr_allowed_season_pre"] = g["ftr_allowed_game"].apply(lambda s: s.shift(1).expanding(min_periods=1).mean()).reset_index(level=0, drop=True)

    out["efg_allowed_l7_pre"] = g["efg_allowed_game"].apply(lambda s: s.shift(1).rolling(7, min_periods=1).mean()).reset_index(level=0, drop=True)
    out["efg_allowed_season_pre"] = g["efg_allowed_game"].apply(lambda s: s.shift(1).expanding(min_periods=1).mean()).reset_index(level=0, drop=True)

    return out


def _add_sos_proxies(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds schedule quality proxies based on opponent pregame ratings.
    """
    out = df.copy()
    out = out.sort_values(["team", "game_dt", "event_id"])
    g = out.groupby("team", sort=False)

    out["opp_netrtg_pre_base"] = out["opp_netrtg_season_pre"]
    out["opp_ortg_pre_base"] = out["opp_ortg_season_pre"]
    out["opp_drtg_pre_base"] = out["opp_opp_drtg_season_pre"] if "opp_opp_drtg_season_pre" in out.columns else out["opp_drtg_season_pre"]

    out["avg_opp_netrtg_l7_pre"] = g["opp_netrtg_pre_base"].apply(lambda s: s.shift(1).rolling(7, min_periods=1).mean()).reset_index(level=0, drop=True)
    out["avg_opp_ortg_l7_pre"] = g["opp_ortg_pre_base"].apply(lambda s: s.shift(1).rolling(7, min_periods=1).mean()).reset_index(level=0, drop=True)
    out["avg_opp_drtg_l7_pre"] = g["opp_drtg_pre_base"].apply(lambda s: s.shift(1).rolling(7, min_periods=1).mean()).reset_index(level=0, drop=True)

    out["sos_season_pre"] = g["opp_netrtg_pre_base"].apply(lambda s: s.shift(1).expanding(min_periods=1).mean()).reset_index(level=0, drop=True)
    return out


def _add_opponent_adjusted_deltas(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds opponent-adjusted deltas for Four Factors + netrtg.
    Includes L7, season, and home/away (ha_) split versions.
    """
    out = df.copy()

    def delta(a, b):
        return a - b

    # L7
    out["netrtg_adj_l7"] = delta(out["netrtg_l7_pre"], out["opp_netrtg_l7_pre"])
    out["efg_adj_l7"] = delta(out["efg_l7_pre"], out["opp_efg_l7_pre"])
    out["tov_adj_l7"] = delta(out["opp_tov_pct_l7_pre"], out["tov_pct_l7_pre"])
    out["orb_adj_l7"] = delta(out["orb_pct_l7_pre"], out["opp_drb_pct_l7_pre"])
    out["ftr_adj_l7"] = delta(out["ftr_l7_pre"], out["opp_ftr_l7_pre"])

    # season
    out["netrtg_adj_season"] = delta(out["netrtg_season_pre"], out["opp_netrtg_season_pre"])
    out["efg_adj_season"] = delta(out["efg_season_pre"], out["opp_efg_season_pre"])
    out["tov_adj_season"] = delta(out["opp_tov_pct_season_pre"], out["tov_pct_season_pre"])
    out["orb_adj_season"] = delta(out["orb_pct_season_pre"], out["opp_drb_pct_season_pre"])
    out["ftr_adj_season"] = delta(out["ftr_season_pre"], out["opp_ftr_season_pre"])

    # home/away split rollups
    out["netrtg_adj_ha_l7"] = delta(out["ha_netrtg_l7_pre"], out["opp_ha_netrtg_l7_pre"])
    out["efg_adj_ha_l7"] = delta(out["ha_efg_l7_pre"], out["opp_ha_efg_l7_pre"])
    out["tov_adj_ha_l7"] = delta(out["opp_ha_tov_pct_l7_pre"], out["ha_tov_pct_l7_pre"])
    out["orb_adj_ha_l7"] = delta(out["ha_orb_pct_l7_pre"], out["opp_ha_drb_pct_l7_pre"])
    out["ftr_adj_ha_l7"] = delta(out["ha_ftr_l7_pre"], out["opp_ha_ftr_l7_pre"])

    out["netrtg_adj_ha_season"] = delta(out["ha_netrtg_season_pre"], out["opp_ha_netrtg_season_pre"])
    out["efg_adj_ha_season"] = delta(out["ha_efg_season_pre"], out["opp_ha_efg_season_pre"])
    out["tov_adj_ha_season"] = delta(out["opp_ha_tov_pct_season_pre"], out["ha_tov_pct_season_pre"])
    out["orb_adj_ha_season"] = delta(out["ha_orb_pct_season_pre"], out["opp_ha_drb_pct_season_pre"])
    out["ftr_adj_ha_season"] = delta(out["ha_ftr_season_pre"], out["opp_ha_ftr_season_pre"])

    return out


def _add_style_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds style fingerprints + matchup distance proxies:
    - style_distance_l7
    - pace_mismatch_l7
    - rim_vs_foul_l7 (team ftr vs opponent ftr_allowed)
    """
    out = df.copy()

    style_cols_team = {
        "3par_l7_pre": "opp_3par_l7_pre",
        "ftr_l7_pre": "opp_ftr_l7_pre",
        "tov_pct_l7_pre": "opp_tov_pct_l7_pre",
        "orb_pct_l7_pre": "opp_orb_pct_l7_pre",
        "pace_l7_pre": "opp_pace_l7_pre",
    }

    diffs = []
    for a, b in style_cols_team.items():
        if a in out.columns and b in out.columns:
            diffs.append((out[a] - out[b]).abs())

    out["style_distance_l7"] = np.nan
    if diffs:
        out["style_distance_l7"] = sum(diffs)

    if "pace_l7_pre" in out.columns and "opp_pace_l7_pre" in out.columns:
        out["pace_mismatch_l7"] = out["pace_l7_pre"] - out["opp_pace_l7_pre"]
    else:
        out["pace_mismatch_l7"] = np.nan

    # rim pressure vs opponent foul/ft allowed proxy (rolling)
    if "ftr_l7_pre" in out.columns and "opp_ftr_allowed_l7_pre" in out.columns:
        out["rim_vs_foul_l7"] = out["ftr_l7_pre"] - out["opp_ftr_allowed_l7_pre"]
    else:
        out["rim_vs_foul_l7"] = np.nan

    return out


# ---------------- matchup table builder ----------------
def build_matchups_model_ready(df_features: pd.DataFrame) -> pd.DataFrame:
    """
    One row per game, with home/away pregame features prefixed h_/a_, plus targets:
    home_points, away_points, home_win
    """
    df = df_features.copy()
    df["game_dt"] = pd.to_datetime(df["game_datetime_utc"], utc=True, errors="coerce")

    home = df[df["home_away"] == "home"].copy()
    away = df[df["home_away"] == "away"].copy()

    keep_base = [
        "event_id", "game_datetime_utc", "venue",
        "home_team", "away_team",
        "points_for", "points_against", "margin",
        "completed", "data_ok",
        "state", "status_desc", "status_detail",
    ]
    keep_base = [c for c in keep_base if c in home.columns]

    # choose pregame feature columns
    feat_cols = [c for c in df.columns if c.endswith("_pre") or c.endswith("_noblow_pre") or c in [
        "days_rest", "days_since_last_game", "games_last_7_days", "back_to_back", "three_in_six",
        "avg_opp_netrtg_l7_pre", "avg_opp_ortg_l7_pre", "avg_opp_drtg_l7_pre", "sos_season_pre",
        "netrtg_adj_l7", "efg_adj_l7", "tov_adj_l7", "orb_adj_l7", "ftr_adj_l7",
        "netrtg_adj_season", "efg_adj_season", "tov_adj_season", "orb_adj_season", "ftr_adj_season",
        "netrtg_adj_ha_l7", "efg_adj_ha_l7", "tov_adj_ha_l7", "orb_adj_ha_l7", "ftr_adj_ha_l7",
        "netrtg_adj_ha_season", "efg_adj_ha_season", "tov_adj_ha_season", "orb_adj_ha_season", "ftr_adj_ha_season",
        "style_distance_l7", "pace_mismatch_l7", "rim_vs_foul_l7",
        "blowout",
        "pulled_at_utc", "parse_version", "source",
    ]]

    feat_cols = [c for c in dict.fromkeys(feat_cols) if c in df.columns]

    # IMPORTANT: event_id must appear only once
    home_keep = keep_base + ["team", "team_id"] + feat_cols
    away_keep = ["event_id"] + ["team", "team_id"] + feat_cols

    home_keep = [c for c in dict.fromkeys(home_keep) if c in home.columns]
    away_keep = [c for c in dict.fromkeys(away_keep) if c in away.columns]

    h = home[home_keep].copy()
    a = away[away_keep].copy()

    h = h.rename(columns={c: f"h_{c}" for c in h.columns if c != "event_id"})
    a = a.rename(columns={c: f"a_{c}" for c in a.columns if c != "event_id"})

    m = h.merge(a, on="event_id", how="inner")

    # targets
    if "h_points_for" in m.columns:
        m["home_points"] = m["h_points_for"]
    if "h_points_against" in m.columns:
        m["away_points"] = m["h_points_against"]

    # home win label (only valid if completed + data_ok)
    if all(c in m.columns for c in ["home_points", "away_points", "h_completed", "h_data_ok"]):
        m["home_win"] = np.where(
            (m["h_completed"] == True) & (m["h_data_ok"] == True),
            (m["home_points"] > m["away_points"]).astype(int),
            np.nan,
        )
    else:
        m["home_win"] = np.nan

    # status column
    if "h_completed" in m.columns:
        m["status"] = np.where(m["h_completed"] == True, "final", "not_final")
    elif "h_state" in m.columns:
        m["status"] = np.where(m["h_state"].astype(str).str.lower().eq("post"), "final", "not_final")
    else:
        m["status"] = "unknown"

    # clean up: keep canonical datetime/venue if present
    if "h_game_datetime_utc" in m.columns:
        m["game_datetime_utc"] = m["h_game_datetime_utc"]
    if "h_venue" in m.columns:
        m["venue"] = m["h_venue"]

    if "game_datetime_utc" in m.columns:
        m["game_dt"] = pd.to_datetime(m["game_datetime_utc"], utc=True, errors="coerce")
        m = m.sort_values(["game_dt", "event_id"]).drop(columns=["game_dt"], errors="ignore")
    else:
        m = m.sort_values(["event_id"])

    return m


# ---------------- end-to-end pipeline ----------------
def run_pipeline(days_back: int = DEFAULT_DAYS_BACK):
    """
    1) Pull scoreboard for last N days
    2) For each game_id, pull summary boxscore and build team-game rows
    3) Append+dedupe raw-ish logs forever
    4) Build rolling pregame features + opponent joins + matchup features
       Append+dedupe features forever
    5) Rebuild matchup model table
    """
    pulled_at = _utc_now_iso()
    print(f"Run started: {pulled_at} | DAYS_BACK={days_back} | PARSE_VERSION={PARSE_VERSION}")

    # 1) scoreboard snapshot (append forever)
    games_df = build_espn_games_csv(days_back=days_back, out_csv=OUT_GAMES, verbose=True)
    if games_df.empty:
        print("No games from scoreboard. Exiting.")
        return

    # Only include games within the run window for summary pulls
    # (games_df contains full history because it's append+dedupe)
    now_pst = datetime.now(TZ_PST)
    window_dates = {(now_pst - timedelta(days=i)).strftime("%Y%m%d") for i in range(days_back)}
    run_window = games_df[games_df["date"].astype(str).isin(window_dates)].copy()

    game_ids = run_window["game_id"].astype(str).unique().tolist()
    print(f"Scoreboard game_ids in run window: {len(game_ids)}")

    # 2) pull summaries and build team-game rows
    team_rows = []
    errors = 0
    for i, gid in enumerate(game_ids, 1):
        try:
            s = fetch_and_parse_espn_summary(gid)
            hrow, arow = summary_to_team_rows(s)
            team_rows.append(hrow)
            team_rows.append(arow)
        except Exception as e:
            errors += 1
            if errors <= 10:
                print(f"[WARN] summary parse failed for event {gid}: {e}")
            continue

        if i % 50 == 0:
            print(f"Parsed {i}/{len(game_ids)} summaries...")

        if days_back >= 30:
            time.sleep(0.15)

    if not team_rows:
        print("No team rows parsed. Exiting.")
        return

    df_logs_new = pd.DataFrame(team_rows)
    df_logs_new["game_dt"] = pd.to_datetime(df_logs_new["game_datetime_utc"], utc=True, errors="coerce")

    # 2b) per-game metrics + audit
    df_logs_new = _compute_per_game_advanced_metrics(df_logs_new)

    # 3) append+dedupe logs forever
    df_logs_all = _append_dedupe_write(
        OUT_TEAM_LOGS,
        df_logs_new.drop(columns=["game_dt"], errors="ignore"),
        subset_keys=["event_id", "team_id"],
        sort_cols=["game_datetime_utc", "event_id", "team", "home_away"],
    )
    print(f"{OUT_TEAM_LOGS} total rows: {len(df_logs_all)}")

    # 4) features build from FULL season-to-date logs
    df = df_logs_all.copy()
    df["game_dt"] = pd.to_datetime(df["game_datetime_utc"], utc=True, errors="coerce")
    df = df.sort_values(["team", "game_dt", "event_id"])

    # rolling packs overall + home/away splits
    df = _add_rolling_pack(df, group_cols=["team"], prefix="")
    df = _add_noblow_rollups(df, group_cols=["team"], prefix="")

    df = _add_rolling_pack(df, group_cols=["team", "home_away"], prefix="ha_")
    df = _add_noblow_rollups(df, group_cols=["team", "home_away"], prefix="ha_")

    # rest/schedule density
    df = _time_window_counts_per_team(df)

    # opponent join first (gives per-game allowed proxies)
    df = _merge_opponent_rows(df)

    # allowed rollups (defense proxies)
    df = _add_allowed_rollups(df)

    # re-merge opponent rows to bring opponent allowed rollups into opp_* fields
    df = _merge_opponent_rows(df)

    # SOS proxies
    df = _add_sos_proxies(df)

    # opponent-adjusted deltas
    df = _add_opponent_adjusted_deltas(df)

    # style fingerprints + matchup features
    df = _add_style_features(df)

    # 4h) append+dedupe features forever (only rows from this run's events)
    new_event_ids = set(df_logs_new["event_id"].astype(str).unique().tolist())
    df_features_new = df[df["event_id"].astype(str).isin(new_event_ids)].copy()

    df_features_all = _append_dedupe_write(
        OUT_TEAM_FEATURES,
        df_features_new.drop(columns=["game_dt"], errors="ignore"),
        subset_keys=["event_id", "team_id"],
        sort_cols=["game_datetime_utc", "event_id", "team", "home_away"],
    )
    print(f"{OUT_TEAM_FEATURES} total rows: {len(df_features_all)}")

    # 5) rebuild matchups model-ready from ALL features
    df_feat_all = df_features_all.copy()
    m = build_matchups_model_ready(df_feat_all)
    m = m.drop_duplicates(subset=["event_id"], keep="last")
    m.to_csv(OUT_MATCHUPS, index=False)
    print(f"{OUT_MATCHUPS} written: {len(m)} rows")

    print(f"Run finished. Summary parse errors: {errors}")


def main():
    run_pipeline(days_back=DEFAULT_DAYS_BACK)


if __name__ == "__main__":
    main()

