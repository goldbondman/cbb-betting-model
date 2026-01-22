#!/usr/bin/env python3
"""
ESPN CBB Boxscore + Feature Builder (Season-to-date, append forever)

Outputs:
- espn_games.csv                     (scoreboard snapshot, one row per game, append+dedupe)
- espn_team_game_logs.csv            (team-game rows + per-game metrics + audit, append+dedupe)
- espn_team_game_features.csv        (pregame rolling features + opponent joins + rest/volatility/style, append+dedupe)
- espn_matchups_model_ready.csv      (one row per game, home/away pregame features + labels, rebuild each run)
- espn_feature_diagnostics.csv       (optional: row-level diagnostics for why features are NaN / sparse)

Notes:
- "pregame" features are computed WITHOUT leakage (shifted so current game is excluded).
- Default DAYS_BACK is 3 for daily scheduled runs (captures late-posted boxscores).
- For a big backfill run (like last 80 days), set env DAYS_BACK=80 in GitHub Actions.

Key hardening updates (v1.3.5):
- Filter data_ok rows BEFORE rolling features (prevents bad boxscores from poisoning rolling stats).
- Add coverage counters (games_played_pre, games_played_ha_pre, games_played_noblow_pre).
- Fail-fast validation for opponent merge symmetry (exactly 2 rows per event_id).
- HA rollup fallbacks ("_eff_pre" columns) backfill ha_* from overall when sample size is sparse.
- Optional diagnostics output (why NaNs exist).
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

PARSE_VERSION = "v1.3.5"
SOURCE_NAME = "espn"

TZ_PST = ZoneInfo("America/Los_Angeles")

DEFAULT_DAYS_BACK = int(os.getenv("DAYS_BACK", "3"))
REQUEST_TIMEOUT = int(os.getenv("ESPN_TIMEOUT", "25"))

OUT_GAMES = "espn_games.csv"
OUT_TEAM_LOGS = "espn_team_game_logs.csv"
OUT_TEAM_FEATURES = "espn_team_game_features.csv"
OUT_MATCHUPS = "espn_matchups_model_ready.csv"
OUT_DIAGNOSTICS = "espn_feature_diagnostics.csv"

WRITE_DIAGNOSTICS = os.getenv("WRITE_DIAGNOSTICS", "1").strip() not in ("0", "false", "False", "no", "NO")


# ---------------- helpers ----------------
def _utc_now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _to_int(x, default=0):
    try:
        if x is None:
            return default
        if isinstance(x, str):
            x = x.strip()
            if x in ("", "--"):
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
            if x in ("", "--"):
                return default
            return float(x)
        return float(x)
    except Exception:
        return default


def _parse_made_attempt(display: str):
    if not display or not isinstance(display, str):
        return (0, 0)
    d = display.strip()
    if d in ("--", ""):
        return (0, 0)
    if "-" in d:
        a, b = d.split("-", 1)
        return (_to_int(a, 0), _to_int(b, 0))
    if "/" in d:
        a, b = d.split("/", 1)
        return (_to_int(a, 0), _to_int(b, 0))
    return (0, 0)


def _stat_map(team_stats_list):
    """
    ESPN sometimes returns team stat rows under:
      - team_entry["teamStats"]
      - team_entry["statistics"]
    Both typically contain dicts like {"name": "...", "displayValue": "..."}.
    """
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
            return pd.DataFrame()
    return pd.DataFrame()


def _normalize_id_series(s: pd.Series) -> pd.Series:
    s2 = s.astype(str)
    s2 = s2.str.replace(r"\.0$", "", regex=True)
    s2 = s2.replace({"nan": np.nan, "None": np.nan})
    return s2


def _append_dedupe_write(existing_path: str, new_df: pd.DataFrame, subset_keys, sort_cols=None):
    old = _read_csv_if_exists(existing_path)
    if old.empty:
        combined = new_df.copy()
    else:
        combined = pd.concat([old, new_df], ignore_index=True)

    if subset_keys:
        for k in subset_keys:
            if k in combined.columns:
                combined[k] = _normalize_id_series(combined[k])
        combined = combined.drop_duplicates(subset=subset_keys, keep="last")

    if sort_cols:
        sort_cols_present = [c for c in sort_cols if c in combined.columns]
        if sort_cols_present:
            combined = combined.sort_values(sort_cols_present)

    combined.to_csv(existing_path, index=False)
    return combined


def _iso_to_game_dates(game_datetime_utc: str):
    """
    Returns (game_date_local_pst, game_date_utc) as YYYY-MM-DD strings.
    """
    if not game_datetime_utc:
        return (None, None)
    dt = pd.to_datetime(game_datetime_utc, utc=True, errors="coerce")
    if pd.isna(dt):
        return (None, None)
    game_date_utc = dt.date().isoformat()
    try:
        dt_pst = dt.tz_convert(TZ_PST)
        game_date_local = dt_pst.date().isoformat()
    except Exception:
        game_date_local = None
    return (game_date_local, game_date_utc)


def _assert_two_rows_per_event(df: pd.DataFrame, label: str):
    """
    Fail-fast if we don't have exactly two rows per event_id.
    This prevents silent opponent merge NaNs and key explosions.
    """
    if "event_id" not in df.columns:
        raise ValueError(f"[{label}] Missing event_id")
    counts = df.groupby("event_id").size()
    bad = counts[counts != 2]
    if len(bad) > 0:
        sample = bad.head(15).to_dict()
        raise ValueError(f"[{label}] Expected exactly 2 rows per event_id. Bad counts sample: {sample}")


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
    """
    Lightweight player mins + usage proxy (and enough stats to use as a fallback total if teamStats are missing).
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
                three = pick("3pt", "3p", "3fg", "3-point fg")
                ft = pick("ft", "free throws")
                to = pick("to", "tov", "turnovers")
                oreb = pick("oreb", "off reb", "offensive rebounds")
                dreb = pick("dreb", "def reb", "defensive rebounds")
                reb = pick("reb", "rebs", "rebounds")
                ast = pick("ast", "assists")

                fgm, fga = _parse_made_attempt(fg) if isinstance(fg, str) else (0, 0)
                tpm, tpa = _parse_made_attempt(three) if isinstance(three, str) else (0, 0)
                ftm, fta = _parse_made_attempt(ft) if isinstance(ft, str) else (0, 0)

                row["fgm"] = fgm
                row["fga"] = fga
                row["tpm"] = tpm
                row["tpa"] = tpa
                row["ftm"] = ftm
                row["fta"] = fta
                row["tov"] = _to_int(to, 0)
                row["orb"] = _to_int(oreb, 0)
                row["drb"] = _to_int(dreb, 0)
                row["reb"] = _to_int(reb, row["orb"] + row["drb"])
                row["ast"] = _to_int(ast, 0)

                row["usage_proxy"] = row["fga"] + 0.44 * row["fta"] + row["tov"]
                players.append(row)

    return players


def _sum_player_totals(players: list):
    """
    Fallback: sum player stats to approximate team totals when teamStats are absent/empty.
    Only used when teamStats-derived totals are obviously missing (e.g., FGA==0 on a completed game).
    """
    if not players:
        return None

    keys = ["fgm", "fga", "tpm", "tpa", "ftm", "fta", "tov", "orb", "drb", "reb"]
    totals = {k: 0 for k in keys}
    for p in players:
        for k in keys:
            totals[k] += _to_int(p.get(k), 0)
    return totals


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

    game_datetime_utc = comp0.get("date")
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

        stats_list = team_entry.get("teamStats") or team_entry.get("statistics") or []
        smap = _stat_map(stats_list)

        # team totals from teamStats/statistics
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

        return {
            "team_id": tid,
            "team": name,
            "fgm": fgm, "fga": fga,
            "tpm": tpm, "tpa": tpa,
            "ftm": ftm, "fta": fta,
            "tov": tov,
            "orb": orb, "drb": drb, "reb": reb,
        }

    parsed = [parse_team(te) for te in teams]

    if home_team_id and away_team_id:
        home_row = next((x for x in parsed if x["team_id"] == home_team_id), None)
        away_row = next((x for x in parsed if x["team_id"] == away_team_id), None)
        if home_row is None or away_row is None:
            home_row, away_row = parsed[0], parsed[1]
    else:
        home_row, away_row = parsed[0], parsed[1]

    # player tables (for fallback)
    players_home = _extract_players(data, home_row["team_id"])
    players_away = _extract_players(data, away_row["team_id"])

    # If team totals are clearly missing on a completed game, try fallback to summed player totals
    def apply_player_fallback(row, players):
        if not completed:
            return row
        # "missing" heuristic: completed but no shot volume
        if _to_int(row.get("fga"), 0) > 0:
            return row
        totals = _sum_player_totals(players)
        if not totals:
            return row
        for k, v in totals.items():
            # only overwrite zeros
            if _to_int(row.get(k), 0) == 0 and _to_int(v, 0) > 0:
                row[k] = _to_int(v, 0)
        return row

    home_row = apply_player_fallback(home_row, players_home)
    away_row = apply_player_fallback(away_row, players_away)

    # derived per-game metrics that do not require opponent
    def add_independent_derivatives(row):
        fgm = _to_int(row.get("fgm"), 0)
        fga = _to_int(row.get("fga"), 0)
        tpm = _to_int(row.get("tpm"), 0)
        tpa = _to_int(row.get("tpa"), 0)
        fta = _to_int(row.get("fta"), 0)
        tov = _to_int(row.get("tov"), 0)
        orb = _to_int(row.get("orb"), 0)

        efg = _safe_div((fgm + 0.5 * tpm), fga, np.nan)
        ftr = _safe_div(fta, fga, np.nan)
        threepar = _safe_div(tpa, fga, np.nan)
        poss = _estimate_possessions(fga, fta, tov, orb)

        row["efg"] = float(efg) if pd.notna(efg) else np.nan
        row["ftr"] = float(ftr) if pd.notna(ftr) else np.nan
        row["3par"] = float(threepar) if pd.notna(threepar) else np.nan
        row["poss"] = float(poss)
        return row

    home_row = add_independent_derivatives(home_row)
    away_row = add_independent_derivatives(away_row)

    # opponent-dependent metrics
    home_row["orb_pct"] = _safe_div(home_row["orb"], (home_row["orb"] + away_row["drb"]), np.nan)
    away_row["orb_pct"] = _safe_div(away_row["orb"], (away_row["orb"] + home_row["drb"]), np.nan)

    home_row["drb_pct"] = _safe_div(home_row["drb"], (home_row["drb"] + away_row["orb"]), np.nan)
    away_row["drb_pct"] = _safe_div(away_row["drb"], (away_row["drb"] + home_row["orb"]), np.nan)

    home_row["tov_pct"] = _safe_div(home_row["tov"], home_row["poss"], np.nan)
    away_row["tov_pct"] = _safe_div(away_row["tov"], away_row["poss"], np.nan)

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
        "players_home": players_home,
        "players_away": players_away,
    }


def summary_to_team_rows(parsed_summary: dict):
    """Flatten parsed_summary into 2 team-game rows."""
    event_id = str(parsed_summary.get("event_id"))
    game_dt = parsed_summary.get("game_datetime_utc")
    venue = parsed_summary.get("venue")

    game_date_local, game_date_utc = _iso_to_game_dates(game_dt)

    meta = {
        "event_id": event_id,
        "game_datetime_utc": game_dt,
        "game_date": game_date_local,      # YYYY-MM-DD in America/Los_Angeles
        "game_date_utc": game_date_utc,    # YYYY-MM-DD UTC
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
        row["event_id"] = str(row.get("event_id", event_id))
        row["team_id"] = str(row.get("team_id", ""))

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
    Adds ORtg/DRtg/Net/Pace (poss), blowout, and integrity checks.
    Assumes team-game rows include points_for, points_against, poss.
    """
    out = df.copy()

    # normalize IDs early
    if "event_id" in out.columns:
        out["event_id"] = _normalize_id_series(out["event_id"])
    if "team_id" in out.columns:
        out["team_id"] = _normalize_id_series(out["team_id"])

    for c in ["points_for", "points_against", "poss", "fga", "fta", "tov", "orb", "drb", "reb", "margin",
              "efg", "ftr", "3par", "tov_pct", "orb_pct", "drb_pct"]:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")

    out["pace"] = out["poss"]

    out["ortg"] = out.apply(lambda r: _safe_div(r.get("points_for", np.nan) * 100.0, r.get("poss", np.nan), np.nan), axis=1)
    out["drtg"] = out.apply(lambda r: _safe_div(r.get("points_against", np.nan) * 100.0, r.get("poss", np.nan), np.nan), axis=1)
    out["netrtg"] = out["ortg"] - out["drtg"]

    out["blowout"] = (out["margin"].abs() >= 18).astype(int)

    # integrity checks (tightened a bit)
    out["data_ok"] = True
    out.loc[out["poss"].fillna(0) <= 40, "data_ok"] = False
    out.loc[(out["completed"] == True) & (out["fga"].fillna(0) == 0), "data_ok"] = False
    out.loc[(out["completed"] == True) & (out["points_for"].fillna(0) == 0) & (out["points_against"].fillna(0) == 0), "data_ok"] = False

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


def _add_coverage_counts(df: pd.DataFrame, group_cols, prefix: str) -> pd.DataFrame:
    """
    Adds prior-game coverage counts so NaNs can be explained as 'not enough history' vs data failure.
    """
    out = df.copy()
    g = out.groupby(group_cols, sort=False)

    # Use ortg as the proxy for a "valid prior game"
    proxy = "ortg" if "ortg" in out.columns else None
    if proxy is None:
        out[f"{prefix}games_played_pre"] = np.nan
        return out

    out[f"{prefix}games_played_pre"] = g[proxy].apply(lambda s: s.shift(1).expanding(min_periods=1).count()).reset_index(level=group_cols, drop=True)
    return out


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

    # coverage counts
    out = _add_coverage_counts(out, group_cols=group_cols, prefix=prefix)

    return out


def _add_noblow_rollups(df: pd.DataFrame, group_cols, prefix: str):
    """
    Adds L7 no-blowout rollups for ortg/drtg/netrtg, and a prior-count coverage.
    """
    out = df.copy()
    if "blowout" not in out.columns:
        out["blowout"] = 0

    for metric in ["ortg", "drtg", "netrtg"]:
        if metric not in out.columns:
            continue

        tmp_col = f"__{metric}_noblow"
        out[tmp_col] = out[metric].where(out["blowout"] == 0, np.nan)

        out[f"{prefix}{metric}_l7_noblow_pre"] = out.groupby(group_cols, sort=False)[tmp_col].apply(
            lambda s: s.shift(1).rolling(7, min_periods=1).mean()
        ).reset_index(level=group_cols, drop=True)

        # count of prior non-blow games used
        out[f"{prefix}games_played_noblow_pre"] = out.groupby(group_cols, sort=False)[tmp_col].apply(
            lambda s: s.shift(1).expanding(min_periods=1).count()
        ).reset_index(level=group_cols, drop=True)

        out = out.drop(columns=[tmp_col], errors="ignore")

    return out


def _time_window_counts_per_team(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds rest and schedule density features (prior games only):
    - days_since_last_game
    - days_rest (max(days_since_last_game - 1, 0))
    - games_last_7_days
    - back_to_back
    - three_in_six
    """
    out = df.copy()
    out["game_dt"] = pd.to_datetime(out["game_datetime_utc"], utc=True, errors="coerce")

    key = "team_id" if "team_id" in out.columns else "team"
    out = out.sort_values([key, "game_dt", "event_id"])

    out["prev_game_dt"] = out.groupby(key)["game_dt"].shift(1)
    out["days_since_last_game"] = (out["game_dt"] - out["prev_game_dt"]).dt.total_seconds() / 86400.0
    out["days_rest"] = (out["days_since_last_game"] - 1.0).clip(lower=0)
    out["back_to_back"] = (out["days_since_last_game"].fillna(999) <= 1.5).astype(int)

    games_last_7 = []
    three_in_six = []

    by_team = defaultdict(deque)
    for _, r in out.iterrows():
        k = r.get(key)
        dt = r.get("game_dt")
        dq = by_team[k]

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
    return out.drop(columns=["prev_game_dt"], errors="ignore")

#!/usr/bin/env python3
"""
ESPN CBB Boxscore + Feature Builder (Season-to-date, append forever)

Outputs:
- espn_games.csv                     (scoreboard snapshot, one row per game, append+dedupe)
- espn_team_game_logs.csv            (team-game rows + per-game metrics + audit, append+dedupe)
- espn_team_game_features.csv        (pregame rolling features + opponent joins + rest/volatility/style, append+dedupe)
- espn_matchups_model_ready.csv      (one row per game, home/away pregame features + labels, rebuild each run)
- espn_feature_diagnostics.csv       (optional: row-level diagnostics for why features are NaN / sparse)

Notes:
- "pregame" features are computed WITHOUT leakage (shifted so current game is excluded).
- Default DAYS_BACK is 3 for daily scheduled runs (captures late-posted boxscores).
- For a big backfill run (like last 80 days), set env DAYS_BACK=80 in GitHub Actions.

Key hardening updates (v1.3.5):
- Filter data_ok rows BEFORE rolling features (prevents bad boxscores from poisoning rolling stats).
- Add coverage counters (games_played_pre, games_played_ha_pre, games_played_noblow_pre).
- Fail-fast validation for opponent merge symmetry (exactly 2 rows per event_id).
- HA rollup fallbacks ("_eff_pre" columns) backfill ha_* from overall when sample size is sparse.
- Optional diagnostics output (why NaNs exist).
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

PARSE_VERSION = "v1.3.5"
SOURCE_NAME = "espn"

TZ_PST = ZoneInfo("America/Los_Angeles")

DEFAULT_DAYS_BACK = int(os.getenv("DAYS_BACK", "3"))
REQUEST_TIMEOUT = int(os.getenv("ESPN_TIMEOUT", "25"))

OUT_GAMES = "espn_games.csv"
OUT_TEAM_LOGS = "espn_team_game_logs.csv"
OUT_TEAM_FEATURES = "espn_team_game_features.csv"
OUT_MATCHUPS = "espn_matchups_model_ready.csv"
OUT_DIAGNOSTICS = "espn_feature_diagnostics.csv"

WRITE_DIAGNOSTICS = os.getenv("WRITE_DIAGNOSTICS", "1").strip() not in ("0", "false", "False", "no", "NO")


# ---------------- helpers ----------------
def _utc_now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _to_int(x, default=0):
    try:
        if x is None:
            return default
        if isinstance(x, str):
            x = x.strip()
            if x in ("", "--"):
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
            if x in ("", "--"):
                return default
            return float(x)
        return float(x)
    except Exception:
        return default


def _parse_made_attempt(display: str):
    if not display or not isinstance(display, str):
        return (0, 0)
    d = display.strip()
    if d in ("--", ""):
        return (0, 0)
    if "-" in d:
        a, b = d.split("-", 1)
        return (_to_int(a, 0), _to_int(b, 0))
    if "/" in d:
        a, b = d.split("/", 1)
        return (_to_int(a, 0), _to_int(b, 0))
    return (0, 0)


def _stat_map(team_stats_list):
    """
    ESPN sometimes returns team stat rows under:
      - team_entry["teamStats"]
      - team_entry["statistics"]
    Both typically contain dicts like {"name": "...", "displayValue": "..."}.
    """
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
            return pd.DataFrame()
    return pd.DataFrame()


def _normalize_id_series(s: pd.Series) -> pd.Series:
    s2 = s.astype(str)
    s2 = s2.str.replace(r"\.0$", "", regex=True)
    s2 = s2.replace({"nan": np.nan, "None": np.nan})
    return s2


def _append_dedupe_write(existing_path: str, new_df: pd.DataFrame, subset_keys, sort_cols=None):
    old = _read_csv_if_exists(existing_path)
    if old.empty:
        combined = new_df.copy()
    else:
        combined = pd.concat([old, new_df], ignore_index=True)

    if subset_keys:
        for k in subset_keys:
            if k in combined.columns:
                combined[k] = _normalize_id_series(combined[k])
        combined = combined.drop_duplicates(subset=subset_keys, keep="last")

    if sort_cols:
        sort_cols_present = [c for c in sort_cols if c in combined.columns]
        if sort_cols_present:
            combined = combined.sort_values(sort_cols_present)

    combined.to_csv(existing_path, index=False)
    return combined


def _iso_to_game_dates(game_datetime_utc: str):
    """
    Returns (game_date_local_pst, game_date_utc) as YYYY-MM-DD strings.
    """
    if not game_datetime_utc:
        return (None, None)
    dt = pd.to_datetime(game_datetime_utc, utc=True, errors="coerce")
    if pd.isna(dt):
        return (None, None)
    game_date_utc = dt.date().isoformat()
    try:
        dt_pst = dt.tz_convert(TZ_PST)
        game_date_local = dt_pst.date().isoformat()
    except Exception:
        game_date_local = None
    return (game_date_local, game_date_utc)


def _assert_two_rows_per_event(df: pd.DataFrame, label: str):
    """
    Fail-fast if we don't have exactly two rows per event_id.
    This prevents silent opponent merge NaNs and key explosions.
    """
    if "event_id" not in df.columns:
        raise ValueError(f"[{label}] Missing event_id")
    counts = df.groupby("event_id").size()
    bad = counts[counts != 2]
    if len(bad) > 0:
        sample = bad.head(15).to_dict()
        raise ValueError(f"[{label}] Expected exactly 2 rows per event_id. Bad counts sample: {sample}")


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
    """
    Lightweight player mins + usage proxy (and enough stats to use as a fallback total if teamStats are missing).
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
                three = pick("3pt", "3p", "3fg", "3-point fg")
                ft = pick("ft", "free throws")
                to = pick("to", "tov", "turnovers")
                oreb = pick("oreb", "off reb", "offensive rebounds")
                dreb = pick("dreb", "def reb", "defensive rebounds")
                reb = pick("reb", "rebs", "rebounds")
                ast = pick("ast", "assists")

                fgm, fga = _parse_made_attempt(fg) if isinstance(fg, str) else (0, 0)
                tpm, tpa = _parse_made_attempt(three) if isinstance(three, str) else (0, 0)
                ftm, fta = _parse_made_attempt(ft) if isinstance(ft, str) else (0, 0)

                row["fgm"] = fgm
                row["fga"] = fga
                row["tpm"] = tpm
                row["tpa"] = tpa
                row["ftm"] = ftm
                row["fta"] = fta
                row["tov"] = _to_int(to, 0)
                row["orb"] = _to_int(oreb, 0)
                row["drb"] = _to_int(dreb, 0)
                row["reb"] = _to_int(reb, row["orb"] + row["drb"])
                row["ast"] = _to_int(ast, 0)

                row["usage_proxy"] = row["fga"] + 0.44 * row["fta"] + row["tov"]
                players.append(row)

    return players


def _sum_player_totals(players: list):
    """
    Fallback: sum player stats to approximate team totals when teamStats are absent/empty.
    Only used when teamStats-derived totals are obviously missing (e.g., FGA==0 on a completed game).
    """
    if not players:
        return None

    keys = ["fgm", "fga", "tpm", "tpa", "ftm", "fta", "tov", "orb", "drb", "reb"]
    totals = {k: 0 for k in keys}
    for p in players:
        for k in keys:
            totals[k] += _to_int(p.get(k), 0)
    return totals


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

    game_datetime_utc = comp0.get("date")
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

        stats_list = team_entry.get("teamStats") or team_entry.get("statistics") or []
        smap = _stat_map(stats_list)

        # team totals from teamStats/statistics
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

        return {
            "team_id": tid,
            "team": name,
            "fgm": fgm, "fga": fga,
            "tpm": tpm, "tpa": tpa,
            "ftm": ftm, "fta": fta,
            "tov": tov,
            "orb": orb, "drb": drb, "reb": reb,
        }

    parsed = [parse_team(te) for te in teams]

    if home_team_id and away_team_id:
        home_row = next((x for x in parsed if x["team_id"] == home_team_id), None)
        away_row = next((x for x in parsed if x["team_id"] == away_team_id), None)
        if home_row is None or away_row is None:
            home_row, away_row = parsed[0], parsed[1]
    else:
        home_row, away_row = parsed[0], parsed[1]

    # player tables (for fallback)
    players_home = _extract_players(data, home_row["team_id"])
    players_away = _extract_players(data, away_row["team_id"])

    # If team totals are clearly missing on a completed game, try fallback to summed player totals
    def apply_player_fallback(row, players):
        if not completed:
            return row
        # "missing" heuristic: completed but no shot volume
        if _to_int(row.get("fga"), 0) > 0:
            return row
        totals = _sum_player_totals(players)
        if not totals:
            return row
        for k, v in totals.items():
            # only overwrite zeros
            if _to_int(row.get(k), 0) == 0 and _to_int(v, 0) > 0:
                row[k] = _to_int(v, 0)
        return row

    home_row = apply_player_fallback(home_row, players_home)
    away_row = apply_player_fallback(away_row, players_away)

    # derived per-game metrics that do not require opponent
    def add_independent_derivatives(row):
        fgm = _to_int(row.get("fgm"), 0)
        fga = _to_int(row.get("fga"), 0)
        tpm = _to_int(row.get("tpm"), 0)
        tpa = _to_int(row.get("tpa"), 0)
        fta = _to_int(row.get("fta"), 0)
        tov = _to_int(row.get("tov"), 0)
        orb = _to_int(row.get("orb"), 0)

        efg = _safe_div((fgm + 0.5 * tpm), fga, np.nan)
        ftr = _safe_div(fta, fga, np.nan)
        threepar = _safe_div(tpa, fga, np.nan)
        poss = _estimate_possessions(fga, fta, tov, orb)

        row["efg"] = float(efg) if pd.notna(efg) else np.nan
        row["ftr"] = float(ftr) if pd.notna(ftr) else np.nan
        row["3par"] = float(threepar) if pd.notna(threepar) else np.nan
        row["poss"] = float(poss)
        return row

    home_row = add_independent_derivatives(home_row)
    away_row = add_independent_derivatives(away_row)

    # opponent-dependent metrics
    home_row["orb_pct"] = _safe_div(home_row["orb"], (home_row["orb"] + away_row["drb"]), np.nan)
    away_row["orb_pct"] = _safe_div(away_row["orb"], (away_row["orb"] + home_row["drb"]), np.nan)

    home_row["drb_pct"] = _safe_div(home_row["drb"], (home_row["drb"] + away_row["orb"]), np.nan)
    away_row["drb_pct"] = _safe_div(away_row["drb"], (away_row["drb"] + home_row["orb"]), np.nan)

    home_row["tov_pct"] = _safe_div(home_row["tov"], home_row["poss"], np.nan)
    away_row["tov_pct"] = _safe_div(away_row["tov"], away_row["poss"], np.nan)

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
        "players_home": players_home,
        "players_away": players_away,
    }


def summary_to_team_rows(parsed_summary: dict):
    """Flatten parsed_summary into 2 team-game rows."""
    event_id = str(parsed_summary.get("event_id"))
    game_dt = parsed_summary.get("game_datetime_utc")
    venue = parsed_summary.get("venue")

    game_date_local, game_date_utc = _iso_to_game_dates(game_dt)

    meta = {
        "event_id": event_id,
        "game_datetime_utc": game_dt,
        "game_date": game_date_local,      # YYYY-MM-DD in America/Los_Angeles
        "game_date_utc": game_date_utc,    # YYYY-MM-DD UTC
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
        row["event_id"] = str(row.get("event_id", event_id))
        row["team_id"] = str(row.get("team_id", ""))

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
    Adds ORtg/DRtg/Net/Pace (poss), blowout, and integrity checks.
    Assumes team-game rows include points_for, points_against, poss.
    """
    out = df.copy()

    # normalize IDs early
    if "event_id" in out.columns:
        out["event_id"] = _normalize_id_series(out["event_id"])
    if "team_id" in out.columns:
        out["team_id"] = _normalize_id_series(out["team_id"])

    for c in ["points_for", "points_against", "poss", "fga", "fta", "tov", "orb", "drb", "reb", "margin",
              "efg", "ftr", "3par", "tov_pct", "orb_pct", "drb_pct"]:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")

    out["pace"] = out["poss"]

    out["ortg"] = out.apply(lambda r: _safe_div(r.get("points_for", np.nan) * 100.0, r.get("poss", np.nan), np.nan), axis=1)
    out["drtg"] = out.apply(lambda r: _safe_div(r.get("points_against", np.nan) * 100.0, r.get("poss", np.nan), np.nan), axis=1)
    out["netrtg"] = out["ortg"] - out["drtg"]

    out["blowout"] = (out["margin"].abs() >= 18).astype(int)

    # integrity checks (tightened a bit)
    out["data_ok"] = True
    out.loc[out["poss"].fillna(0) <= 40, "data_ok"] = False
    out.loc[(out["completed"] == True) & (out["fga"].fillna(0) == 0), "data_ok"] = False
    out.loc[(out["completed"] == True) & (out["points_for"].fillna(0) == 0) & (out["points_against"].fillna(0) == 0), "data_ok"] = False

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


def _add_coverage_counts(df: pd.DataFrame, group_cols, prefix: str) -> pd.DataFrame:
    """
    Adds prior-game coverage counts so NaNs can be explained as 'not enough history' vs data failure.
    """
    out = df.copy()
    g = out.groupby(group_cols, sort=False)

    # Use ortg as the proxy for a "valid prior game"
    proxy = "ortg" if "ortg" in out.columns else None
    if proxy is None:
        out[f"{prefix}games_played_pre"] = np.nan
        return out

    out[f"{prefix}games_played_pre"] = g[proxy].apply(lambda s: s.shift(1).expanding(min_periods=1).count()).reset_index(level=group_cols, drop=True)
    return out


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

    # coverage counts
    out = _add_coverage_counts(out, group_cols=group_cols, prefix=prefix)

    return out


def _add_noblow_rollups(df: pd.DataFrame, group_cols, prefix: str):
    """
    Adds L7 no-blowout rollups for ortg/drtg/netrtg, and a prior-count coverage.
    """
    out = df.copy()
    if "blowout" not in out.columns:
        out["blowout"] = 0

    for metric in ["ortg", "drtg", "netrtg"]:
        if metric not in out.columns:
            continue

        tmp_col = f"__{metric}_noblow"
        out[tmp_col] = out[metric].where(out["blowout"] == 0, np.nan)

        out[f"{prefix}{metric}_l7_noblow_pre"] = out.groupby(group_cols, sort=False)[tmp_col].apply(
            lambda s: s.shift(1).rolling(7, min_periods=1).mean()
        ).reset_index(level=group_cols, drop=True)

        # count of prior non-blow games used
        out[f"{prefix}games_played_noblow_pre"] = out.groupby(group_cols, sort=False)[tmp_col].apply(
            lambda s: s.shift(1).expanding(min_periods=1).count()
        ).reset_index(level=group_cols, drop=True)

        out = out.drop(columns=[tmp_col], errors="ignore")

    return out


def _time_window_counts_per_team(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds rest and schedule density features (prior games only):
    - days_since_last_game
    - days_rest (max(days_since_last_game - 1, 0))
    - games_last_7_days
    - back_to_back
    - three_in_six
    """
    out = df.copy()
    out["game_dt"] = pd.to_datetime(out["game_datetime_utc"], utc=True, errors="coerce")

    key = "team_id" if "team_id" in out.columns else "team"
    out = out.sort_values([key, "game_dt", "event_id"])

    out["prev_game_dt"] = out.groupby(key)["game_dt"].shift(1)
    out["days_since_last_game"] = (out["game_dt"] - out["prev_game_dt"]).dt.total_seconds() / 86400.0
    out["days_rest"] = (out["days_since_last_game"] - 1.0).clip(lower=0)
    out["back_to_back"] = (out["days_since_last_game"].fillna(999) <= 1.5).astype(int)

    games_last_7 = []
    three_in_six = []

    by_team = defaultdict(deque)
    for _, r in out.iterrows():
        k = r.get(key)
        dt = r.get("game_dt")
        dq = by_team[k]

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
    return out.drop(columns=["prev_game_dt"], errors="ignore")

