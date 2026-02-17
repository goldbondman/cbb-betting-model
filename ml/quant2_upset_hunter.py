#!/usr/bin/env python3
"""Quant 2 - Upset Hunter: underdog upset probability modeling."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping

import pandas as pd

ROUND_ADJUSTMENTS = {
    "NCAA_R64": 1.00,
    "NCAA_R32": 0.94,
    "NCAA_FIRST_FOUR": 0.97,
    "CONF_QF": 1.03,
    "CONF_SF": 1.00,
    "CONF_F": 0.96,
    "NIT_R1": 1.04,
    "NIT_R2": 1.01,
}


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


def _weighted_mean(parts: Dict[str, float], weights: Dict[str, float]) -> float:
    tot_w = sum(weights.values()) or 1.0
    return sum(parts[k] * weights.get(k, 0.0) for k in parts) / tot_w


def _upset_label(prob: float) -> str:
    if prob >= 0.50:
        return "LIVE_DOG"
    if prob >= 0.38:
        return "VOLATILE_DOG"
    return "LONGSHOT_DOG"


def upset_probability_model(
    underdog_row: Mapping[str, Any],
    favorite_row: Mapping[str, Any],
    spread: float,
    round_name: str = "NCAA_R64",
) -> Dict[str, Any]:
    defense_over_offense_gap = max(0.0, (_i(underdog_row, "off_eff_rank", 999) - _i(underdog_row, "def_eff_rank", 999) - 30) / 80.0)
    slow_it_down = max(0.0, (_i(favorite_row, "pace_rank", 175) - _i(underdog_row, "pace_rank", 175) - 30) / 140.0)
    ft_disparity = max(0.0, (_f(underdog_row, "ft_rate", 0.0) - _f(favorite_row, "opp_ft_rate_allowed", 0.0)) / 0.15)
    three_pt_def_mismatch = 1.0 if (_i(underdog_row, "three_pt_def_rank", 999) <= 30 and _i(favorite_row, "three_pt_off_rank", 999) <= 30) else 0.0
    rebounding_edge = max(0.0, (_f(underdog_row, "off_reb_rate", 0.0) - _f(favorite_row, "def_reb_rate", 0.0) + 0.05) / 0.15)
    turnover_machine = 1.0 if (_i(underdog_row, "steal_rate_rank", 999) <= 40 and _i(underdog_row, "forced_to_rate_rank", 999) <= 40) else 0.0
    experienced_pressure = 1.0 if _i(underdog_row, "players_10plus_tourney_minutes_positive_pm", 0) >= 3 else 0.0
    long_range_heat = 1.0 if _i(underdog_row, "high_volume_38pct_3pt_shooters", 0) >= 2 else 0.0
    coach_specialist = max(0.0, (_f(underdog_row, "coach_dog_ats_score", 0.50) - 0.50) / 0.25)
    kenpom_underrated = max(0.0, (_i(underdog_row, "seed", 16) - _i(underdog_row, "kenpom_equivalent_seed", 16) - 1) / 10.0)

    parts = {
        "DEFENSE_OVER_OFFENSE_GAP": _clip01(defense_over_offense_gap),
        "SLOW_IT_DOWN_CAPACITY": _clip01(slow_it_down),
        "FREE_THROW_DISPARITY": _clip01(ft_disparity),
        "3PT_DEFENSE_VS_3PT_OFFENSE_MISMATCH": three_pt_def_mismatch,
        "REBOUNDING_PARITY_OR_EDGE": _clip01(rebounding_edge),
        "TURNOVER_FORCING_MACHINE": turnover_machine,
        "EXPERIENCED_UNDER_PRESSURE": experienced_pressure,
        "LONG_RANGE_HEAT_CHECK_RISK": long_range_heat,
        "COACH_UNDERDOG_SPECIALIST": _clip01(coach_specialist),
        "KenPom_UNDERRATED": _clip01(kenpom_underrated),
    }
    weights = {
        "DEFENSE_OVER_OFFENSE_GAP": 1.1,
        "SLOW_IT_DOWN_CAPACITY": 1.35,
        "FREE_THROW_DISPARITY": 1.15,
        "3PT_DEFENSE_VS_3PT_OFFENSE_MISMATCH": 0.8,
        "REBOUNDING_PARITY_OR_EDGE": 0.9,
        "TURNOVER_FORCING_MACHINE": 1.0,
        "EXPERIENCED_UNDER_PRESSURE": 0.85,
        "LONG_RANGE_HEAT_CHECK_RISK": 0.75,
        "COACH_UNDERDOG_SPECIALIST": 0.8,
        "KenPom_UNDERRATED": 1.2,
    }
    dog_dna_score = _clip01(_weighted_mean(parts, weights))
    spread_abs = abs(float(spread))
    base_upset = _clip01(0.15 + min(spread_abs, 14.0) * 0.015)
    round_adj = ROUND_ADJUSTMENTS.get(round_name, 1.0)
    upset_probability = _clip01(base_upset * (0.75 + dog_dna_score) * round_adj)
    probability_cap_triggered = False
    if _i(underdog_row, "auto_bid", 0) == 1 and _i(favorite_row, "power_program_flag", 0) == 1 and (_i(underdog_row, "seed", 16) - _i(favorite_row, "seed", 1)) >= 12:
        upset_probability = min(upset_probability, 0.30)
        probability_cap_triggered = True

    drivers: List[str] = [k for k, v in parts.items() if v >= 0.6]
    market_edge_vs_spread = float((upset_probability - base_upset) * spread_abs)
    return {
        "dog_dna_score": dog_dna_score,
        "upset_probability": upset_probability,
        "upset_archetype_label": _upset_label(upset_probability),
        "key_upset_drivers": drivers,
        "market_edge_vs_spread": market_edge_vs_spread,
        "round": round_name,
        "upset_probability_cap_triggered": probability_cap_triggered,
    }


def evaluate_underdog_matchups(matchups: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in matchups:
        out.append(
            upset_probability_model(
                m.get("underdog", {}),
                m.get("favorite", {}),
                float(m.get("spread", 0.0)),
                str(m.get("round", "NCAA_R64")),
            )
        )
    return out


def validate_against_closing_line(seasons: Iterable[int] = range(2015, 2025), games_df: pd.DataFrame | None = None) -> Dict[str, Any]:
    if games_df is None or games_df.empty:
        return {"seasons": list(seasons), "sample_size": 0, "clv_delta": 0.0, "upset_brier": 0.0}
    df = games_df.copy()
    if "upset_probability" not in df.columns or "upset_won" not in df.columns:
        return {"seasons": list(seasons), "sample_size": len(df), "clv_delta": 0.0, "upset_brier": 0.0}
    p = df["upset_probability"].astype(float).clip(0, 1)
    y = df["upset_won"].astype(float).clip(0, 1)
    brier = ((p - y) ** 2).mean()
    clv = (df.get("closing_spread", pd.Series([0.0] * len(df))) - df.get("open_spread", pd.Series([0.0] * len(df)))).astype(float).mean()
    return {"seasons": list(seasons), "sample_size": len(df), "clv_delta": float(clv), "upset_brier": float(brier)}
