#!/usr/bin/env python3
"""Quant 3 - Executioner: favorite fragility modeling."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping

import pandas as pd

BRAND_TEAMS = {"duke", "kentucky", "kansas", "north carolina", "gonzaga"}


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


def _clip01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def favorite_fragility_index(favorite_row: Mapping[str, Any], underdog_row: Mapping[str, Any]) -> Dict[str, Any]:
    eff_gap = max(0.0, (_i(favorite_row, "point_diff_rank", 999) - _i(favorite_row, "kenpom_eff_rank", 999) - 30) / 90.0)
    turnover_timebomb = 1.0 if (_i(favorite_row, "turnover_rate_rank", 999) > 100 and _i(underdog_row, "forced_to_rate_rank", 999) <= 40) else 0.0
    perimeter_exposure = 1.0 if (_i(favorite_row, "three_pt_def_rank", 999) > 80 and _f(underdog_row, "three_pt_attempt_rate", 0.0) >= 0.40) else 0.0
    depth_illusion = _clip01((_f(favorite_row, "minutes_share_top6", 0.70) - 0.85) / 0.10)
    slow_start = _clip01((_f(favorite_row, "second_half_net_rating", 0.0) - _f(favorite_row, "first_half_net_rating", 0.0) - 5.0) / 12.0)
    road_warrior_false = _clip01((_f(favorite_row, "road_eff", 1.0) - _f(favorite_row, "neutral_eff", 1.0) - 0.05) / 0.20)
    recency_trap = _clip01((_f(favorite_row, "last8_actual_wins", 0.0) - _f(favorite_row, "last8_pythag_wins", 0.0) - 1.0) / 5.0)
    interior_fallacy = 1.0 if (_i(favorite_row, "two_pt_pct_rank", 999) <= 20 and _f(underdog_row, "zone_rate", 0.0) >= 0.25) else 0.0

    parts = {
        "EFFICIENCY_CLIFF": _clip01(eff_gap),
        "TURNOVER_TIMEBOMB": turnover_timebomb,
        "PERIMETER_EXPOSURE": perimeter_exposure,
        "DEPTH_ILLUSION": depth_illusion,
        "SLOW_START_TENDENCY": slow_start,
        "ROAD_WARRIOR_FALSE": road_warrior_false,
        "RECENCY_TRAP": recency_trap,
        "INTERIOR_DOMINANCE_FALLACY": interior_fallacy,
    }
    fragility_index = _clip01(sum(parts.values()) / len(parts))

    fatigue_multiplier = 1.0 if (_i(favorite_row, "games_last_9_days", 0) >= 5 and _i(underdog_row, "games_last_9_days", 0) <= 3) else 0.0
    hangover = 1.0 if _i(favorite_row, "lost_conf_tourney_final", 0) == 1 else 0.0
    first_round_exit_probability = _clip01(0.08 + fragility_index * 0.55 + fatigue_multiplier * 0.08 + hangover * 0.04)
    second_round_exit_probability = _clip01(0.14 + fragility_index * 0.45 + fatigue_multiplier * 0.05)

    brand_premium = 1.25 if str(favorite_row.get("team", "")).strip().lower() in BRAND_TEAMS else 0.0
    recommended_action = "fade" if fragility_index >= 0.55 else ("monitor" if fragility_index >= 0.35 else "avoid")
    drivers: List[str] = [k for k, v in parts.items() if v >= 0.55]

    return {
        "fragility_index": fragility_index,
        "early_exit_probability": max(first_round_exit_probability, second_round_exit_probability),
        "first_round_exit_probability": first_round_exit_probability,
        "second_round_exit_probability": second_round_exit_probability,
        "fragility_drivers": drivers,
        "brand_tax_points": brand_premium,
        "recommended_action": recommended_action,
    }


def validate_against_closing_line(seasons: Iterable[int] = range(2015, 2025), games_df: pd.DataFrame | None = None) -> Dict[str, Any]:
    if games_df is None or games_df.empty:
        return {"seasons": list(seasons), "sample_size": 0, "clv_delta": 0.0, "fragility_alpha": 0.0}
    df = games_df.copy()
    high_frag = df.get("fragility_index", pd.Series([0.0] * len(df))).astype(float) >= 0.55
    ats = df.get("favorite_ats_result", pd.Series([0.0] * len(df))).astype(float)
    frag_alpha = float(-ats[high_frag].mean()) if high_frag.any() else 0.0
    clv = (df.get("closing_spread", pd.Series([0.0] * len(df))) - df.get("open_spread", pd.Series([0.0] * len(df)))).astype(float).mean()
    return {"seasons": list(seasons), "sample_size": len(df), "clv_delta": float(clv), "fragility_alpha": frag_alpha}
