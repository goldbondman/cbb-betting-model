#!/usr/bin/env python3
"""
Tournament totals projection helpers for full game and halves.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class TotalsBetRules:
    min_edge_full_game: float = 1.5
    min_edge_first_half: float = 1.0
    min_edge_second_half_live: float = 1.5
    max_bet_first_half: float = 0.6
    max_bet_full_game: float = 1.0
    max_bet_second_half_live: float = 0.75
    timing_full_game: str = "open_to_3hrs_before"
    timing_first_half: str = "open_to_1hr_before"
    timing_second_half: str = "within_4min_of_halftime"
    avoid_high_variance_teams: bool = True


_ROUND_DEFLATOR = {
    "opening": -0.015,
    "quarterfinal": -0.02,
    "semifinal": -0.025,
    "final": -0.03,
    "round_of_64": -0.02,
    "round_of_32": -0.025,
    "sweet_16": -0.03,
    "elite_8": -0.032,
    "final_4": -0.035,
    "championship": -0.04,
}

_TIER_DEFLATOR = {"power": -0.01, "mid_major": -0.015, "group_of_5": -0.02}
GAME_SECONDS = 2400.0
MIN_SECONDS_PER_POSSESSION = 30.0
CONFIDENCE_INTERVAL_HALF_WIDTH = 6.0


def tournament_deflator(round_name: str, conference_tier: str) -> float:
    return _ROUND_DEFLATOR.get(str(round_name).lower(), -0.02) + _TIER_DEFLATOR.get(str(conference_tier).lower(), -0.01)


def predict_possessions(
    team_a_pace: float,
    team_b_pace: float,
    elimination_pace_suppressor: float = 0.0,
    fatigue_pace_adjustment: float = 0.0,
    foul_rate_pace_suppressor: float = 0.0,
) -> Tuple[float, Tuple[int, int]]:
    base = (float(team_a_pace) + float(team_b_pace)) / 2.0
    projected = max(55.0, base + elimination_pace_suppressor + fatigue_pace_adjustment + foul_rate_pace_suppressor)
    span = 4.0 if projected < 68 else 5.0
    return projected, (int(round(projected - span)), int(round(projected + span)))


def project_ppp(
    offensive_ppp: float,
    defensive_ppp_allowed: float,
    global_deflator: float,
    foul_mismatch_premium: float = 0.0,
) -> float:
    base = (float(offensive_ppp) + float(defensive_ppp_allowed)) / 2.0
    return max(0.75, base + float(global_deflator) + float(foul_mismatch_premium) / 100.0)


def project_totals_contract(game: Dict[str, float | str]) -> Dict[str, object]:
    team_a = str(game["team_a"])
    team_b = str(game["team_b"])
    venue_adj = float(game.get("venue_scoring_profile", 0.0))
    spread = float(game.get("spread", 0.0))
    market_full = float(game.get("market_total", 0.0))
    market_1h = float(game.get("market_first_half", market_full / 2.0))
    market_2h = float(game.get("market_second_half", market_full / 2.0))

    deflator = tournament_deflator(str(game.get("round", "")), str(game.get("conference_tier", "power")))
    poss, poss_range = predict_possessions(
        float(game.get("team_a_pace", 68.0)),
        float(game.get("team_b_pace", 68.0)),
        float(game.get("elimination_pace_suppressor", -1.5)),
        float(game.get("fatigue_pace_adjustment", 0.0)),
        float(game.get("foul_rate_pace_suppressor", -0.5)),
    )
    team_a_ppp = project_ppp(
        float(game.get("team_a_off_ppp", 1.02)),
        float(game.get("team_b_def_ppp", 0.98)),
        deflator,
        float(game.get("foul_mismatch_premium_a", 0.0)),
    )
    team_b_ppp = project_ppp(
        float(game.get("team_b_off_ppp", 1.0)),
        float(game.get("team_a_def_ppp", 0.97)),
        deflator,
        float(game.get("foul_mismatch_premium_b", 0.0)),
    )

    full_projection = (team_a_ppp + team_b_ppp) * poss + venue_adj
    first_half_projection = full_projection * 0.485 + float(game.get("first_half_nerves_discount", -1.0))
    second_half_projection = full_projection - first_half_projection + float(game.get("halftime_lead_pace_modifier", 0.0))
    confidence_interval = [
        int(round(full_projection - CONFIDENCE_INTERVAL_HALF_WIDTH)),
        int(round(full_projection + CONFIDENCE_INTERVAL_HALF_WIDTH)),
    ]

    return {
        "q2_possessions": {
            "predicted_possessions": round(poss, 1),
            "possession_range_80pct": [poss_range[0], poss_range[1]],
            "pace_battle_winner": team_a if float(game.get("team_a_pace_control_rate", 0.5)) >= 0.5 else team_b,
            "overtime_probability": round(float(game.get("overtime_probability", 0.06)), 3),
            "fatigue_pace_adjustment": float(game.get("fatigue_pace_adjustment", 0.0)),
            "shot_clock_utilization_projection": round(
                GAME_SECONDS / max(MIN_SECONDS_PER_POSSESSION, (GAME_SECONDS / poss)),
                1,
            ),
        },
        "q3_efficiency": {
            "team_a_ppp": round(team_a_ppp, 3),
            "team_b_ppp": round(team_b_ppp, 3),
            "tournament_deflator": round(deflator, 3),
            "foul_mismatch_premium": f"+{round(float(game.get('foul_mismatch_premium_a', 0.0)) + float(game.get('foul_mismatch_premium_b', 0.0)), 1)}pts",
        },
        "q4_halves": {
            "first_half_projection": round(first_half_projection, 1),
            "first_half_market": market_1h,
            "first_half_edge": round(first_half_projection - market_1h, 1),
            "second_half_projection_pregame": round(second_half_projection, 1),
            "second_half_market_pregame": market_2h,
            "second_half_edge": round(second_half_projection - market_2h, 1),
            "regression_flags": ["shooting_luck_correction"] if float(game.get("first_half_shooting_delta", 0.0)) >= 0.07 else [],
            "foul_situation_triggers": ["star_foul_trouble_under"] if bool(game.get("star_in_foul_trouble", False)) else [],
        },
        "q5_final": {
            "full_game_projection": round(full_projection, 1),
            "full_game_market": market_full,
            "full_game_edge": round(full_projection - market_full, 1),
            "confidence_interval_80pct": confidence_interval,
            "spread_implied_total_adjustment": round(0.4 if abs(spread) >= 12 else (-0.4 if abs(spread) < 4 else 0.0), 1),
        },
    }
