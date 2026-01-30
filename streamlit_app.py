import os
import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import requests
from zoneinfo import ZoneInfo
from dateutil import parser as date_parser
from difflib import get_close_matches

from supabase import create_client, Client

st.set_page_config(page_title="CBB Betting Model", page_icon="🏀", layout="wide")

MODEL_VERSION = "2026-01-13-m1-m3-fixed"
LOCAL_TIMEZONE = "America/Los_Angeles"
ESPN_GROUP = "50"
ESPN_LIMIT = "500"

# ============================================================
# UTILITY HELPERS
# ============================================================

def safe_float(value, default=0.0):
    """Safely convert to float with fallback"""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default

def safe_get(data_dict, key, default=0):
    """Legacy helper - kept for compatibility"""
    if key in data_dict:
        val = data_dict[key]
        return float(val) if pd.notna(val) else default
    if key.lower() in data_dict:
        val = data_dict[key.lower()]
        return float(val) if pd.notna(val) else default
    key_under = key.replace(" ", "_").lower()
    if key_under in data_dict:
        val = data_dict[key_under]
        return float(val) if pd.notna(val) else default
    return default

# ============================================================
# SCHEMA VALIDATION
# ============================================================

REQUIRED_PREDICTION_FIELDS = {
    "game_date",
    "team_a",
    "team_b",
    "ensemble",
    "models",
}

REQUIRED_ENSEMBLE_FIELDS = {
    "prediction",
    "confidence",
    "win_prob",
    "kelly",
    "is_alpha",
}

REQUIRED_KELLY_FIELDS = {
    "kelly_pct",
    "kelly_dollars",
    "recommended",
}

def validate_prediction_payload(prediction_data: dict) -> list[str]:
    errors = []
    if not isinstance(prediction_data, dict):
        return ["Prediction payload must be a dict."]

    missing_fields = REQUIRED_PREDICTION_FIELDS - set(prediction_data.keys())
    if missing_fields:
        errors.append(f"Missing prediction fields: {sorted(missing_fields)}")

    ensemble = prediction_data.get("ensemble")
    if not isinstance(ensemble, dict):
        errors.append("ensemble must be a dict.")
    else:
        missing_ensemble = REQUIRED_ENSEMBLE_FIELDS - set(ensemble.keys())
        if missing_ensemble:
            errors.append(f"Missing ensemble fields: {sorted(missing_ensemble)}")
        kelly = ensemble.get("kelly")
        if not isinstance(kelly, dict):
            errors.append("ensemble.kelly must be a dict.")
        else:
            missing_kelly = REQUIRED_KELLY_FIELDS - set(kelly.keys())
            if missing_kelly:
                errors.append(f"Missing kelly fields: {sorted(missing_kelly)}")

    models = prediction_data.get("models")
    if not isinstance(models, dict) or len(models) == 0:
        errors.append("models must be a non-empty dict.")

    return errors

# ============================================================
# SUPABASE STORAGE
# ============================================================

def _get_supabase_client() -> Client:
    url = None
    key = None

    try:
        url = st.secrets.get("SUPABASE_URL", None)
        key = st.secrets.get("SUPABASE_ANON_KEY", None)
    except Exception:
        pass

    if not url:
        url = os.environ.get("SUPABASE_URL")
    if not key:
        key = os.environ.get("SUPABASE_ANON_KEY")

    if not url or not key:
        st.error("Missing Supabase credentials. Set SUPABASE_URL and SUPABASE_ANON_KEY in Streamlit Secrets or env vars.")
        st.stop()

    return create_client(url, key)

@st.cache_resource
def supabase_client() -> Client:
    return _get_supabase_client()

def has_supabase_creds() -> bool:
    url = None
    key = None
    try:
        url = st.secrets.get("SUPABASE_URL", None)
        key = st.secrets.get("SUPABASE_ANON_KEY", None)
    except Exception:
        pass
    if not url:
        url = os.environ.get("SUPABASE_URL")
    if not key:
        key = os.environ.get("SUPABASE_ANON_KEY")
    return bool(url and key)

def sb_optional_client() -> Client | None:
    if not has_supabase_creds():
        return None
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_ANON_KEY")
    if not url or not key:
        try:
            url = url or st.secrets.get("SUPABASE_URL")
            key = key or st.secrets.get("SUPABASE_ANON_KEY")
        except Exception:
            return None
    if not url or not key:
        return None
    return create_client(url, key)

def make_game_id(team_a: str, team_b: str, game_date: str) -> str:
    return f"{team_a}_vs_{team_b}_{game_date}".replace(" ", "_")

def sb_upsert_prediction(prediction_data: dict) -> str:
    """Fixed version - no duplicate keys"""
    errors = validate_prediction_payload(prediction_data)
    if errors:
        raise ValueError("; ".join(errors))

    sb = supabase_client()

    prediction_key = prediction_data.get("prediction_key") or make_game_id(
        prediction_data["team_a"],
        prediction_data["team_b"],
        prediction_data["game_date"],
    )
    game_id = prediction_data.get("id") or prediction_key

    record = {
        "id": game_id,
        "prediction_key": prediction_key,
        "game_date": prediction_data["game_date"],
        "team_a": prediction_data["team_a"],
        "team_b": prediction_data["team_b"],
        "home_team": prediction_data.get("home_team"),
        "away_team": prediction_data.get("away_team"),
        "venue": prediction_data.get("venue"),
        "ensemble_prediction": safe_float(prediction_data["ensemble"]["prediction"]),
        "confidence": safe_float(prediction_data["ensemble"]["confidence"]),
        "is_alpha": bool(prediction_data["ensemble"]["is_alpha"]),
        "alpha_reasons": prediction_data["ensemble"].get("alpha_reasons", []),
        "vegas_line": safe_float(prediction_data.get("vegas_line")),
        "vegas_edge": safe_float(prediction_data.get("vegas_edge")),
        "ensemble_win_prob": safe_float(prediction_data["ensemble"]["win_prob"]),
        "kelly_pct": safe_float(prediction_data["ensemble"]["kelly"]["kelly_pct"]),
        "kelly_dollars": safe_float(prediction_data["ensemble"]["kelly"]["kelly_dollars"]),
        "kelly_recommended": prediction_data["ensemble"]["kelly"]["recommended"],
        "model_predictions": prediction_data["models"],
        "model_version": prediction_data.get("model_version"),
        "inputs": prediction_data.get("inputs"),
        "actual_team_a_score": None,
        "actual_team_b_score": None,
        "actual_spread": None,
        "won": None,
        "ensemble_error": None,
        "model_accuracy": None,
    }

    sb.table("predictions").upsert(record).execute()
    return game_id

def sb_fetch_predictions(limit: int = 5000) -> list:
    sb = supabase_client()
    resp = (
        sb.table("predictions")
        .select("*")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return resp.data or []

def sb_fetch_predictions_by_keys(prediction_keys: list[str]) -> list:
    if not prediction_keys:
        return []
    sb = supabase_client()
    resp = (
        sb.table("predictions")
        .select("*")
        .in_("prediction_key", prediction_keys)
        .execute()
    )
    return resp.data or []

def sb_fetch_pending_predictions(limit: int = 5000) -> list:
    sb = supabase_client()
    resp = (
        sb.table("predictions")
        .select("*")
        .is_("actual_team_a_score", "null")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return resp.data or []

def sb_update_prediction_result(game_id: str, team_a_score: int, team_b_score: int) -> dict:
    sb = supabase_client()

    existing = sb.table("predictions").select("*").eq("id", game_id).limit(1).execute().data
    if not existing:
        raise ValueError("Prediction not found in Supabase.")

    pred = existing[0]

    actual_spread = float(team_a_score - team_b_score)
    ensemble_pred = safe_float(pred.get("ensemble_prediction"), 0.0)

    ensemble_correct = (
        (ensemble_pred > 0 and actual_spread > 0)
        or (ensemble_pred < 0 and actual_spread < 0)
    )
    ensemble_error = abs(ensemble_pred - actual_spread)

    model_predictions = pred.get("model_predictions") or {}
    model_accuracy = {}
    for model_name, model_data in model_predictions.items():
        mp = safe_float(model_data.get("prediction"), 0.0)
        correct = (
            (mp > 0 and actual_spread > 0)
            or (mp < 0 and actual_spread < 0)
        )
        model_accuracy[model_name] = {
            "correct": bool(correct),
            "error": float(abs(mp - actual_spread)),
        }

    patch = {
        "actual_team_a_score": int(team_a_score),
        "actual_team_b_score": int(team_b_score),
        "actual_spread": actual_spread,
        "won": bool(ensemble_correct),
        "ensemble_error": ensemble_error,
        "model_accuracy": model_accuracy,
    }

    sb.table("predictions").update(patch).eq("id", game_id).execute()
    pred.update(patch)
    return pred

def sb_log_data_quality(source: str, error_message: str, payload: dict | None = None) -> None:
    sb = sb_optional_client()
    if sb is None:
        return
    record = {
        "source": source,
        "error_message": error_message,
        "payload": payload or {},
    }
    try:
        sb.table("data_quality_logs").insert(record).execute()
    except Exception:
        pass

def sb_get_performance_stats() -> dict | None:
    rows = sb_fetch_predictions(limit=5000)
    completed = [r for r in rows if r.get("actual_team_a_score") is not None and r.get("actual_team_b_score") is not None]

    if len(completed) == 0:
        return None

    ensemble_correct = sum(1 for r in completed if r.get("won") is True)
    ensemble_accuracy = ensemble_correct / len(completed)

    errors = [safe_float(r.get("ensemble_error"), 0.0) for r in completed if r.get("ensemble_error") is not None]
    avg_error = float(np.mean(errors)) if errors else 0.0

    total_kelly = sum(safe_float(r.get("kelly_dollars"), 0.0) for r in completed)

    model_names = ["M1_Schedule", "M2_FourFactors", "M3_Bidirectional", "M4_Situational"]
    model_stats = {}
    for mn in model_names:
        correct = 0
        errs = []
        denom_rows = 0
        for r in completed:
            ma = (r.get("model_accuracy") or {}).get(mn)
            if not ma:
                continue
            denom_rows += 1
            if ma.get("correct"):
                correct += 1
            e = ma.get("error")
            if e is not None:
                errs.append(float(e))
        
        if denom_rows == 0:
            continue
        
        model_stats[mn] = {
            "accuracy": correct / denom_rows,
            "correct": correct,
            "total": denom_rows,
            "avg_error": float(np.mean(errs)) if errs else None,
        }

    alpha_predictions = sum(1 for r in rows if r.get("is_alpha") is True)

    return {
        "total_predictions": len(completed),
        "ensemble_accuracy": ensemble_accuracy,
        "ensemble_correct": ensemble_correct,
        "avg_error": avg_error,
        "total_kelly_bet": total_kelly,
        "model_stats": model_stats,
        "alpha_predictions": alpha_predictions,
        "recent": completed[:10],
    }

# ============================================================
# LOAD DATA
# ============================================================

@st.cache_data
def load_data(data_version):
    bart = pd.read_csv("barttorvik.csv")
    games = pd.read_csv("espn_games.csv")
    teams_esp = pd.read_csv("espn_teams.csv")

    try:
        hasla = pd.read_csv("haslametrics.csv")
    except Exception:
        hasla = pd.DataFrame()

    bart.columns = [str(col).strip().lower().replace(" ", "_").replace(".", "") for col in bart.columns]
    if "adjoe" in bart.columns and "adjde" in bart.columns:
        bart["adjem"] = bart["adjoe"] - bart["adjde"]

    if len(hasla) > 0:
        hasla.columns = [str(col).strip().lower().replace(" ", "_").replace(".", "") for col in hasla.columns]

    return bart, hasla, games, teams_esp

def get_data_version():
    files = ["barttorvik.csv", "espn_games.csv", "espn_teams.csv", "haslametrics.csv"]
    return tuple(os.path.getmtime(f) if os.path.exists(f) else 0 for f in files)

bart_clean, hasla_clean, espn_games, espn_teams = load_data(get_data_version())

# ============================================================
# ESPN SCOREBOARD - FIXED
# ============================================================

ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
UA_HEADERS = {"User-Agent": "Mozilla/5.0"}
LOCAL_TZ = ZoneInfo(LOCAL_TIMEZONE)

@st.cache_data(ttl=900)
def get_d1_team_id_set() -> set[str]:
    try:
        df = pd.read_csv("espn_teams.csv")
        if "id" in df.columns:
            ids = df["id"].dropna().astype(str).tolist()
        elif "espn_id" in df.columns:
            ids = df["espn_id"].dropna().astype(str).tolist()
        elif "team_id" in df.columns:
            ids = df["team_id"].dropna().astype(str).tolist()
        else:
            ids = []
        return set(ids)
    except Exception:
        return set()

def _dates_list(days_ahead: int, tz=LOCAL_TZ) -> list[str]:
    base = datetime.now(tz).date()
    return [(base + timedelta(days=i)).strftime("%Y%m%d") for i in range(days_ahead)]

def _parse_start_utc_to_local_dt(iso_utc: str, tz=LOCAL_TZ) -> datetime | None:
    if not iso_utc:
        return None
    try:
        dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
        return dt.astimezone(tz)
    except Exception:
        return None

def _pick_home_away(competitors: list) -> tuple[dict | None, dict | None]:
    home = None
    away = None
    for competitor in competitors or []:
        ha = (competitor.get("homeAway") or "").lower()
        if ha == "home":
            home = competitor
        elif ha == "away":
            away = competitor

    if (home is None or away is None) and competitors and len(competitors) >= 2:
        home = home or competitors[0]
        away = away or competitors[1]

    return home, away

def _extract_odds(comp: dict) -> dict:
    odds_list = comp.get("odds") or []
    if not odds_list:
        return {
            "odds_provider": None,
            "vegas_spread": None,
            "vegas_total": None,
            "vegas_details": None,
            "ml_home": None,
            "ml_away": None,
        }

    odds = odds_list[0]
    provider = (odds.get("provider") or {}).get("name") or (odds.get("provider") or {}).get("displayName")
    details = odds.get("details")
    total = safe_float(odds.get("overUnder"))

    spread = safe_float(odds.get("spread"))
    if spread is None and details:
        try:
            spread = safe_float(str(details).split()[-1])
        except Exception:
            spread = None

    moneyline = odds.get("moneyline") or {}
    ml_home = ((moneyline.get("home") or {}).get("close") or {}).get("odds")
    ml_away = ((moneyline.get("away") or {}).get("close") or {}).get("odds")

    return {
        "odds_provider": provider,
        "vegas_spread": spread,
        "vegas_total": total,
        "vegas_details": details,
        "ml_home": ml_home,
        "ml_away": ml_away,
    }

def _fetch_scoreboard_json(date_yyyymmdd: str, timeout=20) -> dict:
    params = {"dates": date_yyyymmdd, "groups": ESPN_GROUP, "limit": ESPN_LIMIT}
    r = requests.get(ESPN_SCOREBOARD_URL, params=params, headers=UA_HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()

def _normalize_scoreboard(requested_date: str, data: dict, d1_ids: set[str]) -> tuple[pd.DataFrame, dict]:
    rows = []
    events = data.get("events") or []
    stats = {
        "requested_date": requested_date,
        "events_total": len(events),
        "missing_competitors": 0,
        "missing_names": 0,
        "non_d1_filtered": 0,
        "missing_event_id": 0,
        "rows_kept": 0,
    }

    for ev in events:
        event_id = str(ev.get("id") or "")
        if not event_id:
            stats["missing_event_id"] += 1
            continue
        start_utc = ev.get("date")

        local_dt = _parse_start_utc_to_local_dt(start_utc)
        if local_dt is None:
            local_game_date = requested_date
            local_time_str = None
        else:
            local_game_date = local_dt.strftime("%Y%m%d")
            local_time_str = local_dt.strftime("%Y-%m-%d %I:%M %p %Z")

        competitions = ev.get("competitions") or []
        comp = competitions[0] if competitions else {}

        home_c, away_c = _pick_home_away(comp.get("competitors") or [])
        if home_c is None or away_c is None:
            stats["missing_competitors"] += 1
            continue

        home_team = (home_c.get("team") or {})
        away_team = (away_c.get("team") or {})

        home_name = home_team.get("displayName")
        away_name = away_team.get("displayName")
        home_id = str(home_team.get("id") or "")
        away_id = str(away_team.get("id") or "")

        if not home_name or not away_name:
            stats["missing_names"] += 1
            continue

        if d1_ids and ((home_id not in d1_ids) or (away_id not in d1_ids)):
            stats["non_d1_filtered"] += 1
            continue

        venue = (comp.get("venue") or {}).get("fullName")
        status_type = ((ev.get("status") or {}).get("type") or {})
        status_state = status_type.get("state")
        status_desc = status_type.get("description")
        status_detail = status_type.get("detail") or status_type.get("shortDetail")

        broadcast = None
        broadcasts = ev.get("broadcasts") or []
        if broadcasts:
            names = (broadcasts[0] or {}).get("names") or []
            if names:
                broadcast = names[0]

        odds = _extract_odds(comp)

        today = datetime.now(LOCAL_TZ).date()
        game_day = datetime.strptime(local_game_date, "%Y%m%d").date()
        off = (game_day - today).days
        day_label = "TODAY" if off == 0 else "TOMORROW" if off == 1 else f"+{off} days"

        rows.append({
            "game_id": event_id,
            "game_date": local_game_date,
            "requested_date": requested_date,
            "day_label": day_label,
            "home_team": home_name,
            "away_team": away_name,
            "home_team_id": home_id,
            "away_team_id": away_id,
            "start_time_utc": start_utc,
            "event_time_local": local_time_str,
            "status_state": status_state,
            "status": status_desc,
            "status_detail": status_detail,
            "venue": venue,
            "broadcast": broadcast,
            "odds_provider": odds["odds_provider"],
            "vegas_spread": odds["vegas_spread"],
            "vegas_total": odds["vegas_total"],
            "vegas_details": odds["vegas_details"],
            "vegas_moneyline_home": odds["ml_home"],
            "vegas_moneyline_away": odds["ml_away"],
            "prediction_key": f"{local_game_date}:{event_id}",
        })
        stats["rows_kept"] += 1

    df = pd.DataFrame(rows)
    if len(df) == 0:
        return df, stats

    return df.sort_values(["game_date", "start_time_utc", "home_team"], ascending=[True, True, True]), stats

@st.cache_data(ttl=900)
def get_upcoming_games(days_ahead: int = 7) -> tuple[pd.DataFrame, list[dict], list[dict]]:
    errors = []
    all_dfs = []
    stats_list = []

    d1_ids = get_d1_team_id_set()
    if not d1_ids:
        sb_log_data_quality(
            source="d1_filter",
            error_message="espn_teams.csv missing/invalid, D1 filtering disabled",
            payload={},
        )

    for date_str in _dates_list(days_ahead, tz=LOCAL_TZ):
        try:
            data = _fetch_scoreboard_json(date_str)
            df, stats = _normalize_scoreboard(date_str, data, d1_ids)
            all_dfs.append(df)
            stats_list.append(stats)
        except Exception as exc:
            errors.append({"date": date_str, "error": str(exc)})
            sb_log_data_quality(
                source="espn_scoreboard",
                error_message=str(exc),
                payload={"date": date_str, "url": f"{ESPN_SCOREBOARD_URL}?dates={date_str}"},
            )

    if not all_dfs:
        return pd.DataFrame(), errors, stats_list

    df = pd.concat(all_dfs, ignore_index=True)
    if len(df) > 0:
        df = df.drop_duplicates(subset=["game_id"], keep="first")

    return df, errors, stats_list

# ============================================================
# TEAM NAME MATCHING - FIXED
# ============================================================

MANUAL_TEAM_MAPPINGS = {
    "uconn": "connecticut",
    "unc": "north carolina",
    "nc state": "north carolina state",
    "lsu": "louisiana state",
    "vcu": "virginia commonwealth",
    "smu": "southern methodist",
    "tcu": "texas christian",
    "byu": "brigham young",
    "unlv": "nevada las vegas",
    "utep": "texas el paso",
    "utsa": "texas san antonio",
    "uab": "alabama birmingham",
    "ucf": "central florida",
    "fiu": "florida international",
    "liu": "long island university",
    "umass": "massachusetts",
    "usc": "southern california",
    "ucsd": "california san diego",
    "ucsb": "california santa barbara",
    "uci": "california irvine",
    "ucr": "california riverside",
    "cal": "california",
    "pitt": "pittsburgh",
    "ole miss": "mississippi",
    "miami": "miami florida",
    "miami fl": "miami florida",
    "miami oh": "miami ohio",
    "penn": "pennsylvania",
    "nc": "north carolina",
    "sc": "south carolina",
    "the ohio state": "ohio state",
    "the citadel": "citadel",
    "st johns": "st john's",
    "st josephs": "st joseph's",
    "st marys": "st mary's",
    "st peters": "st peter's",
    "st thomas": "st thomas",
    "st bonaventure": "st bonaventure",
    "massachusetts lowell": "umass lowell",
    "texas arlington": "ut arlington",
    "texas rio grande valley": "ut rio grande valley",
    "texas san antonio": "utsa",
}

def normalize_team_name(team_name):
    if not isinstance(team_name, str):
        return ""
    cleaned = team_name.lower().strip()
    cleaned = cleaned.replace("&", "and")
    cleaned = cleaned.replace("st.", "state").replace("st ", "state ")
    cleaned = "".join(ch for ch in cleaned if ch.isalnum() or ch.isspace())
    cleaned = " ".join(cleaned.split())
    return MANUAL_TEAM_MAPPINGS.get(cleaned, cleaned)

bart_clean["team_normalized"] = bart_clean["team"].apply(normalize_team_name)

@st.cache_data
def build_team_name_map(bart_df, espn_df):
    mapping = {}
    bart_names = bart_df["team"].tolist()
    bart_norm = {normalize_team_name(name): name for name in bart_names}

    for name in bart_names:
        mapping[normalize_team_name(name)] = name

    if "name" in espn_df.columns:
        for espn_name in espn_df["name"].dropna().unique():
            norm = normalize_team_name(espn_name)
            if norm in bart_norm:
                mapping[norm] = bart_norm[norm]
                continue
            close = get_close_matches(norm, bart_norm.keys(), n=1, cutoff=0.92)
            if close:
                mapping[norm] = bart_norm[close[0]]

    return mapping

TEAM_NAME_MAP = build_team_name_map(bart_clean, espn_teams)

def find_team_row(team_name):
    direct = bart_clean[bart_clean["team"] == team_name]
    if len(direct) > 0:
        return direct
    normalized = normalize_team_name(team_name)
    mapped = TEAM_NAME_MAP.get(normalized, normalized)
    mapped_norm = normalize_team_name(mapped)
    return bart_clean[bart_clean["team_normalized"] == mapped_norm]

# ============================================================
# RECENT FORM HELPERS - FIXED
# ============================================================

def _coerce_date_series(series):
    if series is None:
        return None
    try:
        return pd.to_datetime(series, format="%Y%m%d", errors="coerce")
    except Exception:
        return pd.to_datetime(series, errors="coerce")

def _get_game_date_col(games_df):
    for col in ["date", "game_date", "start_date", "startTime", "start_time"]:
        if col in games_df.columns:
            return col
    return None

def _normalize_team_name_basic(value):
    return normalize_team_name(value)

def _team_games(games_df, team):
    team_norm = _normalize_team_name_basic(team)
    if "home_team" not in games_df.columns or "away_team" not in games_df.columns:
        return games_df.iloc[0:0].copy()
    home_norm = games_df["home_team"].astype(str).apply(_normalize_team_name_basic)
    away_norm = games_df["away_team"].astype(str).apply(_normalize_team_name_basic)
    return games_df[(home_norm == team_norm) | (away_norm == team_norm)].copy()

def _calc_margin_row(row, team):
    team_norm = _normalize_team_name_basic(team)
    home = _normalize_team_name_basic(row.get("home_team"))
    if home == team_norm:
        return float(row.get("home_score", 0) - row.get("away_score", 0))
    return float(row.get("away_score", 0) - row.get("home_score", 0))

def _get_opponent(row, team):
    team_norm = _normalize_team_name_basic(team)
    home = _normalize_team_name_basic(row.get("home_team"))
    away = _normalize_team_name_basic(row.get("away_team"))
    return away if home == team_norm else home

def build_recent_profile(games_df, team, n=7):
    """Fixed version - removes broken z-score calculation"""
    team_norm = _normalize_team_name_basic(team)
    
    if games_df is None or len(games_df) == 0:
        return {
            "games_played": 0,
            "margin_last_n": 0.0,
            "wins_last_n": 0,
            "avg_points_for": 0.0,
            "avg_points_against": 0.0,
            "opponent_quality_avg": 0.0,
        }

    df = games_df.copy()
    date_col = _get_game_date_col(df)
    if date_col:
        df["_dt"] = _coerce_date_series(df[date_col])
    else:
        df["_dt"] = pd.NaT

    for col in ["home_score", "away_score"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df[df["home_score"].notna() & df["away_score"].notna()].copy()

    team_df = _team_games(df, team_norm)
    if len(team_df) == 0:
        return {
            "games_played": 0,
            "margin_last_n": 0.0,
            "wins_last_n": 0,
            "avg_points_for": 0.0,
            "avg_points_against": 0.0,
            "opponent_quality_avg": 0.0,
        }

    if date_col:
        team_df = team_df.sort_values("_dt", ascending=False)
    else:
        team_df = team_df.iloc[::-1]

    team_df = team_df.head(n).copy()

    margins = []
    wins = 0
    pts_for = []
    pts_against = []
    opponent_list = []

    for _, row in team_df.iterrows():
        margin = _calc_margin_row(row, team_norm)
        margins.append(margin)
        if margin > 0:
            wins += 1

        if _normalize_team_name_basic(row.get("home_team")) == team_norm:
            pf = float(row.get("home_score", 0))
            pa = float(row.get("away_score", 0))
        else:
            pf = float(row.get("away_score", 0))
            pa = float(row.get("home_score", 0))

        pts_for.append(pf)
        pts_against.append(pa)
        opponent_list.append(_get_opponent(row, team_norm))

    games_played = len(margins)
    margin_last_n = float(np.mean(margins)) if games_played else 0.0
    avg_pf = float(np.mean(pts_for)) if games_played else 0.0
    avg_pa = float(np.mean(pts_against)) if games_played else 0.0

    # Opponent quality: simple average of their recent margins
    opponent_margins = []
    for opponent in opponent_list:
        opp_df = _team_games(df, opponent)
        if len(opp_df) == 0:
            continue
        
        if date_col:
            opp_df = opp_df.sort_values("_dt", ascending=False)
        opp_df = opp_df.head(n)
        
        opp_margins = [_calc_margin_row(r, opponent) for _, r in opp_df.iterrows()]
        if opp_margins:
            opponent_margins.append(float(np.mean(opp_margins)))

    opponent_quality = float(np.mean(opponent_margins)) if opponent_margins else 0.0

    return {
        "games_played": games_played,
        "margin_last_n": round(margin_last_n, 3),
        "wins_last_n": wins,
        "avg_points_for": round(avg_pf, 2),
        "avg_points_against": round(avg_pa, 2),
        "opponent_quality_avg": round(opponent_quality, 3),
    }

# ============================================================
# MODELS - FIXED
# ============================================================

def model1_recent_form(A, B):
    """Fixed Model 1: Schedule-adjusted with recent form"""
    adjem_a = safe_get(A, "adjem", 0)
    adjem_b = safe_get(B, "adjem", 0)
    base = adjem_a - adjem_b

    m7_a = safe_get(A, "margin_last_7", 0)
    m7_b = safe_get(B, "margin_last_7", 0)
    recent = m7_a - m7_b
    recent_component = 3.0 * np.tanh(recent / 8.0)

    sos_a = safe_get(A, "sos", 0)
    sos_b = safe_get(B, "sos", 0)
    sos_component = 0.0
    if sos_a and sos_b:
        sos_component = 0.8 * np.tanh((sos_a - sos_b) / 20.0)

    return float(base + recent_component + sos_component)

def model2_four_factors(A, B):
    efg_a = safe_get(A, "efg", 0.50)
    efg_b = safe_get(B, "efg", 0.50)
    tov_a = safe_get(A, "tov", 18)
    tov_b = safe_get(B, "tov", 18)
    orb_a = safe_get(A, "orb", 28)
    drb_b = safe_get(B, "drb", 72)
    ftr_a = safe_get(A, "ftr", 0.30)
    ftr_b = safe_get(B, "ftr", 0.30)

    efg_impact = (efg_a - efg_b) * 100
    tov_impact = (tov_b - tov_a) * 0.5
    reb_impact = (orb_a - drb_b) * 0.3
    ft_impact = (ftr_a - ftr_b) * 10

    return efg_impact * 0.4 + tov_impact * 0.3 + reb_impact * 0.2 + ft_impact * 0.1

def model3_bidirectional_updated(A, B, team_logs_df=None):
    """Fixed Model 3: Bidirectional compatibility"""
    factors = {}

    eff = safe_get(A, "adjem", 0) - safe_get(B, "adjem", 0)
    factors["efficiency"] = eff * 1.00

    mom = safe_get(A, "margin_last_7", 0) - safe_get(B, "margin_last_7", 0)
    factors["momentum"] = 0.90 * np.tanh(mom / 8.0) * 5.0

    win_a = safe_get(A, "barthag", 0.5)
    win_b = safe_get(B, "barthag", 0.5)
    factors["win_pct"] = (win_a - win_b) * 8.0

    if team_logs_df is not None and len(team_logs_df) > 0:
        def _recent_team_logs(team, n=7):
            team_df = team_logs_df[team_logs_df["team"].astype(str) == str(team)].copy()
            if len(team_df) == 0:
                return None
            if "game_date" in team_df.columns:
                team_df["_dt"] = _coerce_date_series(team_df["game_date"])
                team_df = team_df.sort_values("_dt", ascending=False)
            return team_df.head(n)

        A_logs = _recent_team_logs(A.get("team", ""), 7)
        B_logs = _recent_team_logs(B.get("team", ""), 7)

        if A_logs is not None and B_logs is not None:
            A_efg = float(A_logs["efg"].mean())
            B_efg = float(B_logs["efg"].mean())
            A_tov = float(A_logs["tov_pct"].mean())
            B_tov = float(B_logs["tov_pct"].mean())
            A_orb = float(A_logs["orb_pct"].mean())
            B_orb = float(B_logs["orb_pct"].mean())
            A_ftr = float(A_logs["ftr"].mean())
            B_ftr = float(B_logs["ftr"].mean())

            factors["box_efg"] = (A_efg - B_efg) * 12.0
            factors["box_tov"] = (B_tov - A_tov) * 6.0
            factors["box_orb"] = (A_orb - B_orb) * 5.0
            factors["box_ftr"] = (A_ftr - B_ftr) * 4.0

    raw_signals = [
        np.sign(eff),
        np.sign(mom),
        np.sign(win_a - win_b),
    ]
    agreement = abs(sum(raw_signals))
    factors["agreement_bonus"] = 0.6 if agreement == 3 else (0.2 if agreement == 1 else 0.0)

    return float(sum(factors.values())), factors

def model4_situational(A, B):
    tempo_a = safe_get(A, "adjt", 70)
    tempo_b = safe_get(B, "adjt", 70)
    return (tempo_a - tempo_b) * 0.15

def home_court_advantage(venue):
    venues = {"Cameron Indoor Stadium": 4.2, "Allen Fieldhouse": 4.0, "Rupp Arena": 3.8}
    return venues.get(venue, 2.7)

def calculate_confidence_updated(models, A, B):
    """Fixed confidence calculation"""
    values = [v for v in models.values() if isinstance(v, (int, float, np.floating))]
    
    if len(values) == 0:
        return 0.70
    
    variance = float(max(values) - min(values))
    base = float(np.clip(1.0 - variance / 22.0, 0.70, 0.99))

    games_a = int(A.get("games_played", 7))
    games_b = int(B.get("games_played", 7))
    min_games = min(games_a, games_b)
    data_penalty = 0.05 * max(0, 5 - min_games) / 5.0

    tempo_a = safe_float(A.get("adjt"), 70.0)
    tempo_b = safe_float(B.get("adjt"), 70.0)
    tempo_diff = abs(tempo_a - tempo_b)
    tempo_penalty = 0.03 if tempo_diff > 8.0 else 0.0

    opp_qual_a = safe_float(A.get("opponent_quality_avg"), 0.0)
    opp_qual_b = safe_float(B.get("opponent_quality_avg"), 0.0)
    opp_diff = opp_qual_a - opp_qual_b
    opp_adjustment = np.clip(opp_diff / 200.0, -0.03, 0.03)

    final_conf = base - data_penalty - tempo_penalty + opp_adjustment
    return float(np.clip(final_conf, 0.70, 0.98))

def detect_alpha(pred, conf, models, vegas=None):
    is_alpha = False
    reasons = []

    if abs(pred) > 2.5 and conf > 0.90:
        is_alpha = True
        reasons.append(f"High conf ({conf:.0%})")

    if vegas is not None and abs(pred - vegas) > 3.0:
        is_alpha = True
        reasons.append(f"Vegas edge {abs(pred - vegas):.1f}pts")

    signs = [1 if v > 0 else -1 for v in models.values() if isinstance(v, (int, float))]
    if len(signs) > 0 and len(set(signs)) == 1 and abs(pred) > 2.0:
        is_alpha = True
        reasons.append("Unanimous")

    return {"is_alpha": is_alpha, "reasons": reasons}

def calculate_kelly_bet(win_prob, odds=-110, bankroll=1000, kelly_fraction=0.25):
    if odds > 0:
        decimal_odds = (odds / 100) + 1
    else:
        decimal_odds = (100 / abs(odds)) + 1

    b = decimal_odds - 1
    p = win_prob
    q = 1 - p

    kelly_pct = (b * p - q) / b
    kelly_pct = kelly_pct * kelly_fraction
    kelly_pct = max(0, min(kelly_pct, 0.05))

    kelly_dollars = bankroll * kelly_pct

    return {
        "kelly_pct": round(kelly_pct, 4),
        "kelly_dollars": round(kelly_dollars, 2),
        "recommended": "BET" if kelly_pct > 0.01 else "PASS",
    }

# ============================================================
# PREDICT GAME - FIXED (NO AUTO-SAVE)
# ============================================================

def predict_game(team_a_name, team_b_name, home_a=True, venue="default", 
                 vegas=None, bankroll=1000, game_date=None):
    """Fixed version - does NOT auto-save to Supabase"""
    A = find_team_row(team_a_name)
    B = find_team_row(team_b_name)

    if len(A) == 0 or len(B) == 0:
        return None

    A_data = A.iloc[0].to_dict()
    B_data = B.iloc[0].to_dict()

    A_prof = build_recent_profile(espn_games, team_a_name, n=7)
    B_prof = build_recent_profile(espn_games, team_b_name, n=7)

    A_data["margin_last_7"] = A_prof["margin_last_n"]
    B_data["margin_last_7"] = B_prof["margin_last_n"]
    A_data["opponent_quality_avg"] = A_prof["opponent_quality_avg"]
    B_data["opponent_quality_avg"] = B_prof["opponent_quality_avg"]
    A_data["games_played"] = A_prof["games_played"]
    B_data["games_played"] = B_prof["games_played"]
    A_data["team"] = team_a_name
    B_data["team"] = team_b_name

    m1 = model1_recent_form(A_data, B_data)
    m2 = model2_four_factors(A_data, B_data)
    m3, _m3_factors = model3_bidirectional_updated(A_data, B_data, team_logs_df=None)
    m4 = model4_situational(A_data, B_data)

    model_predictions = {}
    for model_name, model_pred in [
        ("M1_Schedule", m1),
        ("M2_FourFactors", m2),
        ("M3_Bidirectional", m3),
        ("M4_Situational", m4),
    ]:
        hc = home_court_advantage(venue)
        final_pred = model_pred + (hc if home_a else -hc)
        win_prob = 1 / (1 + 10 ** (-final_pred / 15))
        kelly = calculate_kelly_bet(win_prob, -110, bankroll, 0.25)

        model_predictions[model_name] = {
            "prediction": round(final_pred, 1),
            "win_prob": round(win_prob, 3),
            "kelly": kelly,
        }

    ensemble_pred = 0.45 * m3 + 0.25 * m1 + 0.20 * m2 + 0.10 * m4
    hc = home_court_advantage(venue)
    ensemble_pred += hc if home_a else -hc

    ensemble_win_prob = 1 / (1 + 10 ** (-ensemble_pred / 15))
    ensemble_kelly = calculate_kelly_bet(ensemble_win_prob, -110, bankroll, 0.25)

    models = {
        "M1_Schedule": m1,
        "M2_FourFactors": m2,
        "M3_Bidirectional": m3,
        "M4_Situational": m4
    }
    conf = calculate_confidence_updated(models, A_data, B_data)
    alpha = detect_alpha(ensemble_pred, conf, models, vegas)

    result = {
        "game_date": game_date or datetime.now().strftime("%Y%m%d"),
        "team_a": team_a_name,
        "team_b": team_b_name,
        "home_team": team_a_name if home_a else team_b_name,
        "away_team": team_b_name if home_a else team_a_name,
        "venue": venue,
        "vegas_line": vegas,
        "vegas_edge": round(ensemble_pred - vegas, 1) if vegas is not None else None,
        "ensemble": {
            "prediction": round(ensemble_pred, 1),
            "confidence": conf,
            "win_prob": round(ensemble_win_prob, 3),
            "kelly": ensemble_kelly,
            "is_alpha": alpha["is_alpha"],
            "alpha_reasons": alpha["reasons"],
        },
        "models": model_predictions,
        "timestamp": datetime.now().isoformat(),
    }

    return result

# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.title("🏀 CBB Model")
st.sidebar.markdown("---")

st.sidebar.header("💰 Settings")
bankroll = st.sidebar.number_input("Bankroll ($)", value=1000, step=100, min_value=100)

st.sidebar.markdown("---")

# Filters (only on Upcoming Games page)
show_filters = False
if 'page' in locals():
    show_filters = (page == "📅 Upcoming Games")

if show_filters:
    st.sidebar.subheader("🎯 Filters")
    show_bets_only = st.sidebar.checkbox("💰 Bets Only", value=False)
    show_alpha_only = st.sidebar.checkbox("🚨 Alpha Only", value=False)
    min_conf = st.sidebar.slider("Min Confidence", min_value=0.50, max_value=0.95, value=0.70, step=0.05)
    min_kelly = st.sidebar.number_input("Min Kelly ($)", min_value=0, max_value=500, value=0, step=10)
else:
    show_bets_only = False
    show_alpha_only = False
    min_conf = 0.70
    min_kelly = 0

st.sidebar.markdown("---")

page = st.sidebar.radio(
    "Navigate",
    [
        "📅 Upcoming Games",
        "📆 Today's Schedule",
        "🎯 Single Prediction",
        "📊 Model Performance",
        "📝 Update Results",
    ],
)

st.sidebar.markdown("---")
st.sidebar.caption(f"Updated: {datetime.now().strftime('%m/%d %I:%M%p')}")
st.sidebar.caption(f"{len(bart_clean)} teams | {len(espn_games)} games")
if len(hasla_clean) > 0:
    st.sidebar.caption(f"✅ Haslametrics: {len(hasla_clean)} teams")

# ============================================================
# PAGES
# ============================================================

if page == "📅 Upcoming Games":
    st.title("📅 Upcoming Games")

    if not has_supabase_creds():
        st.error("Supabase credentials are required to load and save predictions.")
        st.stop()

    upcoming, upcoming_errors, upcoming_stats = get_upcoming_games(days_ahead=7)

    st.caption(f"Found {len(upcoming)} D1 games")
    
    if upcoming_errors:
        with st.expander("⚠️ ESPN Fetch Errors", expanded=False):
            for err in upcoming_errors:
                st.error(f"{err['date']}: {err['error']}")

    if st.checkbox("🔍 Debug ESPN API"):
        local_tz = ZoneInfo(LOCAL_TIMEZONE)
        test_date = datetime.now(local_tz).strftime("%Y%m%d")
        test_url = f"{ESPN_SCOREBOARD_URL}?dates={test_date}&groups={ESPN_GROUP}&limit={ESPN_LIMIT}"
        st.code(test_url, language="text")
        
        try:
            data = _fetch_scoreboard_json(test_date)
            st.success(f"✅ Status: OK | Events: {len(data.get('events', []))}")
            
            if st.checkbox("Show raw JSON (first event)"):
                events = data.get("events", [])
                if events:
                    st.json(events[0])
        except Exception as e:
            st.error(f"❌ Fetch failed: {e}")

    if len(upcoming) == 0:
        st.warning("No games found. Check the debug panel above.")
        st.stop()

    # Summary metrics
    day_counts = upcoming["day_label"].value_counts().to_dict()
    cols = st.columns(min(5, len(day_counts)))
    for i, (label, count) in enumerate(sorted(day_counts.items())):
        cols[i % len(cols)].metric(label, count)

    st.markdown("---")

    # Build game entries
    prediction_keys = []
    all_game_entries = []
    
    for _, game in upcoming.iterrows():
        prediction_key = f"{game['game_date']}:{game['game_id']}"
        prediction_keys.append(prediction_key)
        all_game_entries.append({
            "game_id": game["game_id"],
            "prediction_key": prediction_key,
            "day_label": game["day_label"],
            "home_team": game["home_team"],
            "away_team": game["away_team"],
            "venue": game.get("venue", "Neutral"),
            "game_date": game["game_date"],
            "vegas_spread": game.get("vegas_spread"),
            "vegas_total": game.get("vegas_total"),
            "event_time": game.get("event_time_local"),
        })

    # Fetch existing predictions
    predictions = sb_fetch_predictions_by_keys(prediction_keys)
    predictions_by_key = {row.get("prediction_key"): row for row in predictions}
    
    for entry in all_game_entries:
        pred = predictions_by_key.get(entry["prediction_key"])
        entry["prediction"] = pred
        entry["has_prediction"] = pred is not None
        entry["is_stale"] = (pred and pred.get("model_version") != MODEL_VERSION) if pred else False

    # Summary stats
    games_with_predictions = [g for g in all_game_entries if g["has_prediction"]]
    alpha_count = sum(1 for g in games_with_predictions if g["prediction"].get("is_alpha") is True)
    bet_count = sum(1 for g in games_with_predictions if g["prediction"].get("kelly_recommended") == "BET")
    total_kelly = sum(safe_float(g["prediction"].get("kelly_dollars"), 0.0) 
                     for g in games_with_predictions 
                     if g["prediction"].get("kelly_recommended") == "BET")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Predictions", f"{len(games_with_predictions)}/{len(all_game_entries)}")
    with col2:
        st.metric("🚨 Alpha", alpha_count)
    with col3:
        st.metric("💰 Bets", bet_count)
    with col4:
        st.metric("Kelly Total", f"${total_kelly:.0f}")

    # Action buttons
    col1, col2 = st.columns(2)
    with col1:
        if st.button("▶️ Run Missing Predictions"):
            with st.spinner("Running predictions..."):
                count = 0
                for entry in all_game_entries:
                    if entry["has_prediction"] and not entry["is_stale"]:
                        continue
                    
                    result = predict_game(
                        team_a_name=entry["home_team"],
                        team_b_name=entry["away_team"],
                        home_a=True,
                        venue=entry["venue"],
                        vegas=entry.get("vegas_spread"),
                        bankroll=bankroll,
                        game_date=entry["game_date"],
                    )
                    
                    if result is None:
                        continue
                    
                    payload = {
                        **result,
                        "prediction_key": entry["prediction_key"],
                        "model_version": MODEL_VERSION,
                        "inputs": {
                            "home_team": entry["home_team"],
                            "away_team": entry["away_team"],
                            "venue": entry["venue"],
                            "vegas_spread": entry.get("vegas_spread"),
                            "vegas_total": entry.get("vegas_total"),
                        },
                    }
                    
                    try:
                        sb_upsert_prediction(payload)
                        count += 1
                    except Exception as e:
                        st.error(f"Failed to save {entry['home_team']} vs {entry['away_team']}: {e}")
                
                st.success(f"✅ Saved {count} predictions")
                st.rerun()
    
    with col2:
        if st.button("🔁 Re-run All Predictions"):
            with st.spinner("Re-running all predictions..."):
                count = 0
                for entry in all_game_entries:
                    result = predict_game(
                        team_a_name=entry["home_team"],
                        team_b_name=entry["away_team"],
                        home_a=True,
                        venue=entry["venue"],
                        vegas=entry.get("vegas_spread"),
                        bankroll=bankroll,
                        game_date=entry["game_date"],
                    )
                    
                    if result is None:
                        continue
                    
                    payload = {
                        **result,
                        "prediction_key": entry["prediction_key"],
                        "model_version": MODEL_VERSION,
                        "inputs": {
                            "home_team": entry["home_team"],
                            "away_team": entry["away_team"],
                            "venue": entry["venue"],
                            "vegas_spread": entry.get("vegas_spread"),
                            "vegas_total": entry.get("vegas_total"),
                        },
                    }
                    
                    try:
                        sb_upsert_prediction(payload)
                        count += 1
                    except Exception as e:
                        st.error(f"Failed: {e}")
                
                st.success(f"✅ Saved {count} predictions")
                st.rerun()

    st.markdown("---")

    # Group by day
    def _label_order(label: str) -> tuple[int, int, str]:
        if label == "TODAY":
            return (0, 0, label)
        if label == "TOMORROW":
            return (1, 0, label)
        if label.startswith("+"):
            try:
                days = int(label.split()[0].replace("+", ""))
            except ValueError:
                days = 99
            return (2, days, label)
        return (3, 0, label)

    day_labels = sorted({entry["day_label"] for entry in all_game_entries}, key=_label_order)
    
    if len(day_labels) == 0:
        st.info("No games in schedule")
    else:
        tabs = st.tabs([f"📅 {dl}" for dl in day_labels[:7]])
        
        for t_i, dl in enumerate(day_labels[:7]):
            with tabs[t_i]:
                day_games = [g for g in all_game_entries if g["day_label"] == dl]
                
                if len(day_games) == 0:
                    st.info(f"No games for {dl}")
                    continue

                # Apply filters
                filtered = []
                for entry in day_games:
                    if not entry["has_prediction"]:
                        filtered.append(entry)
                        continue
                    
                    pred = entry["prediction"]
                    conf = safe_float(pred.get("confidence"), 0.0)
                    is_bet = pred.get("kelly_recommended") == "BET"
                    is_alpha = pred.get("is_alpha") is True
                    kelly_amt = safe_float(pred.get("kelly_dollars"), 0.0)
                    
                    if show_bets_only and not is_bet:
                        continue
                    if show_alpha_only and not is_alpha:
                        continue
                    if conf < min_conf:
                        continue
                    if kelly_amt < min_kelly:
                        continue
                    
                    filtered.append(entry)

                if len(filtered) == 0:
                    st.info("No games match your filters")
                    continue

                # Sort: predictions first (by confidence desc), then no predictions
                def sort_key(entry):
                    if not entry["has_prediction"]:
                        return (1, 0)
                    return (0, -safe_float(entry["prediction"].get("confidence"), 0.0))

                for entry in sorted(filtered, key=sort_key):
                    pred = entry["prediction"]
                    
                    if not pred:
                        # No prediction
                        title = f"⚠️ {entry['home_team']} vs {entry['away_team']}"
                        with st.expander(title, expanded=False):
                            st.warning("Prediction not available")
                            
                            col1, col2 = st.columns(2)
                            with col1:
                                st.caption("🏟️ Venue")
                                st.write(entry.get("venue", "N/A"))
                            with col2:
                                st.caption("🕐 Time")
                                st.write(entry.get("event_time", "TBD"))
                            
                            if st.button("🔄 Run Prediction", key=f"run_{entry['prediction_key']}"):
                                result = predict_game(
                                    team_a_name=entry["home_team"],
                                    team_b_name=entry["away_team"],
                                    home_a=True,
                                    venue=entry["venue"],
                                    vegas=entry.get("vegas_spread"),
                                    bankroll=bankroll,
                                    game_date=entry["game_date"],
                                )
                                
                                if result:
                                    payload = {
                                        **result,
                                        "prediction_key": entry["prediction_key"],
                                        "model_version": MODEL_VERSION,
                                    }
                                    sb_upsert_prediction(payload)
                                    st.success("Saved!")
                                    st.rerun()
                                else:
                                    st.error("Failed - check team names")
                        continue
                    
                    # Has prediction
                    conf = safe_float(pred.get("confidence"), 0.0)
                    is_alpha = pred.get("is_alpha") is True
                    is_bet = pred.get("kelly_recommended") == "BET"
                    kelly_amt = safe_float(pred.get("kelly_dollars"), 0.0)
                    ensemble_pred = safe_float(pred.get("ensemble_prediction"), 0.0)
                    
                    # Build title
                    icons = []
                    if is_alpha:
                        icons.append("🚨")
                    if is_bet:
                        icons.append("💰")
                    if conf > 0.85:
                        icons.append("⭐")
                    
                    icon_str = " ".join(icons) if icons else "📊"
                    
                    title = f"{icon_str} {pred.get('team_a')} vs {pred.get('team_b')} | {conf:.0%}"
                    if is_bet:
                        title += f" | ${kelly_amt:.0f}"
                    
                    if entry["is_stale"]:
                        title = "🔄 " + title + " (OLD MODEL)"
                    
                    expand = is_alpha or (is_bet and kelly_amt > 50)
                    
                    with st.expander(title, expanded=expand):
                        # Stale warning
                        if entry["is_stale"]:
                            st.warning(f"⚠️ Using old model: {pred.get('model_version')}")
                            if st.button("Update to current model", key=f"update_{entry['prediction_key']}"):
                                result = predict_game(
                                    team_a_name=entry["home_team"],
                                    team_b_name=entry["away_team"],
                                    home_a=True,
                                    venue=entry["venue"],
                                    vegas=entry.get("vegas_spread"),
                                    bankroll=bankroll,
                                    game_date=entry["game_date"],
                                )
                                if result:
                                    payload = {**result, "prediction_key": entry["prediction_key"], "model_version": MODEL_VERSION}
                                    sb_upsert_prediction(payload)
                                    st.rerun()
                        
                        # Main metrics
                        col1, col2, col3, col4 = st.columns(4)
                        with col1:
                            st.metric("Prediction", f"{pred.get('team_a')} {ensemble_pred:+.1f}")
                        with col2:
                            st.metric("Confidence", f"{conf:.0%}")
                        with col3:
                            st.metric("Win %", f"{safe_float(pred.get('ensemble_win_prob'), 0.0):.1%}")
                        with col4:
                            if is_bet:
                                st.metric("💰 Kelly", f"${kelly_amt:.0f}", f"{safe_float(pred.get('kelly_pct'), 0.0):.1%}")
                            else:
                                st.info("PASS")
                        
                        # Game details
                        st.markdown("---")
                        col1, col2, col3 = st.columns(3)
                        with col1:
                            st.caption("🏟️ Venue")
                            st.write(entry.get("venue", "N/A"))
                        with col2:
                            st.caption("🕐 Time")
                            st.write(entry.get("event_time", "TBD"))
                        with col3:
                            if entry.get("vegas_spread"):
                                st.caption("🎰 Vegas")
                                st.write(f"{entry['vegas_spread']:+.1f}")
                                edge = abs(ensemble_pred - entry['vegas_spread'])
                                if edge > 3:
                                    st.success(f"**{edge:.1f}pt edge**")
                        
                        # Alpha reasons
                        if is_alpha:
                            st.markdown("---")
                            st.success("🚨 **ALPHA SIGNAL**")
                            for reason in pred.get("alpha_reasons", []):
                                st.markdown(f"• {reason}")
                        
                        # Model breakdown
                        with st.expander("📊 Model Details"):
                            model_preds = pred.get("model_predictions", {})
                            if model_preds:
                                model_data = []
                                for name, data in model_preds.items():
                                    model_data.append({
                                        "Model": name.replace("M1_", "1: ").replace("M2_", "2: ").replace("M3_", "3: ").replace("M4_", "4: "),
                                        "Prediction": f"{safe_float(data.get('prediction'), 0.0):+.1f}",
                                        "Win %": f"{safe_float(data.get('win_prob'), 0.0):.1%}",
                                    })
                                st.dataframe(pd.DataFrame(model_data), use_container_width=True, hide_index=True)

elif page == "📆 Today's Schedule":
    st.title("📆 Today's Schedule")
    
    upcoming, _, _ = get_upcoming_games(days_ahead=1)
    
    if len(upcoming) == 0:
        st.warning("No games found for today")
    else:
        schedule_rows = []
        for _, game in upcoming.iterrows():
            schedule_rows.append({
                "Time": game.get("event_time_local", "TBD"),
                "Away": game.get("away_team", ""),
                "@": "vs",
                "Home": game.get("home_team", ""),
                "Venue": game.get("venue", ""),
                "TV": game.get("broadcast", ""),
            })
        
        st.dataframe(pd.DataFrame(schedule_rows), use_container_width=True, hide_index=True)

elif page == "🎯 Single Prediction":
    st.title("🎯 Single Prediction")
    
    team_options = sorted(bart_clean["team"].dropna().unique().tolist())
    
    col1, col2 = st.columns(2)
    with col1:
        home_team = st.selectbox("Home Team", team_options, index=0)
    with col2:
        away_team = st.selectbox("Away Team", team_options, index=1 if len(team_options) > 1 else 0)
    
    venue = st.text_input("Venue", value="Neutral")
    
    col1, col2 = st.columns(2)
    with col1:
        game_date = st.text_input("Game Date (YYYYMMDD)", value=datetime.now().strftime("%Y%m%d"))
    with col2:
        vegas_line = st.number_input("Vegas Spread (optional)", value=0.0, step=0.5)

    has_sb_creds = has_supabase_creds()
    auto_save = st.checkbox(
        "Auto-save to Supabase",
        value=False,
        disabled=not has_sb_creds,
        help="Requires SUPABASE_URL and SUPABASE_ANON_KEY.",
    )
    if not has_sb_creds:
        st.caption("Supabase credentials missing: set SUPABASE_URL and SUPABASE_ANON_KEY to enable auto-save.")
    
    if home_team == away_team:
        st.error("Teams must be different")
    else:
        if st.button("🔮 Generate Prediction", type="primary"):
            with st.spinner("Analyzing matchup..."):
                result = predict_game(
                    team_a_name=home_team,
                    team_b_name=away_team,
                    home_a=True,
                    venue=venue,
                    vegas=vegas_line if vegas_line != 0.0 else None,
                    bankroll=bankroll,
                    game_date=game_date,
                )
            
            if result is None:
                st.error("❌ Prediction failed - check team names in BartTorvik data")
            else:
                st.success("✅ Prediction complete")
                
                ens = result["ensemble"]

                payload = {
                    **result,
                    "prediction_key": f"{game_date}:{make_game_id(home_team, away_team, game_date)}",
                    "model_version": MODEL_VERSION,
                    "inputs": {
                        "home_team": home_team,
                        "away_team": away_team,
                        "venue": venue,
                        "vegas_spread": vegas_line if vegas_line != 0.0 else None,
                    },
                }

                if auto_save:
                    try:
                        sb_upsert_prediction(payload)
                        st.success("✅ Auto-saved to Supabase")
                    except Exception as e:
                        st.error(f"❌ Auto-save failed: {e}")
                
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("Prediction", f"{home_team} {ens['prediction']:+.1f}")
                with col2:
                    st.metric("Confidence", f"{ens['confidence']:.0%}")
                with col3:
                    st.metric("Win %", f"{ens['win_prob']:.1%}")
                with col4:
                    if ens["kelly"]["recommended"] == "BET":
                        st.metric("Kelly", f"${ens['kelly']['kelly_dollars']:.0f}")
                    else:
                        st.info("PASS")
                
                if ens["is_alpha"]:
                    st.success(f"🚨 ALPHA: {', '.join(ens['alpha_reasons'])}")
                
                with st.expander("📋 Full Result JSON"):
                    st.json(result)
                
                if st.button("💾 Save to Supabase"):
                    try:
                        sb_upsert_prediction(payload)
                        st.success("✅ Saved to Supabase")
                    except Exception as e:
                        st.error(f"❌ Save failed: {e}")

elif page == "📊 Model Performance":
    st.title("📊 Model Performance")
    
    if not has_supabase_creds():
        st.error("Supabase credentials required")
        st.stop()
    
    try:
        stats = sb_get_performance_stats()
    except Exception as e:
        stats = None
        st.error(f"Failed to load stats: {e}")
    
    if stats is None:
        st.info("No completed predictions yet. Update game results to see performance stats.")
    else:
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Total Games", stats["total_predictions"])
        with col2:
            st.metric("Ensemble Accuracy", f"{stats['ensemble_accuracy']:.1%}")
        with col3:
            st.metric("Avg Error", f"{stats['avg_error']:.1f} pts")
        with col4:
            st.metric("Total Kelly", f"${stats['total_kelly_bet']:.0f}")
        
        st.markdown("---")
        st.subheader("Model Breakdown")
        
        model_data = []
        for name, data in stats["model_stats"].items():
            model_data.append({
                "Model": name.replace("_", " "),
                "Accuracy": f"{data['accuracy']:.1%}",
                "Correct": f"{data['correct']}/{data['total']}",
                "Avg Error": f"{data['avg_error']:.1f} pts" if data["avg_error"] else "N/A",
            })
        
        st.dataframe(pd.DataFrame(model_data), use_container_width=True, hide_index=True)
        
        st.markdown("---")
        st.subheader("Recent Predictions")
        
        recent_data = []
        for r in stats["recent"][:10]:
            recent_data.append({
                "Game": f"{r.get('team_a')} vs {r.get('team_b')}",
                "Prediction": f"{safe_float(r.get('ensemble_prediction'), 0.0):+.1f}",
                "Actual": f"{safe_float(r.get('actual_spread'), 0.0):+.1f}",
                "Error": f"{safe_float(r.get('ensemble_error'), 0.0):.1f}",
                "Result": "✅" if r.get("won") else "❌",
            })
        
        st.dataframe(pd.DataFrame(recent_data), use_container_width=True, hide_index=True)

elif page == "📝 Update Results":
    st.title("📝 Update Game Results")
    
    if not has_supabase_creds():
        st.error("Supabase credentials required")
        st.stop()
    
    try:
        pending = sb_fetch_pending_predictions()
    except Exception as e:
        pending = []
        st.error(f"Failed to load: {e}")
    
    if len(pending) == 0:
        st.success("No pending predictions")
    else:
        st.info(f"Found {len(pending)} predictions awaiting results")
        
        options = [
            f"{p.get('team_a', 'Team A')} vs {p.get('team_b', 'Team B')} ({p.get('game_date', '')})" 
            for p in pending
        ]
        
        selected_idx = st.selectbox("Select Game", range(len(options)), format_func=lambda i: options[i])
        selected = pending[selected_idx]
        
        st.markdown("---")
        
        col1, col2 = st.columns(2)
        with col1:
            score_a = st.number_input(
                f"{selected.get('team_a', 'Team A')} Score",
                min_value=0,
                max_value=200,
                value=0,
                step=1
            )
        with col2:
            score_b = st.number_input(
                f"{selected.get('team_b', 'Team B')} Score",
                min_value=0,
                max_value=200,
                value=0,
                step=1
            )
        
        if st.button("💾 Update Result", type="primary"):
            try:
                updated = sb_update_prediction_result(selected["id"], int(score_a), int(score_b))
                st.success("✅ Result updated!")
                
                actual_spread = score_a - score_b
                pred_spread = safe_float(selected.get("ensemble_prediction"), 0.0)
                won = updated.get("won")
                
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Predicted", f"{pred_spread:+.1f}")
                with col2:
                    st.metric("Actual", f"{actual_spread:+.1f}")
                with col3:
                    st.metric("Result", "✅ WIN" if won else "❌ LOSS")
                
                st.balloons() if won else st.snow()
                
            except Exception as e:
                st.error(f"❌ Update failed: {e}")
