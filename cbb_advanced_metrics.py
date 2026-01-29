#!/usr/bin/env python3
"""
cbb_advanced_metrics.py

Add-on metrics for ESPN CBB pipeline:
- matchup-level expected margin (dynamic per opponent)
- game performance score vs expectation (GPS)
- volatility/consistency measures (incl 3P variance + reliance)
- style/mismatch metrics (team vs opponent pregame profiles)
- generic leak-free last-N rolling means/stds (shifted)

Designed to plug into espn_boxscore_builder.py AFTER opponent merge
(PASS 5) and BEFORE writing espn_team_game_features.csv.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


EPS = 1e-9


def safe_div(num, den, default=np.nan):
    if den is None:
        return default
    try:
        if float(den) == 0.0:
            return default
        return float(num) / float(den)
    except Exception:
        return default


def ensure_numeric(df: pd.DataFrame, cols: Sequence[str]) -> pd.DataFrame:
    out = df.copy()
    for c in cols:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def require_cols(df: pd.DataFrame, cols: Sequence[str], ctx: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"{ctx}: missing required columns: {missing}")


# ----------------------------
# Expected (matchup-dynamic) engine
# ----------------------------

@dataclass(frozen=True)
class ExpectedConfig:
    # Which pregame columns to use (team side)
    ortg_pre: str = "ortg_l7_pre"
    drtg_pre: str = "drtg_l7_pre"
    pace_pre: str = "pace_l7_pre"

    # Opponent pregame columns (from opponent merge)
    opp_ortg_pre: str = "opp_ortg_l7_pre"
    opp_drtg_pre: str = "opp_drtg_l7_pre"
    opp_pace_pre: str = "opp_pace_l7_pre"

    # If l7 not present, fall back to season_pre
    ortg_pre_fallback: str = "ortg_season_pre"
    drtg_pre_fallback: str = "drtg_season_pre"
    pace_pre_fallback: str = "pace_season_pre"
    opp_ortg_pre_fallback: str = "opp_ortg_season_pre"
    opp_drtg_pre_fallback: str = "opp_drtg_season_pre"
    opp_pace_pre_fallback: str = "opp_pace_season_pre"

    # output column names
    out_exp_pace: str = "exp_pace"
    out_exp_ortg: str = "exp_ortg"
    out_exp_drtg: str = "exp_drtg"
    out_exp_netrtg: str = "exp_netrtg"
    out_exp_margin: str = "exp_margin"


def _pick_first_present(df: pd.DataFrame, primary: str, fallback: str) -> pd.Series:
    if primary in df.columns:
        s = pd.to_numeric(df[primary], errors="coerce")
        if s.notna().any():
            return s
    if fallback in df.columns:
        return pd.to_numeric(df[fallback], errors="coerce")
    return pd.Series(np.nan, index=df.index)


def add_expected_matchup_metrics(df: pd.DataFrame, cfg: ExpectedConfig = ExpectedConfig()) -> pd.DataFrame:
    """
    Adds exp_pace, exp_ortg, exp_drtg, exp_netrtg, exp_margin.
    Uses simple blending:
      exp_ortg = avg(team_ortg_pre, opp_drtg_pre)
      exp_drtg = avg(team_drtg_pre, opp_ortg_pre)
      exp_pace = avg(team_pace_pre, opp_pace_pre)
      exp_margin = (exp_pace / 100) * (exp_ortg - exp_drtg)

    All inputs must be pregame (already shifted in your pipeline).
    """
    out = df.copy()

    team_ortg = _pick_first_present(out, cfg.ortg_pre, cfg.ortg_pre_fallback)
    team_drtg = _pick_first_present(out, cfg.drtg_pre, cfg.drtg_pre_fallback)
    team_pace = _pick_first_present(out, cfg.pace_pre, cfg.pace_pre_fallback)

    opp_ortg = _pick_first_present(out, cfg.opp_ortg_pre, cfg.opp_ortg_pre_fallback)
    opp_drtg = _pick_first_present(out, cfg.opp_drtg_pre, cfg.opp_drtg_pre_fallback)
    opp_pace = _pick_first_present(out, cfg.opp_pace_pre, cfg.opp_pace_pre_fallback)

    out[cfg.out_exp_pace] = 0.5 * (team_pace + opp_pace)
    out[cfg.out_exp_ortg] = 0.5 * (team_ortg + opp_drtg)
    out[cfg.out_exp_drtg] = 0.5 * (team_drtg + opp_ortg)
    out[cfg.out_exp_netrtg] = out[cfg.out_exp_ortg] - out[cfg.out_exp_drtg]
    out[cfg.out_exp_margin] = (out[cfg.out_exp_pace] / 100.0) * out[cfg.out_exp_netrtg]

    return out


# ----------------------------
# Performance vs expectation (GPS + efficiency deltas)
# ----------------------------

def add_vs_expectation_scores(
    df: pd.DataFrame,
    exp_margin_col: str = "exp_margin",
    exp_ortg_col: str = "exp_ortg",
    exp_drtg_col: str = "exp_drtg",
    margin_col: str = "margin",
    ortg_col: str = "ortg",
    drtg_col: str = "drtg",
) -> pd.DataFrame:
    """
    Adds:
      gps = margin - exp_margin
      off_delta = ortg - exp_ortg
      def_delta = exp_drtg - drtg  (positive = better defense than expected)
      net_over_exp = (ortg - drtg) - (exp_ortg - exp_drtg)
    """
    out = df.copy()
    need = [exp_margin_col, exp_ortg_col, exp_drtg_col, margin_col, ortg_col, drtg_col]
    for c in need:
        if c not in out.columns:
            out[c] = np.nan

    out = ensure_numeric(out, need)

    out["gps"] = out[margin_col] - out[exp_margin_col]
    out["off_delta"] = out[ortg_col] - out[exp_ortg_col]
    out["def_delta"] = out[exp_drtg_col] - out[drtg_col]
    out["net_over_exp"] = (out[ortg_col] - out[drtg_col]) - (out[exp_ortg_col] - out[exp_drtg_col])

    return out


# ----------------------------
# Style / mismatch features (pregame)
# ----------------------------

def add_style_mismatch(
    df: pd.DataFrame,
    team_cols: Sequence[str] = ("pace_l7_pre", "3par_l7_pre", "ftr_l7_pre", "orb_pct_l7_pre", "tov_pct_l7_pre", "efg_l7_pre"),
    opp_cols: Sequence[str] = ("opp_pace_l7_pre", "opp_3par_l7_pre", "opp_ftr_l7_pre", "opp_orb_pct_l7_pre", "opp_tov_pct_l7_pre", "opp_efg_l7_pre"),
    out_distance_col: str = "style_distance_l7",
    out_pace_mismatch_col: str = "pace_mismatch_l7",
) -> pd.DataFrame:
    """
    Adds:
      - style_distance_l7: Euclidean distance between team + opp style vectors
      - pace_mismatch_l7: abs(pace - opp_pace)
    Uses l7_pre by default (pregame leak-free).
    """
    out = df.copy()
    for c in list(team_cols) + list(opp_cols):
        if c not in out.columns:
            out[c] = np.nan

    out = ensure_numeric(out, list(team_cols) + list(opp_cols))

    tv = out[list(team_cols)].to_numpy(dtype=float)
    ov = out[list(opp_cols)].to_numpy(dtype=float)

    # nan-safe distance: treat NaNs as 0 contribution (but this can understate distance)
    diff = np.nan_to_num(tv - ov, nan=0.0)
    out[out_distance_col] = np.sqrt((diff ** 2).sum(axis=1))

    if "pace_l7_pre" in out.columns and "opp_pace_l7_pre" in out.columns:
        out[out_pace_mismatch_col] = (out["pace_l7_pre"] - out["opp_pace_l7_pre"]).abs()
    else:
        out[out_pace_mismatch_col] = np.nan

    return out


# ----------------------------
# Shooting profile + volatility helpers
# ----------------------------

def add_shooting_profile(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds game-level:
      - tp_pct, two_pct, ft_pct
    Requires: fgm,fga,tpm,tpa,ftm,fta if present.
    """
    out = df.copy()
    for c in ["fgm", "fga", "tpm", "tpa", "ftm", "fta"]:
        if c not in out.columns:
            out[c] = np.nan

    out = ensure_numeric(out, ["fgm", "fga", "tpm", "tpa", "ftm", "fta"])

    out["tp_pct"] = out.apply(lambda r: safe_div(r["tpm"], r["tpa"]), axis=1)
    out["two_m"] = out["fgm"] - out["tpm"]
    out["two_a"] = out["fga"] - out["tpa"]
    out["two_pct"] = out.apply(lambda r: safe_div(r["two_m"], r["two_a"]), axis=1)
    out["ft_pct"] = out.apply(lambda r: safe_div(r["ftm"], r["fta"]), axis=1)

    return out.drop(columns=["two_m", "two_a"], errors="ignore")


# ----------------------------
# Generic leak-free last-N rolling (shifted)
# ----------------------------

def add_lastn_rollups(
    df: pd.DataFrame,
    group_cols: Sequence[str] = ("team_id",),
    order_col: str = "game_datetime_utc",
    n: int = 10,
    metrics: Sequence[str] = (
        "netrtg", "ortg", "drtg", "pace", "efg", "tov_pct", "orb_pct", "drb_pct", "ftr", "3par",
        "tp_pct", "gps", "net_over_exp",
    ),
    prefix: str = "l10_",
) -> pd.DataFrame:
    """
    For each metric x:
      - {prefix}{x}_pre = mean( x shifted 1, rolling n )
      - {prefix}{x}_std_pre = std( x shifted 1, rolling n )

    This is leak-free if df is sorted by time within group.
    """
    out = df.copy()

    if order_col in out.columns:
        out["_ord"] = pd.to_datetime(out[order_col], utc=True, errors="coerce")
    else:
        out["_ord"] = np.arange(len(out), dtype=float)

    # ensure existence
    for m in metrics:
        if m not in out.columns:
            out[m] = np.nan
    out = ensure_numeric(out, metrics)

    out = out.sort_values(list(group_cols) + ["_ord"])

    g = out.groupby(list(group_cols), sort=False)

    for m in metrics:
        s = g[m]
        out[f"{prefix}{m}_pre"] = s.apply(lambda x: x.shift(1).rolling(n, min_periods=1).mean()).reset_index(level=list(group_cols), drop=True)
        out[f"{prefix}{m}_std_pre"] = s.apply(lambda x: x.shift(1).rolling(n, min_periods=2).std(ddof=0)).reset_index(level=list(group_cols), drop=True)

    out = out.drop(columns=["_ord"], errors="ignore")
    return out


def add_volatility_composites(df: pd.DataFrame, prefix: str = "l10_") -> pd.DataFrame:
    """
    Assumes last-N rollups exist.
    Adds:
      - shoot_vol_{prefix}: 3par_pre * tp_pct_std_pre
      - three_vol_risk_{prefix}: 3par_pre * (tp_pct_std_pre + 0.5*efg_std_pre)
      - consistency_{prefix}: 1/(1 + netrtg_std_pre)
    """
    out = df.copy()

    need = [
        f"{prefix}3par_pre",
        f"{prefix}tp_pct_std_pre",
        f"{prefix}efg_std_pre",
        f"{prefix}netrtg_std_pre",
    ]
    for c in need:
        if c not in out.columns:
            out[c] = np.nan
    out = ensure_numeric(out, need)

    out[f"shoot_vol_{prefix}"] = out[f"{prefix}3par_pre"] * out[f"{prefix}tp_pct_std_pre"]
    out[f"three_vol_risk_{prefix}"] = out[f"{prefix}3par_pre"] * (out[f"{prefix}tp_pct_std_pre"] + 0.5 * out[f"{prefix}efg_std_pre"])
    out[f"consistency_{prefix}"] = 1.0 / (1.0 + out[f"{prefix}netrtg_std_pre"].fillna(np.nan))

    return out


# ----------------------------
# Optional player-level features (if you have player game logs)
# ----------------------------

def build_player_concentration_features(
    df_players: pd.DataFrame,
    team_keys: Sequence[str] = ("event_id", "team_id"),
    usage_col: str = "usage_proxy",
    minutes_col: str = "minutes",
    top_k: int = 3,
) -> pd.DataFrame:
    """
    Input df_players should have at least: event_id, team_id, player, usage_proxy, minutes
    Output per (event_id, team_id):
      - usage_hhi
      - top{K}_usage_share
      - minutes_hhi
      - top{K}_minutes_share
    """
    require_cols(df_players, list(team_keys) + [usage_col, minutes_col], "build_player_concentration_features")

    p = df_players.copy()
    p[usage_col] = pd.to_numeric(p[usage_col], errors="coerce").fillna(0.0)
    p[minutes_col] = pd.to_numeric(p[minutes_col], errors="coerce").fillna(0.0)

    def _agg(g: pd.DataFrame) -> pd.Series:
        u = g[usage_col].to_numpy(dtype=float)
        m = g[minutes_col].to_numpy(dtype=float)

        u_sum = u.sum()
        m_sum = m.sum()

        u_shares = (u / u_sum) if u_sum > 0 else np.zeros_like(u)
        m_shares = (m / m_sum) if m_sum > 0 else np.zeros_like(m)

        usage_hhi = float((u_shares ** 2).sum())
        minutes_hhi = float((m_shares ** 2).sum())

        topu = np.sort(u_shares)[::-1][:top_k].sum() if u_sum > 0 else 0.0
        topm = np.sort(m_shares)[::-1][:top_k].sum() if m_sum > 0 else 0.0

        return pd.Series(
            {
                "usage_hhi": usage_hhi,
                f"top{top_k}_usage_share": float(topu),
                "minutes_hhi": minutes_hhi,
                f"top{top_k}_minutes_share": float(topm),
            }
        )

    out = p.groupby(list(team_keys), sort=False).apply(_agg).reset_index()
    return out


# ----------------------------
# One-call convenience wrapper
# ----------------------------

def add_all_advanced_metrics(df: pd.DataFrame, n_last: int = 10) -> pd.DataFrame:
    """
    Recommended call order:
      1) add_shooting_profile (tp_pct, etc)
      2) add_expected_matchup_metrics
      3) add_vs_expectation_scores
      4) add_style_mismatch
      5) add_lastn_rollups (includes gps)
      6) add_volatility_composites
    """
    out = df.copy()
    out = add_shooting_profile(out)
    out = add_expected_matchup_metrics(out)
    out = add_vs_expectation_scores(out)
    out = add_style_mismatch(out)
    out = add_lastn_rollups(out, n=n_last, prefix=f"l{n_last}_")
    out = add_volatility_composites(out, prefix=f"l{n_last}_")
    return out
