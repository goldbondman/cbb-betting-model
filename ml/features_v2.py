#!/usr/bin/env python3
"""
High-ROI, low-leakage pregame features.

All features here must be computed from pregame metrics (e.g., *_l7_pre).

Design goals (future-proofing):
- Backwards compatible exports: FEATURES_V2, add_features_v2(df)
- Safe with missing columns: compute what we can, fill missing outputs with NaN
- Deterministic output schema: always create every FEATURES_V2 column
- No target leakage: only uses *_pre and schedule/rest inputs (games_last_*), plus style_distance_* (pregame)
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


# Public contract: other modules import this list.
FEATURES_V2: List[str] = [
    "tempo_gap_l7",
    "shot_quality_gap_l7",
    "turnover_gap_l7",
    "rebound_gap_l7",
    "ftr_gap_l7",
    "three_rate_gap_l7",
    "netrtg_gap_l7",
    "rest_gap_3d",
    "rest_gap_7d",
    "style_distance_l7",
]

# Internal mapping of output feature -> required input columns.
# Keep this stable; it drives both safe-compute and debug reporting.
_FEATURE_INPUTS: Dict[str, Tuple[str, ...]] = {
    "tempo_gap_l7": ("pace_l7_pre_home", "pace_l7_pre_away"),
    "shot_quality_gap_l7": ("efg_l7_pre_home", "efg_l7_pre_away"),
    # turnover: lower is better, so we keep the historical sign convention:
    # positive value favors home (away_tov - home_tov)
    "turnover_gap_l7": ("tov_pct_l7_pre_away", "tov_pct_l7_pre_home"),
    "rebound_gap_l7": (
        "orb_pct_l7_pre_home",
        "drb_pct_l7_pre_away",
        "orb_pct_l7_pre_away",
        "drb_pct_l7_pre_home",
    ),
    "ftr_gap_l7": ("ftr_l7_pre_home", "ftr_l7_pre_away"),
    "three_rate_gap_l7": ("3par_l7_pre_home", "3par_l7_pre_away"),
    "netrtg_gap_l7": ("netrtg_l7_pre_home", "netrtg_l7_pre_away"),
    # rest: fewer games in last N days = more rest.
    # keep convention: positive value favors home (away_games - home_games)
    "rest_gap_3d": ("games_last_3_days_away", "games_last_3_days_home"),
    "rest_gap_7d": ("games_last_7_days_away", "games_last_7_days_home"),
    "style_distance_l7": ("style_distance_l7_home", "style_distance_l7_away"),
}


def _to_numeric_safe(df: pd.DataFrame, cols: Sequence[str]) -> None:
    """
    In-place coercion to numeric for referenced cols (errors->NaN).
    """
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")


def _ensure_outputs(out: pd.DataFrame) -> None:
    """
    Ensure all FEATURES_V2 columns exist (NaN default).
    """
    for c in FEATURES_V2:
        if c not in out.columns:
            out[c] = np.nan


def add_features_v2(df: pd.DataFrame, *, debug: bool = False) -> pd.DataFrame:
    """
    Adds FEATURES_V2 columns to df.

    Behavior:
    - Computes each feature only if required inputs are present.
    - If inputs are missing, leaves that feature as NaN.
    - Always returns a DF containing all FEATURES_V2 columns.

    debug=True will print which features could not be computed due to missing inputs.
    """
    out = df.copy()

    # Coerce referenced inputs to numeric (prevents string subtraction explosions).
    referenced_inputs: List[str] = []
    for cols in _FEATURE_INPUTS.values():
        referenced_inputs.extend(list(cols))
    _to_numeric_safe(out, referenced_inputs)

    # tempo gap
    if all(c in out.columns for c in _FEATURE_INPUTS["tempo_gap_l7"]):
        out["tempo_gap_l7"] = out["pace_l7_pre_home"] - out["pace_l7_pre_away"]

    # shot quality gap
    if all(c in out.columns for c in _FEATURE_INPUTS["shot_quality_gap_l7"]):
        out["shot_quality_gap_l7"] = out["efg_l7_pre_home"] - out["efg_l7_pre_away"]

    # turnover gap (positive favors home)
    if all(c in out.columns for c in _FEATURE_INPUTS["turnover_gap_l7"]):
        out["turnover_gap_l7"] = out["tov_pct_l7_pre_away"] - out["tov_pct_l7_pre_home"]

    # rebound gap (captures OREB vs opponent DREB on both sides, then diff)
    if all(c in out.columns for c in _FEATURE_INPUTS["rebound_gap_l7"]):
        out["rebound_gap_l7"] = (
            (out["orb_pct_l7_pre_home"] - out["drb_pct_l7_pre_away"])
            - (out["orb_pct_l7_pre_away"] - out["drb_pct_l7_pre_home"])
        )

    # free throw rate gap
    if all(c in out.columns for c in _FEATURE_INPUTS["ftr_gap_l7"]):
        out["ftr_gap_l7"] = out["ftr_l7_pre_home"] - out["ftr_l7_pre_away"]

    # 3pt attempt rate gap
    if all(c in out.columns for c in _FEATURE_INPUTS["three_rate_gap_l7"]):
        out["three_rate_gap_l7"] = out["3par_l7_pre_home"] - out["3par_l7_pre_away"]

    # net rating gap
    if all(c in out.columns for c in _FEATURE_INPUTS["netrtg_gap_l7"]):
        out["netrtg_gap_l7"] = out["netrtg_l7_pre_home"] - out["netrtg_l7_pre_away"]

    # rest gaps (positive favors home)
    if all(c in out.columns for c in _FEATURE_INPUTS["rest_gap_3d"]):
        out["rest_gap_3d"] = out["games_last_3_days_away"] - out["games_last_3_days_home"]

    if all(c in out.columns for c in _FEATURE_INPUTS["rest_gap_7d"]):
        out["rest_gap_7d"] = out["games_last_7_days_away"] - out["games_last_7_days_home"]

    # style distance: average of both teams' pregame style distance (if available)
    if all(c in out.columns for c in _FEATURE_INPUTS["style_distance_l7"]):
        out["style_distance_l7"] = (out["style_distance_l7_home"] + out["style_distance_l7_away"]) / 2.0

    _ensure_outputs(out)

    if debug:
        missing = []
        for feat, req in _FEATURE_INPUTS.items():
            if feat not in out.columns or out[feat].isna().all():
                # Only flag if truly uncomputable due to missing inputs
                # (as opposed to computed but NaN because inputs were NaN).
                missing_inputs = [c for c in req if c not in out.columns]
                if missing_inputs:
                    missing.append((feat, missing_inputs))
        if missing:
            print("[WARN] add_features_v2 missing inputs for:")
            for feat, mi in missing:
                print(f"  - {feat}: missing {mi}")

    return out
