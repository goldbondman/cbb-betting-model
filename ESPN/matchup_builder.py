"""
Matchup Builder
Build model-ready matchup table with home/away features side-by-side.
Converts two team-game rows (home + away) into one matchup row.
"""

import hashlib
from typing import List

import pandas as pd
import numpy as np

from data_utils import (
    _normalize_id_series,
    _normalize_home_away_series,
    _stable_row_hash,
)


def build_matchups_model_ready(df_features: pd.DataFrame) -> pd.DataFrame:
    """
    Build matchup table (one row per game, home/away features side-by-side).
    
    Input: Team-game rows with pregame features
    Output: Matchup rows with h_* and a_* features
    
    Process:
    1. Split into home and away dataframes
    2. Select features (pregame only)
    3. Merge on event_id
    4. Add outcome labels (home_win)
    5. Generate row hash
    
    Args:
        df_features: DataFrame with team-game features
        
    Returns:
        DataFrame with matchup-level features (one row per game)
    """
    df = df_features.copy()
    df["event_id"] = _normalize_id_series(df["event_id"])
    df["home_away"] = _normalize_home_away_series(df["home_away"])
    df["game_dt"] = pd.to_datetime(df["game_datetime_utc"], utc=True, errors="coerce")

    # Split home/away
    home = df[df["home_away"] == "home"].copy()
    away = df[df["home_away"] == "away"].copy()

    # Base columns (game metadata)
    keep_base = [
        "event_id", "game_datetime_utc", "game_date", "game_date_utc", "venue",
        "home_team", "away_team",
        "points_for", "points_against", "margin",
        "completed", "data_ok",
        "state", "status_desc", "status_detail",
        "neutral_site", "is_ot", "num_ot",
    ]
    keep_base = [c for c in keep_base if c in home.columns]

    # Feature columns (pregame features only)
    feat_cols = [c for c in df.columns if (
        c.endswith("_pre") or 
        c.endswith("_noblow_pre") or 
        c.endswith("_eff_pre") or 
        c in [
            "days_rest", "days_since_last_game", "games_last_7_days", 
            "back_to_back", "three_in_six",
            "avg_opp_netrtg_l7_pre", "avg_opp_ortg_l7_pre", "avg_opp_drtg_l7_pre", 
            "sos_season_pre",
            "netrtg_adj_l7", "efg_adj_l7", "tov_adj_l7", "orb_adj_l7", "ftr_adj_l7",
            "netrtg_adj_season", "efg_adj_season", "tov_adj_season", 
            "orb_adj_season", "ftr_adj_season",
            "style_distance_l7", "pace_mismatch_l7", "rim_vs_foul_l7",
            "blowout",
            "pulled_at_utc", "parse_version", "source",
            "opp_join_ok",
        ]
    )]
    feat_cols = [c for c in dict.fromkeys(feat_cols) if c in df.columns]

    # Home columns
    home_keep = keep_base + ["team", "team_id"] + feat_cols
    home_keep = [c for c in dict.fromkeys(home_keep) if c in home.columns]

    # Away columns (no base, just team + features)
    away_keep = ["event_id"] + ["team", "team_id"] + feat_cols
    away_keep = [c for c in dict.fromkeys(away_keep) if c in away.columns]

    h = home[home_keep].copy()
    a = away[away_keep].copy()

    # Normalize IDs
    h["team_id"] = _normalize_id_series(h["team_id"])
    a["team_id"] = _normalize_id_series(a["team_id"])

    # Prefix features
    h = h.rename(columns={c: f"h_{c}" for c in h.columns if c != "event_id"})
    a = a.rename(columns={c: f"a_{c}" for c in a.columns if c != "event_id"})

    # Merge on event_id
    m = h.merge(a, on="event_id", how="inner")

    # Add outcome labels
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

    # Status
    if "h_completed" in m.columns:
        m["status"] = np.where(m["h_completed"] == True, "final", "not_final")
    elif "h_state" in m.columns:
        m["status"] = np.where(m["h_state"].astype(str).str.lower().eq("post"), "final", "not_final")
    else:
        m["status"] = "unknown"

    # Add convenience columns
    if "h_game_datetime_utc" in m.columns:
        m["game_datetime_utc"] = m["h_game_datetime_utc"]
    if "h_venue" in m.columns:
        m["venue"] = m["h_venue"]

    # Sort
    if "game_datetime_utc" in m.columns:
        m["game_dt"] = pd.to_datetime(m["game_datetime_utc"], utc=True, errors="coerce")
        m = m.sort_values(["game_dt", "event_id"]).drop(columns=["game_dt"], errors="ignore")
    else:
        m = m.sort_values(["event_id"])

    # Row hash (required for DB upsert / NOT NULL)
    # Build deterministic hash from stable identifiers
    hash_keys = [
        "event_id",
        "game_datetime_utc",
        "h_team_id",
        "a_team_id",
        "h_team",
        "a_team",
        "h_parse_version",
        "a_parse_version",
    ]
    present_keys = [k for k in hash_keys if k in m.columns]

    def _row_hash_from_row(r: pd.Series) -> str:
        d = {}
        for k in present_keys:
            v = r.get(k)
            if pd.isna(v):
                v = ""
            d[k] = str(v)
        if present_keys:
            return _stable_row_hash(d, keys=present_keys)
        return hashlib.sha1(str(r.to_dict()).encode("utf-8")).hexdigest()

    m["row_hash"] = m.apply(_row_hash_from_row, axis=1)

    # Hard guarantee: no nulls/blanks
    m["row_hash"] = m["row_hash"].astype(str)
    bad = m["row_hash"].isin(["", "nan", "None"])
    if bad.any() and "event_id" in m.columns:
        m.loc[bad, "row_hash"] = m.loc[bad, "event_id"].astype(str).apply(
            lambda x: hashlib.sha1(x.encode("utf-8")).hexdigest()
        )

    return m
