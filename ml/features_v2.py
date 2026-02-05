#!/usr/bin/env python3
"""
High-ROI, low-leakage pregame features.

All features here must be computed from pregame metrics (e.g., *_l7_pre).
"""

from __future__ import annotations

from typing import List

import numpy as np
import pandas as pd


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


def add_features_v2(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["tempo_gap_l7"] = out["pace_l7_pre_home"] - out["pace_l7_pre_away"]
    out["shot_quality_gap_l7"] = out["efg_l7_pre_home"] - out["efg_l7_pre_away"]
    out["turnover_gap_l7"] = out["tov_pct_l7_pre_away"] - out["tov_pct_l7_pre_home"]
    out["rebound_gap_l7"] = (
        (out["orb_pct_l7_pre_home"] - out["drb_pct_l7_pre_away"])
        - (out["orb_pct_l7_pre_away"] - out["drb_pct_l7_pre_home"])
    )
    out["ftr_gap_l7"] = out["ftr_l7_pre_home"] - out["ftr_l7_pre_away"]
    out["three_rate_gap_l7"] = out["3par_l7_pre_home"] - out["3par_l7_pre_away"]
    out["netrtg_gap_l7"] = out["netrtg_l7_pre_home"] - out["netrtg_l7_pre_away"]
    out["rest_gap_3d"] = out["games_last_3_days_away"] - out["games_last_3_days_home"]
    out["rest_gap_7d"] = out["games_last_7_days_away"] - out["games_last_7_days_home"]
    if "style_distance_l7_home" in out.columns and "style_distance_l7_away" in out.columns:
        out["style_distance_l7"] = (out["style_distance_l7_home"] + out["style_distance_l7_away"]) / 2.0
    return out
