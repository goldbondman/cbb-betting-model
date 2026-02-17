from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional


POWER_6 = {"acc", "big ten", "big 12", "sec", "big east", "pac-12"}
HIGH_MAJOR_MID = {"a-10", "mwc", "wcc"}
TRUE_MID = {"mvc", "caa", "mac"}


@dataclass(frozen=True)
class ConfTourneyBetRules:
    max_bet_early_rounds: float = 0.5
    max_bet_semifinals: float = 0.75
    max_bet_finals: float = 1.0
    bet_timing_early_rounds: str = "open_to_12hrs_before"
    bet_timing_semifinals: str = "open_to_6hrs_before"
    load_management_alert_override: bool = True
    min_edge_power6: float = 2.0
    min_edge_midmajor: float = 1.5
    min_edge_lowmajor: float = 1.0


def conference_tier(conference: str) -> str:
    key = (conference or "").strip().lower()
    if key in POWER_6:
        return "power6"
    if key in HIGH_MAJOR_MID:
        return "high_major_mid_major"
    if key in TRUE_MID:
        return "true_mid_major"
    return "low_major"


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _clv_gate(value: float, clv_validation_years: int) -> float:
    return float(value) if int(clv_validation_years) >= 5 else 0.0


def build_conf_tourney_composite_edge(
    *,
    archetype_alignment_conf: float,
    conf_upset_dog_dna_score: float,
    conf_fragility_index: float,
    situational_adjustment_conf: float,
    third_meeting_adjustment: float,
    load_management_probability: float,
    market_implied_edge: float,
    clv_validation_years: int = 5,
    weights: Optional[Mapping[str, float]] = None,
) -> float:
    default_weights = {
        "w1": 0.15,
        "w2": 0.13,
        "w3": 0.16,
        "w4": 0.20,
        "w5": 0.12,
        "w6": 0.24,
    }
    w = dict(weights or default_weights)

    clv_years = int(clv_validation_years)
    w_sum = sum(w.values()) or 1.0
    normalized = {k: v / w_sum for k, v in w.items()}

    model_edge = (
        normalized["w1"] * _clv_gate(archetype_alignment_conf, clv_years)
        + normalized["w2"] * _clv_gate(conf_upset_dog_dna_score, clv_years)
        + normalized["w3"] * _clv_gate(conf_fragility_index, clv_years)
        + normalized["w4"] * _clv_gate(situational_adjustment_conf, clv_years)
        + normalized["w5"] * _clv_gate(third_meeting_adjustment, clv_years)
        + normalized["w6"] * _clv_gate(load_management_probability, clv_years)
    )
    return float(model_edge - market_implied_edge)


def build_conf_tourney_archetype_profile(
    team: Mapping[str, Any],
    conference: str,
    conference_base_rates: Optional[Mapping[str, float]] = None,
) -> Dict[str, Any]:
    revenge_score = _clip01(team.get("revenge_score", 0.0))
    coach_alpha = float(team.get("conf_tourney_coach_alpha", 0.0))
    motivation = max(1, min(10, int(team.get("motivation_clarity_score", 5))))
    second_meeting = _clip01(team.get("second_meeting_adjuster", 0.0))
    depth_adv = float(team.get("depth_vs_field_advantage", 0.0))
    road_warrior = _clip01(team.get("neutral_site_road_warrior", 0.0))
    load_mgmt = _clip01(team.get("load_management_risk_score", 0.0))

    winning: List[str] = []
    if revenge_score >= 0.65:
        winning.append("REVENGE_MACHINE")
    if coach_alpha > 0:
        winning.append("CONF_TOURNEY_SPECIALIST")
    if motivation >= 8:
        winning.append("MOTIVATION_CLARITY")
    if second_meeting >= 0.55:
        winning.append("SECOND_MEETING_ADJUSTER")
    if depth_adv >= 1.0:
        winning.append("DEPTH_ADVANTAGE_FORMAT")
    if road_warrior >= 0.6:
        winning.append("NEUTRAL_SITE_ROAD_WARRIOR")

    failure: List[str] = []
    if load_mgmt >= 0.55:
        failure.append("STAR_PRESERVATION_MODE")
    if _clip01(team.get("first_place_hangover", 0.0)) >= 0.5:
        failure.append("FIRST_PLACE_HANGOVER")
    if _clip01(team.get("familiarity_exploited", 0.0)) >= 0.5:
        failure.append("FAMILIARITY_EXPLOITED")
    if _clip01(team.get("bubble_paralysis", 0.0)) >= 0.5:
        failure.append("BUBBLE_PARALYSIS")
    if int(team.get("first_year_transfers", 0)) >= 4:
        failure.append("TRANSFER_PORTAL_CHAOS")
    if _clip01(team.get("conf_tourney_exhausted", 0.0)) >= 0.5:
        failure.append("CONFERENCE_TOURNEY_EXHAUSTED")

    projected_rounds_survived = {
        "0": round(_clip01(0.35 - depth_adv * 0.03 + load_mgmt * 0.15), 3),
        "1": round(_clip01(0.3 + motivation * 0.02), 3),
        "2": round(_clip01(0.22 + coach_alpha * 0.05), 3),
        "3_plus": round(_clip01(0.12 + max(depth_adv, 0.0) * 0.04 + road_warrior * 0.08), 3),
    }

    return {
        "team": team.get("team"),
        "conference": conference,
        "winning_archetypes": winning,
        "failure_archetypes": failure,
        "revenge_score": revenge_score,
        "conf_tourney_coach_alpha": coach_alpha,
        "motivation_clarity_score": motivation,
        "second_meeting_adjuster": second_meeting,
        "depth_vs_field_advantage": depth_adv,
        "neutral_site_road_warrior": road_warrior,
        "load_management_risk_score": load_mgmt,
        "historical_base_rates": dict(conference_base_rates or {}),
        "projected_rounds_survived": projected_rounds_survived,
    }


def build_conf_upset_view(game: Mapping[str, Any]) -> Dict[str, Any]:
    dog_dna = _clip01(game.get("dog_dna_score_conf", 0.5))
    third_meeting = float(game.get("third_meeting_adjustment", 0.0))
    fatigue = float(game.get("fatigue_differential", 0.0))
    upset_prob = _clip01(0.25 + 0.35 * dog_dna + 0.08 * third_meeting + 0.04 * fatigue)

    drivers = []
    if third_meeting > 0:
        drivers.append("third_meeting_motivation_asymmetry")
    if fatigue > 0:
        drivers.append("fatigue_differential")
    if game.get("press_break_mismatch"):
        drivers.append("PRESS_BREAK_OR_DIE")
    if game.get("pace_hijack_signal"):
        drivers.append("PACE_HIJACK")
    if game.get("zone_surprise_signal"):
        drivers.append("ZONE_SURPRISE_VALUE")

    return {
        "conf_upset_probability": upset_prob,
        "dog_dna_score_conf": dog_dna,
        "third_meeting_adjustment": third_meeting,
        "fatigue_differential": fatigue,
        "upset_archetype": "FATIGUE_BASED_UPSET" if fatigue > third_meeting else "THIRD_MEETING_EDGE",
        "key_upset_drivers": drivers,
        "market_edge": float(game.get("market_edge", 0.0)),
        "conference_tier_adjustment": conference_tier(game.get("conference", "")),
    }


def build_conf_fragility_view(team: Mapping[str, Any], game: Mapping[str, Any]) -> Dict[str, Any]:
    load_mgmt = _clip01(team.get("load_management_risk", team.get("load_management_probability", 0.0)))
    game3_wall = _clip01(team.get("game_3_fatigue_wall_score", 0.0))
    dependency = _clip01(team.get("key_player_dependency_score", 0.0))
    bracket_motivation = _clip01(team.get("bracket_motivation_score", 0.5))

    fragility = _clip01(0.45 * load_mgmt + 0.25 * game3_wall + 0.2 * dependency + 0.1 * (1 - bracket_motivation))
    drivers = [
        d
        for d, on in {
            "LOAD_MANAGEMENT_EXPOSURE": load_mgmt >= 0.5,
            "THIRD-GAME-IN-FOUR-DAYS_WALL": game3_wall >= 0.5,
            "KEY_PLAYER_DEPENDENCY": dependency >= 0.5,
            "BRACKET_KNOWLEDGE_TRAP": bracket_motivation <= 0.4,
        }.items()
        if on
    ]

    return {
        "conf_fragility_index": fragility,
        "load_management_probability": load_mgmt,
        "bracket_motivation_score": bracket_motivation,
        "game_day_fatigue_level": float(game.get("game_day_fatigue_level", game3_wall)),
        "fragility_drivers": drivers,
        "recommended_action": "fade_favorite" if fragility >= 0.55 else "no_fade",
        "live_betting_watch_flag": bool(fragility >= 0.45 or dependency >= 0.55),
    }


def build_conf_situational_view(team: Mapping[str, Any], game: Mapping[str, Any]) -> Dict[str, Any]:
    motivation = team.get("motivation_matrix_label", "LOCKED_IN_COASTING")
    situational_adj = float(team.get("situational_adjustment_points", 0.0))
    distraction_flags = list(team.get("distraction_flags", []))

    return {
        "motivation_matrix_label": motivation,
        "situational_adjustment_points": situational_adj,
        "distraction_flags": distraction_flags,
        "crowd_advantage_estimate": float(game.get("crowd_advantage_estimate", 0.0)),
        "rest_differential_score": float(game.get("rest_differential_score", 0.0)),
        "officiating_mismatch_score": float(game.get("officiating_mismatch_score", 0.0)),
        "contextual_risk_summary": "HIGH" if distraction_flags else "LOW",
    }


def build_conf_bet_card(game: Mapping[str, Any], rules: ConfTourneyBetRules = ConfTourneyBetRules()) -> Dict[str, Any]:
    team_a = dict(game.get("team_a", {}))
    team_b = dict(game.get("team_b", {}))

    archetype_a = build_conf_tourney_archetype_profile(team_a, game.get("conference", ""), game.get("historical_base_rates", {}))
    archetype_b = build_conf_tourney_archetype_profile(team_b, game.get("conference", ""), game.get("historical_base_rates", {}))

    upset = build_conf_upset_view(
        {
            **game,
            "conference": game.get("conference", ""),
            "dog_dna_score_conf": game.get("dog_dna_score_conf", 0.5),
            "third_meeting_adjustment": game.get("third_meeting_adjustment", 0.0),
            "fatigue_differential": game.get("fatigue_differential", 0.0),
        }
    )
    fragility = build_conf_fragility_view(team_a, game)
    situational = build_conf_situational_view(team_b, game)

    composite_edge = build_conf_tourney_composite_edge(
        archetype_alignment_conf=float(game.get("archetype_alignment_conf", 0.0)),
        conf_upset_dog_dna_score=float(upset["dog_dna_score_conf"]),
        conf_fragility_index=float(fragility["conf_fragility_index"]),
        situational_adjustment_conf=float(situational["situational_adjustment_points"]),
        third_meeting_adjustment=float(upset["third_meeting_adjustment"]),
        load_management_probability=float(fragility["load_management_probability"]),
        market_implied_edge=float(game.get("market_implied_edge", 0.0)),
        clv_validation_years=int(game.get("clv_validation_years", 5)),
    )

    tier = conference_tier(game.get("conference", ""))
    min_edge = {
        "power6": rules.min_edge_power6,
        "high_major_mid_major": rules.min_edge_midmajor,
        "true_mid_major": rules.min_edge_midmajor,
        "low_major": rules.min_edge_lowmajor,
    }[tier]

    recommended_bet = game.get("default_recommended_bet", "pass")
    if composite_edge >= min_edge:
        recommended_bet = f"{team_b.get('team', 'Team B')} +{game.get('market_spread', 0)}"

    round_name = (game.get("tournament_round") or "").lower()
    max_bet = rules.max_bet_early_rounds
    timing = rules.bet_timing_early_rounds
    if "semi" in round_name:
        max_bet = rules.max_bet_semifinals
        timing = rules.bet_timing_semifinals
    elif "final" in round_name:
        max_bet = rules.max_bet_finals
        timing = "open_to_4hrs_before"

    return {
        "game_id": game.get("game_id"),
        "conference": game.get("conference"),
        "tournament_round": game.get("tournament_round"),
        "game_number_in_tournament": game.get("game_number_in_tournament", {}),
        "hours_rest": game.get("hours_rest", {}),
        "team_a": team_a,
        "team_b": team_b,
        "q1_archetypes": {
            "team_a": archetype_a["winning_archetypes"][0] if archetype_a["winning_archetypes"] else "NONE",
            "team_b": archetype_b["winning_archetypes"][0] if archetype_b["winning_archetypes"] else "NONE",
        },
        "q2_upset": {
            "dog_dna_score": upset["dog_dna_score_conf"],
            "upset_prob": upset["conf_upset_probability"],
            "fatigue_differential": game.get("fatigue_differential_label", game.get("fatigue_differential", 0.0)),
        },
        "q3_fragility": {
            "fragility_index": fragility["conf_fragility_index"],
            "load_mgmt_prob": fragility["load_management_probability"],
            "bracket_motivation": "low" if fragility["bracket_motivation_score"] < 0.5 else "high",
        },
        "q4_situational": {
            "motivation_asymmetry": game.get("motivation_asymmetry", "MEDIUM"),
            "coaching_distraction": ",".join(situational["distraction_flags"]) if situational["distraction_flags"] else "NONE",
            "crowd_advantage": game.get("crowd_advantage_label", situational["crowd_advantage_estimate"]),
            "situational_adjustment": situational["situational_adjustment_points"],
        },
        "q5_final": {
            "composite_edge": round(composite_edge, 3),
            "recommended_bet": recommended_bet,
            "kelly_recommended": round(max(0.0, composite_edge / 70.0), 4),
            "confidence_tier": "A" if composite_edge >= min_edge + 1 else "B" if composite_edge >= min_edge else "C",
            "timing": f"bet now — {timing}",
            "best_book": game.get("best_book", "DraftKings"),
            "live_bet_watch": "star foul trouble trigger active" if fragility["live_betting_watch_flag"] else "none",
            "correlation_group": game.get("correlation_group", "UNASSIGNED"),
            "conference_tier_adjustment": tier,
            "max_bet_units": max_bet,
        },
    }
