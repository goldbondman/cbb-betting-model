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
    home_row["margin"] = home_row["points_for"] - home_row["points_against"]
    away_row["margin"] = away_row["points_for"] - away_row["points_against"]

    home_row["poss_source"] = "derived"
    away_row["poss_source"] = "derived"
    home_row["efg_source"] = "derived"
    away_row["efg_source"] = "derived"

    home_row["base_totals_source"] = "player_sum" if (completed and home_fallback_changed) else "team_stats"
    away_row["base_totals_source"] = "player_sum" if (completed and away_fallback_changed) else "team_stats"

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
    out = df.copy()

    if "event_id" in out.columns:
        out["event_id"] = _normalize_id_series(out["event_id"])
    if "team_id" in out.columns:
        out["team_id"] = _normalize_id_series(out["team_id"])
    if "home_away" in out.columns:
        out["home_away"] = _normalize_home_away_series(out["home_away"])

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

    out["ortg_source"] = pd.Series(pd.NA, index=out.index, dtype="string")
    out["drtg_source"] = pd.Series(pd.NA, index=out.index, dtype="string")
    out["netrtg_source"] = pd.Series(pd.NA, index=out.index, dtype="string")

    m = out["ortg"].notna()
    out.loc[m, "ortg_source"] = "derived"
    m = out["drtg"].notna()
    out.loc[m, "drtg_source"] = "derived"
    m = out["netrtg"].notna()
    out.loc[m, "netrtg_source"] = "derived"

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

    games_col = f"{prefix}games_played_noblow_pre"
    if games_col not in out.columns:
        out[games_col] = np.nan

    for metric in ["ortg", "drtg", "netrtg"]:
        if metric not in out.columns:
            out[metric] = np.nan

        tmp_col = f"__{metric}_noblow"
        out[tmp_col] = out[metric].where(out["blowout"] == 0, np.nan)

        out[f"{prefix}{metric}_l7_noblow_pre"] = out.groupby(group_cols, sort=False)[tmp_col].apply(
            lambda s: s.shift(1).rolling(7, min_periods=1).mean()
        ).reset_index(level=group_cols, drop=True)

        cnt = out.groupby(group_cols, sort=False)[tmp_col].apply(
            lambda s: s.shift(1).expanding(min_periods=1).count()
        ).reset_index(level=group_cols, drop=True)
        out[games_col] = out[games_col].fillna(cnt)

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


def _flip_home_away(val: Any) -> Optional[str]:
    v = str(val).strip().lower() if val is not None else ""
    if v == "home":
        return "away"
    if v == "away":
        return "home"
    return None

def _merge_opponent_rows(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    for c in ["event_id", "home_away"]:
        if c not in out.columns:
            raise ValueError(f"_merge_opponent_rows requires column: {c}")

    out["event_id"] = _normalize_id_series(out["event_id"])
    out["home_away"] = _normalize_home_away_series(out["home_away"])

    out = out.drop(columns=[c for c in out.columns if c.startswith("opp_")], errors="ignore")

    out["_key"] = out["event_id"].astype(str) + "|" + out["home_away"].astype("string")

    out["_opp_ha"] = out["home_away"].apply(_flip_home_away)
    out["_opp_key"] = out["event_id"].astype(str) + "|" + out["_opp_ha"].astype("string")

    out = out.drop_duplicates(subset=["_key"], keep="last")

    # Get ALL columns that should be opponent features
    opp_cols = [c for c in out.columns if (
        c.endswith("_pre") or 
        c in ["team", "team_id", "points_for", "points_against", 
              "efg", "tov_pct", "orb_pct", "drb_pct", "ftr", "3par",
              "ortg", "drtg", "netrtg", "pace", "_key"]
    )]
    lookup = out[opp_cols].copy()
    
    lookup = lookup.rename(columns={"_key": "_lookup_key"})
    lookup = lookup.rename(columns={c: f"opp_{c}" for c in lookup.columns if c != "_lookup_key"})

    out = out.merge(
        lookup,
        left_on="_opp_key",
        right_on="_lookup_key",
        how="left",
        validate="many_to_one",
    ).drop(columns=["_lookup_key"], errors="ignore")

    out["efg_allowed_game"] = out["opp_efg"] if "opp_efg" in out.columns else np.nan
    out["ftr_allowed_game"] = out["opp_ftr"] if "opp_ftr" in out.columns else np.nan
    out["tov_forced_game"] = out["opp_tov_pct"] if "opp_tov_pct" in out.columns else np.nan

    out["opp_join_ok"] = out["opp_team_id"].notna() if "opp_team_id" in out.columns else out["opp_team"].notna() if "opp_team" in out.columns else False
    out["opp_join_source"] = np.where(out["opp_join_ok"] == True, "merge", pd.NA)

    return out.drop(columns=["_key", "_opp_key", "_opp_ha"], errors="ignore")


# ---------------- matchup table builder ----------------
def build_matchups_model_ready(df_features: pd.DataFrame) -> pd.DataFrame:
    df = df_features.copy()
    df["event_id"] = _normalize_id_series(df["event_id"])
    df["home_away"] = _normalize_home_away_series(df["home_away"])
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

# ---------------- end-to-end pipeline ----------------
def run_pipeline(days_back: int = DEFAULT_DAYS_BACK):
    pulled_at = _utc_now_iso()
    print(f"Run started: {pulled_at} | DAYS_BACK={days_back} | PARSE_VERSION={PARSE_VERSION}")

    _ensure_csv_exists(OUT_GAMES, columns=["date","game_id","game_datetime_utc","venue","home_team","away_team","home_score","away_score","home_win","away_win","completed","state","status_desc","status_detail","pulled_at_utc","source"])
    _ensure_csv_exists(OUT_TEAM_LOGS, columns=["event_id","team_id","team","home_away","game_datetime_utc","game_date","game_date_utc","venue","points_for","points_against","margin","fga","fta","tov","orb","drb","reb","poss","efg","ftr","3par","tov_pct","orb_pct","drb_pct","ortg","drtg","netrtg","pace","data_ok","completed","state","status_desc","status_detail","pulled_at_utc","source","parse_version"])
    _ensure_csv_exists(OUT_TEAM_FEATURES, columns=["event_id","team_id","team","home_away","game_datetime_utc"])
    _ensure_csv_exists(OUT_MATCHUPS, columns=["event_id"])
    _ensure_csv_exists(OUT_DIAGNOSTICS, columns=["event_id","team_id","team","diagnostic_reason"])
    _ensure_csv_exists(OUT_DQ_AUDIT, columns=["event_id","team_id","team","dq_missing_fields","dq_reason_codes","dq_action_plan","dq_repair_success","dq_repair_actions_taken","pulled_at_utc","parse_version"])

    # PASS 0: Build games CSV
    games_df = build_espn_games_csv(days_back=days_back, out_csv=OUT_GAMES, verbose=True)
    if games_df.empty:
        print("No games from scoreboard. Exiting.")
        write_error_summary()
        return

    now_pst = datetime.now(TZ_PST)
    window_dates = {(now_pst - timedelta(days=i)).strftime("%Y%m%d") for i in range(days_back)}
    run_window = games_df[games_df["date"].astype(str).isin(window_dates)].copy()
    game_ids = run_window["game_id"].astype(str).unique().tolist()
    print(f"Scoreboard game_ids in run window: {len(game_ids)}")

    checkpoint = load_checkpoint()
    processed = set(map(str, checkpoint.get("processed_game_ids", [])))

    team_rows = []
    errors = 0

    for i, gid in enumerate(game_ids, 1):
        if str(gid) in processed:
            continue

        try:
            s = fetch_and_parse_espn_summary(gid)
            hrow, arow = summary_to_team_rows(s)
            team_rows.append(hrow)
            team_rows.append(arow)
            processed.add(str(gid))
        except Exception as e:
            errors += 1
            log_error("summary_parse", e, event_id=str(gid))
            if errors <= 10:
                print(f"[WARN] summary parse failed for event {gid}: {e}")

        if i % CHECKPOINT_EVERY_N_GAMES == 0:
            print(f"Parsed {i}/{len(game_ids)} summaries (including skips from checkpoint)...")
            save_checkpoint({
                "processed_game_ids": list(processed),
                "last_updated_utc": _utc_now_iso(),
                "errors_so_far": len(ERROR_LOG),
            })

        if days_back >= 30:
            time.sleep(0.15)

    if not team_rows:
        print("No team rows parsed (or all were already checkpointed). Exiting.")
        write_error_summary()
        return

    # Success path: we have new rows, so clear checkpoint now
    clear_checkpoint()

    # PASS 1: Compute metrics, dedupe, write team logs
    df_logs_new = pd.DataFrame(team_rows)
    df_logs_new["event_id"] = _normalize_id_series(df_logs_new["event_id"])
    df_logs_new["team_id"] = _normalize_id_series(df_logs_new["team_id"])
    df_logs_new["home_away"] = _normalize_home_away_series(df_logs_new["home_away"])
    df_logs_new["game_dt"] = pd.to_datetime(df_logs_new["game_datetime_utc"], utc=True, errors="coerce")

    df_logs_new = _compute_per_game_advanced_metrics(df_logs_new)

    df_logs_new = _dedupe_by_completeness(df_logs_new, keys=["event_id", "team_id"], label="PASS1 logs_new")
    df_logs_new = _drop_bad_event_ids_keep_good(df_logs_new, label="PASS1 logs_new symmetry")

    df_logs_all = _append_dedupe_write(
        OUT_TEAM_LOGS,
        df_logs_new.drop(columns=["game_dt"], errors="ignore"),
        subset_keys=["event_id", "team_id"],
        sort_cols=["game_datetime_utc", "event_id", "team_id", "home_away"],
    )
    print(f"{OUT_TEAM_LOGS} total rows: {len(df_logs_all)}")

    # PASS 2: Load all historical logs, normalize, filter
    df = df_logs_all.copy()
    df["event_id"] = _normalize_id_series(df["event_id"])
    df["team_id"] = _normalize_id_series(df["team_id"])
    if "home_away" in df.columns:
        df["home_away"] = _normalize_home_away_series(df["home_away"])
        bad = df[~df["home_away"].isin(list(VALID_HOME_AWAY)) & df["home_away"].notna()]
        if len(bad) > 0:
            print(f"[WARN] Found {len(bad)} invalid home_away values in historical logs. Dropping them.")
            df = df[df["home_away"].isin(list(VALID_HOME_AWAY)) | df["home_away"].isna()].copy()

    df["game_dt"] = pd.to_datetime(df["game_datetime_utc"], utc=True, errors="coerce")
    df = df.sort_values(["team_id", "game_dt", "event_id", "home_away"])

    df_clean = df[df["data_ok"] == True].copy()
    print(f"PASS2: {len(df_clean)}/{len(df)} rows with data_ok=True")

    # PASS 3: Rolling features (all games, home/away splits)
    df_clean = _add_rolling_pack(df_clean, group_cols=["team_id"], prefix="")
    df_clean = _add_rolling_pack(df_clean, group_cols=["team_id", "home_away"], prefix="ha_")
    df_clean = _add_noblow_rollups(df_clean, group_cols=["team_id"], prefix="")
    df_clean = _add_noblow_rollups(df_clean, group_cols=["team_id", "home_away"], prefix="ha_")
    print(f"PASS3: Rolling features computed on {len(df_clean)} rows")

    # PASS 4: Time-based features
    df_clean = _time_window_counts_per_team(df_clean)
    print(f"PASS4: Time window features added")

    # PASS 5: Opponent merge
    df_clean = _merge_opponent_rows(df_clean)
    
    # Check merge success
    opp_join_rate = df_clean["opp_join_ok"].sum() / len(df_clean) if len(df_clean) > 0 else 0
    print(f"PASS5: Opponent merge complete. Join rate: {opp_join_rate*100:.2f}%")
    
    if opp_join_rate < GATE_MIN_OPP_JOIN_RATE_FINAL:
        print(f"[WARN] Opponent join rate {opp_join_rate*100:.2f}% below gate {GATE_MIN_OPP_JOIN_RATE_FINAL*100:.2f}%")

    # Write features CSV
    df_features = _append_dedupe_write(
        OUT_TEAM_FEATURES,
        df_clean.drop(columns=["game_dt"], errors="ignore"),
        subset_keys=["event_id", "team_id"],
        sort_cols=["game_datetime_utc", "event_id", "team_id", "home_away"],
    )
    print(f"{OUT_TEAM_FEATURES} total rows: {len(df_features)}")

    # Build matchups table
    df_matchups = build_matchups_model_ready(df_features)
    
    if DRY_RUN:
        print(f"[DRY RUN] Would write {len(df_matchups)} rows -> {OUT_MATCHUPS}")
    else:
        _atomic_csv_write(df_matchups, OUT_MATCHUPS)
        print(f"{OUT_MATCHUPS} written: {len(df_matchups)} rows")

    # Write diagnostics if enabled
    if WRITE_DIAGNOSTICS:
        diagnostics = []
        for _, row in df_features.iterrows():
            issues = []
            if pd.isna(row.get("poss")):
                issues.append("missing_poss")
            if pd.isna(row.get("ortg")):
                issues.append("missing_ortg")
            if not row.get("opp_join_ok"):
                issues.append("opp_join_failed")
            
            if issues:
                diagnostics.append({
                    "event_id": row.get("event_id"),
                    "team_id": row.get("team_id"),
                    "team": row.get("team"),
                    "diagnostic_reason": "|".join(issues),
                })
        
        if diagnostics:
            df_diag = pd.DataFrame(diagnostics)
            if DRY_RUN:
                print(f"[DRY RUN] Would write {len(df_diag)} diagnostic rows")
            else:
                _atomic_csv_write(df_diag, OUT_DIAGNOSTICS)
                print(f"{OUT_DIAGNOSTICS} written: {len(df_diag)} rows")

    print(f"Run finished. Summary parse errors: {errors}")
    write_error_summary()


def main():
    run_pipeline(days_back=DEFAULT_DAYS_BACK)


if __name__ == "__main__":
    main()

