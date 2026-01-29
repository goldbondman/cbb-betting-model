#!/usr/bin/env python3
"""
plus_and_fit.py

Builds opponent-adjusted "plus" metrics, matchup fit metrics, and composite indices.

This module assumes you already have, per team-game row:
- team metrics (e.g., efg, tov_pct, orb_pct, ftr, ortg/off_ppp, drtg/def_ppp, poss, points_for/against, etc.)
- opponent join columns (prefixed with "opp_") from your opponent merge
- pregame (leak-free) rolling columns for BOTH team and opponent when needed

Key outputs:
1) Opponent-adjusted PLUS metrics (team-game, pregame-aware where required):
   - efg_plus = team_efg - opp_efg_allowed_pre
   - tov_plus = opp_tov_forced_pre - team_tov_pct          (higher is better)
   - orb_plus = team_orb_pct - opp_orb_allowed_pre
   - ftr_plus = team_ftr - opp_ftr_allowed_pre
   - ppp_plus = team_off_ppp - opp_def_ppp_allowed_pre     (or ortg - opp_drtg_allowed_pre)

2) Prediction-time FIT metrics for matchup A vs B (computed on one-row matchup table):
   - ShootingFit, TurnoverFit, GlassFit, FTRFit, Fit3

3) Composite indices (team-level features you can roll):
   - PWR, PWR_plus
   - EPI (extra possessions index, per game or per 100)
   - Triangle, Triangle_plus
   - MOI (Modern Offense Index)
   - RimProxy
   - Simple defense composites if you have corresponding opponent/allowed columns

Important assumptions and guardrails
-----------------------------------
- "Allowed/forced" opponent baselines MUST be leak-free (as-of that date). This module expects
  them as columns like:
    opp_efg_allowed_pre, opp_tov_forced_pre, opp_orb_allowed_pre, opp_ftr_allowed_pre, opp_def_ppp_allowed_pre
  If you don't have them yet, this module can optionally build them from opponent *defensive* rolling columns
  if present (fallback mapping).

- Z-score based composites should be computed on a stable reference set (league / season). This module provides:
    compute_league_zcols(df, cols)
  which z-scores across the passed df (you should pass a season slice).

- No em dashes anywhere (per your preference).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

EPS = 1e-12


def _to_num(df: pd.DataFrame, cols: Sequence[str]) -> pd.DataFrame:
    out = df.copy()
    for c in cols:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def _ensure(df: pd.DataFrame, cols: Sequence[str], ctx: str) -> None:
    miss = [c for c in cols if c not in df.columns]
    if miss:
        raise ValueError(f"{ctx}: missing columns: {miss}")


def _safe_div(a, b):
    try:
        a = float(a)
        b = float(b)
        if not np.isfinite(b) or abs(b) < EPS:
            return np.nan
        return a / b
    except Exception:
        return np.nan


def zscore_cols(df: pd.DataFrame, cols: Sequence[str], prefix: str = "z_") -> pd.DataFrame:
    """
    Adds z-scored versions of cols across df.
    Use on a stable league slice (for example entire season-to-date).
    """
    out = df.copy()
    for c in cols:
        if c not in out.columns:
            out[f"{prefix}{c}"] = np.nan
            continue
        s = pd.to_numeric(out[c], errors="coerce")
        mu = s.mean(skipna=True)
        sd = s.std(skipna=True, ddof=0)
        if not np.isfinite(sd) or sd < EPS:
            out[f"{prefix}{c}"] = (s - mu) * 0.0
        else:
            out[f"{prefix}{c}"] = (s - mu) / sd
    return out


@dataclass(frozen=True)
class PlusConfig:
    # Team metric columns (single-game, not pregame)
    efg_col: str = "efg"
    tov_pct_col: str = "tov_pct"
    orb_pct_col: str = "orb_pct"
    ftr_col: str = "ftr"

    # PPP columns (you may have ortg/drtg instead)
    off_ppp_col: str = "off_ppp"
    def_ppp_col: str = "def_ppp"

    # Fallback if ppp not present
    ortg_col: str = "ortg"
    drtg_col: str = "drtg"

    # Opponent allowed/forced columns (must be leak-free pregame)
    opp_efg_allowed_pre: str = "opp_efg_allowed_pre"
    opp_tov_forced_pre: str = "opp_tov_forced_pre"
    opp_orb_allowed_pre: str = "opp_orb_allowed_pre"
    opp_ftr_allowed_pre: str = "opp_ftr_allowed_pre"
    opp_def_ppp_allowed_pre: str = "opp_def_ppp_allowed_pre"

    # Fallback mappings if the above are missing (optional)
    # Example: use opponent defensive rolling pregame columns, if available, as proxies for allowed.
    # You can point these at your existing columns once you confirm names.
    opp_efg_allowed_fallback: Optional[str] = "opp_efg_allowed_proxy_pre"
    opp_tov_forced_fallback: Optional[str] = "opp_tov_forced_proxy_pre"
    opp_orb_allowed_fallback: Optional[str] = "opp_orb_allowed_proxy_pre"
    opp_ftr_allowed_fallback: Optional[str] = "opp_ftr_allowed_proxy_pre"
    opp_def_ppp_allowed_fallback: Optional[str] = "opp_def_ppp_allowed_proxy_pre"

    # Outputs
    out_efg_plus: str = "efg_plus"
    out_tov_plus: str = "tov_plus"
    out_orb_plus: str = "orb_plus"
    out_ftr_plus: str = "ftr_plus"
    out_ppp_plus: str = "ppp_plus"


def _pick(df: pd.DataFrame, primary: str, fallback: Optional[str]) -> pd.Series:
    if primary in df.columns:
        s = pd.to_numeric(df[primary], errors="coerce")
        if s.notna().any():
            return s
    if fallback and fallback in df.columns:
        return pd.to_numeric(df[fallback], errors="coerce")
    return pd.Series(np.nan, index=df.index)


def add_plus_metrics(df: pd.DataFrame, cfg: PlusConfig = PlusConfig()) -> pd.DataFrame:
    """
    Adds plus metrics.

    Definitions (team-game g):
      efg_plus = team_efg - opp_efg_allowed_pre
      tov_plus = opp_tov_forced_pre - team_tov_pct    (higher is better)
      orb_plus = team_orb_pct - opp_orb_allowed_pre
      ftr_plus = team_ftr - opp_ftr_allowed_pre
      ppp_plus = team_off_ppp - opp_def_ppp_allowed_pre

    If off_ppp/def_ppp are not present, ppp_plus falls back to:
      ppp_plus = ortg - opp_drtg_allowed_pre (proxy) if those columns exist.

    This assumes opponent allowed/forced columns are leak-free (as-of that date).
    """
    out = df.copy()

    # ensure team cols exist (or create nans)
    for c in [cfg.efg_col, cfg.tov_pct_col, cfg.orb_pct_col, cfg.ftr_col]:
        if c not in out.columns:
            out[c] = np.nan

    out = _to_num(out, [cfg.efg_col, cfg.tov_pct_col, cfg.orb_pct_col, cfg.ftr_col,
                        cfg.off_ppp_col, cfg.def_ppp_col, cfg.ortg_col, cfg.drtg_col])

    opp_efg_allowed = _pick(out, cfg.opp_efg_allowed_pre, cfg.opp_efg_allowed_fallback)
    opp_tov_forced = _pick(out, cfg.opp_tov_forced_pre, cfg.opp_tov_forced_fallback)
    opp_orb_allowed = _pick(out, cfg.opp_orb_allowed_pre, cfg.opp_orb_allowed_fallback)
    opp_ftr_allowed = _pick(out, cfg.opp_ftr_allowed_pre, cfg.opp_ftr_allowed_fallback)
    opp_def_ppp_allowed = _pick(out, cfg.opp_def_ppp_allowed_pre, cfg.opp_def_ppp_allowed_fallback)

    out[cfg.out_efg_plus] = out[cfg.efg_col] - opp_efg_allowed
    out[cfg.out_tov_plus] = opp_tov_forced - out[cfg.tov_pct_col]
    out[cfg.out_orb_plus] = out[cfg.orb_pct_col] - opp_orb_allowed
    out[cfg.out_ftr_plus] = out[cfg.ftr_col] - opp_ftr_allowed

    # ppp_plus
    if cfg.off_ppp_col in out.columns and out[cfg.off_ppp_col].notna().any():
        out[cfg.out_ppp_plus] = out[cfg.off_ppp_col] - opp_def_ppp_allowed
    else:
        # fallback: use ortg and opponent "drtg allowed"
        if cfg.ortg_col in out.columns and opp_def_ppp_allowed.notna().any():
            out[cfg.out_ppp_plus] = out[cfg.ortg_col] - opp_def_ppp_allowed
        else:
            out[cfg.out_ppp_plus] = np.nan

    return out


# ----------------------------
# Extra possessions index + simple derived metrics
# ----------------------------

def add_epi_pwr(
    df: pd.DataFrame,
    orb_col: str = "orb",
    opp_orb_col: str = "opp_orb",
    tov_col: str = "tov",
    opp_tov_col: str = "opp_tov",
    poss_col: str = "poss",
    out_epi: str = "epi",
    out_epi_per100: str = "epi_per100",
    out_pwr: str = "pwr_raw",
) -> pd.DataFrame:
    """
    EPI_g = (ORB - opp_ORB) - (TOV - opp_TOV)
    optionally per 100 possessions.

    PWR_raw (non-z) proxy:
      pwr_raw = orb_pct - tov_pct   (if those exist, you should prefer that)
    """
    out = df.copy()
    for c in [orb_col, opp_orb_col, tov_col, opp_tov_col, poss_col]:
        if c not in out.columns:
            out[c] = np.nan
    out = _to_num(out, [orb_col, opp_orb_col, tov_col, opp_tov_col, poss_col])

    out[out_epi] = (out[orb_col] - out[opp_orb_col]) - (out[tov_col] - out[opp_tov_col])
    out[out_epi_per100] = (out[out_epi] / out[poss_col]) * 100.0
    out[out_pwr] = np.nan  # fill later if you z-score + combine orb% and tov%

    return out


# ----------------------------
# Composite indices (Triangle, MOI, RimProxy, PWR+)
# ----------------------------

@dataclass(frozen=True)
class CompositeConfig:
    # Inputs
    efg_col: str = "efg"
    ftr_col: str = "ftr"
    tov_pct_col: str = "tov_pct"
    threepar_col: str = "3par"  # 3PA_rate
    fta_per100_col: str = "fta_per100"  # you can create this upstream if you have FTA + poss
    orb_pct_col: str = "orb_pct"

    # Plus inputs
    efg_plus_col: str = "efg_plus"
    ftr_plus_col: str = "ftr_plus"
    tov_plus_col: str = "tov_plus"
    orb_plus_col: str = "orb_plus"

    # Outputs
    out_triangle: str = "triangle"
    out_triangle_plus: str = "triangle_plus"
    out_moi: str = "moi"
    out_rimproxy: str = "rim_proxy"
    out_pwr: str = "pwr"
    out_pwr_plus: str = "pwr_plus"

    # Weights
    tri_w1: float = 1.0  # efg
    tri_w2: float = 1.0  # ftr
    tri_w3: float = 1.0  # (1 - tov%)

    tri_plus_w1: float = 1.0
    tri_plus_w2: float = 1.0
    tri_plus_w3: float = 1.0


def add_composites(
    df: pd.DataFrame,
    cfg: CompositeConfig = CompositeConfig(),
    z_prefix: str = "z_",
    create_fta_per100_if_missing: bool = True,
    fta_col: str = "fta",
    poss_col: str = "poss",
) -> pd.DataFrame:
    """
    Adds composites using z-scored components.
    Caller should ensure z-scored columns exist or let this function compute them.

    Triangle:
      Triangle = w1*z(eFG) + w2*z(FTR) + w3*z(1 - TOV%)
    Triangle+:
      Triangle+ = w1*z(eFG_plus) + w2*z(FTR_plus) + w3*z(TOV_plus)

    MOI:
      MOI = z(3PA_rate) + z(FTR) - z(TOV%)

    RimProxy:
      RimProxy = z(FTA_per_100) + z(ORB%)

    PWR:
      PWR = z(ORB%) - z(TOV%)
    PWR+:
      PWR+ = z(ORB_plus) + z(TOV_plus)
    """
    out = df.copy()

    # optionally create fta_per100
    if create_fta_per100_if_missing and cfg.fta_per100_col not in out.columns:
        if fta_col in out.columns and poss_col in out.columns:
            out = _to_num(out, [fta_col, poss_col])
            out[cfg.fta_per100_col] = (out[fta_col] / out[poss_col]) * 100.0
        else:
            out[cfg.fta_per100_col] = np.nan

    base_cols = [cfg.efg_col, cfg.ftr_col, cfg.tov_pct_col, cfg.threepar_col, cfg.fta_per100_col, cfg.orb_pct_col]
    plus_cols = [cfg.efg_plus_col, cfg.ftr_plus_col, cfg.tov_plus_col, cfg.orb_plus_col]

    # ensure columns exist
    for c in base_cols + plus_cols:
        if c not in out.columns:
            out[c] = np.nan
    out = _to_num(out, base_cols + plus_cols)

    # build 1 - tov%
    out["one_minus_tov"] = 1.0 - out[cfg.tov_pct_col]

    # z-score required inputs
    z_inputs = [
        cfg.efg_col, cfg.ftr_col, "one_minus_tov", cfg.tov_pct_col,
        cfg.threepar_col, cfg.fta_per100_col, cfg.orb_pct_col,
        cfg.efg_plus_col, cfg.ftr_plus_col, cfg.tov_plus_col, cfg.orb_plus_col
    ]
    out = zscore_cols(out, z_inputs, prefix=z_prefix)

    # Triangle
    out[cfg.out_triangle] = (
        cfg.tri_w1 * out[f"{z_prefix}{cfg.efg_col}"]
        + cfg.tri_w2 * out[f"{z_prefix}{cfg.ftr_col}"]
        + cfg.tri_w3 * out[f"{z_prefix}one_minus_tov"]
    )

    # Triangle+
    out[cfg.out_triangle_plus] = (
        cfg.tri_plus_w1 * out[f"{z_prefix}{cfg.efg_plus_col}"]
        + cfg.tri_plus_w2 * out[f"{z_prefix}{cfg.ftr_plus_col}"]
        + cfg.tri_plus_w3 * out[f"{z_prefix}{cfg.tov_plus_col}"]
    )

    # MOI
    out[cfg.out_moi] = (
        out[f"{z_prefix}{cfg.threepar_col}"]
        + out[f"{z_prefix}{cfg.ftr_col}"]
        - out[f"{z_prefix}{cfg.tov_pct_col}"]
    )

    # RimProxy
    out[cfg.out_rimproxy] = out[f"{z_prefix}{cfg.fta_per100_col}"] + out[f"{z_prefix}{cfg.orb_pct_col}"]

    # PWR and PWR+
    out[cfg.out_pwr] = out[f"{z_prefix}{cfg.orb_pct_col}"] - out[f"{z_prefix}{cfg.tov_pct_col}"]
    out[cfg.out_pwr_plus] = out[f"{z_prefix}{cfg.orb_plus_col}"] + out[f"{z_prefix}{cfg.tov_plus_col}"]

    out = out.drop(columns=["one_minus_tov"], errors="ignore")
    return out


# ----------------------------
# Matchup FIT metrics (prediction-time)
# ----------------------------

@dataclass(frozen=True)
class FitConfig:
    # Prefer opponent-adjusted rollups if available, else raw
    A_efg_off_plus: str = "A_efg_plus"
    B_efg_def_allowed_plus: str = "B_efg_allowed_plus"

    A_tov_off_plus: str = "A_tov_plus"
    B_tov_forced_plus: str = "B_tov_forced_plus"

    A_orb_plus: str = "A_orb_plus"
    B_drb_plus: str = "B_drb_plus"

    A_ftr_plus: str = "A_ftr_plus"
    B_ftr_allowed_plus: str = "B_ftr_allowed_plus"

    out_shoot: str = "ShootingFit"
    out_tov: str = "TurnoverFit"
    out_glass: str = "GlassFit"
    out_ftr: str = "FTRFit"
    out_fit3: str = "Fit3"

    c1: float = 1.0
    c2: float = 1.0
    c3: float = 1.0


def add_fit_metrics(matchup_df: pd.DataFrame, cfg: FitConfig = FitConfig()) -> pd.DataFrame:
    """
    Expects a one-row-per-game matchup table with A_* and B_* columns already attached.
    Produces:
      ShootingFit = A_eFG_off_plus - B_eFG_def_allowed_plus
      TurnoverFit = B_TOV_forced_plus - A_TOV_off_plus
      GlassFit = A_ORB_plus - B_DRB_plus
      FTRFit = A_FTR_plus - B_FTR_allowed_plus
      Fit3 = c1*ShootingFit + c2*TurnoverFit + c3*GlassFit
    """
    out = matchup_df.copy()
    needed = [
        cfg.A_efg_off_plus, cfg.B_efg_def_allowed_plus,
        cfg.A_tov_off_plus, cfg.B_tov_forced_plus,
        cfg.A_orb_plus, cfg.B_drb_plus,
        cfg.A_ftr_plus, cfg.B_ftr_allowed_plus
    ]
    for c in needed:
        if c not in out.columns:
            out[c] = np.nan
    out = _to_num(out, needed)

    out[cfg.out_shoot] = out[cfg.A_efg_off_plus] - out[cfg.B_efg_def_allowed_plus]
    out[cfg.out_tov] = out[cfg.B_tov_forced_plus] - out[cfg.A_tov_off_plus]
    out[cfg.out_glass] = out[cfg.A_orb_plus] - out[cfg.B_drb_plus]
    out[cfg.out_ftr] = out[cfg.A_ftr_plus] - out[cfg.B_ftr_allowed_plus]
    out[cfg.out_fit3] = cfg.c1 * out[cfg.out_shoot] + cfg.c2 * out[cfg.out_tov] + cfg.c3 * out[cfg.out_glass]
    return out


# ----------------------------
# Convenience wrapper for team-game features
# ----------------------------

def add_all_plus_and_composites(
    df: pd.DataFrame,
    plus_cfg: PlusConfig = PlusConfig(),
    comp_cfg: CompositeConfig = CompositeConfig(),
) -> pd.DataFrame:
    """
    One-call add-on:
      - plus metrics
      - EPI (requires orb/opp_orb/tov/opp_tov/poss)
      - composites (Triangle, Triangle+, MOI, RimProxy, PWR, PWR+)

    It intentionally does NOT compute opponent "allowed/forced" baselines.
    Those should be built upstream as leak-free opponent defensive rollups and merged in.
    """
    out = df.copy()
    out = add_plus_metrics(out, plus_cfg)
    out = add_epi_pwr(out)  # pwr_raw placeholder, pwr computed in add_composites
    out = add_composites(out, comp_cfg)
    return out


if __name__ == "__main__":
    # Minimal smoke test (schema-only)
    d = pd.DataFrame({
        "efg": [0.50, 0.52],
        "tov_pct": [0.18, 0.16],
        "orb_pct": [0.32, 0.28],
        "ftr": [0.28, 0.22],
        "poss": [70, 68],
        "fta": [18, 14],
        "3par": [0.40, 0.35],
        "orb": [10, 8],
        "opp_orb": [9, 11],
        "tov": [12, 10],
        "opp_tov": [11, 13],
        "opp_efg_allowed_pre": [0.49, 0.50],
        "opp_tov_forced_pre": [0.17, 0.18],
        "opp_orb_allowed_pre": [0.30, 0.31],
        "opp_ftr_allowed_pre": [0.25, 0.24],
        "opp_def_ppp_allowed_pre": [1.02, 1.00],
        "off_ppp": [1.08, 1.03],
    })
    d2 = add_all_plus_and_composites(d)
    print(d2.columns.tolist())
    print(d2.head().to_string(index=False))
