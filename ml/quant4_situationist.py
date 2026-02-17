#!/usr/bin/env python3
"""Quant 4 - Situationist: contextual and psychological adjustments."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping

import pandas as pd


def _f(row: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(row.get(key, default))
    except Exception:
        return float(default)


def _i(row: Mapping[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(row.get(key, default))
    except Exception:
        return int(default)


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(v)))


def situational_edge(
    team_row: Mapping[str, Any],
    opponent_row: Mapping[str, Any],
    game_context: Mapping[str, Any],
) -> Dict[str, Any]:
    proving_ground = 1.0 if _i(team_row, "selection_criticized_flag", 0) == 1 else 0.0
    senior_farewell = 1.0 if _i(team_row, "seniors_rotation_count", 0) >= 3 else 0.0
    auto_bid_relief = _clip((_f(team_row, "conference_tourney_margin", 0.0) + 8.0) / 16.0, 0.0, 1.0) if _i(team_row, "auto_bid", 0) == 1 else 0.0
    nit_demoralization = _clip((_f(team_row, "projected_in_ncaa_days", 0.0) - 20.0) / 35.0, 0.0, 1.0) if _i(game_context, "is_nit", 0) == 1 else 0.0
    if _i(game_context, "is_nit", 0) == 1:
        tier = str(team_row.get("program_tier", "")).lower()
        nit_demoralization = _clip(nit_demoralization * (1.2 if tier in {"blue_blood", "power"} else 0.8), 0.0, 1.0)
    revenge = 1.0 if _i(game_context, "rematch_controversial_flag", 0) == 1 and _i(team_row, "lost_prior_meeting_flag", 0) == 1 else 0.0

    travel_burden = _clip((_f(team_row, "travel_miles", 0.0) / 2200.0) + (_f(team_row, "timezone_changes", 0.0) * 0.15), 0.0, 1.0)
    altitude_adj = _clip((_f(game_context, "site_altitude_ft", 0.0) - _f(team_row, "campus_altitude_ft", 0.0)) / 5000.0, 0.0, 1.0)
    b2b_fatigue = _clip((_i(team_row, "games_in_72h", 1) - 2) / 2.0, 0.0, 1.0)
    tip_mismatch = _clip(abs(_f(team_row, "avg_tipoff_local_hour", 19.0) - _f(game_context, "tipoff_local_hour", 19.0)) / 9.0, 0.0, 1.0)
    officiating_mismatch = _clip(abs(_f(team_row, "physicality_index", 0.5) - _f(game_context, "crew_whistle_rate", 0.5)), 0.0, 1.0)
    crowd_comp = _clip((_f(game_context, "crowd_support_pct", 0.50) - 0.50) / 0.20, -1.0, 1.0)
    media_overload = _clip((_f(team_row, "media_obligations_per_day", 0.0) - 8.0) / 10.0, 0.0, 1.0)
    first_four_short_rest = _clip((_f(team_row, "hours_since_first_four", 72.0) - 36.0) / -24.0, 0.0, 1.0) if _i(game_context, "is_first_four_followup", 0) == 1 else 0.0
    campus_site_hostile = _clip((_f(game_context, "campus_site_home_edge", 0.0)) / 3.0, 0.0, 1.0) if _i(game_context, "campus_site_flag", 0) == 1 else 0.0

    positive = (proving_ground + senior_farewell + auto_bid_relief + revenge + max(crowd_comp, 0.0)) / 5.0
    negative = (
        nit_demoralization
        + travel_burden
        + altitude_adj
        + b2b_fatigue
        + tip_mismatch
        + officiating_mismatch
        + media_overload
        + max(-crowd_comp, 0.0)
        + first_four_short_rest
        + campus_site_hostile
    ) / 10.0
    situational_edge_score = _clip(positive - negative, -1.0, 1.0)
    situational_adjustment_points = round(situational_edge_score * 2.5, 2)

    motivation_flag = "HIGH_DOG" if positive >= 0.60 else ("LOW_MOTIVATION" if nit_demoralization > 0.55 else "NEUTRAL")
    physical_state_rating = _clip(1.0 - (travel_burden + altitude_adj + b2b_fatigue + tip_mismatch) / 4.0, 0.0, 1.0)
    risk_flags = []
    if travel_burden > 0.60:
        risk_flags.append("TRAVEL_BURDEN_SCORE")
    if altitude_adj > 0.55:
        risk_flags.append("ALTITUDE_ADJUSTMENT")
    if b2b_fatigue > 0.45:
        risk_flags.append("BACK_TO_BACK_FATIGUE_PATTERN")
    if tip_mismatch > 0.50:
        risk_flags.append("TIP_TIME_MISMATCH")
    if officiating_mismatch > 0.60:
        risk_flags.append("OFFICIATING_STYLE_MISMATCH")
    if media_overload > 0.45:
        risk_flags.append("MEDIA_PRESSURE_OVERLOAD")
    if first_four_short_rest > 0.45:
        risk_flags.append("FIRST_FOUR_SHORT_REST")
    if campus_site_hostile > 0.45:
        risk_flags.append("CAMPUS_SITE_HOSTILE_ENVIRONMENT")

    return {
        "situational_edge_score": situational_edge_score,
        "motivation_flag": motivation_flag,
        "physical_state_rating": physical_state_rating,
        "contextual_risk_flags": risk_flags,
        "situational_adjustment_points": situational_adjustment_points,
    }


def validate_against_closing_line(seasons: Iterable[int] = range(2015, 2025), games_df: pd.DataFrame | None = None) -> Dict[str, Any]:
    if games_df is None or games_df.empty:
        return {"seasons": list(seasons), "sample_size": 0, "clv_delta": 0.0, "situational_alpha": 0.0}
    df = games_df.copy()
    adj = df.get("situational_adjustment_points", pd.Series([0.0] * len(df))).astype(float)
    ats = df.get("ats_result", pd.Series([0.0] * len(df))).astype(float)
    corr = float(adj.corr(ats)) if len(df) > 1 else 0.0
    clv = (df.get("closing_spread", pd.Series([0.0] * len(df))) - df.get("open_spread", pd.Series([0.0] * len(df)))).astype(float).mean()
    return {"seasons": list(seasons), "sample_size": len(df), "clv_delta": float(clv), "situational_alpha": corr}
