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

Hardening additions (v1.4.2):
- fetch_with_retry() for ESPN endpoints (timeouts, 429, 5xx backoff)
- Atomic CSV writes with backup
- Checkpointing for summary parsing loop (resume-safe)
- Error log to JSON for post-run audit
- home_away normalization on read + merge key safety
- Integrity gate before writing CSVs (fail fast on missing required columns)
- game_id and team_id added to all key operations for better tracking
"""

import os
import time
import json
import hashlib
import shutil
import tempfile
from typing import Any, Dict, List, Tuple, Optional
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

PARSE_VERSION = "v1.4.2"
SOURCE_NAME = "espn"
TZ_PST = ZoneInfo("America/Los_Angeles")

DEFAULT_DAYS_BACK = int(os.getenv("DAYS_BACK", "3"))
REQUEST_TIMEOUT = int(os.getenv("ESPN_TIMEOUT", "25"))

# Retry hardening (shared)
MAX_RETRIES = int(os.getenv("ESPN_MAX_RETRIES", "3"))
RETRY_INITIAL_DELAY = float(os.getenv("ESPN_RETRY_INITIAL_DELAY", "1.0"))
RETRY_BACKOFF = float(os.getenv("ESPN_RETRY_BACKOFF", "2.0"))

# Checkpointing + logging
CHECKPOINT_FILE = os.getenv("CHECKPOINT_FILE", "espn_pipeline_checkpoint.json")
CHECKPOINT_EVERY_N_GAMES = int(os.getenv("CHECKPOINT_EVERY_N_GAMES", "50"))
ERROR_LOG_PATH = os.getenv("ERROR_LOG_PATH", "espn_pipeline_errors.json")
ERROR_LOG: List[Dict[str, Any]] = []

DRY_RUN = os.getenv("DRY_RUN", "0").strip().lower() in ("1", "true", "yes")

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

# Repair attempts (kept as-is; these happen after initial fetch)
RETRY_SUMMARY_ON_BASE_MISS = int(os.getenv("RETRY_SUMMARY_ON_BASE_MISS", "1"))
MAX_SUMMARY_RETRIES = int(os.getenv("MAX_SUMMARY_RETRIES", "1"))
SUMMARY_RETRY_SLEEP_SEC = float(os.getenv("SUMMARY_RETRY_SLEEP_SEC", "0.35"))

VALID_HOME_AWAY = {"home", "away"}


# ---------------- helpers ----------------
def _utc_now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def log_error(context: str, error: Exception, event_id: Optional[str] = None, extra: Optional[Dict[str, Any]] = None) -> None:
    rec = {
        "ts_utc": _utc_now_iso(),
        "context": context,
        "event_id": event_id,
        "error_type": type(error).__name__,
        "error_message": str(error)[:600],
    }
    if extra:
        rec.update(extra)
    ERROR_LOG.append(rec)


def write_error_summary(path: str = ERROR_LOG_PATH) -> None:
    if not ERROR_LOG:
        return
    payload = {
        "run_ts_utc": _utc_now_iso(),
        "total_errors": len(ERROR_LOG),
        "errors": ERROR_LOG,
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)


def load_checkpoint() -> Dict[str, Any]:
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            log_error("checkpoint_load", e, extra={"path": CHECKPOINT_FILE})
            return {}
    return {}


def save_checkpoint(payload: Dict[str, Any]) -> None:
    try:
        with open(CHECKPOINT_FILE, "w") as f:
            json.dump(payload, f, indent=2)
    except Exception as e:
        log_error("checkpoint_save", e, extra={"path": CHECKPOINT_FILE})


def clear_checkpoint() -> None:
    try:
        if os.path.exists(CHECKPOINT_FILE):
            os.remove(CHECKPOINT_FILE)
    except Exception as e:
        log_error("checkpoint_clear", e, extra={"path": CHECKPOINT_FILE})


def fetch_with_retry(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = REQUEST_TIMEOUT,
    max_retries: int = MAX_RETRIES,
    initial_delay: float = RETRY_INITIAL_DELAY,
    backoff: float = RETRY_BACKOFF,
) -> Dict[str, Any]:
    last_exc: Optional[Exception] = None
    hdrs = (headers or {}).copy()

    for attempt in range(max_retries):
        if attempt > 0:
            delay = initial_delay * (backoff ** (attempt - 1))
            time.sleep(delay)

        try:
            r = requests.get(url, headers=hdrs, timeout=timeout)

            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After")
                extra = float(retry_after) if retry_after and retry_after.isdigit() else (initial_delay * (backoff ** attempt))
                time.sleep(extra)
                last_exc = requests.exceptions.HTTPError(f"429 Too Many Requests: {url}")
                continue

            r.raise_for_status()
            return r.json()

        except requests.exceptions.HTTPError as e:
            last_exc = e
            status = getattr(e.response, "status_code", None)
            if status is not None and status >= 500:
                continue
            raise

        except requests.exceptions.Timeout as e:
            last_exc = e
            continue

        except requests.exceptions.RequestException as e:
            last_exc = e
            continue

    raise RuntimeError(f"Failed after {max_retries} attempts: {url} | last_error={last_exc}")


def _atomic_csv_write(df: pd.DataFrame, path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(suffix=".csv", dir=os.path.dirname(path) or ".")
    try:
        df.to_csv(tmp_path, index=False)
        os.close(fd)

        if os.path.exists(path) and os.path.getsize(path) > 0:
            shutil.copy2(path, f"{path}.backup")

        shutil.move(tmp_path, path)

        backup = f"{path}.backup"
        if os.path.exists(backup):
            os.remove(backup)

    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        raise


def _ensure_csv_exists(path: str, columns: list):
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
    return float(fga + 0.44 * fta - orb + tov)


def _safe_div(num, den, default=np.nan):
    return default if den in (0, 0.0, None) else (num / den)


def _stable_row_hash(d: dict, keys):
    payload = "|".join([str(d.get(k, "")) for k in keys])
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _normalize_home_away_series(s: pd.Series) -> pd.Series:
    s2 = s.astype("string")
    s2 = s2.str.strip().str.lower()
    s2 = s2.replace({"": pd.NA, "nan": pd.NA, "None": pd.NA})
    return s2


def _read_csv_if_exists(path: str) -> pd.DataFrame:
    if os.path.exists(path) and os.path.getsize(path) > 0:
        try:
            df = pd.read_csv(path)
            # Normalize IDs immediately on read
            if "game_id" in df.columns:
                df["game_id"] = _normalize_id_series(df["game_id"])
            if "event_id" in df.columns:
                df["event_id"] = _normalize_id_series(df["event_id"])
            if "team_id" in df.columns:
                df["team_id"] = _normalize_id_series(df["team_id"])
            if "home_away" in df.columns:
                df["home_away"] = _normalize_home_away_series(df["home_away"])
            return df
        except Exception as e:
            log_error("read_csv", e, extra={"path": path})
            return pd.DataFrame()
    return pd.DataFrame()


def _normalize_id_series(s: pd.Series) -> pd.Series:
    s2 = s.astype(str)
    s2 = s2.str.replace(r"\.0$", "", regex=True)
    s2 = s2.replace({"nan": np.nan, "None": np.nan})
    return s2


def _completeness_score_row(r: pd.Series) -> float:
    completed = 1.0 if bool(r.get("completed")) else 0.0
    data_ok = 1.0 if bool(r.get("data_ok")) else 0.0

    critical = ["points_for", "points_against", "fga", "fta", "tov", "orb", "drb", "reb", "poss"]
    present = 0.0
    for c in critical:
        v = r.get(c, np.nan)
        if pd.notna(v):
            present += 1.0
    critical_frac = present / float(len(critical)) if critical else 0.0

    pulled = r.get("pulled_at_utc")
    pulled_bonus = 0.0
    if isinstance(pulled, str) and pulled.strip():
        pulled_bonus = 0.05

    return (2.0 * completed) + (2.0 * data_ok) + (1.0 * critical_frac) + pulled_bonus


def verify_dataframe_integrity(df: pd.DataFrame, filename: str) -> Tuple[bool, List[str]]:
    issues: List[str] = []
    if df is None or df.empty:
        return False, [f"{filename}: dataframe is empty"]

    required_cols = {
        "espn_games.csv": ["date", "game_id", "game_datetime_utc", "home_team", "away_team", "completed"],
        "espn_team_game_logs.csv": ["event_id", "team_id", "team", "home_away", "game_datetime_utc"],
        "espn_team_game_features.csv": ["event_id", "team_id", "game_datetime_utc"],
        "espn_matchups_model_ready.csv": ["event_id"],
    }

    if filename in required_cols:
        missing = [c for c in required_cols[filename] if c not in df.columns]
        if missing:
            issues.append(f"{filename}: missing required columns: {missing}")

    if "home_away" in df.columns:
        bad = df[~df["home_away"].isin(list(VALID_HOME_AWAY)) & df["home_away"].notna()]
        if len(bad) > 0:
            issues.append(f"{filename}: {len(bad)} rows have invalid home_away values: {bad['home_away'].unique()[:10].tolist()}")

    if "game_datetime_utc" in df.columns:
        dt = pd.to_datetime(df["game_datetime_utc"], utc=True, errors="coerce")
        bad_dt = int(dt.isna().sum())
        if bad_dt > 0 and (bad_dt / max(1, len(df))) > 0.10:
            issues.append(f"{filename}: {bad_dt}/{len(df)} ({bad_dt/len(df)*100:.1f}%) bad game_datetime_utc values")

    hard_fail = any("missing required columns" in x for x in issues)
    return (not hard_fail), issues


def _append_dedupe_write(existing_path: str, new_df: pd.DataFrame, subset_keys, sort_cols=None):
    """
    Append + deterministic dedupe + atomic write.
    Picks best row per key group using completeness score.
    """
    filename = os.path.basename(existing_path)

    if new_df is not None and not new_df.empty:
        new_df = new_df.copy()
        # Normalize all ID fields
        if "game_id" in new_df.columns:
            new_df["game_id"] = _normalize_id_series(new_df["game_id"])
        if "event_id" in new_df.columns:
            new_df["event_id"] = _normalize_id_series(new_df["event_id"])
        if "team_id" in new_df.columns:
            new_df["team_id"] = _normalize_id_series(new_df["team_id"])
        if "home_away" in new_df.columns:
            new_df["home_away"] = _normalize_home_away_series(new_df["home_away"])

    ok, issues = verify_dataframe_integrity(new_df, filename)
    if issues:
        if not ok:
            raise ValueError("Integrity gate failed:\n  - " + "\n  - ".join(issues))
        print(f"[WARN] Integrity notes for {filename}:")
        for x in issues[:20]:
            print(f"  - {x}")

    old = _read_csv_if_exists(existing_path)
    combined = new_df.copy() if old.empty else pd.concat([old, new_df], ignore_index=True)

    if subset_keys:
        for k in subset_keys:
            if k in combined.columns:
                combined[k] = _normalize_id_series(combined[k])

        combined["_dq_score"] = combined.apply(_completeness_score_row, axis=1)

        if "pulled_at_utc" in combined.columns:
            pulled = pd.to_datetime(combined["pulled_at_utc"], utc=True, errors="coerce")
            combined["_pulled_ts"] = pulled.astype("int64")
        else:
            combined["_pulled_ts"] = 0

        sort_by = list(subset_keys) + ["_dq_score", "_pulled_ts"]
        asc = [True] * len(subset_keys) + [False, False]
        combined = combined.sort_values(sort_by, ascending=asc)
        combined = combined.drop_duplicates(subset=subset_keys, keep="first")
        combined = combined.drop(columns=["_dq_score", "_pulled_ts"], errors="ignore")

    if sort_cols:
        sort_cols_present = [c for c in sort_cols if c in combined.columns]
        if sort_cols_present:
            combined = combined.sort_values(sort_cols_present)

    if DRY_RUN:
        print(f"[DRY RUN] Would write {len(combined)} rows -> {existing_path}")
        return combined

    _atomic_csv_write(combined, existing_path)
    return combined


def _iso_to_game_dates(game_datetime_utc: str):
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


def _drop_bad_event_ids_keep_good(df: pd.DataFrame, label: str):
    if "event_id" not in df.columns:
        return df
    df = df.copy()
    df["event_id"] = _normalize_id_series(df["event_id"])
    counts = df.groupby("event_id").size()
    good_ids = counts[counts == 2].index
    bad_ids = counts[counts != 2].index
    if len(bad_ids) > 0:
        print(f"[WARN] {label}: dropping {len(bad_ids)} event_ids not exactly 2 rows. Sample:", counts.loc[bad_ids].head(15).to_dict())
    return df[df["event_id"].isin(good_ids)].copy()


def _dedupe_by_completeness(df: pd.DataFrame, keys: list, label: str) -> pd.DataFrame:
    out = df.copy()
    for k in keys:
        if k in out.columns:
            out[k] = _normalize_id_series(out[k])

    out["_dq_score"] = out.apply(_completeness_score_row, axis=1)
    out = out.sort_values(keys + ["_dq_score"], ascending=[True] * len(keys) + [False])
    out = out.drop_duplicates(subset=keys, keep="first").drop(columns=["_dq_score"], errors="ignore")
    print(f"{label}: deduped to {len(out)} rows using completeness score on keys={keys}")
    return out


# ---------------- ESPN fetch: scoreboard ----------------
def fetch_scoreboard_games(date_yyyymmdd: str, timeout: int = REQUEST_TIMEOUT):
    url = ESPN_SCOREBOARD_URL.format(date=date_yyyymmdd)
    try:
        data = fetch_with_retry(url, headers=DEFAULT_HEADERS, timeout=timeout)
    except Exception as e:
        log_error("fetch_scoreboard", e, extra={"url": url, "date": date_yyyymmdd})
        return []

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
        try:
            return fetch_with_retry(url, headers=DEFAULT_HEADERS, timeout=timeout)
        except Exception as e:
            log_error("fetch_summary", e, event_id=str(eid), extra={"url": url})
            raise

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
                    if home_team_id and away_team_id:
                        h2 = next((x for x in parsed2 if x["team_id"] == home_team_id), None)
                        a2 = next((x for x in parsed2 if x["team_id"] == away_team_id), None)
                        if h2 and a2:
                            home_row, away_row = h2, a2
                        else:
                            home_row, away_row = parsed2[0], parsed2[1]
                    else:
                        home_row, away_row = parsed2[0], parsed2[1]
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

    home_row["off_rtg"] = _safe_div(home_row["points_for"], home_row["poss"], np.nan) * 100.0
    away_row["off_rtg"] = _safe_div(away_row["points_for"], away_row["poss"], np.nan) * 100.0

    home_row["def_rtg"] = _safe_div(home_row["points_against"], home_row["poss"], np.nan) * 100.0
    away_row["def_rtg"] = _safe_div(away_row["points_against"], away_row["poss"], np.nan) * 100.0

    # Common fields for both
    common = {
        "event_id": str(event_id),
        "game_datetime_utc": game_datetime_utc,
        "venue": venue,
        "completed": completed,
        "state": state,
        "status_desc": status_desc,
        "status_detail": status_detail,
        "source": SOURCE_NAME,
        "pulled_at_utc": _utc_now_iso(),
        "data_ok": (home_row["poss"] > 0) and (away_row["poss"] > 0)
    }

    gdate, gutc = _iso_to_game_dates(game_datetime_utc)
    common["game_date"] = gdate
    
    home_row.update(common)
    away_row.update(common)

    home_row["home_away"] = "home"
    away_row["home_away"] = "away"
    
    home_row["opponent_team_id"] = away_row["team_id"]
    away_row["opponent_team_id"] = home_row["team_id"]
    home_row["opponent_name"] = away_row["team"]
    away_row["opponent_name"] = home_row["team"]

    if completed:
        home_row["win"] = 1 if home_row["points_for"] > home_row["points_against"] else 0
        away_row["win"] = 1 if away_row["points_for"] > away_row["points_against"] else 0
    else:
        home_row["win"] = np.nan
        away_row["win"] = np.nan

    return [home_row, away_row]


def update_team_game_logs(games_csv=OUT_GAMES, logs_csv=OUT_TEAM_LOGS):
    """
    Reads games list, finds missing/unprocessed event_ids in logs, fetches summary, appends to logs.
    Includes checkpointing to resume mid-run.
    """
    df_games = _read_csv_if_exists(games_csv)
    if df_games.empty:
        print("No games found to process.")
        return pd.DataFrame()

    df_logs = _read_csv_if_exists(logs_csv)
    processed_ids = set()
    if not df_logs.empty and "event_id" in df_logs.columns:
        processed_ids = set(df_logs["event_id"].astype(str).unique())

    # We want to process completed games that are missing OR existing games that were incomplete but now are completed.
    # Simpler approach: Process anything missing. Process anything marked 'completed' in games but not fully populated in logs?
    # For simplicity in this append-forever model: Filter for event_ids not in processed_ids.
    
    # Optional: re-process incomplete entries. But for now, just new IDs.
    if "game_id" not in df_games.columns:
        print("Games CSV missing game_id.")
        return df_logs

    candidates = df_games[~df_games["game_id"].astype(str).isin(processed_ids)].copy()
    
    # Sort so we process chronological
    if "game_datetime_utc" in candidates.columns:
        candidates = candidates.sort_values("game_datetime_utc")
    
    to_process = candidates["game_id"].unique().tolist()
    if not to_process:
        print("No new games to process for logs.")
        return df_logs

    print(f"Found {len(to_process)} new games to parse.")
    
    new_rows = []
    checkpoint = load_checkpoint()
    last_processed_idx = checkpoint.get("last_idx", -1)
    
    for idx, eid in enumerate(to_process):
        if idx <= last_processed_idx:
            continue
            
        try:
            results = fetch_and_parse_espn_summary(eid)
            new_rows.extend(results)
            print(f"[{idx+1}/{len(to_process)}] Parsed {eid}")
        except Exception as e:
            log_error("update_logs_loop", e, event_id=eid)
            print(f"[{idx+1}/{len(to_process)}] Failed {eid}: {e}")
        
        # Checkpoint
        if (idx + 1) % CHECKPOINT_EVERY_N_GAMES == 0:
            if new_rows:
                # Flush partial
                partial_df = pd.DataFrame(new_rows)
                _append_dedupe_write(logs_csv, partial_df, subset_keys=["event_id", "team_id"], sort_cols=["game_datetime_utc"])
                new_rows = []
            save_checkpoint({"last_idx": idx})
    
    # Final flush
    if new_rows:
        df_new = pd.DataFrame(new_rows)
        df_final = _append_dedupe_write(logs_csv, df_new, subset_keys=["event_id", "team_id"], sort_cols=["game_datetime_utc"])
        clear_checkpoint()
        return df_final
    
    clear_checkpoint()
    return _read_csv_if_exists(logs_csv)


# ---------------- Feature Engineering ----------------

def build_team_features(logs_csv=OUT_TEAM_LOGS, out_features=OUT_TEAM_FEATURES):
    """
    Rolling averages (shifted) + opponent joins.
    """
    df = _read_csv_if_exists(logs_csv)
    if df.empty:
        return pd.DataFrame()
    
    # Ensure datetypes
    df["game_datetime_utc"] = pd.to_datetime(df["game_datetime_utc"], utc=True)
    df = df.sort_values(["team_id", "game_datetime_utc"])
    
    # Metrics to roll
    metrics = ["off_rtg", "def_rtg", "poss", "efg", "tov_pct", "orb_pct", "ftr", "3par"]
    
    # We want PRE-GAME stats. So we group by team and shift(1).
    # Rolling windows: last 5, last 10, season (expanding).
    
    # Only use rows where data_ok is True for calculation, but keep all rows for the join structure?
    # Safer: calculate on valid data, forward fill? Or just leave NaN if not enough history.
    
    # Filter valid for calc
    # We'll calculate columns on the whole DF but masking invalid might be complex. 
    # Simply rolling on the sorted DF implies chronological.
    
    features = df[["event_id", "team_id", "game_datetime_utc", "home_away", "opponent_team_id"]].copy()
    
    # Helper to apply rolling shift
    def add_rolling(sub_df, window, label):
        # shift(1) excludes current game
        shifted = sub_df[metrics].shift(1)
        if window == "season":
            rolled = shifted.expanding().mean()
        else:
            rolled = shifted.rolling(window=window, min_periods=1).mean()
        
        rolled.columns = [f"roll_{label}_{c}" for c in rolled.columns]
        return rolled
        
    grouped = df.groupby("team_id", group_keys=False)
    
    # Calculate feature sets
    f_l5 = grouped.apply(lambda x: add_rolling(x, 5, "L5"))
    f_l10 = grouped.apply(lambda x: add_rolling(x, 10, "L10"))
    f_szn = grouped.apply(lambda x: add_rolling(x, "season", "szn"))
    
    # Concat horizontally (index should align due to group_keys=False and sorting)
    # Note: groupby().apply() with group_keys=False maintains index if shape matches.
    # However, sometimes it's safer to join by index.
    
    features = features.join(f_l5).join(f_l10).join(f_szn)
    
    # Add Days Rest
    features["prev_game_date"] = grouped["game_datetime_utc"].shift(1)
    features["days_rest"] = (features["game_datetime_utc"] - features["prev_game_date"]).dt.total_seconds() / 86400.0
    features["days_rest"] = features["days_rest"].fillna(7) # Default to plenty rest for first game
    
    # Write features
    _append_dedupe_write(
        out_features, 
        features, 
        subset_keys=["event_id", "team_id"],
        sort_cols=["game_datetime_utc"]
    )
    return features


def build_matchups_file(features_csv=OUT_TEAM_FEATURES, games_csv=OUT_GAMES, out_matchups=OUT_MATCHUPS):
    """
    Joins Home Features + Away Features => Single Row per game.
    Adds labels (scores) if game is completed.
    """
    df_feats = _read_csv_if_exists(features_csv)
    df_games = _read_csv_if_exists(games_csv)
    
    if df_feats.empty or df_games.empty:
        return
        
    # Standardize IDs
    df_feats["event_id"] = _normalize_id_series(df_feats["event_id"])
    df_games["game_id"] = _normalize_id_series(df_games["game_id"])
    
    # Filter Features by Home/Away
    home_feats = df_feats[df_feats["home_away"] == "home"].copy()
    away_feats = df_feats[df_feats["home_away"] == "away"].copy()
    
    # Rename columns to H_ and A_
    # keeping event_id as join key
    exclude = ["event_id", "team_id", "game_datetime_utc", "home_away", "opponent_team_id", "prev_game_date"]
    
    h_cols = {c: f"home_{c}" for c in home_feats.columns if c not in exclude and c != "event_id"}
    a_cols = {c: f"away_{c}" for c in away_feats.columns if c not in exclude and c != "event_id"}
    
    home_feats = home_feats.rename(columns=h_cols)[["event_id"] + list(h_cols.values())]
    away_feats = away_feats.rename(columns=a_cols)[["event_id"] + list(a_cols.values())]
    
    # Merge
    matchups = pd.merge(df_games, home_feats, left_on="game_id", right_on="event_id", how="left")
    matchups = pd.merge(matchups, away_feats, left_on="game_id", right_on="event_id", how="left")
    
    # Drop extra event_id cols
    matchups = matchups.drop(columns=["event_id_x", "event_id_y"], errors="ignore")
    
    # Add target labels if available (margin)
    if "home_score" in matchups.columns and "away_score" in matchups.columns:
        matchups["score_margin"] = matchups["home_score"] - matchups["away_score"]
        matchups["total_points"] = matchups["home_score"] + matchups["away_score"]
    
    # Save (rebuild mode usually, but we use atomic write)
    _atomic_csv_write(matchups, out_matchups)
    return matchups


def run_diagnostics_and_audit():
    """
    Checks sparseness and data quality.
    """
    if not WRITE_DIAGNOSTICS and not WRITE_DQ_AUDIT:
        return

    logs = _read_csv_if_exists(OUT_TEAM_LOGS)
    if logs.empty:
        return

    # DQ Audit: Check for missing critical stats in logs where completed=True
    if WRITE_DQ_AUDIT:
        audit_rows = []
        if "completed" in logs.columns:
            completed_logs = logs[logs["completed"] == True]
            for idx, row in completed_logs.iterrows():
                issues = []
                if row.get("poss", 0) <= 0:
                    issues.append("Zero/Nan Possessions")
                if row.get("points_for", 0) == 0 and row.get("points_against", 0) == 0:
                    issues.append("Zero Scores")
                
                if issues:
                    audit_rows.append({
                        "event_id": row.get("event_id"),
                        "team_id": row.get("team_id"),
                        "date": row.get("game_date"),
                        "issues": ";".join(issues),
                        "action": "Flagged"
                    })
        
        if audit_rows:
            pd.DataFrame(audit_rows).to_csv(OUT_DQ_AUDIT, index=False)
            print(f"DQ Audit: Flagged {len(audit_rows)} bad rows in {OUT_DQ_AUDIT}")

    # Feature Diagnostics: NaNs in features
    if WRITE_DIAGNOSTICS:
        feats = _read_csv_if_exists(OUT_TEAM_FEATURES)
        if not feats.empty:
            nan_counts = feats.isna().sum()
            nan_counts = nan_counts[nan_counts > 0]
            if not nan_counts.empty:
                nan_counts.to_csv(OUT_DIAGNOSTICS, header=["nan_count"])
                print(f"Diagnostics written to {OUT_DIAGNOSTICS}")


# ---------------- Main ----------------
def main():
    print(f"--- ESPN CBB Pipeline {PARSE_VERSION} ---")
    print(f"Time: {_utc_now_iso()}")
    
    # Step 1: Scoreboard
    print("\n[1/5] Fetching Scoreboard...")
    build_espn_games_csv(days_back=DEFAULT_DAYS_BACK)
    
    # Step 2: Game Logs (Boxscores)
    print("\n[2/5] Updating Team Game Logs...")
    update_team_game_logs()
    
    # Step 3: Features
    print("\n[3/5] Building Feature Set...")
    build_team_features()
    
    # Step 4: Matchups
    print("\n[4/5] assembling Matchups Model File...")
    build_matchups_file()
    
    # Step 5: Diagnostics
    print("\n[5/5] Diagnostics & Audit...")
    run_diagnostics_and_audit()
    
    write_error_summary()
    print("\nDone.")


if __name__ == "__main__":
    main()
