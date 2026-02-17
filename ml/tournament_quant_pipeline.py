#!/usr/bin/env python3
"""Tournament quant execution order and unified output assembly."""

from __future__ import annotations

from typing import Any, Dict, Mapping

from ml.quant1_archeologist import team_archetype_profile
from ml.quant2_upset_hunter import is_auto_bid_power_mismatch, upset_probability_model
from ml.quant3_executioner import favorite_fragility_index
from ml.quant4_situationist import program_tier_disappointment_multiplier, situational_edge
from ml.quant5_mathematician import build_tournament_bet_card_row, composite_edge_score
from ml.tournament_contract import build_tournament_game


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


def _style_clash_label(team_x: Mapping[str, Any], team_y: Mapping[str, Any]) -> str:
    pace_gap = abs(_i(team_x, "pace_rank", 175) - _i(team_y, "pace_rank", 175))
    if pace_gap >= 120:
        return "SLOW_VS_TRANSITION"
    if _i(team_x, "princeton_offense_flag", 0) == 1 or _i(team_y, "princeton_offense_flag", 0) == 1:
        return "PRINCETON_SYSTEM_CLASH"
    if _i(team_x, "pack_line_defense_flag", 0) == 1 or _i(team_y, "pack_line_defense_flag", 0) == 1:
        return "PACK_LINE_CLASH"
    return "STYLE_BALANCED"


def run_team_execution_order(
    game_id: str,
    team_a: Mapping[str, Any],
    team_b: Mapping[str, Any],
    base_model_output: Mapping[str, Any],
    market_implied_edge: float = 0.0,
    weights: Mapping[str, float] | None = None,
) -> Dict[str, Any]:
    shared = build_tournament_game(game_id, dict(team_a), dict(team_b), dict(base_model_output))
    # Quant 1
    shared["q1_archetype"] = {
        "team_a_profile": team_archetype_profile(team_a),
        "team_b_profile": team_archetype_profile(team_b),
    }
    # Quant 2 (assume team_b is underdog if spread negative for team_a)
    spread = float(base_model_output.get("spread", 0.0))
    underdog, favorite = (team_b, team_a) if spread < 0 else (team_a, team_b)
    underdog_line = abs(spread)
    q2 = upset_probability_model(underdog, favorite, spread=spread, round_name=str(base_model_output.get("round", "NCAA_R64")))
    shared["q2_upset"] = {
        "dog_dna_score": q2["dog_dna_score"],
        "upset_prob": q2["upset_probability"],
        "upset_drivers": q2["key_upset_drivers"],
    }
    # Quant 3
    q3 = favorite_fragility_index(favorite, underdog)
    shared["q3_fragility"] = {
        "fragility_index": q3["fragility_index"],
        "brand_tax": q3["brand_tax_points"],
        "early_exit_prob": q3["early_exit_probability"],
        "fragility_drivers": q3["fragility_drivers"],
        "recommended_action": q3["recommended_action"],
    }
    # Quant 4
    q4 = situational_edge(underdog, favorite, base_model_output)
    shared["q4_situational"] = {
        "situational_adjustment": q4["situational_adjustment_points"],
        "motivation_flag": q4["motivation_flag"],
        "risk_flags": q4["contextual_risk_flags"],
    }
    # Quant 5
    w = dict(weights or {"w1": 0.25, "w2": 0.25, "w3": 0.25, "w4": 0.25})
    comp_edge = composite_edge_score(
        archetype_alignment_score=float(shared["q1_archetype"]["team_b_profile"]["winning_archetypes"]["ELITE_DEFENSE_WINS"]["confidence"]),
        upset_dog_dna_score=float(q2["dog_dna_score"]),
        favorite_fragility_index=float(q3["fragility_index"]),
        situational_adjustment_points=float(q4["situational_adjustment_points"]),
        market_implied_edge=float(market_implied_edge),
        weights=w,
    )
    q5_row = build_tournament_bet_card_row(
        game={"game_id": game_id, "recommended_bet": f"{underdog.get('name', 'Underdog')} +{underdog_line:.1f}"},
        composite_edge=comp_edge,
        fair_prob=max(0.01, min(0.99, 1 - float(base_model_output.get("win_prob_a", 0.5)))),
        market_odds=float(base_model_output.get("market_odds", -110)),
        correlation_group=str(base_model_output.get("correlation_group", "Unknown_Region")),
        best_book=str(base_model_output.get("best_book", "Pinnacle")),
        line_shopping_value=float(base_model_output.get("line_shopping_value", 0.0)),
    )
    shared["q5_final"] = {
        "composite_edge": q5_row["composite_edge"],
        "recommended_bet": q5_row["recommended_bet"],
        "kelly_recommended": q5_row["kelly_recommended"],
        "confidence_tier": q5_row["confidence_tier"],
        "timing": q5_row["timing"],
        "best_book": q5_row["best_book"],
        "correlation_group": q5_row["correlation_group"],
    }
    round_name = str(base_model_output.get("round", "NCAA_R64"))
    is_first_four = _i(base_model_output, "is_first_four", 0) == 1 or round_name == "NCAA_FIRST_FOUR"
    team_b_byes = _i(team_b, "conference_tournament_bye_rounds", 0)
    team_a_byes = _i(team_a, "conference_tournament_bye_rounds", 0)
    bye_rest_advantage = _clip((team_b_byes - team_a_byes) * 0.4, -1.5, 1.5)
    rhythm_penalty = _clip((_f(team_b, "days_since_last_game", 2.0) - 4.0) * 0.35, 0.0, 1.2)
    shared["tournament_structure"] = {
        "bye_rest_advantage_points": round(bye_rest_advantage - rhythm_penalty, 3),
        "site_type": str(base_model_output.get("site_type", "neutral")),
        "campus_site_flag": _i(base_model_output, "campus_site_flag", 0),
        "first_four_short_rest_flag": int(is_first_four and _i(team_b, "played_first_four", 0) == 1),
        "nit_demoralization_score": round(
            _clip(
                _f(team_b, "nit_rejection_disappointment", 0.0)
                * program_tier_disappointment_multiplier(team_b),
                0.0,
                1.0,
            ),
            3,
        ),
    }
    shared["selection_committee"] = {
        "conference_seed_bias": round(_f(underdog, "conference_seed_bias_10y", 0.0), 3),
        "recent_form_seed_delta": round(_f(underdog, "recent_form_seed_delta", 0.0), 3),
        "brand_protection_flag": _i(base_model_output, "brand_protection_flag", 0),
        "defense_undervalued_flag": int(_i(underdog, "adj_def_rank", 999) <= 30 and _i(underdog, "seed", 16) >= 8),
    }
    shared["seed_historical_angles"] = {
        "seed_matchup": f"{_i(favorite, 'seed', 1)}v{_i(underdog, 'seed', 16)}",
        "rolling_12v5_clv_delta": round(_f(base_model_output, "rolling_12v5_clv_delta", 0.0), 3),
        "eleven_seed_type": str(underdog.get("seed_entry_type", "unknown")),
        "seed_vs_efficiency_gap": _i(underdog, "seed", 16) - _i(underdog, "kenpom_equivalent_seed", _i(underdog, "seed", 16)),
        "r1_one_seed_cover_base_rate": round(_f(base_model_output, "r1_one_seed_cover_base_rate", 0.0), 3),
        "upset_probability_cap_triggered": int(is_auto_bid_power_mismatch(underdog, favorite)),
    }
    proximity_edge = _clip((_f(team_a, "distance_to_site_miles", 500.0) - _f(team_b, "distance_to_site_miles", 500.0)) / 350.0, -2.0, 2.0)
    shared["geography_region"] = {
        "team_b_proximity_score": round(_clip(1.0 - _f(team_b, "distance_to_site_miles", 500.0) / 1200.0, 0.0, 1.0), 3),
        "crowd_advantage_points": round(proximity_edge, 3),
        "venue_upset_rate_10y": round(_f(base_model_output, "venue_upset_rate_10y", 0.0), 3),
        "weather_region_mismatch_flag": _i(base_model_output, "weather_region_mismatch_flag", 0),
    }
    shared["round_1_style_clash"] = {
        "style_clash_tag": _style_clash_label(favorite, underdog),
        "slow_vs_fast_conference_flag": int(
            abs(_i(favorite, "pace_rank", 175) - _i(underdog, "pace_rank", 175)) >= 100
            and str(round_name).startswith("NCAA_R")
        ),
        "round_1_style_clash_database_tag": str(base_model_output.get("round_1_style_clash_database_tag", "")),
    }
    return shared
