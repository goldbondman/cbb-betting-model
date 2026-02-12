"""
Opponent Merge Logic
Symmetric opponent matching for team-game rows.
Joins home/away opponents to enable defensive metrics and opponent-adjusted features.
"""

from typing import Optional

import pandas as pd
import numpy as np

from data_utils import (
    _normalize_id_series,
    _normalize_home_away_series,
    _flip_home_away,
)


def _merge_opponent_rows(df: pd.DataFrame) -> pd.DataFrame:
    """
    Merge opponent data for each game using symmetric join.
    
    Strategy:
    1. Create merge keys: event_id + home_away
    2. Create opponent keys: event_id + opposite_home_away
    3. Join to get opponent's stats as opp_* columns
    4. Validate symmetry (each event should have exactly 2 rows)
    
    This enables defensive metrics like:
    - efg_allowed (what opponent shot against us)
    - tov_forced (opponent's turnover rate against us)
    - Opponent strength adjustments
    
    Args:
        df: DataFrame with team-game rows (must have event_id, home_away)
        
    Returns:
        DataFrame with opp_* columns added for opponent's stats
        
    Raises:
        ValueError: If required columns missing
    """
    out = df.copy()

    # Validate required columns
    for c in ["event_id", "home_away"]:
        if c not in out.columns:
            raise ValueError(f"_merge_opponent_rows requires column: {c}")

    # Normalize keys
    out["event_id"] = _normalize_id_series(out["event_id"])
    out["home_away"] = _normalize_home_away_series(out["home_away"])

    # Drop any existing opp_* columns (clean slate)
    out = out.drop(columns=[c for c in out.columns if c.startswith("opp_")], errors="ignore")

    # Create merge keys
    out["_key"] = out["event_id"].astype(str) + "|" + out["home_away"].astype("string")
    out["_opp_ha"] = out["home_away"].apply(_flip_home_away)
    out["_opp_key"] = out["event_id"].astype(str) + "|" + out["_opp_ha"].astype("string")

    # Dedupe before join (keep last by key to get most complete data)
    out = out.drop_duplicates(subset=["_key"], keep="last")

    # Select columns to join from opponent
    # Include: identifying info, game stats, and any pregame features (*_pre)
    opp_cols = [c for c in out.columns if (
        c.endswith("_pre") or
        c in ["team", "team_id", "points_for", "points_against",
              "efg", "tov_pct", "orb_pct", "drb_pct", "ftr", "3par",
              "off_ppp", "def_ppp",
              "ortg", "drtg", "netrtg", "pace", "_key"]
    )]

    # Create lookup table
    lookup = out[opp_cols].copy()
    lookup = lookup.rename(columns={"_key": "_lookup_key"})
    lookup = lookup.rename(columns={c: f"opp_{c}" for c in lookup.columns if c != "_lookup_key"})

    # Perform symmetric join
    out = out.merge(
        lookup,
        left_on="_opp_key",
        right_on="_lookup_key",
        how="left",
        validate="many_to_one",  # Each team row joins to exactly one opponent row
    ).drop(columns=["_lookup_key"], errors="ignore")

    # Create defensive metric aliases (what opponent did against us)
    out["efg_allowed_game"] = out["opp_efg"] if "opp_efg" in out.columns else np.nan
    out["ftr_allowed_game"] = out["opp_ftr"] if "opp_ftr" in out.columns else np.nan
    out["tov_forced_game"] = out["opp_tov_pct"] if "opp_tov_pct" in out.columns else np.nan

    # Validate join success
    out["opp_join_ok"] = out["opp_team_id"].notna() if "opp_team_id" in out.columns else (
        out["opp_team"].notna() if "opp_team" in out.columns else False
    )
    out["opp_join_source"] = np.where(out["opp_join_ok"] == True, "merge", pd.NA)

    # Clean up temporary columns
    return out.drop(columns=["_key", "_opp_key", "_opp_ha"], errors="ignore")


def _drop_bad_event_ids_keep_good(df: pd.DataFrame, label: str) -> pd.DataFrame:
    """
    Drop events that don't have exactly 2 rows (home + away).
    
    Valid games should have exactly 2 rows: one home, one away.
    Events with 1 row (missing opponent) or >2 rows (duplicates) are invalid.
    
    Args:
        df: DataFrame with event_id column
        label: Description for logging (e.g., "PASS1 logs_new")
        
    Returns:
        DataFrame with only valid events (exactly 2 rows each)
    """
    if "event_id" not in df.columns:
        return df
    
    df = df.copy()
    df["event_id"] = _normalize_id_series(df["event_id"])
    
    # Count rows per event
    counts = df.groupby("event_id").size()
    good_ids = counts[counts == 2].index
    bad_ids = counts[counts != 2].index
    
    if len(bad_ids) > 0:
        print(
            f"[WARN] {label}: dropping {len(bad_ids)} event_ids not exactly 2 rows. "
            f"Sample: {counts.loc[bad_ids].head(15).to_dict()}"
        )
    
    return df[df["event_id"].isin(good_ids)].copy()


def validate_opponent_merge(df: pd.DataFrame) -> dict:
    """
    Validate opponent merge quality and return diagnostics.
    
    Checks:
    - Join success rate
    - Symmetry (each event has 2 rows)
    - Key stats presence in opponent data
    
    Args:
        df: DataFrame after opponent merge
        
    Returns:
        Dictionary with validation metrics:
        - join_rate: Fraction of rows with successful opponent join
        - symmetric_events: Fraction of events with exactly 2 rows
        - avg_opp_features: Average number of opp_*_pre features present
    """
    diagnostics = {}
    
    # Join success rate
    if "opp_join_ok" in df.columns:
        diagnostics["join_rate"] = df["opp_join_ok"].sum() / len(df) if len(df) > 0 else 0.0
    else:
        diagnostics["join_rate"] = 0.0
    
    # Symmetry check
    if "event_id" in df.columns:
        counts = df.groupby("event_id").size()
        symmetric = (counts == 2).sum()
        total = len(counts)
        diagnostics["symmetric_events"] = symmetric / total if total > 0 else 0.0
    else:
        diagnostics["symmetric_events"] = 0.0
    
    # Feature coverage
    opp_pre_cols = [c for c in df.columns if c.startswith("opp_") and c.endswith("_pre")]
    if opp_pre_cols:
        coverage = df[opp_pre_cols].notna().mean().mean()
        diagnostics["avg_opp_features"] = coverage
    else:
        diagnostics["avg_opp_features"] = 0.0
    
    return diagnostics
