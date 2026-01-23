#!/usr/bin/env python3
"""
ESPN CBB Boxscore + Feature Builder (Season-to-date, append forever)

Outputs:
- espn_games.csv                     (scoreboard snapshot, one row per game, append+dedupe)
- espn_team_game_logs.csv            (team-game rows + per-game metrics + audit, append+dedupe)
- espn_team_game_features.csv        (pregame rolling features + opponent joins + rest/volatility/style, append+dedupe)
- espn_matchups_model_ready.csv      (one row per game, home/away pregame features + labels, rebuild each run)
- espn_feature_diagnostics.csv       (row-level diagnostics for sparse/NaN fields)
- espn_dq_audit.csv                  (Data Quality Repair Gate audit, per-row reasons + actions)

Key guarantees:
- Pregame features are leak-free (shifted so current game is excluded).
- Bad boxscores do not poison rolling stats (data_ok filter for rollup history).
- Opponent merge is validated (event symmetry, merge validation).
- Deterministic dedupe (completeness score prevents overwriting good rows with partial ones).
- Segmented pipeline with gates; if a gate fails, we drop bad games and proceed (daily automation safe).
- Step 11: Data Quality Repair Gate (DQRG) attempts self-heal when raw inputs exist but derived fields are missing.
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

PARSE_VERSION = "v1.4.0"
SOURCE_NAME = "espn"
TZ_PST = ZoneInfo("America/Los_Angeles")

DEFAULT_DAYS_BACK = int(os.getenv("DAYS_BACK", "3"))
REQUEST_TIMEOUT = int(os.getenv("ESPN_TIMEOUT", "25"))

OUT_GAMES = "espn_games.csv"
OUT_TEAM_LOGS = "espn_team_game_logs.csv"
OUT_TEAM_FEATURES = "espn_team_game_features.csv"
OUT_MATCHUPS = "espn_matchups_model_ready.csv"
OUT_DIAGNOSTICS = "espn_feature_diagnostics.csv"
OUT_DQ_AUDIT = "espn_dq_audit.csv"

WRITE_DIAGNOSTICS = os.getenv("WRITE_DIAGNOSTICS", "1").strip() not in ("0", "false", "False", "no", "NO")
WRITE_DQ_AUDIT = os.getenv("WRITE_DQ_AUDIT", "1").strip() not in ("0", "false", "False", "no", "NO")

# Gates (daily automation defaults)
GATE_MIN_OPP_JOIN_RATE_FINAL = float(os.getenv("GATE_MIN_OPP_JOIN_RATE_FINAL", "0.985"))
GATE_MIN_POSS_PRESENT_FINAL = float(os.getenv("GATE_MIN_POSS_PRESENT_FINAL", "0.985"))
GATE_MIN_EXPECTED_PRESENT_FINAL = float(os.getenv("GATE_MIN_EXPECTED_PRESENT_FINAL", "0.970"))

# Repair attempts
RETRY_SUMMARY_ON_BASE_MISS = int(os.getenv("RETRY_SUMMARY_ON_BASE_MISS", "1"))
MAX_SUMMARY_RETRIES = int(os.getenv("MAX_SUMMARY_RETRIES", "1"))
SUMMARY_RETRY_SLEEP_SEC = float(os.getenv("SUMMARY_RETRY_SLEEP_SEC", "0.35"))


# ---------------- helpers ----------------
def _utc_now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ensure_csv_exists(path: str, columns: list):
    """
    Guarantees a file exists so CI steps and artifact uploads never fail due to missing outputs.
    Writes an empty CSV with headers if missing.
    """
    if os.path.exists(path):
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    pd.DataFrame(columns=columns).to_csv(path, index=False)


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
    if "event_id" not in df.columns:
        raise ValueError(f"[{label}] Missing event_id")
    counts = df.groupby("event_id").size()
    bad = counts[counts != 2]
    if len(bad) > 0:
        sample = bad.head(15).to_dict()
        raise ValueError(f"[{label}] Expected exactly 2 rows per event_id. Bad counts sample: {sample}")


def _drop_bad_event_ids_keep_good(df: pd.DataFrame, label: str):
    """
    Daily automation safe: drop event_ids that do not have exactly 2 rows and continue.
    """
    if "event_id" not in df.columns:
        return df
    counts = df.groupby("event_id").size()
    good_ids = counts[counts == 2].index
    bad_ids = counts[counts != 2].index
    if len(bad_ids) > 0:
        print(f"[WARN] {label}: dropping {len(bad_ids)} event_ids not exactly 2 rows. Sample:", counts.loc[bad_ids].head(15).to_dict())
    return df[df["event_id"].isin(good_ids)].copy()


def _completeness_score_row(r: pd.Series) -> float:
    """
    Deterministic row quality score to prevent overwriting good rows with partial rows.
    """
    completed = 1.0 if bool(r.get("completed")) else 0.0
    data_ok = 1.0 if bool(r.get("data_ok")) else 0.0

    critical = ["points_for", "points_against", "fga", "fta", "tov", "orb", "drb", "poss"]
    present = 0.0
    for c in critical:
        v = r.get(c, np.nan)
        if pd.notna(v) and not (isinstance(v, (int, float)) and float(v) == 0.0 and c in ("fga", "poss") and completed == 1.0):
            present += 1.0
    critical_frac = present / float(len(critical))

    # pulled_at as tie-breaker
    pulled = r.get("pulled_at_utc")
    pulled_bonus = 0.0
    try:
        if isinstance(pulled, str) and pulled:
            pulled_bonus = 0.05
    except Exception:
        pulled_bonus = 0.0

    return (2.0 * completed) + (2.0 * data_ok) + (1.0 * critical_frac) + pulled_bonus


def _dedupe_by_completeness(df: pd.DataFrame, keys: list, label: str) -> pd.DataFrame:
    """
    Choose the best row per key group by completeness score.
    """
    out = df.copy()
    for k in keys:
        if k in out.columns:
            out[k] = _normalize_id_series(out[k])

    out["_dq_score"] = out.apply(_completeness_score_row, axis=1)
    out = out.sort_values(keys + ["_dq_score"], ascending=[True]*len(keys) + [False])
    out = out.drop_duplicates(subset=keys, keep="first").drop(columns=["_dq_score"], errors="ignore")
    print(f"{label}: deduped to {len(out)} rows using completeness score on keys={keys}")
    return out


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

        game_dt = comp.get("date") or e.get("date")
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
    Includes ESPN Recovery attempt (retry) when base totals are missing on a completed game.
    """
    def _fetch_once(eid: str):
        url = ESPN_SUMMARY_URL.format(event_id=eid)
        r = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
        r.raise_for_status()
        return r.json()

    data = _fetch_once(event_id)

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

    players_home = _extract_players(data, home_row["team_id"])
    players_away = _extract_players(data, away_row["team_id"])

    def apply_player_fallback(row, players):
        if not completed:
            return row, False
        if _to_int(row.get("fga"), 0) > 0:
            return row, False
        totals = _sum_player_totals(players)
        if not totals:
            return row, False
        changed = False
        for k, v in totals.items():
            if _to_int(row.get(k), 0) == 0 and _to_int(v, 0) > 0:
                row[k] = _to_int(v, 0)
                changed = True
        return row, changed

    home_row, home_fallback_changed = apply_player_fallback(home_row, players_home)
    away_row, away_fallback_changed = apply_player_fallback(away_row, players_away)

    # Optional one retry if completed game still has missing base totals after player-sum fallback
    if completed and RETRY_SUMMARY_ON_BASE_MISS:
        base_missing = (_to_int(home_row.get("fga"), 0) == 0) or (_to_int(away_row.get("fga"), 0) == 0)
        if base_missing and MAX_SUMMARY_RETRIES > 0:
            for _ in range(MAX_SUMMARY_RETRIES):
                time.sleep(SUMMARY_RETRY_SLEEP_SEC)
                data2 = _fetch_once(event_id)
                box2 = data2.get("boxscore", {}) if isinstance(data2, dict) else {}
                teams2 = box2.get("teams", [])
                if isinstance(teams2, list) and len(teams2) >= 2:
                    parsed2 = [parse_team(te) for te in teams2]
                    # reselect home/away
                    if home_team_id and away_team_id:
                        h2 = next((x for x in parsed2 if x["team_id"] == home_team_id), None)
                        a2 = next((x for x in parsed2 if x["team_id"] == away_team_id), None)
                        if h2 and a2:
                            home_row, away_row = h2, a2
                        else:
                            home_row, away_row = parsed2[0], parsed2[1]
                    else:
                        home_row, away_row = parsed2[0], parsed2[1]
                # break if fixed
                if (_to_int(home_row.get("fga"), 0) > 0) and (_to_int(away_row.get("fga"), 0) > 0):
                    break

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

    # lineage markers
    home_row["poss_source"] = "derived"
    away_row["poss_source"] = "derived"
    home_row["efg_source"] = "derived"
    away_row["efg_source"] = "derived"

    if completed and home_fallback_changed:
        home_row["base_totals_source"] = "player_sum"
    else:
        home_row["base_totals_source"] = "team_stats"

    if completed and away_fallback_changed:
        away_row["base_totals_source"] = "player_sum"
    else:
        away_row["base_totals_source"] = "team_stats"

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
    event_id = str(parsed_summary.get("event_id"))
    game_dt = parsed_summary.get("game_datetime_utc")
    venue = parsed_summary.get("venue")

    game_date_local, game_date_utc = _iso_to_game_dates(game_dt)

    meta = {
        "event_id": event_id,
        "game_datetime_utc": game_dt,
        "game_date": game_date_local,
        "game_date_utc": game_date_utc,
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

    if "event_id" in out.columns:
        out["event_id"] = _normalize_id_series(out["event_id"])
    if "team_id" in out.columns:
        out["team_id"] = _normalize_id_series(out["team_id"])

    numeric_cols = [
        "points_for", "points_against", "poss", "fga", "fta", "tov", "orb", "drb", "reb", "margin",
        "efg", "ftr", "3par", "tov_pct", "orb_pct", "drb_pct",
    ]
    for c in numeric_cols:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")

    out["pace"] = out["poss"]

    out["ortg"] = out.apply(lambda r: _safe_div(r.get("points_for", np.nan) * 100.0, r.get("poss", np.nan), np.nan), axis=1)
    out["drtg"] = out.apply(lambda r: _safe_div(r.get("points_against", np.nan) * 100.0, r.get("poss", np.nan), np.nan), axis=1)
    out["netrtg"] = out["ortg"] - out["drtg"]

    out["blowout"] = (out["margin"].abs() >= 18).astype(int)

    # integrity checks
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

    # lineage markers for advanced ratings
    out["ortg_source"] = np.where(out["ortg"].notna(), "derived", np.nan)
    out["drtg_source"] = np.where(out["drtg"].notna(), "derived", np.nan)
    out["netrtg_source"] = np.where(out["netrtg"].notna(), "derived", np.nan)

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
    out = df.copy()
    g = out.groupby(group_cols, sort=False)
    proxy = "ortg" if "ortg" in out.columns else None
    if proxy is None:
        out[f"{prefix}games_played_pre"] = np.nan
        return out
    out[f"{prefix}games_played_pre"] = g[proxy].apply(lambda s: s.shift(1).expanding(min_periods=1).count()).reset_index(level=group_cols, drop=True)
    return out


def _add_rolling_pack(df: pd.DataFrame, group_cols, prefix: str):
    """
    Adds L3/L7 means + L7 std + season (expanding) means for core metrics.
    Guaranteed column creation even when upstream values are missing.
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
            out[col] = np.nan
        else:
            out[col] = pd.to_numeric(out[col], errors="coerce")

        out[f"{prefix}{metric}_l3_pre"] = g[col].apply(lambda s: _group_shift_rolling(s, 3, "mean")).reset_index(level=group_cols, drop=True)
        out[f"{prefix}{metric}_l7_pre"] = g[col].apply(lambda s: _group_shift_rolling(s, 7, "mean")).reset_index(level=group_cols, drop=True)
        out[f"{prefix}{metric}_std_l7_pre"] = g[col].apply(lambda s: _group_shift_rolling(s, 7, "std")).reset_index(level=group_cols, drop=True)
        out[f"{prefix}{metric}_season_pre"] = g[col].apply(lambda s: _group_shift_expanding_mean(s)).reset_index(level=group_cols, drop=True)

    out = _add_coverage_counts(out, group_cols=group_cols, prefix=prefix)
    return out


def _add_noblow_rollups(df: pd.DataFrame, group_cols, prefix: str):
    out = df.copy()
    if "blowout" not in out.columns:
        out["blowout"] = 0

    for metric in ["ortg", "drtg", "netrtg"]:
        if metric not in out.columns:
            out[metric] = np.nan

        tmp_col = f"__{metric}_noblow"
        out[tmp_col] = out[metric].where(out["blowout"] == 0, np.nan)

        out[f"{prefix}{metric}_l7_noblow_pre"] = out.groupby(group_cols, sort=False)[tmp_col].confirm  # type: ignore
        out[f"{prefix}{metric}_l7_noblow_pre"] = out.groupby(group_cols, sort=False)[tmp_col].apply(
            lambda s: s.shift(1).rolling(7, min_periods=1).mean()
        ).reset_index(level=group_cols, drop=True)

        out[f"{prefix}games_played_noblow_pre"] = out.groupby(group_cols, sort=False)[tmp_col].apply(
            lambda s: s.shift(1).expanding(min_periods=1).count()
        ).reset_index(level=group_cols, drop=True)

        out = out.drop(columns=[tmp_col], errors="ignore")

    return out


def _time_window_counts_per_team(df: pd.DataFrame) -> pd.DataFrame:
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


def _merge_opponent_rows(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds opponent per-game values and opponent pregame rolling fields.
    Assumes exactly 2 rows per event_id (home + away). Use _drop_bad_event_ids_keep_good before this.
    """
    out = df.copy()

    for c in ["event_id", "home_away"]:
        if c not in out.columns:
            raise ValueError(f"_merge_opponent_rows requires column: {c}")

    out["event_id"] = _normalize_id_series(out["event_id"])

    # drop any existing opp_* to prevent suffix drift
    out = out.drop(columns=[c for c in out.columns if c.startswith("opp_")], errors="ignore")

    out["_key"] = out["event_id"].astype(str) + "|" + out["home_away"].astype(str)
    out["_opp_key"] = out["event_id"].astype(str) + "|" + out["home_away"].map({"home": "away", "away": "home"}).astype(str)

    out = out.drop_duplicates(subset=["_key"], keep="last")

    candidate_cols = [
        "_key",
        "team", "team_id",
        "points_for", "points_against",
        "efg", "tov_pct", "orb_pct", "drb_pct", "ftr", "3par",
        "ortg", "drtg", "netrtg", "pace",

        # overall rollups
        "ortg_l3_pre", "ortg_l7_pre", "ortg_std_l7_pre", "ortg_season_pre",
        "drtg_l3_pre", "drtg_l7_pre", "drtg_std_l7_pre", "drtg_season_pre",
        "netrtg_l3_pre", "netrtg_l7_pre", "netrtg_std_l7_pre", "netrtg_season_pre",
        "pace_l3_pre", "pace_l7_pre", "pace_std_l7_pre", "pace_season_pre",
        "efg_l3_pre", "efg_l7_pre", "efg_std_l7_pre", "efg_season_pre",
        "tov_pct_l3_pre", "tov_pct_l7_pre", "tov_pct_std_l7_pre", "tov_pct_season_pre",
        "orb_pct_l3_pre", "orb_pct_l7_pre", "orb_pct_std_l7_pre", "orb_pct_season_pre",
        "drb_pct_l3_pre", "drb_pct_l7_pre", "drb_pct_std_l7_pre", "drb_pct_season_pre",
        "ftr_l3_pre", "ftr_l7_pre", "ftr_std_l7_pre", "ftr_season_pre",
        "3par_l3_pre", "3par_l7_pre", "3par_std_l7_pre", "3par_season_pre",

        "ortg_l7_noblow_pre", "drtg_l7_noblow_pre", "netrtg_l7_noblow_pre",

        # home/away rollups
        "ha_ortg_l3_pre", "ha_ortg_l7_pre", "ha_ortg_std_l7_pre", "ha_ortg_season_pre",
        "ha_drtg_l3_pre", "ha_drtg_l7_pre", "ha_drtg_std_l7_pre", "ha_drtg_season_pre",
        "ha_netrtg_l3_pre", "ha_netrtg_l7_pre", "ha_netrtg_std_l7_pre", "ha_netrtg_season_pre",
        "ha_pace_l3_pre", "ha_pace_l7_pre", "ha_pace_std_l7_pre", "ha_pace_season_pre",
        "ha_efg_l3_pre", "ha_efg_l7_pre", "ha_efg_std_l7_pre", "ha_efg_season_pre",
        "ha_tov_pct_l3_pre", "ha_tov_pct_l7_pre", "ha_tov_pct_std_l7_pre", "ha_tov_pct_season_pre",
        "ha_orb_pct_l3_pre", "ha_orb_pct_l7_pre", "ha_orb_pct_std_l7_pre", "ha_orb_pct_season_pre",
        "ha_drb_pct_l3_pre", "ha_drb_pct_l7_pre", "ha_drb_pct_std_l7_pre", "ha_drb_pct_season_pre",
        "ha_ftr_l3_pre", "ha_ftr_l7_pre", "ha_ftr_std_l7_pre", "ha_ftr_season_pre",
        "ha_3par_l3_pre", "ha_3par_l7_pre", "ha_3par_std_l7_pre", "ha_3par_season_pre",

        "ha_ortg_l7_noblow_pre", "ha_drtg_l7_noblow_pre", "ha_netrtg_l7_noblow_pre",

        "games_played_pre", "ha_games_played_pre",
        "games_played_noblow_pre", "ha_games_played_noblow_pre",

        "ftr_allowed_l7_pre", "ftr_allowed_season_pre",
        "efg_allowed_l7_pre", "efg_allowed_season_pre",
    ]
    cols = [c for c in candidate_cols if c in out.columns]
    lookup = out[cols].copy()

    lookup = lookup.rename(columns={"_key": "_lookup_key"})
    lookup = lookup.rename(columns={c: f"opp_{c}" for c in lookup.columns if c != "_lookup_key"})

    out = out.merge(
        lookup,
        left_on="_opp_key",
        right_on="_lookup_key",
        how="left",
        validate="many_to_one",
    ).drop(columns=["_lookup_key"], errors="ignore")

    # per-game defensive proxies
    out["efg_allowed_game"] = out["opp_efg"] if "opp_efg" in out.columns else np.nan
    out["ftr_allowed_game"] = out["opp_ftr"] if "opp_ftr" in out.columns else np.nan
    out["tov_forced_game"] = out["opp_tov_pct"] if "opp_tov_pct" in out.columns else np.nan

    # lineage
    out["opp_join_ok"] = out["opp_team_id"].notna() if "opp_team_id" in out.columns else out["opp_team"].notna() if "opp_team" in out.columns else False
    out["opp_join_source"] = np.where(out["opp_join_ok"] == True, "merge", np.nan)

    return out.drop(columns=["_key", "_opp_key"], errors="ignore")


def _add_allowed_rollups(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["game_dt"] = pd.to_datetime(out["game_datetime_utc"], utc=True, errors="coerce")

    key = "team_id" if "team_id" in out.columns else "team"
    out = out.sort_values([key, "game_dt", "event_id"])
    g = out.groupby(key, sort=False)

    out["ftr_allowed_l7_pre"] = g["ftr_allowed_game"].apply(lambda s: s.shift(1).rolling(7, min_periods=1).mean()).reset_index(level=0, drop=True)
    out["ftr_allowed_season_pre"] = g["ftr_allowed_game"].apply(lambda s: s.shift(1).expanding(min_periods=1).mean()).reset_index(level=0, drop=True)

    out["efg_allowed_l7_pre"] = g["efg_allowed_game"].apply(lambda s: s.shift(1).rolling(7, min_periods=1).mean()).reset_index(level=0, drop=True)
    out["efg_allowed_season_pre"] = g["efg_allowed_game"].apply(lambda s: s.shift(1).expanding(min_periods=1).mean()).reset_index(level=0, drop=True)

    out["allowed_source"] = "derived"
    return out


def _add_sos_proxies(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["game_dt"] = pd.to_datetime(out["game_datetime_utc"], utc=True, errors="coerce")

    key = "team_id" if "team_id" in out.columns else "team"
    out = out.sort_values([key, "game_dt", "event_id"])
    g = out.groupby(key, sort=False)

    def pick_col(*candidates):
        for c in candidates:
            if c in out.columns:
                return c
        return None

    net_base_col = pick_col("opp_netrtg_season_pre", "opp_netrtg_l7_pre", "opp_netrtg")
    ort_base_col = pick_col("opp_ortg_season_pre", "opp_ortg_l7_pre", "opp_ortg")
    drt_base_col = pick_col("opp_drtg_season_pre", "opp_drtg_l7_pre", "opp_drtg")

    out["opp_netrtg_pre_base"] = out[net_base_col] if net_base_col else np.nan
    out["opp_ortg_pre_base"] = out[ort_base_col] if ort_base_col else np.nan
    out["opp_drtg_pre_base"] = out[drt_base_col] if drt_base_col else np.nan

    out["avg_opp_netrtg_l7_pre"] = g["opp_netrtg_pre_base"].apply(lambda s: s.shift(1).rolling(7, min_periods=1).mean()).reset_index(level=0, drop=True)
    out["avg_opp_ortg_l7_pre"] = g["opp_ortg_pre_base"].apply(lambda s: s.shift(1).rolling(7, min_periods=1).mean()).reset_index(level=0, drop=True)
    out["avg_opp_drtg_l7_pre"] = g["opp_drtg_pre_base"].apply(lambda s: s.shift(1).rolling(7, min_periods=1).mean()).reset_index(level=0, drop=True)

    out["sos_season_pre"] = g["opp_netrtg_pre_base"].apply(lambda s: s.shift(1).expanding(min_periods=1).mean()).reset_index(level=0, drop=True)

    out["sos_source"] = "derived"
    return out


def _add_opponent_adjusted_deltas(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    def delta(a, b):
        return a - b

    def need(col):
        if col not in out.columns:
            out[col] = np.nan

    required = [
        "netrtg_l7_pre", "opp_netrtg_l7_pre",
        "efg_l7_pre", "opp_efg_l7_pre",
        "tov_pct_l7_pre", "opp_tov_pct_l7_pre",
        "orb_pct_l7_pre", "opp_drb_pct_l7_pre",
        "ftr_l7_pre", "opp_ftr_l7_pre",

        "netrtg_season_pre", "opp_netrtg_season_pre",
        "efg_season_pre", "opp_efg_season_pre",
        "tov_pct_season_pre", "opp_tov_pct_season_pre",
        "orb_pct_season_pre", "opp_drb_pct_season_pre",
        "ftr_season_pre", "opp_ftr_season_pre",
    ]
    for c in required:
        need(c)

    out["netrtg_adj_l7"] = delta(out["netrtg_l7_pre"], out["opp_netrtg_l7_pre"])
    out["efg_adj_l7"] = delta(out["efg_l7_pre"], out["opp_efg_l7_pre"])
    out["tov_adj_l7"] = delta(out["opp_tov_pct_l7_pre"], out["tov_pct_l7_pre"])
    out["orb_adj_l7"] = delta(out["orb_pct_l7_pre"], out["opp_drb_pct_l7_pre"])
    out["ftr_adj_l7"] = delta(out["ftr_l7_pre"], out["opp_ftr_l7_pre"])

    out["netrtg_adj_season"] = delta(out["netrtg_season_pre"], out["opp_netrtg_season_pre"])
    out["efg_adj_season"] = delta(out["efg_season_pre"], out["opp_efg_season_pre"])
    out["tov_adj_season"] = delta(out["opp_tov_pct_season_pre"], out["tov_pct_season_pre"])
    out["orb_adj_season"] = delta(out["orb_pct_season_pre"], out["opp_drb_pct_season_pre"])
    out["ftr_adj_season"] = delta(out["ftr_season_pre"], out["opp_ftr_season_pre"])

    out["adj_source"] = "derived"
    return out


def _add_style_features(df: pd.DataFrame) -> pd.DataFrame:
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

    if "ftr_l7_pre" in out.columns and "opp_ftr_allowed_l7_pre" in out.columns:
        out["rim_vs_foul_l7"] = out["ftr_l7_pre"] - out["opp_ftr_allowed_l7_pre"]
    else:
        out["rim_vs_foul_l7"] = np.nan

    out["style_source"] = "derived"
    return out


def _add_ha_fallbacks(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    pairs = [
        ("ha_ortg_l3_pre", "ortg_l3_pre"),
        ("ha_ortg_l7_pre", "ortg_l7_pre"),
        ("ha_ortg_season_pre", "ortg_season_pre"),
        ("ha_drtg_l3_pre", "drtg_l3_pre"),
        ("ha_drtg_l7_pre", "drtg_l7_pre"),
        ("ha_drtg_season_pre", "drtg_season_pre"),
        ("ha_netrtg_l3_pre", "netrtg_l3_pre"),
        ("ha_netrtg_l7_pre", "netrtg_l7_pre"),
        ("ha_netrtg_season_pre", "netrtg_season_pre"),
        ("ha_pace_l7_pre", "pace_l7_pre"),
        ("ha_efg_l7_pre", "efg_l7_pre"),
        ("ha_tov_pct_l7_pre", "tov_pct_l7_pre"),
        ("ha_orb_pct_l7_pre", "orb_pct_l7_pre"),
        ("ha_drb_pct_l7_pre", "drb_pct_l7_pre"),
        ("ha_ftr_l7_pre", "ftr_l7_pre"),
        ("ha_3par_l7_pre", "3par_l7_pre"),
    ]

    for ha_col, overall_col in pairs:
        if ha_col in out.columns and overall_col in out.columns:
            out[f"{ha_col}_eff_pre"] = out[ha_col].fillna(out[overall_col])

    out["ha_fallback_source"] = "derived"
    return out


def _build_feature_diagnostics(df: pd.DataFrame) -> pd.DataFrame:
    cols_keep = [c for c in [
        "event_id", "team_id", "team", "opponent", "home_away",
        "game_datetime_utc", "completed", "data_ok",
        "fga", "fta", "tov", "orb", "poss",
        "games_played_pre", "ha_games_played_pre",
        "ortg_l7_pre", "ha_ortg_l7_pre",
        "opp_team_id", "opp_ortg_l7_pre", "opp_netrtg_l7_pre",
        "opp_join_ok",
        "base_totals_source", "poss_source",
    ] if c in df.columns]

    d = df[cols_keep].copy()

    def reason(r):
        if r.get("completed") is True and r.get("data_ok") is False:
            return "data_ok_false (missing/bad boxscore totals)"
        if "opp_join_ok" in r and (r.get("completed") is True) and (r.get("opp_join_ok") is False):
            return "opponent_join_missing (event symmetry or merge keys)"
        if pd.isna(r.get("ortg_l7_pre")):
            gp = r.get("games_played_pre")
            if pd.isna(gp) or gp < 1:
                return "no_prior_games"
            if gp < 7:
                return "insufficient_history_for_l7 (expected)"
            return "rolling_nan_unknown"
        return "ok"

    d["diagnostic_reason"] = d.apply(reason, axis=1)
    return d


# ---------------- matchup table builder ----------------
def build_matchups_model_ready(df_features: pd.DataFrame) -> pd.DataFrame:
    df = df_features.copy()
    df["event_id"] = _normalize_id_series(df["event_id"])
    df["game_dt"] = pd.to_datetime(df["game_datetime_utc"], utc=True, errors="coerce")

    home = df[df["home_away"] == "home"].copy()
    away = df[df["home_away"] == "away"].copy()

    keep_base = [
        "event_id", "game_datetime_utc", "game_date", "game_date_utc", "venue",
        "home_team", "away_team",
        "points_for", "points_against", "margin",
        "completed", "data_ok",
        "state", "status_desc", "status_detail",
    ]
    keep_base = [c for c in keep_base if c in home.columns]

    feat_cols = [c for c in df.columns if c.endswith("_pre") or c.endswith("_noblow_pre") or c.endswith("_eff_pre") or c in [
        "days_rest", "days_since_last_game", "games_last_7_days", "back_to_back", "three_in_six",
        "avg_opp_netrtg_l7_pre", "avg_opp_ortg_l7_pre", "avg_opp_drtg_l7_pre", "sos_season_pre",
        "netrtg_adj_l7", "efg_adj_l7", "tov_adj_l7", "orb_adj_l7", "ftr_adj_l7",
        "netrtg_adj_season", "efg_adj_season", "tov_adj_season", "orb_adj_season", "ftr_adj_season",
        "style_distance_l7", "pace_mismatch_l7", "rim_vs_foul_l7",
        "blowout",
        "pulled_at_utc", "parse_version", "source",
        "opp_join_ok",
    ]]
    feat_cols = [c for c in dict.fromkeys(feat_cols) if c in df.columns]

    home_keep = keep_base + ["team", "team_id"] + feat_cols
    away_keep = ["event_id"] + ["team", "team_id"] + feat_cols

    home_keep = [c for c in dict.fromkeys(home_keep) if c in home.columns]
    away_keep = [c for c in dict.fromkeys(away_keep) if c in away.columns]

    h = home[home_keep].copy()
    a = away[away_keep].copy()

    h["team_id"] = _normalize_id_series(h["team_id"])
    a["team_id"] = _normalize_id_series(a["team_id"])

    h = h.rename(columns={c: f"h_{c}" for c in h.columns if c != "event_id"})
    a = a.rename(columns={c: f"a_{c}" for c in a.columns if c != "event_id"})

    m = h.merge(a, on="event_id", how="inner")

    if "h_points_for" in m.columns:
        m["home_points"] = m["h_points_for"]
    if "h_points_against" in m.columns:
        m["away_points"] = m["h_points_against"]

    if all(c in m.columns for c in ["home_points", "away_points", "h_completed", "h_data_ok"]):
        m["home_win"] = np.where(
            (m["h_completed"] == True) & (m["h_data_ok"] == True),
            (m["home_points"] > m["away_points"]).astype(int),
            np.nan,
        )
    else:
        m["home_win"] = np.nan

    if "h_completed" in m.columns:
        m["status"] = np.where(m["h_completed"] == True, "final", "not_final")
    elif "h_state" in m.columns:
        m["status"] = np.where(m["h_state"].astype(str).str.lower().eq("post"), "final", "not_final")
    else:
        m["status"] = "unknown"

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


# ---------------- Step 11: Data Quality Repair Gate (DQRG) ----------------
def _dq_expected_rules():
    """
    Central expected-field rules. Keep this small and explicit.
    """
    must_have_final = [
        "poss", "ortg", "drtg", "netrtg",
        "efg", "tov_pct", "orb_pct", "drb_pct", "ftr", "3par",
    ]
    # expected only if enough prior games exist
    l7_fields = [
        "ortg_l7_pre", "drtg_l7_pre", "netrtg_l7_pre", "pace_l7_pre",
        "efg_l7_pre", "tov_pct_l7_pre", "orb_pct_l7_pre", "drb_pct_l7_pre",
        "ftr_l7_pre", "3par_l7_pre",
    ]
    opp_required_fields = [
        "opp_team_id", "opp_netrtg_l7_pre", "opp_efg_l7_pre", "opp_ftr_l7_pre",
    ]
    derived_from_opp = [
        "netrtg_adj_l7", "efg_adj_l7", "tov_adj_l7", "orb_adj_l7", "ftr_adj_l7",
        "sos_season_pre", "style_distance_l7",
    ]
    return {
        "must_have_final": must_have_final,
        "l7_fields": l7_fields,
        "opp_required_fields": opp_required_fields,
        "derived_from_opp": derived_from_opp,
    }


def _dq_recompute_single_game_fields(df: pd.DataFrame, idxs: list):
    """
    Repair Y1: recompute per-game derived fields when base inputs exist.
    Operates in-place on a copy by index list.
    """
    out = df.copy()
    for i in idxs:
        r = out.loc[i]
        # require base totals
        fga = _to_int(r.get("fga"), 0)
        fgm = _to_int(r.get("fgm"), 0)
        tpm = _to_int(r.get("tpm"), 0)
        tpa = _to_int(r.get("tpa"), 0)
        fta = _to_int(r.get("fta"), 0)
        tov = _to_int(r.get("tov"), 0)
        orb = _to_int(r.get("orb"), 0)

        # recompute
        poss = _estimate_possessions(fga, fta, tov, orb) if fga > 0 else np.nan
        efg = _safe_div((fgm + 0.5 * tpm), fga, np.nan)
        ftr = _safe_div(fta, fga, np.nan)
        threepar = _safe_div(tpa, fga, np.nan)

        out.at[i, "poss"] = out.at[i, "poss"] if pd.notna(out.at[i, "poss"]) else poss
        out.at[i, "efg"] = out.at[i, "efg"] if pd.notna(out.at[i, "efg"]) else efg
        out.at[i, "ftr"] = out.at[i, "ftr"] if pd.notna(out.at[i, "ftr"]) else ftr
        out.at[i, "3par"] = out.at[i, "3par"] if pd.notna(out.at[i, "3par"]) else threepar

        # percentages that depend on poss
        poss2 = out.at[i, "poss"]
        if pd.notna(poss2) and pd.isna(out.at[i, "tov_pct"]):
            out.at[i, "tov_pct"] = _safe_div(_to_float(r.get("tov"), np.nan), poss2, np.nan)

        # ratings
        if pd.notna(poss2):
            pf = _to_float(r.get("points_for"), np.nan)
            pa = _to_float(r.get("points_against"), np.nan)
            if pd.isna(out.at[i, "ortg"]) and pd.notna(pf):
                out.at[i, "ortg"] = _safe_div(pf * 100.0, poss2, np.nan)
                out.at[i, "ortg_source"] = "repaired"
            if pd.isna(out.at[i, "drtg"]) and pd.notna(pa):
                out.at[i, "drtg"] = _safe_div(pa * 100.0, poss2, np.nan)
                out.at[i, "drtg_source"] = "repaired"
            if pd.isna(out.at[i, "netrtg"]) and pd.notna(out.at[i, "ortg"]) and pd.notna(out.at[i, "drtg"]):
                out.at[i, "netrtg"] = out.at[i, "ortg"] - out.at[i, "drtg"]
                out.at[i, "netrtg_source"] = "repaired"

        # data_ok re-eval (only if still unknown)
        if "data_ok" in out.columns and (pd.isna(out.at[i, "data_ok"]) or out.at[i, "data_ok"] is False):
            poss3 = out.at[i, "poss"]
            if bool(r.get("completed")) and pd.notna(poss3) and float(poss3) > 40 and float(fga) > 0:
                out.at[i, "data_ok"] = True

    return out


def _dq_recompute_team_rollups_for_team(df_full: pd.DataFrame, team_id: str):
    """
    Repair Y2: recompute rolling and rest features for one team using valid history, then merge back.
    """
    df = df_full.copy()
    df["team_id"] = _normalize_id_series(df["team_id"])
    df["event_id"] = _normalize_id_series(df["event_id"])
    df["game_dt"] = pd.to_datetime(df["game_datetime_utc"], utc=True, errors="coerce")

    team = df[df["team_id"] == str(team_id)].copy()
    if team.empty:
        return df, False

    team = team.sort_values(["team_id", "game_dt", "event_id"])

    # valid history only for rollups
    team_valid = team[team["data_ok"] == True].copy()
    if team_valid.empty:
        return df, False

    team_valid = _add_rolling_pack(team_valid, group_cols=["team_id"], prefix="")
    team_valid = _add_noblow_rollups(team_valid, group_cols=["team_id"], prefix="")
    team_valid = _add_rolling_pack(team_valid, group_cols=["team_id", "home_away"], prefix="ha_")
    team_valid = _add_noblow_rollups(team_valid, group_cols=["team_id", "home_away"], prefix="ha_")
    team_valid = _time_window_counts_per_team(team_valid)

    key_cols = ["event_id", "team_id"]
    attach_cols = [c for c in team_valid.columns if c.endswith("_pre") or c.endswith("_noblow_pre") or c in [
        "days_since_last_game", "days_rest", "games_last_7_days", "back_to_back", "three_in_six",
        "games_played_pre", "games_played_noblow_pre", "ha_games_played_pre", "ha_games_played_noblow_pre",
    ]]

    merged = team.merge(team_valid[key_cols + attach_cols], on=key_cols, how="left", suffixes=("", "_recalc"))

    # apply only when original is NaN and recalculated exists
    updated = False
    for c in attach_cols:
        cr = f"{c}_recalc"
        if cr in merged.columns:
            mask = merged[c].isna() & merged[cr].notna()
            if mask.any():
                merged.loc[mask, c] = merged.loc[mask, cr]
                updated = True
            merged = merged.drop(columns=[cr], errors="ignore")

    if updated:
        df.loc[merged.index, merged.columns] = merged

    return df, updated


def _dq_repair_opponent_for_event(df_full: pd.DataFrame, event_id: str):
    """
    Repair Z: re-run opponent merge steps for one event_id after dedupe and symmetry checks.
    """
    df = df_full.copy()
    df["event_id"] = _normalize_id_series(df["event_id"])
    eid = str(event_id)

    sub = df[df["event_id"] == eid].copy()
    if sub.empty:
        return df, False

    # ensure exactly 2 rows after local dedupe
    sub = _dedupe_by_completeness(sub, keys=["event_id", "home_away"], label=f"DQ Z dedupe event {eid}")
    sub = _drop_bad_event_ids_keep_good(sub, label=f"DQ Z symmetry event {eid}")
    if sub["event_id"].nunique() != 1 or len(sub) != 2:
        return df, False

    # merge opponent for just these two rows
    sub2 = _merge_opponent_rows(sub)

    # allowed rollups require chronology, but we can still attach opponent columns for this event safely.
    # Replace df rows for this event_id with updated sub2 columns (prefer sub2 values).
    idxs = df.index[df["event_id"] == eid].tolist()
    if not idxs:
        return df, False

    # align columns
    for c in sub2.columns:
        if c not in df.columns:
            df[c] = np.nan
    # overwrite by row matching home_away
    for _, r in sub2.iterrows():
        ha = r.get("home_away")
        mask = (df["event_id"] == eid) & (df["home_away"] == ha)
        for c in sub2.columns:
            if c in ("event_id", "home_away"):
                continue
            if pd.isna(df.loc[mask, c]).all() and pd.notna(r.get(c)):
                df.loc[mask, c] = r.get(c)

    return df, True


def data_quality_repair_gate(df_full: pd.DataFrame) -> (pd.DataFrame, pd.DataFrame):
    """
    Step 11: DQRG
    - Identify missing expected fields (rule-based).
    - Classify as ESPN missing vs pipeline missing.
    - Attempt repairs X (already earlier), Y (recompute derived + rollups), Z (opponent merge for event).
    - Log all actions and outcomes to espn_dq_audit.csv.
    """
    rules = _dq_expected_rules()
    df = df_full.copy()

    # Ensure stable ids
    df["event_id"] = _normalize_id_series(df["event_id"])
    df["team_id"] = _normalize_id_series(df["team_id"])

    # Determine expected sets per row
    audit_rows = []
    idxs_to_recompute_single = []
    teams_to_recompute_rollups = set()
    events_to_repair_opp = set()

    def is_final_and_ok(r):
        return (r.get("completed") is True) and (r.get("data_ok") is True)

    for idx, r in df.iterrows():
        expected_missing = []
        reason_codes = []
        action_plan = []

        # Base must-have on finals
        if is_final_and_ok(r):
            for f in rules["must_have_final"]:
                if f in df.columns and pd.isna(r.get(f)):
                    expected_missing.append(f)

        # L7 expected when games_played_pre >= 7
        gp = r.get("games_played_pre")
        if is_final_and_ok(r) and pd.notna(gp) and float(gp) >= 7:
            for f in rules["l7_fields"]:
                if f in df.columns and pd.isna(r.get(f)):
                    expected_missing.append(f)

        # Opponent required fields expected on finals (because symmetry should hold)
        if is_final_and_ok(r):
            for f in rules["opp_required_fields"]:
                if f in df.columns and pd.isna(r.get(f)):
                    expected_missing.append(f)

        # Derived-from-opponent fields expected only if opp_join_ok
        opp_ok = bool(r.get("opp_join_ok")) if "opp_join_ok" in df.columns else False
        if is_final_and_ok(r) and opp_ok:
            for f in rules["derived_from_opp"]:
                if f in df.columns and pd.isna(r.get(f)):
                    expected_missing.append(f)

        expected_missing = sorted(list(set(expected_missing)))

        if not expected_missing:
            continue

        # Classify likely root cause
        base_inputs_ok = True
        for base in ["fga", "fta", "tov", "orb", "points_for", "points_against"]:
            if base in df.columns and (pd.isna(r.get(base)) or (base == "fga" and _to_int(r.get(base), 0) == 0)):
                base_inputs_ok = False
                break

        if not base_inputs_ok:
            reason_codes.append("espn_base_missing_or_zero")
            action_plan.append("X: already attempted in parser; mark and skip rollup repair")
        else:
            reason_codes.append("pipeline_derived_missing")
            action_plan.append("Y1: recompute per-game derived fields")
            idxs_to_recompute_single.append(idx)

            # If any rolling fields missing, queue team rollup recompute
            if any(f.endswith("_l7_pre") or f.endswith("_season_pre") or f.endswith("_l3_pre") for f in expected_missing):
                teams_to_recompute_rollups.add(str(r.get("team_id")))

        # Opp join missing triggers Z
        if any(f.startswith("opp_") for f in expected_missing) or ("opp_team_id" in expected_missing):
            events_to_repair_opp.add(str(r.get("event_id")))
            action_plan.append("Z: repair opponent join for event")

        audit_rows.append({
            "event_id": r.get("event_id"),
            "team_id": r.get("team_id"),
            "team": r.get("team"),
            "home_away": r.get("home_away"),
            "game_datetime_utc": r.get("game_datetime_utc"),
            "completed": r.get("completed"),
            "data_ok": r.get("data_ok"),
            "games_played_pre": r.get("games_played_pre"),
            "opp_join_ok": r.get("opp_join_ok") if "opp_join_ok" in df.columns else np.nan,
            "dq_missing_fields": ",".join(expected_missing),
            "dq_reason_codes": ",".join(reason_codes),
            "dq_action_plan": " | ".join(action_plan),
            "dq_repair_success": 0,
            "dq_repair_actions_taken": "",
            "pulled_at_utc": _utc_now_iso(),
            "parse_version": PARSE_VERSION,
        })

    # Execute Y1
    actions_taken = set()
    if idxs_to_recompute_single:
        df2 = _dq_recompute_single_game_fields(df, idxs_to_recompute_single)
        df = df2
        actions_taken.add("Y1")

    # Execute Y2 by team
    any_rollup = False
    for tid in sorted(list(teams_to_recompute_rollups)):
        df, updated = _dq_recompute_team_rollups_for_team(df, tid)
        any_rollup = any_rollup or updated
    if teams_to_recompute_rollups:
        actions_taken.add("Y2")

    # Execute Z by event
    any_opp = False
    for eid in sorted(list(events_to_repair_opp)):
        df, updated = _dq_repair_opponent_for_event(df, eid)
        any_opp = any_opp or updated
    if events_to_repair_opp:
        actions_taken.add("Z")

    # Recompute dependent layers after repairs (lightweight)
    # If opponent repairs occurred, rebuild derived-from-opp layers globally for consistency.
    if any_opp:
        df = _drop_bad_event_ids_keep_good(df, label="post-DQRG pre-merge symmetry")
        df = _merge_opponent_rows(df)
        df = _add_allowed_rollups(df)
        df = _merge_opponent_rows(df)
        df = _add_sos_proxies(df)
        df = _add_opponent_adjusted_deltas(df)
        df = _add_style_features(df)
        df = _add_ha_fallbacks(df)
        actions_taken.add("postZ_rebuild")

    # Update audit success where the row no longer has missing fields
    if audit_rows:
        audit_df = pd.DataFrame(audit_rows)
        # quick success check: if any listed missing fields are still missing
        def still_missing(row):
            eid = str(row.get("event_id"))
            tid = str(row.get("team_id"))
            ha = row.get("home_away")
            subset = df[(df["event_id"].astype(str) == eid) & (df["team_id"].astype(str) == tid) & (df["home_away"] == ha)]
            if subset.empty:
                return 1
            rr = subset.iloc[0]
            missing = []
            for f in str(row.get("dq_missing_fields") or "").split(","):
                f = f.strip()
                if not f:
                    continue
                if f in df.columns and pd.isna(rr.get(f)):
                    missing.append(f)
            return 1 if missing else 0

        audit_df["_still_missing"] = audit_df.apply(still_missing, axis=1)
        audit_df["dq_repair_success"] = np.where(audit_df["_still_missing"] == 0, 1, 0)
        audit_df["dq_repair_actions_taken"] = ",".join(sorted(list(actions_taken)))
        audit_df = audit_df.drop(columns=["_still_missing"], errors="ignore")
    else:
        audit_df = pd.DataFrame(columns=[
            "event_id", "team_id", "team", "home_away", "game_datetime_utc",
            "completed", "data_ok", "games_played_pre", "opp_join_ok",
            "dq_missing_fields", "dq_reason_codes", "dq_action_plan",
            "dq_repair_success", "dq_repair_actions_taken",
            "pulled_at_utc", "parse_version",
        ])

    return df, audit_df


# ---------------- end-to-end pipeline ----------------
def run_pipeline(days_back: int = DEFAULT_DAYS_BACK):
    """
    Segmented pipeline with gates.
    PASS 1: CLEAN logs (parse, compute per-game metrics, deterministic dedupe)
    PASS 2: TEAM features (rollups, rest, coverage) computed from data_ok history
    PASS 3: OPPONENT features (merge, allowed, merge, sos, deltas, style, HA fallbacks)
    PASS 4: Step 11 DQRG (repair expected missing fields, log)
    PASS 5: write outputs (features append+dedupe; matchups rebuild)
    """
    pulled_at = _utc_now_iso()
    print(f"Run started: {pulled_at} | DAYS_BACK={days_back} | PARSE_VERSION={PARSE_VERSION}")

    # Always ensure output files exist (CI safety)
    _ensure_csv_exists(OUT_GAMES, columns=["date","game_id","game_datetime_utc","venue","home_team","away_team","home_score","away_score","home_win","away_win","completed","state","status_desc","status_detail","pulled_at_utc","source"])
    _ensure_csv_exists(OUT_TEAM_LOGS, columns=["event_id","team_id","team","home_away","game_datetime_utc","game_date","game_date_utc","venue","points_for","points_against","margin","fga","fta","tov","orb","drb","reb","poss","efg","ftr","3par","tov_pct","orb_pct","drb_pct","ortg","drtg","netrtg","pace","data_ok","completed","state","status_desc","status_detail","pulled_at_utc","source","parse_version"])
    _ensure_csv_exists(OUT_TEAM_FEATURES, columns=["event_id","team_id","team","home_away","game_datetime_utc"])
    _ensure_csv_exists(OUT_MATCHUPS, columns=["event_id"])
    _ensure_csv_exists(OUT_DIAGNOSTICS, columns=["event_id","team_id","team","diagnostic_reason"])
    _ensure_csv_exists(OUT_DQ_AUDIT, columns=["event_id","team_id","team","dq_missing_fields","dq_reason_codes","dq_action_plan","dq_repair_success","dq_repair_actions_taken","pulled_at_utc","parse_version"])

    games_df = build_espn_games_csv(days_back=days_back, out_csv=OUT_GAMES, verbose=True)
    if games_df.empty:
        print("No games from scoreboard. Exiting.")
        return

    now_pst = datetime.now(TZ_PST)
    window_dates = {(now_pst - timedelta(days=i)).strftime("%Y%m%d") for i in range(days_back)}
    run_window = games_df[games_df["date"].astype(str).isin(window_dates)].copy()
    game_ids = run_window["game_id"].astype(str).unique().tolist()
    print(f"Scoreboard game_ids in run window: {len(game_ids)}")

    # ---------------- PASS 1: CLEAN ----------------
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
    df_logs_new["event_id"] = _normalize_id_series(df_logs_new["event_id"])
    df_logs_new["team_id"] = _normalize_id_series(df_logs_new["team_id"])
    df_logs_new["game_dt"] = pd.to_datetime(df_logs_new["game_datetime_utc"], utc=True, errors="coerce")

    df_logs_new = _compute_per_game_advanced_metrics(df_logs_new)

    # Deterministic dedupe within this run before appending
    df_logs_new = _dedupe_by_completeness(df_logs_new, keys=["event_id", "team_id"], label="PASS1 logs_new")
    df_logs_new = _drop_bad_event_ids_keep_good(df_logs_new, label="PASS1 logs_new symmetry")

    df_logs_all = _append_dedupe_write(
        OUT_TEAM_LOGS,
        df_logs_new.drop(columns=["game_dt"], errors="ignore"),
        subset_keys=["event_id", "team_id"],
        sort_cols=["game_datetime_utc", "event_id", "team_id", "home_away"],
    )
    print(f"{OUT_TEAM_LOGS} total rows: {len(df_logs_all)}")

    # ---------------- PASS 2: TEAM FEATURES ----------------
    df = df_logs_all.copy()
    df["event_id"] = _normalize_id_series(df["event_id"])
    df["team_id"] = _normalize_id_series(df["team_id"])
    df["game_dt"] = pd.to_datetime(df["game_datetime_utc"], utc=True, errors="coerce")

    # Dedupe per side deterministically
    df = _dedupe_by_completeness(df, keys=["event_id", "home_away"], label="PASS2 logs_all side dedupe")
    df = _drop_bad_event_ids_keep_good(df, label="PASS2 symmetry drop")

    df_valid = df[df["data_ok"] == True].copy()
    print("PASS 2 starting: logs rows =", len(df), "valid rows =", len(df_valid))
    print("Unique event_ids:", df["event_id"].nunique())

    # team overall rollups
    df_valid = _add_rolling_pack(df_valid, group_cols=["team_id"], prefix="")
    df_valid = _add_noblow_rollups(df_valid, group_cols=["team_id"], prefix="")

    # home/away rollups
    df_valid = _add_rolling_pack(df_valid, group_cols=["team_id", "home_away"], prefix="ha_")
    df_valid = _add_noblow_rollups(df_valid, group_cols=["team_id", "home_away"], prefix="ha_")

    # rest features
    df_valid = _time_window_counts_per_team(df_valid)

    key_cols = ["event_id", "team_id"]
    attach_cols = [c for c in df_valid.columns if c.endswith("_pre") or c.endswith("_noblow_pre") or c in [
        "days_since_last_game", "days_rest", "games_last_7_days", "back_to_back", "three_in_six",
        "games_played_pre", "games_played_noblow_pre", "ha_games_played_pre", "ha_games_played_noblow_pre",
    ]]
    attach_cols = [c for c in attach_cols if c in df_valid.columns]

    df = df.merge(df_valid[key_cols + attach_cols], on=key_cols, how="left")

    # ---------------- PASS 3: OPPONENT FEATURES ----------------
    df = _drop_bad_event_ids_keep_good(df, label="PASS3 pre-opponent symmetry")
    df = _merge_opponent_rows(df)

    # Gate: opponent join rate for final games
    if "opp_join_ok" in df.columns:
        final = df[(df["completed"] == True) & (df["data_ok"] == True)].copy()
        if len(final) > 0:
            opp_join_rate = float(final["opp_join_ok"].fillna(False).mean())
            print("PASS3 gate: opp_join_rate_final =", round(opp_join_rate, 4))
            if opp_join_rate < GATE_MIN_OPP_JOIN_RATE_FINAL:
                print("[WARN] Opponent join rate below threshold. DQRG will attempt repairs; continuing.")

    df = _add_allowed_rollups(df)
    df = _merge_opponent_rows(df)
    df = _add_sos_proxies(df)
    df = _add_opponent_adjusted_deltas(df)
    df = _add_style_features(df)
    df = _add_ha_fallbacks(df)

    # Gate: poss present on finals
    final2 = df[(df["completed"] == True) & (df["data_ok"] == True)].copy()
    if len(final2) > 0:
        poss_rate = float(final2["poss"].notna().mean()) if "poss" in df.columns else 0.0
        print("PASS3 gate: poss_present_final =", round(poss_rate, 4))
        if poss_rate < GATE_MIN_POSS_PRESENT_FINAL:
            print("[WARN] Poss present below threshold. Likely ESPN base totals missing or overwritten. DQRG will attempt local repairs; continuing.")

    # diagnostics (optional)
    if WRITE_DIAGNOSTICS:
        try:
            diag = _build_feature_diagnostics(df)
            diag.to_csv(OUT_DIAGNOSTICS, index=False)
            print(f"{OUT_DIAGNOSTICS} written: {len(diag)} rows")
        except Exception as e:
            print(f"[WARN] diagnostics write failed: {e}")

    # ---------------- PASS 4: Step 11 DQRG ----------------
    df, dq_audit = data_quality_repair_gate(df)
    if WRITE_DQ_AUDIT:
        try:
            dq_audit.to_csv(OUT_DQ_AUDIT, index=False)
            print(f"{OUT_DQ_AUDIT} written: {len(dq_audit)} rows")
        except Exception as e:
            print(f"[WARN] dq audit write failed: {e}")

    # Gate: expected present rate (rule-driven)
    rules = _dq_expected_rules()
    final3 = df[(df["completed"] == True) & (df["data_ok"] == True)].copy()
    if len(final3) > 0:
        # simple: must_have_final presence
        present_rates = []
        for f in rules["must_have_final"]:
            if f in df.columns:
                present_rates.append(float(final3[f].notna().mean()))
        expected_present_rate = float(np.mean(present_rates)) if present_rates else 1.0
        print("PASS4 gate: expected_present_rate_final =", round(expected_present_rate, 4))
        if expected_present_rate < GATE_MIN_EXPECTED_PRESENT_FINAL:
            print("[WARN] Expected-present rate below threshold after DQRG. Check espn_dq_audit.csv for reasons.")

    # ---------------- PASS 5: write outputs ----------------
    # only append feature rows for newly parsed event_ids (keeps feature CSV stable)
    new_event_ids = set(_normalize_id_series(df_logs_new["event_id"]).unique().tolist())
    df_features_new = df[df["event_id"].astype(str).isin(new_event_ids)].copy()

    df_features_all = _append_dedupe_write(
        OUT_TEAM_FEATURES,
        df_features_new.drop(columns=["game_dt"], errors="ignore"),
        subset_keys=["event_id", "team_id"],
        sort_cols=["game_datetime_utc", "event_id", "team_id", "home_away"],
    )
    print(f"{OUT_TEAM_FEATURES} total rows: {len(df_features_all)}")

    # matchup table (rebuild each run)
    df_feat_all = df_features_all.copy()
    df_feat_all["event_id"] = _normalize_id_series(df_feat_all["event_id"])
    if "team_id" in df_feat_all.columns:
        df_feat_all["team_id"] = _normalize_id_series(df_feat_all["team_id"])

    m = build_matchups_model_ready(df_feat_all)
    m = m.drop_duplicates(subset=["event_id"], keep="last")
    m.to_csv(OUT_MATCHUPS, index=False)
    print(f"{OUT_MATCHUPS} written: {len(m)} rows")

    print(f"Run finished. Summary parse errors: {errors}")


def main():
    run_pipeline(days_back=DEFAULT_DAYS_BACK)


if __name__ == "__main__":
    main()
