"""
Metrics Calculator
Compute basketball metrics and rolling features.
All calculations are leak-free (pregame features only).
"""

import re
from collections import defaultdict, deque
from typing import List

import pandas as pd
import numpy as np

from data_utils import _safe_div, _estimate_possessions, _to_int


# ---------------- Per-Game Metrics ----------------

def _compute_per_game_advanced_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute advanced metrics for each team-game row.
    
    Metrics calculated:
    - Pace (possessions)
    - Offensive/Defensive Rating (points per 100 possessions)
    - Net Rating
    - Blowout flags
    - OT detection
    - Noise flags (extreme pace, blowouts, OT)
    - Data quality flag (data_ok)
    
    Args:
        df: DataFrame with team-game rows
        
    Returns:
        DataFrame with advanced metrics added
    """
    out = df.copy()

    # Normalize key columns
    if "event_id" in out.columns:
        from data_utils import _normalize_id_series
        out["event_id"] = _normalize_id_series(out["event_id"])
    if "team_id" in out.columns:
        from data_utils import _normalize_id_series
        out["team_id"] = _normalize_id_series(out["team_id"])
    if "home_away" in out.columns:
        from data_utils import _normalize_home_away_series
        out["home_away"] = _normalize_home_away_series(out["home_away"])

    # Convert to numeric
    numeric_cols = [
        "points_for", "points_against", "poss", "fga", "fta", "tov", "orb", "drb", "reb", "margin",
        "efg", "ftr", "3par", "3p_pct", "ft_pct", "tov_pct", "orb_pct", "drb_pct",
    ]
    for c in numeric_cols:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")

    # Pace (possessions)
    out["pace"] = out["poss"]

    # Offensive/Defensive Rating (per 100 possessions)
    out["ortg"] = out.apply(
        lambda r: _safe_div(r.get("points_for", np.nan) * 100.0, r.get("poss", np.nan), np.nan),
        axis=1
    )
    out["drtg"] = out.apply(
        lambda r: _safe_div(r.get("points_against", np.nan) * 100.0, r.get("poss", np.nan), np.nan),
        axis=1
    )
    out["netrtg"] = out["ortg"] - out["drtg"]

    # Blowout detection (18+ point margin)
    out["blowout"] = (out["margin"].abs() >= 18).astype(int)

    # OT detection from status strings (backup to parsed flags)
    status_detail = out["status_detail"] if "status_detail" in out.columns else pd.Series("", index=out.index)
    status_desc = out["status_desc"] if "status_desc" in out.columns else pd.Series("", index=out.index)
    status_txt = (status_detail.astype(str) + " " + status_desc.astype(str)).str.upper()

    if "is_ot" not in out.columns:
        out["is_ot"] = status_txt.str.contains(r"\bOT\b|/OT|OT$", regex=True).astype(int)
    else:
        out["is_ot"] = pd.to_numeric(out["is_ot"], errors="coerce").fillna(0).astype(int)

    if "num_ot" not in out.columns:
        ot_num = status_txt.str.extract(r"/(\d+)OT", expand=False)
        out["num_ot"] = pd.to_numeric(ot_num, errors="coerce").fillna(0).astype(int)
        out.loc[(out["is_ot"] == 1) & (out["num_ot"] == 0), "num_ot"] = 1
    else:
        out["num_ot"] = pd.to_numeric(out["num_ot"], errors="coerce").fillna(0).astype(int)

    # Noise flags
    out["extreme_pace_flag"] = ((out["poss"].fillna(0) >= 85) | (out["poss"].fillna(999) <= 55)).astype(int)
    out["blowout_flag"] = out["blowout"].fillna(0).astype(int)
    out["noise_flag"] = ((out["is_ot"] == 1) | (out["extreme_pace_flag"] == 1)).astype(int)

    # Data quality flag
    out["data_ok"] = True
    out.loc[out["poss"].fillna(0) <= 40, "data_ok"] = False
    out.loc[(out.get("completed", False) == True) & (out["fga"].fillna(0) == 0), "data_ok"] = False
    out.loc[
        (out.get("completed", False) == True) & 
        (out["points_for"].fillna(0) == 0) & 
        (out["points_against"].fillna(0) == 0), 
        "data_ok"
    ] = False

    # Row hash for deduplication
    from data_utils import _stable_row_hash
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

    # Mark rating sources
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


# ---------------- Rolling Features ----------------

def _group_shift_rolling(s: pd.Series, window: int, fn: str) -> pd.Series:
    """
    Leak-free rolling calculation (shift then roll).
    
    Args:
        s: Series to compute rolling stat on
        window: Rolling window size
        fn: Function ('mean' or 'std')
        
    Returns:
        Series with rolling stat (current game excluded)
    """
    s2 = s.shift(1)  # Exclude current game
    if fn == "mean":
        return s2.rolling(window=window, min_periods=1).mean()
    if fn == "std":
        return s2.rolling(window=window, min_periods=2).std(ddof=0)
    raise ValueError(f"Unsupported fn: {fn}")


def _group_shift_expanding_mean(s: pd.Series) -> pd.Series:
    """
    Leak-free expanding mean (shift then expand).
    
    Args:
        s: Series to compute expanding mean on
        
    Returns:
        Series with season-to-date average (current game excluded)
    """
    return s.shift(1).expanding(min_periods=1).mean()


def _add_coverage_counts(df: pd.DataFrame, group_cols: tuple, prefix: str) -> pd.DataFrame:
    """
    Add games_played_pre count for sample size tracking.
    
    Args:
        df: DataFrame with team-game rows
        group_cols: Columns to group by (e.g., ("team_id",))
        prefix: Prefix for output column (e.g., "" or "ha_")
        
    Returns:
        DataFrame with {prefix}games_played_pre column
    """
    out = df.copy()
    g = out.groupby(list(group_cols), sort=False)
    
    proxy = "ortg" if "ortg" in out.columns else None
    if proxy is None:
        out[f"{prefix}games_played_pre"] = np.nan
        return out
    
    out[f"{prefix}games_played_pre"] = g[proxy].apply(
        lambda s: s.shift(1).expanding(min_periods=1).count()
    ).reset_index(level=list(group_cols), drop=True)
    
    return out


def _add_rolling_pack(df: pd.DataFrame, group_cols: tuple, prefix: str) -> pd.DataFrame:
    """
    Add rolling features (L3, L7, season) for core metrics.
    
    Features computed:
    - L3 (last 3 games average)
    - L7 (last 7 games average)
    - L7 std (volatility)
    - Season (expanding average)
    
    Metrics:
    - ortg, drtg, netrtg, pace
    - efg, tov_pct, orb_pct, drb_pct, ftr, 3par
    
    Args:
        df: DataFrame with team-game rows (must be sorted by team, date)
        group_cols: Columns to group by (e.g., ("team_id",) or ("team_id", "home_away"))
        prefix: Prefix for output columns (e.g., "" or "ha_")
        
    Returns:
        DataFrame with rolling features added
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

    g = out.groupby(list(group_cols), sort=False)

    for metric, col in core.items():
        if col not in out.columns:
            out[col] = np.nan
        else:
            out[col] = pd.to_numeric(out[col], errors="coerce")

        # L3, L7, L7 std, season
        out[f"{prefix}{metric}_l3_pre"] = g[col].apply(
            lambda s: _group_shift_rolling(s, 3, "mean")
        ).reset_index(level=list(group_cols), drop=True)
        
        out[f"{prefix}{metric}_l7_pre"] = g[col].apply(
            lambda s: _group_shift_rolling(s, 7, "mean")
        ).reset_index(level=list(group_cols), drop=True)
        
        out[f"{prefix}{metric}_std_l7_pre"] = g[col].apply(
            lambda s: _group_shift_rolling(s, 7, "std")
        ).reset_index(level=list(group_cols), drop=True)
        
        out[f"{prefix}{metric}_season_pre"] = g[col].apply(
            lambda s: _group_shift_expanding_mean(s)
        ).reset_index(level=list(group_cols), drop=True)

    out = _add_coverage_counts(out, group_cols=group_cols, prefix=prefix)
    return out


def _add_noblow_rollups(df: pd.DataFrame, group_cols: tuple, prefix: str) -> pd.DataFrame:
    """
    Add non-blowout filtered metrics (excludes games with 18+ point margin).
    
    Useful for more stable estimates when blowouts create noise.
    
    Args:
        df: DataFrame with team-game rows
        group_cols: Columns to group by
        prefix: Prefix for output columns
        
    Returns:
        DataFrame with noblow features added
    """
    out = df.copy()
    if "blowout" not in out.columns:
        out["blowout"] = 0

    games_col = f"{prefix}games_played_noblow_pre"
    if games_col not in out.columns:
        out[games_col] = np.nan

    for metric in ["ortg", "drtg", "netrtg"]:
        if metric not in out.columns:
            out[metric] = np.nan

        # Create temporary column with blowouts masked
        tmp_col = f"__{metric}_noblow"
        out[tmp_col] = out[metric].where(out["blowout"] == 0, np.nan)

        # Rolling mean (L7) excluding blowouts
        out[f"{prefix}{metric}_l7_noblow_pre"] = out.groupby(list(group_cols), sort=False)[tmp_col].apply(
            lambda s: s.shift(1).rolling(7, min_periods=1).mean()
        ).reset_index(level=list(group_cols), drop=True)

        # Count non-blowout games
        cnt = out.groupby(list(group_cols), sort=False)[tmp_col].apply(
            lambda s: s.shift(1).expanding(min_periods=1).count()
        ).reset_index(level=list(group_cols), drop=True)
        out[games_col] = out[games_col].fillna(cnt)

        out = out.drop(columns=[tmp_col], errors="ignore")

    return out


# ---------------- Defensive Metrics ----------------

def _add_allowed_forced_pack(df: pd.DataFrame, group_cols: tuple, prefix: str) -> pd.DataFrame:
    """
    Add defensive baselines derived from opponent game stats (leak-free).
    
    Requires per-row game-level columns from opponent merge:
    - efg_allowed_game (opponent's eFG% against us)
    - ftr_allowed_game (opponent's FTr against us)
    - orb_allowed_game (opponent's ORB% against us)
    - tov_forced_game (opponent's TOV% against us)
    - def_ppp_allowed_game (opponent's PPP against us)
    
    Args:
        df: DataFrame with team-game rows (after opponent merge)
        group_cols: Columns to group by
        prefix: Prefix for output columns
        
    Returns:
        DataFrame with defensive rolling features
    """
    out = df.copy()
    g = out.groupby(list(group_cols), sort=False)

    core = {
        "efg_allowed": "efg_allowed_game",
        "ftr_allowed": "ftr_allowed_game",
        "orb_allowed": "orb_allowed_game",
        "tov_forced": "tov_forced_game",
        "def_ppp_allowed": "def_ppp_allowed_game",
    }

    for metric, col in core.items():
        if col not in out.columns:
            out[col] = np.nan
        else:
            out[col] = pd.to_numeric(out[col], errors="coerce")

        # L3, L7, season
        out[f"{prefix}{metric}_l3_pre"] = g[col].apply(
            lambda s: _group_shift_rolling(s, 3, "mean")
        ).reset_index(level=list(group_cols), drop=True)
        
        out[f"{prefix}{metric}_l7_pre"] = g[col].apply(
            lambda s: _group_shift_rolling(s, 7, "mean")
        ).reset_index(level=list(group_cols), drop=True)
        
        out[f"{prefix}{metric}_season_pre"] = g[col].apply(
            lambda s: _group_shift_expanding_mean(s)
        ).reset_index(level=list(group_cols), drop=True)

    return out


# ---------------- Time-Based Features ----------------

def _time_window_counts_per_team(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate time-based features: rest days, back-to-backs, games in windows.
    
    Features:
    - days_since_last_game
    - days_rest (days_since_last_game - 1, clipped at 0)
    - back_to_back (1 if <= 1.5 days since last game)
    - games_last_N_days (for N in 3-12)
    - three_in_six (1 if 3+ games in last 6 days)
    
    Args:
        df: DataFrame with team-game rows (must have game_datetime_utc)
        
    Returns:
        DataFrame with time-based features
    """
    out = df.copy()
    out["game_dt"] = pd.to_datetime(out["game_datetime_utc"], utc=True, errors="coerce")

    key = "team_id" if "team_id" in out.columns else "team"
    out = out.sort_values([key, "game_dt", "event_id"])

    # Days since last game
    out["prev_game_dt"] = out.groupby(key)["game_dt"].shift(1)
    out["days_since_last_game"] = (out["game_dt"] - out["prev_game_dt"]).dt.total_seconds() / 86400.0
    out["days_rest"] = (out["days_since_last_game"] - 1.0).clip(lower=0)
    out["back_to_back"] = (out["days_since_last_game"].fillna(999) <= 1.5).astype(int)

    # Games in rolling time windows
    windows = list(range(3, 13))
    games_last_n = {n: [] for n in windows}
    games_last_7 = []
    three_in_six = []

    by_team = defaultdict(deque)
    for _, r in out.iterrows():
        k = r.get(key)
        dt = r.get("game_dt")
        dq = by_team[k]

        if pd.isna(dt):
            for n in windows:
                games_last_n[n].append(0)
            games_last_7.append(0)
            three_in_six.append(0)
            continue

        # Remove games older than max window
        cutoff_max = dt - pd.Timedelta(days=max(windows))
        while dq and dq[0] < cutoff_max:
            dq.popleft()

        # Count games in each window
        for n in windows:
            cutoff = dt - pd.Timedelta(days=n)
            games_last_n[n].append(sum(1 for x in dq if x >= cutoff))

        games_last_7.append(games_last_n[7][-1])

        # Three-in-six detection
        cutoff6 = dt - pd.Timedelta(days=6)
        cnt6 = sum(1 for x in dq if x >= cutoff6)
        three_in_six.append(1 if cnt6 >= 2 else 0)

        # Add current game to deque
        dq.append(dt)

    # Assign to dataframe
    for n in windows:
        out[f"games_last_{n}_days"] = games_last_n[n]
    out["games_last_7_days"] = games_last_7
    out["three_in_six"] = three_in_six
    
    return out.drop(columns=["prev_game_dt"], errors="ignore")
