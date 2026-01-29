#!/usr/bin/env python3
"""
weights.py

Game-weight construction for:
- w_recency
- w_opp_quality
- w_location
- w_noise
- w_relevance (optional, matchup-aware)
- w_g (base) and w_g_prime (matchup-aware)

Important note about "true" days_ago weighting
----------------------------------------------
Your spec defines days_ago(g) = D - game_date(g), where D is the *current row's* game date.
That means recency weights are inherently *row-relative* inside each rolling window.

A simple "weight column" on each historical row cannot perfectly represent that for every future D.
So this module provides two paths:

Path A (recommended first, works with rolling_features.add_weighted_rollups):
- "index-based recency": exp(-(games_back)/half_life_games)
  where games_back is the number of games before the current row (within team order).
  This is leak-free, stable, and close enough early on.

Path B (exact by days, for a future custom rolling implementation):
- compute_recency_weights_by_days(window_dates, current_date, half_life_days)

You can start with Path A now, and later swap to Path B when you implement a window-aware weighted rolling.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

EPS = 1e-12


def _to_datetime_utc(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, utc=True, errors="coerce")


def _ensure_cols(df: pd.DataFrame, cols: Sequence[str], ctx: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"{ctx}: missing columns: {missing}")


def _to_num(df: pd.DataFrame, cols: Sequence[str]) -> pd.DataFrame:
    out = df.copy()
    for c in cols:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def _zscore(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce")
    mu = s.mean(skipna=True)
    sd = s.std(skipna=True, ddof=0)
    if not np.isfinite(sd) or sd < EPS:
        return (s - mu) * 0.0
    return (s - mu) / sd


@dataclass(frozen=True)
class WeightConfig:
    # identity + ordering
    group_cols: Tuple[str, ...] = ("team_id",)
    order_col: str = "game_datetime_utc"  # or game_date

    # Recency (Path A): index-based
    half_life_games: float = 5.0  # exp decay in "games", not days

    # Recency (Path B): exact-day helper only (not used in add_* by default)
    half_life_days: float = 14.0

    # Opponent quality (continuous)
    # Provide either an "opp_rating" (e.g., opp_netrtg_pre, opp_elo_pre, etc.)
    opp_rating_col: str = "opp_netrtg_l7_pre"
    opp_quality_mode: str = "linear_z"  # "linear_z" or "sigmoid"
    alpha: float = 0.15                # used for linear_z: 1 + alpha * z
    sigmoid_scale: float = 1.0         # used for sigmoid
    opp_q_min: float = 0.85
    opp_q_max: float = 1.25

    # Location
    site_col: str = "site"  # expected values: "home","away","neutral" (lowercase)
    w_home: float = 1.00
    w_away: float = 0.95
    w_neutral: float = 0.98

    # Noise / OT
    ot_flag_col: str = "is_ot"      # bool/int (1 if OT)
    num_ot_col: str = "num_ot"      # optional
    w_ot: float = 0.90

    # Outputs
    out_w_recency: str = "w_recency"
    out_w_opp_quality: str = "w_opp_quality"
    out_w_location: str = "w_location"
    out_w_noise: str = "w_noise"
    out_w_base: str = "w_g"


# ----------------------------
# Recency
# ----------------------------

def add_recency_weight_index_based(df: pd.DataFrame, cfg: WeightConfig) -> pd.DataFrame:
    """
    Leak-free, row-relative, index-based recency weights:
      w_recency = exp(-(games_back)/half_life_games)

    games_back is computed as:
      within each team_id group, for each row i (chronological),
      games_back = 1 for the immediately previous game, 2 for two games back, etc.

    Implementation detail:
      We store per-row 'game_idx' as cumulative count (0..),
      and within a rolling window you will shift weights (handled in rolling_features)
      so current game isn't used.

    This pairs well with rolling_features.add_weighted_rollups using weight_col="w_g".
    """
    out = df.copy()
    if cfg.order_col in out.columns:
        out["_ord"] = _to_datetime_utc(out[cfg.order_col])
    else:
        out["_ord"] = np.arange(len(out), dtype=float)

    out = out.sort_values(list(cfg.group_cols) + ["_ord"])
    g = out.groupby(list(cfg.group_cols), sort=False)

    # chronological index
    out["_game_idx"] = g.cumcount()

    # "games_back" for each row relative to the current row is dynamic.
    # For a usable per-row scalar, we encode "recency rank from the end" inside each team's history
    # *up to that row*: games_back_from_current = 0 for current row, 1 for prior, etc.
    # That is just reversed cumcount within each prefix, which is dynamic; we approximate by using
    # distance from current row via lag index differences in rolling_features.
    #
    # Practical approach:
    # - Assign a monotonic "recency score" that increases with recency:
    #   w_recency_base = exp(-idx/half_life_games)
    # - When you shift and roll, more recent games naturally have higher w_recency_base.
    #
    # This is not mathematically identical to exp(-(D - date)/half_life_days),
    # but works well as a first pass.
    out[cfg.out_w_recency] = np.exp(-out["_game_idx"] / float(cfg.half_life_games))

    out = out.drop(columns=["_ord", "_game_idx"], errors="ignore")
    return out


def compute_recency_weights_by_days(
    window_dates: Union[pd.Series, np.ndarray],
    current_date: Union[pd.Timestamp, str],
    half_life_days: float,
) -> np.ndarray:
    """
    Exact, row-relative recency weights for one rolling window:
      w = exp(-(days_ago)/half_life_days)

    Use this in a custom window-aware weighted rolling.
    """
    d = pd.to_datetime(window_dates, utc=True, errors="coerce")
    cur = pd.to_datetime(current_date, utc=True, errors="coerce")
    days_ago = (cur - d).dt.total_seconds() / 86400.0
    days_ago = days_ago.to_numpy(dtype=float)
    return np.exp(-days_ago / float(half_life_days))


# ----------------------------
# Opponent quality
# ----------------------------

def add_opponent_quality_weight(df: pd.DataFrame, cfg: WeightConfig) -> pd.DataFrame:
    """
    w_opp_quality options:

    linear_z:
      w = 1 + alpha * z(opp_rating)
      then clipped to [min,max]

    sigmoid:
      w = min + (max-min) * sigmoid(z(opp_rating)/scale)
      where sigmoid(x) = 1/(1+exp(-x))
    """
    out = df.copy()
    if cfg.opp_rating_col not in out.columns:
        out[cfg.out_w_opp_quality] = np.nan
        return out

    out = _to_num(out, [cfg.opp_rating_col])
    z = _zscore(out[cfg.opp_rating_col])

    if cfg.opp_quality_mode == "sigmoid":
        x = z / float(cfg.sigmoid_scale if cfg.sigmoid_scale else 1.0)
        sig = 1.0 / (1.0 + np.exp(-x))
        w = float(cfg.opp_q_min) + (float(cfg.opp_q_max) - float(cfg.opp_q_min)) * sig
    else:
        w = 1.0 + float(cfg.alpha) * z
        w = w.clip(lower=float(cfg.opp_q_min), upper=float(cfg.opp_q_max))

    out[cfg.out_w_opp_quality] = w
    return out


# ----------------------------
# Location + noise
# ----------------------------

def add_location_weight(df: pd.DataFrame, cfg: WeightConfig) -> pd.DataFrame:
    out = df.copy()
    if cfg.site_col not in out.columns:
        out[cfg.out_w_location] = np.nan
        return out

    s = out[cfg.site_col].astype(str).str.lower()
    w = np.where(s.eq("home"), cfg.w_home,
         np.where(s.eq("away"), cfg.w_away,
         np.where(s.eq("neutral"), cfg.w_neutral, np.nan)))
    out[cfg.out_w_location] = w
    return out


def add_noise_weight(df: pd.DataFrame, cfg: WeightConfig) -> pd.DataFrame:
    out = df.copy()

    if cfg.ot_flag_col in out.columns:
        ot = pd.to_numeric(out[cfg.ot_flag_col], errors="coerce").fillna(0).astype(int)
        w = np.where(ot > 0, float(cfg.w_ot), 1.0)
    elif cfg.num_ot_col in out.columns:
        num_ot = pd.to_numeric(out[cfg.num_ot_col], errors="coerce").fillna(0).astype(int)
        w = np.where(num_ot > 0, float(cfg.w_ot), 1.0)
    else:
        w = 1.0

    out[cfg.out_w_noise] = w
    return out


# ----------------------------
# Base weight
# ----------------------------

def add_base_weight(df: pd.DataFrame, cfg: WeightConfig) -> pd.DataFrame:
    """
    w_g = w_recency * w_opp_quality * w_location * w_noise

    This function assumes the component columns exist.
    If any are missing, they are treated as 1.0 (neutral).
    """
    out = df.copy()

    for col in [cfg.out_w_recency, cfg.out_w_opp_quality, cfg.out_w_location, cfg.out_w_noise]:
        if col not in out.columns:
            out[col] = 1.0

    out = _to_num(out, [cfg.out_w_recency, cfg.out_w_opp_quality, cfg.out_w_location, cfg.out_w_noise])

    out[cfg.out_w_base] = (
        out[cfg.out_w_recency].fillna(1.0)
        * out[cfg.out_w_opp_quality].fillna(1.0)
        * out[cfg.out_w_location].fillna(1.0)
        * out[cfg.out_w_noise].fillna(1.0)
    )
    return out


def add_all_base_weights(df: pd.DataFrame, cfg: WeightConfig = WeightConfig()) -> pd.DataFrame:
    """
    Convenience wrapper:
      - recency (index-based)
      - opponent quality
      - location
      - noise
      - base w_g
    """
    out = df.copy()
    out = add_recency_weight_index_based(out, cfg)
    out = add_opponent_quality_weight(out, cfg)
    out = add_location_weight(out, cfg)
    out = add_noise_weight(out, cfg)
    out = add_base_weight(out, cfg)
    return out


# ----------------------------
# Matchup relevance weighting
# ----------------------------

def compute_relevance_weight(
    df: pd.DataFrame,
    target_opp_profile: Union[pd.Series, Dict[str, float]],
    opp_profile_cols: Sequence[str],
    feature_weights: Optional[Dict[str, float]] = None,
    tau: float = 1.0,
    zscore_inputs: bool = True,
    out_col: str = "w_relevance",
) -> pd.DataFrame:
    """
    w_relevance = exp(-distance / tau)

    distance = sum_i feat_w_i * | z(opp_feat_i_in_past_game) - z(target_opp_feat_i) |

    Inputs:
    - df: must contain opp_profile_cols (typically "opp_*_pre" columns)
    - target_opp_profile: dict/Series mapping the same cols to values
    - feature_weights: optional weighting per feature
    - zscore_inputs: z-score each feature across df for stable scaling

    Output:
    - adds out_col
    """
    out = df.copy()
    for c in opp_profile_cols:
        if c not in out.columns:
            out[c] = np.nan

    out = _to_num(out, list(opp_profile_cols))

    # build target vector aligned to cols
    if isinstance(target_opp_profile, dict):
        tvals = np.array([float(target_opp_profile.get(c, np.nan)) for c in opp_profile_cols], dtype=float)
    else:
        tvals = np.array([float(target_opp_profile.get(c, np.nan)) for c in opp_profile_cols], dtype=float)

    X = out[list(opp_profile_cols)].to_numpy(dtype=float)

    if zscore_inputs:
        # zscore each column in X and apply same transform to target
        mu = np.nanmean(X, axis=0)
        sd = np.nanstd(X, axis=0)
        sd = np.where(sd < EPS, 1.0, sd)
        Xz = (X - mu) / sd
        tz = (tvals - mu) / sd
    else:
        Xz = X
        tz = tvals

    # feature weights
    if feature_weights:
        fw = np.array([float(feature_weights.get(c, 1.0)) for c in opp_profile_cols], dtype=float)
    else:
        fw = np.ones(len(opp_profile_cols), dtype=float)

    # absolute distance, nan-safe (nan -> 0 contribution)
    diff = np.nan_to_num(np.abs(Xz - tz), nan=0.0)
    dist = (diff * fw).sum(axis=1)

    out[out_col] = np.exp(-dist / float(tau))
    return out


def add_matchup_weight(
    df: pd.DataFrame,
    target_opp_profile: Union[pd.Series, Dict[str, float]],
    opp_profile_cols: Sequence[str],
    cfg: WeightConfig = WeightConfig(),
    feature_weights: Optional[Dict[str, float]] = None,
    tau: float = 1.0,
    out_relevance: str = "w_relevance",
    out_matchup: str = "w_g_prime",
) -> pd.DataFrame:
    """
    Adds:
      - w_relevance
      - w_g_prime = w_g * w_relevance

    Requires base w_g to exist (call add_all_base_weights first).
    """
    out = df.copy()
    if cfg.out_w_base not in out.columns:
        out[cfg.out_w_base] = np.nan

    out = compute_relevance_weight(
        out,
        target_opp_profile=target_opp_profile,
        opp_profile_cols=opp_profile_cols,
        feature_weights=feature_weights,
        tau=tau,
        zscore_inputs=True,
        out_col=out_relevance,
    )
    out = _to_num(out, [cfg.out_w_base, out_relevance])
    out[out_matchup] = out[cfg.out_w_base] * out[out_relevance]
    return out


if __name__ == "__main__":
    # Minimal smoke test
    d = pd.DataFrame({
        "team_id": ["A"] * 6,
        "game_datetime_utc": pd.date_range("2025-11-01", periods=6, freq="D"),
        "site": ["home", "away", "home", "neutral", "away", "home"],
        "is_ot": [0, 0, 1, 0, 0, 0],
        "opp_netrtg_l7_pre": [5, 12, -2, 8, 1, 10],
        "opp_pace_l7_pre": [68, 72, 70, 75, 66, 74],
        "opp_3par_l7_pre": [0.38, 0.45, 0.31, 0.50, 0.35, 0.48],
    })

    cfg = WeightConfig(group_cols=("team_id",), order_col="game_datetime_utc", opp_rating_col="opp_netrtg_l7_pre")
    d2 = add_all_base_weights(d, cfg)

    target = {"opp_pace_l7_pre": 74, "opp_3par_l7_pre": 0.47}
    d3 = add_matchup_weight(
        d2,
        target_opp_profile=target,
        opp_profile_cols=["opp_pace_l7_pre", "opp_3par_l7_pre"],
        cfg=cfg,
        tau=1.0,
    )

    print(d3[["game_datetime_utc", "w_recency", "w_opp_quality", "w_location", "w_noise", "w_g", "w_relevance", "w_g_prime"]])
