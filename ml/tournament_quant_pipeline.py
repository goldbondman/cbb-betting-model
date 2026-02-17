#!/usr/bin/env python3
"""Tournament quant execution order and unified output assembly."""

from __future__ import annotations

from typing import Any, Dict, Mapping

from ml.quant1_archeologist import team_archetype_profile
from ml.quant2_upset_hunter import upset_probability_model
from ml.quant3_executioner import favorite_fragility_index
from ml.quant4_situationist import situational_edge
from ml.quant5_mathematician import build_tournament_bet_card_row, composite_edge_score
from ml.tournament_contract import build_tournament_game


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
    return shared
