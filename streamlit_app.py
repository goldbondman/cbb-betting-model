# app.py
# CBB Betting App with Model Studio + Backtest Lab (no-code knobs)
# Built around:
# - espn_team_game_features (pregame rolling features, leak-free)
# - barttorvik_team_results (season strength + SoS)

import os
import json
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from difflib import get_close_matches

import numpy as np
import pandas as pd
import requests
import streamlit as st

from supabase import create_client, Client

# ============================================================
# STREAMLIT
# ============================================================

st.set_page_config(page_title="CBB Betting Model", page_icon="🏀", layout="wide")

LOCAL_TIMEZONE = "America/Los_Angeles"
LOCAL_TZ = ZoneInfo(LOCAL_TIMEZONE)

MODEL_VERSION = "2026-01-29-model-studio-v1"

ESPN_GROUP = "50"
ESPN_LIMIT = "500"
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
UA_HEADERS = {"User-Agent": "Mozilla/5.0"}

# ============================================================
# BASIC HELPERS
# ============================================================

def safe_float(value, default=0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except Exception:
        return default

def clamp(x, lo, hi):
    return max(lo, min(hi, x))

def to_int(x, default=0):
    try:
        return int(x)
    except Exception:
        return default

def american_to_implied_prob(odds):
    o = safe_float(odds, None)
    if o is None or o == 0:
        return None
    if o > 0:
        return 100.0 / (o + 100.0)
    return abs(o) / (abs(o) + 100.0)

def logistic(x, scale=12.0):
    # margin -> win prob
    return 1.0 / (1.0 + math.exp(-x / scale))

# ============================================================
# TEAM NAME NORMALIZATION
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
    "st bonaventure": "st bonaventure",
    "massachusetts lowell": "umass lowell",
    "texas arlington": "ut arlington",
    "texas rio grande valley": "ut rio grande valley",
    "texas san antonio": "utsa",
}

def normalize_team_name(team_name: str) -> str:
    if not isinstance(team_name, str):
        return ""
    cleaned = team_name.lower().strip()
    cleaned = cleaned.replace("&", "and")
    cleaned = cleaned.replace("st.", "st ").replace("st  ", "st ")
    cleaned = "".join(ch for ch in cleaned if ch.isalnum() or ch.isspace() or ch == "'")
    cleaned = " ".join(cleaned.split())
    cleaned = MANUAL_TEAM_MAPPINGS.get(cleaned, cleaned)
    return cleaned

# ============================================================
# SUPABASE (OPTIONAL, GRACEFUL)
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
        raise RuntimeError("Missing Supabase credentials.")

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

def sb_try(table_name, fn_desc, fn):
    """
    Runs a supabase call, but does not crash the app if table does not exist.
    """
    if not has_supabase_creds():
        return None, f"{fn_desc}: Supabase creds missing"
    try:
        sb = supabase_client()
        return fn(sb.table(table_name)), None
    except Exception as e:
        return None, f"{fn_desc}: {e}"

def sb_auth_sign_in(email: str, password: str):
    sb = supabase_client()
    return sb.auth.sign_in_with_password({"email": email, "password": password})

def sb_auth_sign_out():
    sb = supabase_client()
    return sb.auth.sign_out()

def sb_auth_session():
    sb = supabase_client()
    return sb.auth.get_session()

# ============================================================
# LOAD DATA (YOUR UPLOADED FILES FIRST)
# ============================================================

def _read_csv_any(paths):
    for p in paths:
        try:
            if os.path.exists(p):
                return pd.read_csv(p)
        except Exception:
            continue
    return None

@st.cache_data
def load_feature_store():
    # Prefer uploaded paths in this chat, then fall back to repo filenames
    df = _read_csv_any([
        "/mnt/data/espn_team_game_features (1).csv",
        "espn_team_game_features.csv",
    ])
    if df is None or len(df) == 0:
        return pd.DataFrame()

    # enforce types
    df["team_norm"] = df["team"].astype(str).apply(normalize_team_name)
    if "opponent" in df.columns:
        df["opp_norm"] = df["opponent"].astype(str).apply(normalize_team_name)
    else:
        df["opp_norm"] = ""
    df["game_date"] = df["game_date"].astype(str)
    df["event_id"] = df["event_id"].astype(str)

    # numeric columns we care about (exist in your feature file)
    base_metrics = ["ortg", "drtg", "netrtg", "pace", "efg", "tov_pct", "orb_pct", "drb_pct", "ftr", "3par"]
    for c in base_metrics:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # Ensure rolling windows exist for 3/5/7/10, leak-free
    df = df.sort_values(["team_norm", "game_date", "event_id"]).reset_index(drop=True)

    def add_roll(window):
        metrics = ["ortg", "drtg", "netrtg", "pace", "efg", "tov_pct", "orb_pct", "drb_pct", "ftr", "3par"]
        g = df.groupby("team_norm", group_keys=False)

        for m in metrics:
            if m not in df.columns:
                continue
            # shift so current game is excluded
            shifted = g[m].shift(1)
            df[f"{m}_l{window}_pre"] = shifted.groupby(df["team_norm"]).rolling(window, min_periods=3).mean().reset_index(level=0, drop=True)
            df[f"{m}_std_l{window}_pre"] = shifted.groupby(df["team_norm"]).rolling(window, min_periods=3).std().reset_index(level=0, drop=True)

    for w in [3, 5, 7, 10]:
        # If l3/l7 already present, we still recompute to guarantee consistency
        add_roll(w)

    # season-to-date pregame mean (shifted)
    for m in ["ortg", "drtg", "netrtg", "pace", "efg", "tov_pct", "orb_pct", "drb_pct", "ftr", "3par"]:
        if m not in df.columns:
            continue
        g = df.groupby("team_norm", group_keys=False)
        df[f"{m}_season_pre"] = g[m].shift(1).groupby(df["team_norm"]).expanding(min_periods=6).mean().reset_index(level=0, drop=True)

    return df

@st.cache_data
def load_torvik():
    df = _read_csv_any([
        "/mnt/data/barttorvik_team_results.csv",
        "barttorvik_team_results.csv",
        "barttorvik.csv",
    ])
    if df is None or len(df) == 0:
        return pd.DataFrame()

    df.columns = [str(c).strip().lower().replace(" ", "_").replace(".", "") for c in df.columns]
    if "team" not in df.columns:
        return pd.DataFrame()

    df["team_norm"] = df["team"].astype(str).apply(normalize_team_name)
    if "adjoe" in df.columns and "adjde" in df.columns:
        df["adjem"] = pd.to_numeric(df["adjoe"], errors="coerce") - pd.to_numeric(df["adjde"], errors="coerce")

    for c in ["adjoe", "adjde", "adjem", "barthag", "sos", "ncsos"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    return df

@st.cache_data
def load_optional_espn_games():
    # If you have this file in your repo, we will use it for backtesting lines.
    df = _read_csv_any(["espn_games.csv"])
    if df is None or len(df) == 0:
        return pd.DataFrame()
    df.columns = [str(c).strip().lower() for c in df.columns]
    # attempt to standardize ids
    for idcol in ["game_id", "event_id", "id"]:
        if idcol in df.columns:
            df["event_id"] = df[idcol].astype(str)
            break
    if "game_date" in df.columns:
        df["game_date"] = df["game_date"].astype(str)
    if "date" in df.columns and "game_date" not in df.columns:
        df["game_date"] = df["date"].astype(str)
    return df

FEATURES = load_feature_store()
TORVIK = load_torvik()
ESPN_GAMES_OPT = load_optional_espn_games()

if FEATURES.empty:
    st.error("Feature store missing. Expected espn_team_game_features.csv (or the uploaded file).")
    st.stop()

# ============================================================
# TEAM LOOKUPS (TORVIK BASE STRENGTH)
# ============================================================

def get_torvik_row(team_name: str) -> dict:
    if TORVIK.empty:
        return {}
    tn = normalize_team_name(team_name)
    row = TORVIK[TORVIK["team_norm"] == tn]
    if len(row) == 0:
        # try closest match
        close = get_close_matches(tn, TORVIK["team_norm"].dropna().unique().tolist(), n=1, cutoff=0.92)
        if close:
            row = TORVIK[TORVIK["team_norm"] == close[0]]
    if len(row) == 0:
        return {}
    return row.iloc[0].to_dict()

def get_latest_team_snapshot(team_name: str) -> dict:
    """
    Latest pregame features available for that team (based on last completed game).
    This is what we use for upcoming games.
    """
    tn = normalize_team_name(team_name)
    df = FEATURES[FEATURES["team_norm"] == tn]
    if df.empty:
        return {}
    last = df.sort_values(["game_date", "event_id"], ascending=True).iloc[-1].to_dict()
    # attach torvik base
    base = get_torvik_row(team_name)
    out = {**base, **last}
    out["team_display"] = team_name
    out["team_norm"] = tn
    return out

# ============================================================
# MODEL CONFIG (MODEL STUDIO)
# ============================================================

DEFAULT_MODEL_CONFIG = {
    "meta": {
        "name": "Baseline v1",
        "created_at": datetime.now(tz=LOCAL_TZ).isoformat(),
        "version_id": "baseline-v1",
    },
    "spread": {
        "recent_window": 7,
        "recent_blend": 0.35,   # recent vs season
        "weights": {
            "torvik_adjem": 0.55,
            "recent_netrtg": 0.25,
            "four_factors": 0.20,
        },
        "home_court": 2.7,
        "sos_conf_strength": 0.5,  # confidence only
        "volatility_penalty": 0.8,
        "market_anchor": 0.10,
        "market_anchor_min_conf": 0.80,
    },
    "total": {
        "recent_window": 7,
        "recent_blend": 0.35,
        "weights": {
            "tempo": 0.25,
            "efficiency": 0.60,
            "four_factors": 0.15,
        },
        "sos_conf_strength": 0.5,
        "volatility_penalty": 0.9,
        "market_anchor": 0.10,
        "market_anchor_min_conf": 0.82,
    },
    "ml": {
        "recent_window": 7,
        "recent_blend": 0.35,
        "weights": {
            "spread_margin": 0.70,
            "four_factors": 0.30,
        },
        "sos_conf_strength": 0.6,
        "volatility_penalty": 0.9,
        "market_anchor": 0.10,
        "market_anchor_min_conf": 0.84,
    },
}

DEFAULT_STRATEGY = {
    "spread": {"edge_min": 3.0, "conf_min": 0.82},
    "total":  {"edge_min": 3.5, "conf_min": 0.84},
    "ml":     {"edge_min_prob": 0.03, "conf_min": 0.84},  # 3% winprob edge
    "units": {
        "tier_1u_edge_bonus": 0.0,
        "tier_2u_edge_bonus": 1.5,
        "tier_3u_edge_bonus": 3.0,
        "tier_2u_conf_bonus": 0.03,
        "tier_3u_conf_bonus": 0.05,
        "ml_dog_micro_stakes": True,
    },
    "assumptions": {
        "spread_odds_default": -110,
        "total_odds_default": -110,
        "bankroll": 1000,
    }
}

def get_active_model_config() -> dict:
    if "model_config" not in st.session_state:
        st.session_state["model_config"] = DEFAULT_MODEL_CONFIG
    return st.session_state["model_config"]

def set_active_model_config(cfg: dict):
    st.session_state["model_config"] = cfg

def get_strategy() -> dict:
    if "strategy" not in st.session_state:
        st.session_state["strategy"] = DEFAULT_STRATEGY
    return st.session_state["strategy"]

# Optional persistence: model_versions table
# Columns used:
# model_version_id (text), ensemble_weights (jsonb), notes (text), is_active (bool), created_at (timestamptz)

def sb_save_model_config(cfg: dict, is_active=False):
    payload = {
        "model_version_id": cfg.get("meta", {}).get("version_id", f"cfg-{int(datetime.now().timestamp())}"),
        "ensemble_weights": cfg,
        "notes": cfg.get("meta", {}).get("name", "Unnamed"),
        "is_active": bool(is_active),
        "created_at": datetime.now(tz=LOCAL_TZ).isoformat(),
    }

    def _do(tbl):
        return tbl.upsert(payload).execute()

    _, err = sb_try("model_versions", "save model config", _do)
    return err

def sb_load_model_configs():
    def _do(tbl):
        return tbl.select("*").order("created_at", desc=True).limit(50).execute()
    resp, err = sb_try("model_versions", "load model configs", _do)
    if err or resp is None:
        return [], err
    return resp.data or [], None

def sb_set_active_model_config(version_id: str):
    def _deactivate(tbl):
        return tbl.update({"is_active": False}).neq("model_version_id", "___nope___").execute()
    def _activate(tbl):
        return tbl.update({"is_active": True}).eq("model_version_id", version_id).execute()

    _, err1 = sb_try("model_versions", "deactivate all configs", _deactivate)
    _, err2 = sb_try("model_versions", "activate selected config", _activate)
    return err1 or err2

# ============================================================
# LEDGER (PAPER BETS)
# Optional persistence: bet_ledger table
# ============================================================

# Columns recommended:
# id (text), run_date (text), game_date (text), event_id (text), home_team (text), away_team (text),
# market (text), side (text), model_value (float), vegas_value (float),
# edge (float), conf (float), recommended (bool), units (float),
# result (text nullable), pnl (float nullable), model_version (text), meta (jsonb)

def ledger_key(run_date, event_id, market, side):
    return f"{run_date}:{event_id}:{market}:{side}"

def sb_upsert_ledger_rows(rows: list[dict]):
    if not rows:
        return None
    def _do(tbl):
        return tbl.upsert(rows).execute()
    _, err = sb_try("bet_ledger", "upsert ledger rows", _do)
    return err

def get_local_ledger_df():
    if "local_ledger" not in st.session_state:
        st.session_state["local_ledger"] = pd.DataFrame()
    return st.session_state["local_ledger"]

def append_local_ledger(rows: list[dict]):
    if not rows:
        return
    df = pd.DataFrame(rows)
    existing = get_local_ledger_df()
    if existing.empty:
        st.session_state["local_ledger"] = df
    else:
        merged = pd.concat([existing, df], ignore_index=True)
        merged = merged.drop_duplicates(subset=["id"], keep="last")
        st.session_state["local_ledger"] = merged

# ============================================================
# ESPN UPCOMING GAMES (SCOREBOARD)
# ============================================================

def _dates_list(days_ahead: int, tz=LOCAL_TZ) -> list[str]:
    base = datetime.now(tz).date()
    return [(base + timedelta(days=i)).strftime("%Y%m%d") for i in range(days_ahead)]

def _parse_start_utc_to_local_dt(iso_utc: str, tz=LOCAL_TZ):
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
    total = safe_float(odds.get("overUnder"), None)
    spread = safe_float(odds.get("spread"), None)

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

def _normalize_scoreboard(requested_date: str, data: dict) -> pd.DataFrame:
    rows = []
    events = data.get("events") or []
    for ev in events:
        event_id = str(ev.get("id") or "")
        if not event_id:
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
            continue

        home_team = (home_c.get("team") or {})
        away_team = (away_c.get("team") or {})

        home_name = home_team.get("displayName")
        away_name = away_team.get("displayName")

        if not home_name or not away_name:
            continue

        venue = (comp.get("venue") or {}).get("fullName")
        status_type = ((ev.get("status") or {}).get("type") or {})
        status_desc = status_type.get("description")
        status_detail = status_type.get("detail") or status_type.get("shortDetail")

        odds = _extract_odds(comp)

        today = datetime.now(LOCAL_TZ).date()
        game_day = datetime.strptime(local_game_date, "%Y%m%d").date()
        off = (game_day - today).days
        day_label = "TODAY" if off == 0 else "TOMORROW" if off == 1 else f"+{off} days"

        rows.append({
            "event_id": event_id,
            "game_date": local_game_date,
            "day_label": day_label,
            "home_team": home_name,
            "away_team": away_name,
            "venue": venue,
            "event_time_local": local_time_str,
            "status_detail": status_detail,
            "odds_provider": odds["odds_provider"],
            "vegas_spread": odds["vegas_spread"],
            "vegas_total": odds["vegas_total"],
            "ml_home": odds["ml_home"],
            "ml_away": odds["ml_away"],
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values(["game_date", "event_time_local", "home_team"], ascending=[True, True, True])

@st.cache_data(ttl=900)
def get_upcoming_games(days_ahead: int = 7):
    errors = []
    all_dfs = []
    for date_str in _dates_list(days_ahead, tz=LOCAL_TZ):
        try:
            data = _fetch_scoreboard_json(date_str)
            df = _normalize_scoreboard(date_str, data)
            all_dfs.append(df)
        except Exception as exc:
            errors.append({"date": date_str, "error": str(exc)})
    if not all_dfs:
        return pd.DataFrame(), errors
    out = pd.concat(all_dfs, ignore_index=True)
    if not out.empty:
        out = out.drop_duplicates(subset=["event_id"], keep="first")
    return out, errors

# ============================================================
# PREDICTION ENGINE (USES MODEL STUDIO CONFIG)
# ============================================================

def blend(season_val, recent_val, recent_blend):
    sv = safe_float(season_val, None)
    rv = safe_float(recent_val, None)
    if sv is None and rv is None:
        return None
    if sv is None:
        return rv
    if rv is None:
        return sv
    return (1.0 - recent_blend) * sv + recent_blend * rv

def get_recent_cols(metric: str, window: int):
    return f"{metric}_season_pre", f"{metric}_l{window}_pre"

def get_vol_col(metric: str, window: int):
    return f"{metric}_std_l{window}_pre"

def four_factor_edge(A: dict, B: dict, window: int, recent_blend: float) -> float:
    # Uses eFG (higher better), TOV% (lower better), ORB% (higher better), FTR (higher better)
    # Returns a single points-like edge. This is intentionally bounded.
    def g(metric):
        s_col, r_col = get_recent_cols(metric, window)
        return blend(A.get(s_col), A.get(r_col), recent_blend), blend(B.get(s_col), B.get(r_col), recent_blend)

    efg_a, efg_b = g("efg")
    tov_a, tov_b = g("tov_pct")
    orb_a, orb_b = g("orb_pct")
    ftr_a, ftr_b = g("ftr")

    if efg_a is None or efg_b is None:
        return 0.0

    # scale factors to "points impact"
    efg_imp = (safe_float(efg_a) - safe_float(efg_b)) * 12.0
    tov_imp = (safe_float(tov_b) - safe_float(tov_a)) * 6.0
    orb_imp = (safe_float(orb_a) - safe_float(orb_b)) * 5.0
    ftr_imp = (safe_float(ftr_a) - safe_float(ftr_b)) * 4.0

    raw = efg_imp + tov_imp + orb_imp + ftr_imp
    return float(np.tanh(raw / 18.0) * 6.0)

def compute_reliability(A: dict, B: dict, window: int, recent_blend: float, sos_strength: float, vol_penalty: float) -> float:
    # Starts high, then penalizes volatility and missingness.
    base = 0.88

    # Missingness penalty
    required = []
    for m in ["netrtg", "pace", "efg", "tov_pct", "orb_pct", "ftr"]:
        s_col, r_col = get_recent_cols(m, window)
        required.append((A.get(s_col), A.get(r_col), B.get(s_col), B.get(r_col)))
    missing = 0
    for pack in required:
        if all(v is None or (isinstance(v, float) and np.isnan(v)) for v in pack):
            missing += 1
    base -= 0.05 * (missing / max(1, len(required)))

    # Volatility penalty (use netrtg std)
    vol_col = get_vol_col("netrtg", window)
    a_vol = safe_float(A.get(vol_col), 0.0)
    b_vol = safe_float(B.get(vol_col), 0.0)
    vol = (a_vol + b_vol) / 2.0
    base -= vol_penalty * clamp(vol / 10.0, 0.0, 0.10)

    # SoS confidence adjustment (small)
    sos_a = safe_float(A.get("sos"), 0.0)
    sos_b = safe_float(B.get("sos"), 0.0)
    sos_adj = clamp((sos_a - sos_b) / 200.0, -0.03, 0.03) * sos_strength
    base += sos_adj

    return float(clamp(base, 0.70, 0.97))

def predict_markets(home_team: str, away_team: str, venue: str, vegas_spread=None, vegas_total=None, ml_home=None, ml_away=None):
    cfg = get_active_model_config()
    strat = get_strategy()

    A = get_latest_team_snapshot(home_team)
    B = get_latest_team_snapshot(away_team)

    if not A or not B:
        return None

    # Shared knobs per market
    spread_cfg = cfg["spread"]
    total_cfg = cfg["total"]
    ml_cfg = cfg["ml"]

    # Pull blended efficiency components
    def blended_eff(team_dict, metric, window, recent_blend):
        s_col, r_col = get_recent_cols(metric, window)
        return blend(team_dict.get(s_col), team_dict.get(r_col), recent_blend)

    # --------------------------------------------------------
    # SPREAD (home margin)
    # --------------------------------------------------------
    w = spread_cfg["recent_window"]
    rb = spread_cfg["recent_blend"]

    torv_edge = safe_float(A.get("adjem"), 0.0) - safe_float(B.get("adjem"), 0.0)

    a_net = blended_eff(A, "netrtg", w, rb)
    b_net = blended_eff(B, "netrtg", w, rb)
    recent_net_edge = (safe_float(a_net, 0.0) - safe_float(b_net, 0.0))

    # Convert per-100 edge to points using pace
    a_pace = blended_eff(A, "pace", w, rb)
    b_pace = blended_eff(B, "pace", w, rb)
    pace = safe_float((safe_float(a_pace, 70.0) + safe_float(b_pace, 70.0)) / 2.0, 70.0)

    ff_edge = four_factor_edge(A, B, w, rb)

    spread_points = (
        spread_cfg["weights"]["torvik_adjem"] * torv_edge +
        spread_cfg["weights"]["recent_netrtg"] * recent_net_edge +
        spread_cfg["weights"]["four_factors"] * ff_edge
    )

    # Convert the blended "per-100" into game margin using pace
    spread_model_margin = (spread_points * (pace / 100.0)) + safe_float(spread_cfg.get("home_court", 2.7), 2.7)

    spread_conf = compute_reliability(A, B, w, rb, spread_cfg["sos_conf_strength"], spread_cfg["volatility_penalty"])

    # market anchor for stability (only when conf is low)
    v_sp = safe_float(vegas_spread, None)
    if v_sp is not None and spread_conf < safe_float(spread_cfg["market_anchor_min_conf"], 0.80):
        spread_model_margin = (1.0 - spread_cfg["market_anchor"]) * spread_model_margin + spread_cfg["market_anchor"] * v_sp

    spread_edge = None
    spread_side = None
    spread_reco = False
    spread_units = 0.0

    if v_sp is not None:
        spread_edge = spread_model_margin - v_sp
        edge_min = strat["spread"]["edge_min"]
        conf_min = strat["spread"]["conf_min"]

        if spread_edge >= edge_min:
            spread_side = f"{home_team} spread"
        elif spread_edge <= -edge_min:
            spread_side = f"{away_team} spread"

        if spread_side and spread_conf >= conf_min:
            spread_reco = True

            # unit tiers
            edge_abs = abs(spread_edge)
            conf = spread_conf
            units = 1.0
            if edge_abs >= edge_min + strat["units"]["tier_2u_edge_bonus"] and conf >= conf_min + strat["units"]["tier_2u_conf_bonus"]:
                units = 2.0
            if edge_abs >= edge_min + strat["units"]["tier_3u_edge_bonus"] and conf >= conf_min + strat["units"]["tier_3u_conf_bonus"]:
                units = 3.0
            spread_units = units

    # --------------------------------------------------------
    # TOTAL
    # --------------------------------------------------------
    wT = total_cfg["recent_window"]
    rbT = total_cfg["recent_blend"]

    a_ortg = blended_eff(A, "ortg", wT, rbT)
    b_ortg = blended_eff(B, "ortg", wT, rbT)
    a_drtg = blended_eff(A, "drtg", wT, rbT)
    b_drtg = blended_eff(B, "drtg", wT, rbT)

    a_paceT = blended_eff(A, "pace", wT, rbT)
    b_paceT = blended_eff(B, "pace", wT, rbT)
    paceT = safe_float((safe_float(a_paceT, 70.0) + safe_float(b_paceT, 70.0)) / 2.0, 70.0)

    # expected points per 100 for each side
    home_pp100 = (safe_float(a_ortg, 102.0) + safe_float(b_drtg, 102.0)) / 2.0
    away_pp100 = (safe_float(b_ortg, 102.0) + safe_float(a_drtg, 102.0)) / 2.0

    ff_total_adjust = four_factor_edge(A, B, wT, rbT) * 0.6  # smaller impact on totals

    total_model = (paceT / 100.0) * (home_pp100 + away_pp100) + ff_total_adjust

    total_conf = compute_reliability(A, B, wT, rbT, total_cfg["sos_conf_strength"], total_cfg["volatility_penalty"])

    v_tot = safe_float(vegas_total, None)
    if v_tot is not None and total_conf < safe_float(total_cfg["market_anchor_min_conf"], 0.82):
        total_model = (1.0 - total_cfg["market_anchor"]) * total_model + total_cfg["market_anchor"] * v_tot

    total_edge = None
    total_side = None
    total_reco = False
    total_units = 0.0

    if v_tot is not None:
        total_edge = total_model - v_tot
        edge_min = strat["total"]["edge_min"]
        conf_min = strat["total"]["conf_min"]

        if total_edge >= edge_min:
            total_side = "Over"
        elif total_edge <= -edge_min:
            total_side = "Under"

        if total_side and total_conf >= conf_min:
            total_reco = True
            edge_abs = abs(total_edge)
            conf = total_conf
            units = 1.0
            if edge_abs >= edge_min + strat["units"]["tier_2u_edge_bonus"] and conf >= conf_min + strat["units"]["tier_2u_conf_bonus"]:
                units = 2.0
            if edge_abs >= edge_min + strat["units"]["tier_3u_edge_bonus"] and conf >= conf_min + strat["units"]["tier_3u_conf_bonus"]:
                units = 3.0
            total_units = units

    # --------------------------------------------------------
    # MONEYLINE
    # --------------------------------------------------------
    # Use spread margin as main driver
    wM = ml_cfg["recent_window"]
    rbM = ml_cfg["recent_blend"]

    ml_conf = compute_reliability(A, B, wM, rbM, ml_cfg["sos_conf_strength"], ml_cfg["volatility_penalty"])
    model_win_prob_home = logistic(spread_model_margin, scale=12.0)

    imp_home = american_to_implied_prob(ml_home)
    imp_away = american_to_implied_prob(ml_away)

    ml_edge = None
    ml_side = None
    ml_reco = False
    ml_units = 0.0

    if imp_home is not None and imp_away is not None:
        ml_edge = model_win_prob_home - imp_home
        edge_min_prob = strat["ml"]["edge_min_prob"]
        conf_min = strat["ml"]["conf_min"]

        if ml_edge >= edge_min_prob:
            ml_side = f"{home_team} ML"
        elif ml_edge <= -edge_min_prob:
            ml_side = f"{away_team} ML"

        if ml_side and ml_conf >= conf_min:
            ml_reco = True

            # units for ML:
            # - Favorites: 1u to 2u.
            # - Dogs: micro stakes allowed (0.25 or 0.5) to manage variance.
            units = 1.0
            if "away" in (ml_side or "").lower() and safe_float(ml_away, 0) > 0 and strat["units"]["ml_dog_micro_stakes"]:
                # away is a dog if odds positive
                units = 0.25 if abs(ml_edge) < (edge_min_prob + 0.02) else 0.5
            elif "home" in (ml_side or "").lower() and safe_float(ml_home, 0) > 0 and strat["units"]["ml_dog_micro_stakes"]:
                units = 0.25 if abs(ml_edge) < (edge_min_prob + 0.02) else 0.5
            else:
                # favorites
                if abs(ml_edge) >= edge_min_prob + 0.02 and ml_conf >= conf_min + 0.03:
                    units = 2.0
            ml_units = units

    # --------------------------------------------------------
    # OUTPUT
    # --------------------------------------------------------
    return {
        "teams": {"home": home_team, "away": away_team},
        "venue": venue,
        "model_version": cfg["meta"]["version_id"],
        "spread": {
            "model_margin_home": float(spread_model_margin),
            "vegas_spread": v_sp,
            "edge": spread_edge,
            "conf": float(spread_conf),
            "side": spread_side,
            "recommended": bool(spread_reco),
            "units": float(spread_units),
        },
        "total": {
            "model_total": float(total_model),
            "vegas_total": v_tot,
            "edge": total_edge,
            "conf": float(total_conf),
            "side": total_side,
            "recommended": bool(total_reco),
            "units": float(total_units),
        },
        "ml": {
            "model_win_prob_home": float(model_win_prob_home),
            "ml_home": ml_home,
            "ml_away": ml_away,
            "implied_home": imp_home,
            "edge_home": ml_edge,
            "conf": float(ml_conf),
            "side": ml_side,
            "recommended": bool(ml_reco),
            "units": float(ml_units),
        }
    }

# ============================================================
# BACKTEST (USES HISTORICAL LINES IF AVAILABLE)
# ============================================================

def build_backtest_games():
    """
    Uses your feature store, which is completed games only.
    Each event_id has 2 rows (home and away).
    We try to merge market lines from espn_games.csv if available.
    """
    df = FEATURES.copy()
    if df.empty:
        return pd.DataFrame()

    # identify home and away rows
    home = df[df["home_away"].astype(str).str.lower() == "home"].copy()
    away = df[df["home_away"].astype(str).str.lower() == "away"].copy()

    # merge by event_id, game_date
    m = home.merge(
        away,
        on=["event_id", "game_date"],
        suffixes=("_home", "_away"),
        how="inner"
    )

    # actuals
    m["home_points"] = pd.to_numeric(m["points_for_home"], errors="coerce")
    m["away_points"] = pd.to_numeric(m["points_for_away"], errors="coerce")
    m["actual_margin_home"] = m["home_points"] - m["away_points"]
    m["actual_total"] = m["home_points"] + m["away_points"]

    # Try merge lines from espn_games.csv
    if not ESPN_GAMES_OPT.empty and "event_id" in ESPN_GAMES_OPT.columns:
        eg = ESPN_GAMES_OPT.copy()
        eg["event_id"] = eg["event_id"].astype(str)
        if "vegas_spread" not in eg.columns:
            # attempt alternate column names
            for c in ["spread", "line", "closing_spread"]:
                if c in eg.columns:
                    eg["vegas_spread"] = eg[c]
        if "vegas_total" not in eg.columns:
            for c in ["total", "ou", "closing_total"]:
                if c in eg.columns:
                    eg["vegas_total"] = eg[c]
        # moneylines if present
        for c in ["ml_home", "moneyline_home", "vegas_moneyline_home"]:
            if c in eg.columns:
                eg["ml_home"] = eg[c]
        for c in ["ml_away", "moneyline_away", "vegas_moneyline_away"]:
            if c in eg.columns:
                eg["ml_away"] = eg[c]

        keep_cols = ["event_id", "game_date"]
        for c in ["vegas_spread", "vegas_total", "ml_home", "ml_away"]:
            if c in eg.columns:
                keep_cols.append(c)
        eg = eg[keep_cols].drop_duplicates(subset=["event_id"], keep="last")

        m = m.merge(eg, on=["event_id", "game_date"], how="left")

    return m

def backtest_run(df_games: pd.DataFrame, config: dict, strategy: dict,
                date_min=None, date_max=None,
                include_spread=True, include_total=True, include_ml=True):
    if df_games.empty:
        return pd.DataFrame(), {}

    d = df_games.copy()
    d["game_date"] = d["game_date"].astype(str)

    if date_min:
        d = d[d["game_date"] >= date_min]
    if date_max:
        d = d[d["game_date"] <= date_max]

    if d.empty:
        return pd.DataFrame(), {}

    # For backtest, we use the per-game pre columns already in the feature store rows.
    # We create lightweight "snapshots" for home and away using the merged row.
    def build_snapshot(prefix, team_name):
        snap = {"team_display": team_name, "team_norm": normalize_team_name(team_name)}
        # include torvik
        base = get_torvik_row(team_name)
        snap.update(base)

        # include all *_pre columns for that side from merged row
        for c in d.columns:
            if c.endswith(prefix):
                base_col = c.replace(prefix, "")
                snap[base_col] = d.loc[idx, c]
        return snap

    out_rows = []
    cfg = config
    strat = strategy

    for idx in d.index:
        home_team = d.loc[idx, "team_home"]
        away_team = d.loc[idx, "team_away"]
        venue = d.loc[idx, "venue_home"] if "venue_home" in d.columns else None

        # Build snapshots from the merged row (home side and away side)
        A = build_snapshot("_home", home_team)
        B = build_snapshot("_away", away_team)

        # Temporarily set active config in session for predict_markets
        # but avoid mutating global state during loop
        st.session_state["model_config"] = cfg
        st.session_state["strategy"] = strat

        pred = predict_markets(
            home_team=home_team,
            away_team=away_team,
            venue=venue or "Unknown",
            vegas_spread=d.loc[idx, "vegas_spread"] if "vegas_spread" in d.columns else None,
            vegas_total=d.loc[idx, "vegas_total"] if "vegas_total" in d.columns else None,
            ml_home=d.loc[idx, "ml_home"] if "ml_home" in d.columns else None,
            ml_away=d.loc[idx, "ml_away"] if "ml_away" in d.columns else None,
        )
        if pred is None:
            continue

        actual_margin = safe_float(d.loc[idx, "actual_margin_home"], 0.0)
        actual_total = safe_float(d.loc[idx, "actual_total"], 0.0)

        # Spread outcome
        if include_spread and pred["spread"]["vegas_spread"] is not None:
            side = pred["spread"]["side"]
            if pred["spread"]["recommended"] and side:
                v = safe_float(pred["spread"]["vegas_spread"], 0.0)
                # if betting home spread, need home cover: actual_margin > v (because v is home line, often negative)
                # If betting away spread, equivalent is actual_margin < v
                if side.startswith(home_team):
                    won = actual_margin > v
                    bet_side = "HOME"
                else:
                    won = actual_margin < v
                    bet_side = "AWAY"

                odds = strat["assumptions"]["spread_odds_default"]
                risk = 1.0
                win_amt = (100 / abs(odds)) if odds < 0 else (odds / 100)
                pnl = win_amt if won else -risk

                out_rows.append({
                    "game_date": d.loc[idx, "game_date"],
                    "event_id": d.loc[idx, "event_id"],
                    "home": home_team,
                    "away": away_team,
                    "market": "SPREAD",
                    "side": bet_side,
                    "model": round(pred["spread"]["model_margin_home"], 2),
                    "vegas": round(v, 2),
                    "edge": round(pred["spread"]["edge"], 2) if pred["spread"]["edge"] is not None else None,
                    "conf": round(pred["spread"]["conf"], 3),
                    "units": pred["spread"]["units"],
                    "won": won,
                    "pnl": pnl * pred["spread"]["units"],
                })

        # Total outcome
        if include_total and pred["total"]["vegas_total"] is not None:
            side = pred["total"]["side"]
            if pred["total"]["recommended"] and side:
                v = safe_float(pred["total"]["vegas_total"], 0.0)
                won = (actual_total > v) if side == "Over" else (actual_total < v)

                odds = strat["assumptions"]["total_odds_default"]
                risk = 1.0
                win_amt = (100 / abs(odds)) if odds < 0 else (odds / 100)
                pnl = win_amt if won else -risk

                out_rows.append({
                    "game_date": d.loc[idx, "game_date"],
                    "event_id": d.loc[idx, "event_id"],
                    "home": home_team,
                    "away": away_team,
                    "market": "TOTAL",
                    "side": side.upper(),
                    "model": round(pred["total"]["model_total"], 2),
                    "vegas": round(v, 2),
                    "edge": round(pred["total"]["edge"], 2) if pred["total"]["edge"] is not None else None,
                    "conf": round(pred["total"]["conf"], 3),
                    "units": pred["total"]["units"],
                    "won": won,
                    "pnl": pnl * pred["total"]["units"],
                })

        # ML outcome (only if lines exist)
        if include_ml and pred["ml"]["implied_home"] is not None:
            side = pred["ml"]["side"]
            if pred["ml"]["recommended"] and side:
                home_won = actual_margin > 0
                won = home_won if side.startswith(home_team) else (not home_won)

                # Use actual ML odds if present, else skip
                if side.startswith(home_team):
                    odds = safe_float(pred["ml"]["ml_home"], None)
                else:
                    odds = safe_float(pred["ml"]["ml_away"], None)

                if odds is None or odds == 0:
                    continue

                risk = 1.0
                win_amt = (odds / 100) if odds > 0 else (100 / abs(odds))
                pnl = win_amt if won else -risk

                out_rows.append({
                    "game_date": d.loc[idx, "game_date"],
                    "event_id": d.loc[idx, "event_id"],
                    "home": home_team,
                    "away": away_team,
                    "market": "ML",
                    "side": "HOME" if side.startswith(home_team) else "AWAY",
                    "model": round(pred["ml"]["model_win_prob_home"], 3),
                    "vegas": round(pred["ml"]["implied_home"], 3),
                    "edge": round(pred["ml"]["edge_home"], 3) if pred["ml"]["edge_home"] is not None else None,
                    "conf": round(pred["ml"]["conf"], 3),
                    "units": pred["ml"]["units"],
                    "won": won,
                    "pnl": pnl * pred["ml"]["units"],
                })

    res = pd.DataFrame(out_rows)
    if res.empty:
        return res, {}

    summary = {
        "bets": len(res),
        "win_rate": float(res["won"].mean()) if len(res) else 0.0,
        "total_pnl_units": float(res["pnl"].sum()),
        "avg_pnl_per_bet": float(res["pnl"].mean()),
        "by_market": res.groupby("market").agg(
            bets=("pnl", "count"),
            win_rate=("won", "mean"),
            pnl=("pnl", "sum"),
            avg_pnl=("pnl", "mean"),
        ).reset_index(),
    }
    return res, summary

# ============================================================
# UI: SIDEBAR NAV
# ============================================================

st.sidebar.title("🏀 CBB Model")
st.sidebar.markdown("---")

with st.sidebar.expander("Supabase Auth", expanded=False):
    if has_supabase_creds():
        sb_session = None
        try:
            sb_session = sb_auth_session()
        except Exception:
            sb_session = None
        user_email = None
        if sb_session and sb_session.user:
            user_email = sb_session.user.email

        if user_email:
            st.success(f"Signed in as {user_email}")
            if st.button("Sign out"):
                try:
                    sb_auth_sign_out()
                    st.info("Signed out.")
                except Exception as exc:
                    st.warning(f"Sign out failed: {exc}")
        else:
            email = st.text_input("Email", key="sb_auth_email")
            password = st.text_input("Password", type="password", key="sb_auth_password")
            if st.button("Sign in"):
                try:
                    sb_auth_sign_in(email, password)
                    st.success("Signed in.")
                except Exception as exc:
                    st.warning(f"Sign in failed: {exc}")
    else:
        st.info("Supabase credentials missing.")

strategy = get_strategy()
strategy["assumptions"]["bankroll"] = st.sidebar.number_input(
    "Bankroll ($)",
    value=int(strategy["assumptions"]["bankroll"]),
    step=100,
    min_value=100
)

page = st.sidebar.radio(
    "Navigate",
    [
        "📅 Slate (Upcoming)",
        "🧠 Model Studio",
        "🔁 Backtest Lab",
        "📒 Ledger",
    ],
)

st.sidebar.markdown("---")
st.sidebar.caption(f"Updated: {datetime.now(tz=LOCAL_TZ).strftime('%m/%d %I:%M%p')}")

# ============================================================
# PAGE: SLATE (UPCOMING)
# ============================================================

if page == "📅 Slate (Upcoming)":
    st.title("📅 Slate (Upcoming)")
    st.caption("Every game. Spread, Total, ML. Ranked. No cap.")

    upcoming, errors = get_upcoming_games(days_ahead=7)

    if errors:
        with st.expander("ESPN Fetch Errors", expanded=False):
            for e in errors:
                st.error(f"{e['date']}: {e['error']}")

    if upcoming.empty:
        st.warning("No games found.")
        st.stop()

    cfg = get_active_model_config()

    # Strategy controls (placeholders allowed)
    st.subheader("Filters and Strategy (no-code)")
    c1, c2, c3 = st.columns(3)

    with c1:
        strategy["spread"]["edge_min"] = st.slider("Spread edge min (pts)", 0.0, 10.0, float(strategy["spread"]["edge_min"]), 0.5)
        strategy["spread"]["conf_min"] = st.slider("Spread conf min", 0.70, 0.95, float(strategy["spread"]["conf_min"]), 0.01)

    with c2:
        strategy["total"]["edge_min"] = st.slider("Total edge min (pts)", 0.0, 10.0, float(strategy["total"]["edge_min"]), 0.5)
        strategy["total"]["conf_min"] = st.slider("Total conf min", 0.70, 0.95, float(strategy["total"]["conf_min"]), 0.01)

    with c3:
        strategy["ml"]["edge_min_prob"] = st.slider("ML win-prob edge min", 0.00, 0.10, float(strategy["ml"]["edge_min_prob"]), 0.005)
        strategy["ml"]["conf_min"] = st.slider("ML conf min", 0.70, 0.95, float(strategy["ml"]["conf_min"]), 0.01)

    st.markdown("---")

    run_date = datetime.now(tz=LOCAL_TZ).strftime("%Y%m%d")

    rows = []
    ledger_rows = []

    for _, g in upcoming.iterrows():
        pred = predict_markets(
            home_team=g["home_team"],
            away_team=g["away_team"],
            venue=g.get("venue") or "Unknown",
            vegas_spread=g.get("vegas_spread"),
            vegas_total=g.get("vegas_total"),
            ml_home=g.get("ml_home"),
            ml_away=g.get("ml_away"),
        )
        if pred is None:
            continue

        # Build ranked "value score"
        # Simple: prioritize recommended, then edge magnitude, then confidence.
        value_score = 0.0
        for mk in ["spread", "total", "ml"]:
            rec = pred[mk]["recommended"]
            edge = pred[mk].get("edge", pred[mk].get("edge_home", 0.0))
            conf = pred[mk]["conf"]
            if rec and edge is not None:
                value_score += (abs(safe_float(edge, 0.0)) * conf)

        rows.append({
            "Date": g["game_date"],
            "Time": g.get("event_time_local") or "",
            "Matchup": f"{g['away_team']} @ {g['home_team']}",
            "Venue": g.get("venue") or "",
            "Spread Model": round(pred["spread"]["model_margin_home"], 2),
            "Spread Vegas": pred["spread"]["vegas_spread"],
            "Spread Edge": round(pred["spread"]["edge"], 2) if pred["spread"]["edge"] is not None else None,
            "Spread Conf": round(pred["spread"]["conf"], 3),
            "Spread Reco": pred["spread"]["side"] if pred["spread"]["recommended"] else "",
            "Spread Units": pred["spread"]["units"] if pred["spread"]["recommended"] else 0.0,
            "Total Model": round(pred["total"]["model_total"], 1),
            "Total Vegas": pred["total"]["vegas_total"],
            "Total Edge": round(pred["total"]["edge"], 2) if pred["total"]["edge"] is not None else None,
            "Total Conf": round(pred["total"]["conf"], 3),
            "Total Reco": pred["total"]["side"] if pred["total"]["recommended"] else "",
            "Total Units": pred["total"]["units"] if pred["total"]["recommended"] else 0.0,
            "ML Win% Home": round(pred["ml"]["model_win_prob_home"], 3),
            "ML Implied Home": round(pred["ml"]["implied_home"], 3) if pred["ml"]["implied_home"] is not None else None,
            "ML Edge": round(pred["ml"]["edge_home"], 3) if pred["ml"]["edge_home"] is not None else None,
            "ML Conf": round(pred["ml"]["conf"], 3),
            "ML Reco": pred["ml"]["side"] if pred["ml"]["recommended"] else "",
            "ML Units": pred["ml"]["units"] if pred["ml"]["recommended"] else 0.0,
            "Value Score": round(value_score, 4),
            "event_id": g["event_id"],
        })

        # Ledger rows (system WOULD bet)
        for mk in ["spread", "total", "ml"]:
            if mk == "ml":
                side = pred["ml"]["side"]
                rec = pred["ml"]["recommended"]
                conf = pred["ml"]["conf"]
                units = pred["ml"]["units"]
                model_val = pred["ml"]["model_win_prob_home"]
                vegas_val = pred["ml"]["implied_home"]
                edge_val = pred["ml"]["edge_home"]
            else:
                side = pred[mk]["side"]
                rec = pred[mk]["recommended"]
                conf = pred[mk]["conf"]
                units = pred[mk]["units"]
                model_val = pred[mk]["model_margin_home"] if mk == "spread" else pred[mk]["model_total"]
                vegas_val = pred[mk]["vegas_spread"] if mk == "spread" else pred[mk]["vegas_total"]
                edge_val = pred[mk]["edge"]

            if rec and side:
                ledger_rows.append({
                    "id": ledger_key(run_date, g["event_id"], mk.upper(), side),
                    "run_date": run_date,
                    "game_date": g["game_date"],
                    "event_id": g["event_id"],
                    "home_team": g["home_team"],
                    "away_team": g["away_team"],
                    "market": mk.upper(),
                    "side": side,
                    "model_value": float(model_val) if model_val is not None else None,
                    "vegas_value": float(vegas_val) if vegas_val is not None else None,
                    "edge": float(edge_val) if edge_val is not None else None,
                    "conf": float(conf),
                    "recommended": True,
                    "units": float(units),
                    "result": None,
                    "pnl": None,
                    "model_version": cfg["meta"]["version_id"],
                    "meta": {
                        "venue": g.get("venue"),
                        "time": g.get("event_time_local"),
                        "odds_provider": g.get("odds_provider"),
                    }
                })

    slate = pd.DataFrame(rows)
    if slate.empty:
        st.warning("No predictions generated.")
        st.stop()

    slate = slate.sort_values(["Value Score"], ascending=False).reset_index(drop=True)

    st.subheader("Ranked Slate")
    st.dataframe(
        slate.drop(columns=["event_id"]),
        use_container_width=True,
        hide_index=True
    )

    st.markdown("---")
    st.subheader("Game Cards (why the edge exists)")

    # Card view
    for i in range(min(len(slate), 50)):
        r = slate.iloc[i]
        title = f"{r['Matchup']} | {r['Date']} | Value: {r['Value Score']}"
        with st.expander(title, expanded=(i < 8)):
            st.caption(r.get("Venue") or "")

            c1, c2, c3 = st.columns(3)

            with c1:
                st.markdown("### Spread")
                st.write(f"Model margin (home): {r['Spread Model']:+.2f}")
                st.write(f"Vegas: {r['Spread Vegas']}")
                st.write(f"Edge: {r['Spread Edge']}")
                st.write(f"Conf: {r['Spread Conf']}")
                if r["Spread Reco"]:
                    st.success(f"Reco: {r['Spread Reco']} | Units: {r['Spread Units']}")
                else:
                    st.info("No spread bet")

            with c2:
                st.markdown("### Total")
                st.write(f"Model total: {r['Total Model']:.1f}")
                st.write(f"Vegas: {r['Total Vegas']}")
                st.write(f"Edge: {r['Total Edge']}")
                st.write(f"Conf: {r['Total Conf']}")
                if r["Total Reco"]:
                    st.success(f"Reco: {r['Total Reco']} | Units: {r['Total Units']}")
                else:
                    st.info("No total bet")

            with c3:
                st.markdown("### Moneyline")
                st.write(f"Home win%: {r['ML Win% Home']:.1%}")
                st.write(f"Implied home: {r['ML Implied Home']}")
                st.write(f"Edge: {r['ML Edge']}")
                st.write(f"Conf: {r['ML Conf']}")
                if r["ML Reco"]:
                    st.success(f"Reco: {r['ML Reco']} | Units: {r['ML Units']}")
                else:
                    st.info("No ML bet")

    st.markdown("---")
    st.subheader("Daily Ledger: What the system WOULD bet")

    reco_df = pd.DataFrame(ledger_rows)
    st.write(f"Recommended bets today: {len(reco_df)}")

    if not reco_df.empty:
        st.dataframe(reco_df[[
            "game_date", "event_id", "home_team", "away_team",
            "market", "side", "edge", "conf", "units", "model_version"
        ]], use_container_width=True, hide_index=True)

        c1, c2 = st.columns(2)
        with c1:
            if st.button("Save ledger rows"):
                # save to supabase if possible, else keep local
                err = sb_upsert_ledger_rows(reco_df.to_dict(orient="records"))
                if err:
                    append_local_ledger(reco_df.to_dict(orient="records"))
                    st.warning(f"Saved locally only. Supabase issue: {err}")
                else:
                    st.success("Saved to Supabase bet_ledger.")
        with c2:
            csv = reco_df.to_csv(index=False).encode("utf-8")
            st.download_button("Download ledger CSV", csv, file_name=f"ledger_{run_date}.csv", mime="text/csv")

# ============================================================
# PAGE: MODEL STUDIO
# ============================================================

elif page == "🧠 Model Studio":
    st.title("🧠 Model Studio")
    st.caption("No-code knobs. Three tabs: Spread, Total, Moneyline. Save versions. Compare later.")

    cfg = get_active_model_config()

    st.subheader("Model Identity")
    c1, c2 = st.columns(2)
    with c1:
        cfg["meta"]["name"] = st.text_input("Model name", value=cfg["meta"].get("name", "Baseline v1"))
    with c2:
        cfg["meta"]["version_id"] = st.text_input("Version id", value=cfg["meta"].get("version_id", "baseline-v1"))

    st.markdown("---")

    tabs = st.tabs(["Spread", "Total", "Moneyline"])

    def studio_controls(market_key: str, label: str):
        mcfg = cfg[market_key]

        st.markdown(f"### {label} Controls")

        c1, c2, c3 = st.columns(3)
        with c1:
            mcfg["recent_window"] = st.selectbox(
                "Recent window (games)",
                options=[3, 5, 7, 10],
                index=[3, 5, 7, 10].index(int(mcfg.get("recent_window", 7)))
            )
        with c2:
            mcfg["recent_blend"] = st.slider("Recent blend", 0.0, 0.70, float(mcfg.get("recent_blend", 0.35)), 0.05)
        with c3:
            mcfg["volatility_penalty"] = st.slider("Volatility penalty", 0.0, 1.2, float(mcfg.get("volatility_penalty", 0.8)), 0.05)

        if market_key == "spread":
            mcfg["home_court"] = st.slider("Home court points", 0.0, 5.0, float(mcfg.get("home_court", 2.7)), 0.1)

        st.markdown("#### Component Weights")
        w = mcfg["weights"]

        # Keep these simple and stable
        if market_key == "spread":
            w["torvik_adjem"] = st.slider("Season strength (Torvik AdjEM)", 0.0, 1.0, float(w.get("torvik_adjem", 0.55)), 0.05)
            w["recent_netrtg"] = st.slider("Recent net rating (feature store)", 0.0, 1.0, float(w.get("recent_netrtg", 0.25)), 0.05)
            w["four_factors"] = st.slider("Four factors (eFG/TO/ORB/FTR)", 0.0, 1.0, float(w.get("four_factors", 0.20)), 0.05)

        if market_key == "total":
            w["tempo"] = st.slider("Tempo (pace)", 0.0, 1.0, float(w.get("tempo", 0.25)), 0.05)
            w["efficiency"] = st.slider("Efficiency (ORTG/DRTG blend)", 0.0, 1.0, float(w.get("efficiency", 0.60)), 0.05)
            w["four_factors"] = st.slider("Four factors (small total adjustment)", 0.0, 1.0, float(w.get("four_factors", 0.15)), 0.05)

        if market_key == "ml":
            w["spread_margin"] = st.slider("Use spread margin as main driver", 0.0, 1.0, float(w.get("spread_margin", 0.70)), 0.05)
            w["four_factors"] = st.slider("Four factors (winprob tweak)", 0.0, 1.0, float(w.get("four_factors", 0.30)), 0.05)

        st.markdown("#### Confidence Stabilizers (do not pick sides directly)")
        c1, c2, c3 = st.columns(3)
        with c1:
            mcfg["sos_conf_strength"] = st.slider("SoS confidence strength", 0.0, 1.0, float(mcfg.get("sos_conf_strength", 0.5)), 0.05)
        with c2:
            mcfg["market_anchor"] = st.slider("Market anchor %", 0.0, 0.30, float(mcfg.get("market_anchor", 0.10)), 0.02)
        with c3:
            mcfg["market_anchor_min_conf"] = st.slider("Anchor only if conf below", 0.70, 0.95, float(mcfg.get("market_anchor_min_conf", 0.80)), 0.01)

        # Normalize weights to sum to 1.0 to keep behavior predictable
        wt = sum(float(v) for v in w.values())
        if wt > 0:
            for k in list(w.keys()):
                w[k] = float(w[k]) / wt

        cfg[market_key] = mcfg

    with tabs[0]:
        studio_controls("spread", "Spread")

    with tabs[1]:
        studio_controls("total", "Total")

    with tabs[2]:
        studio_controls("ml", "Moneyline")

    st.markdown("---")

    st.subheader("Save / Load Versions")

    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("Save as new version"):
            cfg["meta"]["created_at"] = datetime.now(tz=LOCAL_TZ).isoformat()
            set_active_model_config(cfg)
            err = sb_save_model_config(cfg, is_active=False)
            if err:
                st.warning(f"Saved in-session only. Supabase issue: {err}")
            else:
                st.success("Saved to Supabase model_versions.")
    with c2:
        if st.button("Set active (Supabase)"):
            err = sb_set_active_model_config(cfg["meta"]["version_id"])
            if err:
                st.warning(f"Could not set active in Supabase. {err}")
            else:
                st.success("Active model set in Supabase.")
    with c3:
        if st.button("Reset to Baseline v1"):
            set_active_model_config(json.loads(json.dumps(DEFAULT_MODEL_CONFIG)))
            st.success("Reset.")

    configs, err = sb_load_model_configs()
    if err:
        st.info("Model versions in Supabase not available. If you want persistence, create a table named model_versions.")
    else:
        if configs:
            options = [
                f"{c.get('notes')} | {c.get('model_version_id')} | active={c.get('is_active')}"
                for c in configs
            ]
            pick = st.selectbox("Load a saved model config", options=options, index=0)
            if st.button("Load selected"):
                selected = configs[options.index(pick)]
                cfg_loaded = selected.get("ensemble_weights")
                if isinstance(cfg_loaded, dict):
                    set_active_model_config(cfg_loaded)
                    st.success("Loaded into app.")
                else:
                    st.error("Selected row has no ensemble_weights.")

    st.markdown("---")
    st.subheader("Quick sanity check (single matchup)")
    teams = sorted(TORVIK["team"].dropna().unique().tolist()) if not TORVIK.empty else sorted(FEATURES["team"].dropna().unique().tolist())
    c1, c2 = st.columns(2)
    with c1:
        home = st.selectbox("Home team", options=teams, index=0)
    with c2:
        away = st.selectbox("Away team", options=teams, index=1 if len(teams) > 1 else 0)

    if home == away:
        st.error("Pick two different teams.")
    else:
        pred = predict_markets(home, away, "Neutral", vegas_spread=None, vegas_total=None, ml_home=None, ml_away=None)
        if pred:
            st.json(pred)

# ============================================================
# PAGE: BACKTEST LAB
# ============================================================

elif page == "🔁 Backtest Lab":
    st.title("🔁 Backtest Lab")
    st.caption("Test thresholds and unit rules. See what worked. Then adjust Model Studio and rerun.")

    cfg = get_active_model_config()
    strat = get_strategy()

    games = build_backtest_games()
    if games.empty:
        st.warning("No completed games available for backtest.")
        st.stop()

    has_lines = ("vegas_spread" in games.columns) or ("vegas_total" in games.columns) or ("ml_home" in games.columns)
    if not has_lines:
        st.warning("Historical market lines not found (espn_games.csv). Backtest will likely show no bets. Add espn_games.csv with vegas_spread, vegas_total, and ML if you want ROI backtests.")

    st.subheader("Backtest filters")
    c1, c2, c3 = st.columns(3)
    with c1:
        date_min = st.text_input("From date (YYYYMMDD)", value=str(games["game_date"].min()))
    with c2:
        date_max = st.text_input("To date (YYYYMMDD)", value=str(games["game_date"].max()))
    with c3:
        markets = st.multiselect("Markets", options=["SPREAD", "TOTAL", "ML"], default=["SPREAD", "TOTAL", "ML"])

    st.subheader("Strategy knobs (test without changing the model)")
    c1, c2, c3 = st.columns(3)
    with c1:
        strat["spread"]["edge_min"] = st.slider("Spread edge min (pts)", 0.0, 10.0, float(strat["spread"]["edge_min"]), 0.5)
        strat["spread"]["conf_min"] = st.slider("Spread conf min", 0.70, 0.95, float(strat["spread"]["conf_min"]), 0.01)
    with c2:
        strat["total"]["edge_min"] = st.slider("Total edge min (pts)", 0.0, 10.0, float(strat["total"]["edge_min"]), 0.5)
        strat["total"]["conf_min"] = st.slider("Total conf min", 0.70, 0.95, float(strat["total"]["conf_min"]), 0.01)
    with c3:
        strat["ml"]["edge_min_prob"] = st.slider("ML win-prob edge min", 0.00, 0.10, float(strat["ml"]["edge_min_prob"]), 0.005)
        strat["ml"]["conf_min"] = st.slider("ML conf min", 0.70, 0.95, float(strat["ml"]["conf_min"]), 0.01)

    include_spread = "SPREAD" in markets
    include_total = "TOTAL" in markets
    include_ml = "ML" in markets

    if st.button("Run Backtest", type="primary"):
        with st.spinner("Running..."):
            res, summary = backtest_run(
                games, cfg, strat,
                date_min=date_min, date_max=date_max,
                include_spread=include_spread,
                include_total=include_total,
                include_ml=include_ml
            )

        if res.empty:
            st.warning("No bets triggered under these settings.")
            st.stop()

        st.subheader("Results")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Bets", summary.get("bets", 0))
        c2.metric("Win rate", f"{summary.get('win_rate', 0.0):.1%}")
        c3.metric("Total PnL (units)", f"{summary.get('total_pnl_units', 0.0):+.2f}")
        c4.metric("Avg PnL per bet", f"{summary.get('avg_pnl_per_bet', 0.0):+.3f}")

        st.markdown("### By market")
        st.dataframe(summary["by_market"], use_container_width=True, hide_index=True)

        st.markdown("### Bet log")
        st.dataframe(res.sort_values(["game_date"], ascending=False), use_container_width=True, hide_index=True)

        csv = res.to_csv(index=False).encode("utf-8")
        st.download_button("Download backtest CSV", csv, file_name="backtest_results.csv", mime="text/csv")

# ============================================================
# PAGE: LEDGER
# ============================================================

elif page == "📒 Ledger":
    st.title("📒 Ledger")
    st.caption("This is the daily log of what the system WOULD bet. Use this for accountability and learning.")

    if has_supabase_creds():
        def _do(tbl):
            return tbl.select("*").order("run_date", desc=True).limit(5000).execute()
        resp, err = sb_try("bet_ledger", "load ledger", _do)

        if err or resp is None:
            st.warning("Could not load bet_ledger from Supabase. Showing local ledger (if any).")
            df = get_local_ledger_df()
        else:
            df = pd.DataFrame(resp.data or [])
    else:
        df = get_local_ledger_df()

    if df is None or df.empty:
        st.info("No ledger rows found yet. Go to Slate and click Save ledger rows.")
        st.stop()

    st.subheader("Ledger table")
    st.dataframe(df, use_container_width=True, hide_index=True)

    st.markdown("---")
    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button("Download ledger CSV", csv, file_name="ledger_export.csv", mime="text/csv")
