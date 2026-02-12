#!/usr/bin/env python3
"""
cbb_advanced_metrics.py

Add-on metrics for ESPN CBB pipeline:
- matchup-level expected margin (dynamic per opponent)
- game performance score vs expectation (GPS)
- volatility/consistency measures (incl 3P variance + reliance)
- style/mismatch metrics (team vs opponent pregame profiles)
- generic leak-free last-N rolling means/stds (shifted)
- optional matchup "edge" features when opponent-allowed pregame fields exist

Designed to plug into espn_boxscore_builder.py AFTER opponent merge
(PASS 5) and BEFORE writing espn_team_game_features.csv.

Flexibility + safety goals:
- Never break dependencies: existing function names kept, defaults conservative
- "Write if missing" behavior by default (no overwrites unless overwrite=True)
- Column presence tolerant: creates missing inputs as NaN; computes best-effort
- Faster rolling via groupby().transform + rolling, avoids slow groupby-apply
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

EPS = 1e-9


# ----------------------------
# Utilities
# ----------------------------

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


def _set_col(out: pd.DataFrame, col: str, values: pd.Series | np.ndarray, overwrite: bool) -> None:
    if (not overwrite) and (col in out.columns):
        return
    out[col] = values


def _pick_first_present(df: pd.DataFrame, primary: str, fallback: str) -> pd.Series:
    if primary in df.columns:
        s = pd.to_numeric(df[primary], errors="coerce")
        if s.notna().any():
            return s
    if fallback in df.columns:
        return pd.to_numeric(df[fallback], errors="coerce")
    return pd.Series(np.nan, index=df.index)


def _ensure_cols(df: pd.DataFrame, cols: Sequence[str]) -> pd.DataFrame:
    out = df.copy()
    for c in cols:
        if c not in out.columns:
            out[c] = np.nan
    return out


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


def add_expected_matchup_metrics(
    df: pd.DataFrame,
    cfg: ExpectedConfig = ExpectedConfig(),
    *,
    overwrite: bool = False,
) -> pd.DataFrame:
    """
    Adds exp_pace, exp_ortg, exp_drtg, exp_netrtg, exp_margin.

    Uses simple blending:
      exp_ortg = avg(team_ortg_pre, opp_drtg_pre)
      exp_drtg = avg(team_drtg_pre, opp_ortg_pre)
      exp_pace = avg(team_pace_pre, opp_pace_pre)
      exp_margin = (exp_pace / 100) * (exp_ortg - exp_drtg)

    Safe by default: will NOT overwrite existing columns unless overwrite=True.
    """
    out = df.copy()

    team_ortg = _pick_first_present(out, cfg.ortg_pre, cfg.ortg_pre_fallback)
    team_drtg = _pick_first_present(out, cfg.drtg_pre, cfg.drtg_pre_fallback)
    team_pace = _pick_first_present(out, cfg.pace_pre, cfg.pace_pre_fallback)

    opp_ortg = _pick_first_present(out, cfg.opp_ortg_pre, cfg.opp_ortg_pre_fallback)
    opp_drtg = _pick_first_present(out, cfg.opp_drtg_pre, cfg.opp_drtg_pre_fallback)
    opp_pace = _pick_first_present(out, cfg.opp_pace_pre, cfg.opp_pace_pre_fallback)

    exp_pace = 0.5 * (team_pace + opp_pace)
    exp_ortg = 0.5 * (team_ortg + opp_drtg)
    exp_drtg = 0.5 * (team_drtg + opp_ortg)
    exp_netrtg = exp_ortg - exp_drtg
    exp_margin = (exp_pace / 100.0) * exp_netrtg

    _set_col(out, cfg.out_exp_pace, exp_pace, overwrite)
    _set_col(out, cfg.out_exp_ortg, exp_ortg, overwrite)
    _set_col(out, cfg.out_exp_drtg, exp_drtg, overwrite)
    _set_col(out, cfg.out_exp_netrtg, exp_netrtg, overwrite)
    _set_col(out, cfg.out_exp_margin, exp_margin, overwrite)

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
    *,
    overwrite: bool = False,
) -> pd.DataFrame:
    """
    Adds:
      gps = margin - exp_margin
      off_delta = ortg - exp_ortg
      def_delta = exp_drtg - drtg  (positive = better defense than expected)
      net_over_exp = (ortg - drtg) - (exp_ortg - exp_drtg)

    Safe by default: will NOT overwrite existing output columns unless overwrite=True.
    """
    out = df.copy()
    need = [exp_margin_col, exp_ortg_col, exp_drtg_col, margin_col, ortg_col, drtg_col]
    out = _ensure_cols(out, need)
    out = ensure_numeric(out, need)

    gps = out[margin_col] - out[exp_margin_col]
    off_delta = out[ortg_col] - out[exp_ortg_col]
    def_delta = out[exp_drtg_col] - out[drtg_col]
    net_over_exp = (out[ortg_col] - out[drtg_col]) - (out[exp_ortg_col] - out[exp_drtg_col])

    _set_col(out, "gps", gps, overwrite)
    _set_col(out, "off_delta", off_delta, overwrite)
    _set_col(out, "def_delta", def_delta, overwrite)
    _set_col(out, "net_over_exp", net_over_exp, overwrite)
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
    *,
    overwrite: bool = False,
) -> pd.DataFrame:
    """
    Adds:
      - style_distance_l7: Euclidean distance between team + opp style vectors
      - pace_mismatch_l7: abs(pace - opp_pace)
    Uses l7_pre by default (pregame leak-free).

    Safe by default: will NOT overwrite existing output columns unless overwrite=True.
    """
    out = df.copy()
    out = _ensure_cols(out, list(team_cols) + list(opp_cols))
    out = ensure_numeric(out, list(team_cols) + list(opp_cols))

    tv = out[list(team_cols)].to_numpy(dtype=float)
    ov = out[list(opp_cols)].to_numpy(dtype=float)

    diff = np.nan_to_num(tv - ov, nan=0.0)
    style_distance = np.sqrt((diff ** 2).sum(axis=1))

    if "pace_l7_pre" in out.columns and "opp_pace_l7_pre" in out.columns:
        pace_mismatch = (pd.to_numeric(out["pace_l7_pre"], errors="coerce") - pd.to_numeric(out["opp_pace_l7_pre"], errors="coerce")).abs()
    else:
        pace_mismatch = pd.Series(np.nan, index=out.index)

    _set_col(out, out_distance_col, style_distance, overwrite)
    _set_col(out, out_pace_mismatch_col, pace_mismatch, overwrite)
    return out


# ----------------------------
# Shooting profile helpers
# ----------------------------

def add_shooting_profile(df: pd.DataFrame, *, overwrite: bool = False) -> pd.DataFrame:
    """
    Adds game-level:
      - 3p_pct, ft_pct
    Requires: fgm,fga,tpm,tpa,ftm,fta if present.

    Safe by default: will NOT overwrite existing output columns unless overwrite=True.
    """
    out = df.copy()
    out = _ensure_cols(out, ["fgm", "fga", "tpm", "tpa", "ftm", "fta"])
    out = ensure_numeric(out, ["fgm", "fga", "tpm", "tpa", "ftm", "fta"])

    three_p_pct = out.apply(lambda r: safe_div(r["tpm"], r["tpa"]), axis=1)
    ft_pct = out.apply(lambda r: safe_div(r["ftm"], r["fta"]), axis=1)

    _set_col(out, "3p_pct", three_p_pct, overwrite)
    _set_col(out, "ft_pct", ft_pct, overwrite)
    return out


# ----------------------------
# Leak-free last-N rolling (shifted)
# ----------------------------

def add_lastn_rollups(
    df: pd.DataFrame,
    group_cols: Sequence[str] = ("team_id",),
    order_col: str = "game_datetime_utc",
    n: int = 10,
    metrics: Sequence[str] = (
        "netrtg", "ortg", "drtg", "pace", "efg", "tov_pct", "orb_pct", "drb_pct", "ftr", "3par",
        "3p_pct", "gps", "net_over_exp",
    ),
    prefix: str = "l10_",
    *,
    overwrite: bool = False,
) -> pd.DataFrame:
    """
    For each metric x:
      - {prefix}{x}_pre = mean( x shifted 1, rolling n )
      - {prefix}{x}_std_pre = std( x shifted 1, rolling n )

    Safe by default: will NOT overwrite existing output columns unless overwrite=True.
    """
    out = df.copy()

    # ordering key
    if order_col in out.columns:
        out["_ord"] = pd.to_datetime(out[order_col], utc=True, errors="coerce")
    else:
        out["_ord"] = np.arange(len(out), dtype=float)

    # ensure inputs exist and are numeric
    out = _ensure_cols(out, list(metrics))
    out = ensure_numeric(out, list(metrics))

    # stable sort for rolling
    sort_cols = list(group_cols) + ["_ord"]
    for gc in group_cols:
        if gc not in out.columns:
            out[gc] = np.nan
    out = out.sort_values(sort_cols, kind="mergesort")

    g = out.groupby(list(group_cols), sort=False)

    for m in metrics:
        mean_col = f"{prefix}{m}_pre"
        std_col = f"{prefix}{m}_std_pre"

        if overwrite or (mean_col not in out.columns):
            out[mean_col] = g[m].transform(lambda s: s.shift(1).rolling(n, min_periods=1).mean())

        if overwrite or (std_col not in out.columns):
            out[std_col] = g[m].transform(lambda s: s.shift(1).rolling(n, min_periods=2).std(ddof=0))

    out = out.drop(columns=["_ord"], errors="ignore")
    return out


def add_volatility_composites(df: pd.DataFrame, prefix: str = "l10_", *, overwrite: bool = False) -> pd.DataFrame:
    """
    Assumes last-N rollups exist.

    Adds (names kept stable for downstream):
      - shoot_vol_{prefix}: 3par_pre * 3p_pct_std_pre
      - three_vol_risk_{prefix}: 3par_pre * (3p_pct_std_pre + 0.5*efg_std_pre)
      - consistency_{prefix}: 1/(1 + netrtg_std_pre)

    Flex:
      - supports legacy tp_pct naming if present (tp_pct_std_pre), but prefers 3p_pct_std_pre
    """
    out = df.copy()

    # Prefer 3p_pct; allow tp_pct legacy alias
    col_3p_std = f"{prefix}3p_pct_std_pre"
    col_tp_std = f"{prefix}tp_pct_std_pre"  # legacy
    use_3p_std = col_3p_std if col_3p_std in out.columns else col_tp_std

    need = [
        f"{prefix}3par_pre",
        use_3p_std,
        f"{prefix}efg_std_pre",
        f"{prefix}netrtg_std_pre",
    ]
    out = _ensure_cols(out, need)
    out = ensure_numeric(out, need)

    shoot_vol = out[f"{prefix}3par_pre"] * out[use_3p_std]
    three_vol_risk = out[f"{prefix}3par_pre"] * (out[use_3p_std] + 0.5 * out[f"{prefix}efg_std_pre"])
    consistency = 1.0 / (1.0 + out[f"{prefix}netrtg_std_pre"])

    _set_col(out, f"shoot_vol_{prefix}", shoot_vol, overwrite)
    _set_col(out, f"three_vol_risk_{prefix}", three_vol_risk, overwrite)
    _set_col(out, f"consistency_{prefix}", consistency, overwrite)
    return out


# ----------------------------
# Matchup "edge" features (only if opponent allowed/forced fields exist)
# ----------------------------

def add_matchup_edges(
    df: pd.DataFrame,
    *,
    overwrite: bool = False,
) -> pd.DataFrame:
    """
    Adds leak-free matchup edges when these pregame fields exist:
      - efg_edge_pre = efg_l7_pre - opp_efg_allowed_pre
      - ftr_edge_pre = ftr_l7_pre - opp_ftr_allowed_pre
      - orb_edge_pre = orb_pct_l7_pre - opp_orb_allowed_pre
      - tov_edge_pre = opp_tov_forced_pre - tov_pct_l7_pre   (positive = opponent forces more TOs than you commit)
      - def_ppp_edge_pre = opp_def_ppp_allowed_pre - def_ppp_allowed_l7_pre (if available)

    All are best-effort: if inputs missing, output stays NaN.
    """
    out = df.copy()

    # ensure likely inputs exist (won't error)
    inputs = [
        "efg_l7_pre", "ftr_l7_pre", "orb_pct_l7_pre", "tov_pct_l7_pre",
        "opp_efg_allowed_pre", "opp_ftr_allowed_pre", "opp_orb_allowed_pre", "opp_tov_forced_pre",
        "opp_def_ppp_allowed_pre", "def_ppp_allowed_l7_pre",
    ]
    out = _ensure_cols(out, inputs)
    out = ensure_numeric(out, inputs)

    efg_edge = out["efg_l7_pre"] - out["opp_efg_allowed_pre"]
    ftr_edge = out["ftr_l7_pre"] - out["opp_ftr_allowed_pre"]
    orb_edge = out["orb_pct_l7_pre"] - out["opp_orb_allowed_pre"]
    tov_edge = out["opp_tov_forced_pre"] - out["tov_pct_l7_pre"]

    def_ppp_edge = pd.Series(np.nan, index=out.index)
    if ("opp_def_ppp_allowed_pre" in out.columns) and ("def_ppp_allowed_l7_pre" in out.columns):
        def_ppp_edge = out["opp_def_ppp_allowed_pre"] - out["def_ppp_allowed_l7_pre"]

    _set_col(out, "efg_edge_pre", efg_edge, overwrite)
    _set_col(out, "ftr_edge_pre", ftr_edge, overwrite)
    _set_col(out, "orb_edge_pre", orb_edge, overwrite)
    _set_col(out, "tov_edge_pre", tov_edge, overwrite)
    _set_col(out, "def_ppp_edge_pre", def_ppp_edge, overwrite)
    return out


# ----------------------------
# Optional player-level features (unchanged)
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
      1) add_shooting_profile (3p_pct, ft_pct)
      2) add_expected_matchup_metrics (exp_* incl exp_margin)
      3) add_vs_expectation_scores (gps, deltas)
      4) add_style_mismatch (style_distance_l7 etc)
      5) add_lastn_rollups (includes gps)
      6) add_volatility_composites
      7) add_matchup_edges (if opponent allowed/forced pregame fields exist)

    Default behavior is conservative: does NOT overwrite existing columns.
    """
    out = df.copy()
    out = add_shooting_profile(out, overwrite=False)
    out = add_expected_matchup_metrics(out, overwrite=False)
    out = add_vs_expectation_scores(out, overwrite=False)
    out = add_style_mismatch(out, overwrite=False)
    out = add_lastn_rollups(out, n=n_last, prefix=f"l{n_last}_", overwrite=False)
    out = add_volatility_composites(out, prefix=f"l{n_last}_", overwrite=False)
    out = add_matchup_edges(out, overwrite=False)
    return out
