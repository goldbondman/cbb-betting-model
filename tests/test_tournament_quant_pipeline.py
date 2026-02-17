import pandas as pd

from ml.quant1_archeologist import team_archetype_profile, validate_against_closing_line as q1_validate
from ml.quant2_upset_hunter import upset_probability_model
from ml.quant3_executioner import favorite_fragility_index
from ml.quant4_situationist import situational_edge
from ml.quant5_mathematician import (
    contrarian_value_finder,
    max_correlated_exposure,
    validate_against_closing_line as q5_validate,
)
from ml.tournament_contract import build_tournament_game
from ml.tournament_quant_pipeline import run_team_execution_order


def _team(name: str, seed: int, kenpom: int, **extra):
    row = {"name": name, "team": name, "seed": seed, "kenpom_rank": kenpom}
    row.update(extra)
    return row


def test_shared_contract_and_pipeline_output_shape():
    game = run_team_execution_order(
        game_id="g1",
        team_a=_team("Favorite U", 5, 20, adj_def_rank=28, off_eff_rank=25, def_eff_rank=20),
        team_b=_team(
            "Dog State",
            12,
            38,
            adj_def_rank=12,
            off_eff_rank=85,
            def_eff_rank=18,
            pace_rank=300,
            players_with_tourney_game=4,
            guard_scoring_assist_share=0.62,
        ),
        base_model_output={"spread": -7.5, "win_prob_a": 0.68, "round": "NCAA_R64"},
    )
    assert game["game_id"] == "g1"
    assert "q1_archetype" in game and "q5_final" in game
    assert "team_b_profile" in game["q1_archetype"]
    assert "upset_prob" in game["q2_upset"]
    assert "recommended_bet" in game["q5_final"]
    assert "tournament_structure" in game
    assert "selection_committee" in game
    assert "seed_historical_angles" in game
    assert "geography_region" in game
    assert "round_1_style_clash" in game


def test_quant_modules_return_required_fields():
    favorite = _team("Blueblood", 2, 9, turnover_rate_rank=150, three_pt_def_rank=95, two_pt_pct_rank=15)
    dog = _team("MidMajor", 15, 55, pace_rank=310, off_eff_rank=95, def_eff_rank=22, three_pt_def_rank=18, three_pt_off_rank=12)

    q1 = team_archetype_profile(dog)
    assert "winning_archetypes" in q1 and "failure_archetypes" in q1

    q2 = upset_probability_model(dog, favorite, spread=8.5)
    assert {
        "dog_dna_score",
        "upset_probability",
        "key_upset_drivers",
        "upset_probability_cap_triggered",
    }.issubset(q2.keys())

    q3 = favorite_fragility_index(favorite, dog)
    assert {"fragility_index", "early_exit_probability", "recommended_action"}.issubset(q3.keys())

    q4 = situational_edge(dog, favorite, {"tipoff_local_hour": 12, "site_altitude_ft": 5000})
    assert {"situational_adjustment_points", "motivation_flag", "contextual_risk_flags"}.issubset(q4.keys())


def test_upset_probability_cap_for_auto_bid_vs_power():
    dog = _team("AutoBid", 16, 110, auto_bid=1)
    fav = _team("Blueblood", 1, 5, power_program_flag=1)
    q2 = upset_probability_model(dog, fav, spread=26.0, round_name="NCAA_R64")
    assert q2["upset_probability_cap_triggered"] is True
    assert q2["upset_probability"] <= 0.30


def test_validation_hooks_and_portfolio_helpers():
    games = pd.DataFrame(
        {
            "our_spread": [-5.0, 3.5],
            "closing_spread": [-4.0, 2.5],
            "ats_result": [1.0, -1.0],
            "bet_spread": [-5.0, 3.5],
            "unit_pnl": [0.8, -1.0],
            "correlation_group": ["East", "East"],
            "kelly_recommended": [0.10, 0.10],
            "public_bet_pct": [0.70, 0.40],
            "model_edge_points": [3.4, 1.2],
            "game_id": ["g1", "g2"],
            "recommended_bet": ["A +5", "B +3.5"],
            "composite_edge": [2.2, 0.8],
        }
    )
    assert "clv_delta" in q1_validate(games_df=games)
    assert "roi" in q5_validate(games_df=games)

    capped = max_correlated_exposure(games, max_exposure=0.15)
    assert capped["kelly_recommended"].sum() <= 0.15 + 1e-6

    contra = contrarian_value_finder(games)
    assert len(contra) == 1


def test_contract_builder_minimal():
    game = build_tournament_game(
        "gid",
        {"name": "A", "seed": 1, "kenpom_rank": 2},
        {"name": "B", "seed": 16, "kenpom_rank": 120},
        {"spread": -18.5, "win_prob_a": 0.95},
    )
    assert game["team_a"]["name"] == "A"
    assert game["base_model_output"]["spread"] == -18.5


def test_tournament_structure_and_style_fields_populated():
    game = run_team_execution_order(
        game_id="g2",
        team_a=_team("Fav", 1, 4, pace_rank=20, conference_tournament_bye_rounds=2, distance_to_site_miles=900, power_program_flag=1),
        team_b=_team(
            "Dog",
            16,
            120,
            pace_rank=320,
            conference_tournament_bye_rounds=0,
            days_since_last_game=5,
            played_first_four=1,
            nit_rejection_disappointment=0.8,
            program_tier="blue_blood",
            auto_bid=1,
            kenpom_equivalent_seed=12,
            distance_to_site_miles=120,
        ),
        base_model_output={
            "spread": -22.0,
            "win_prob_a": 0.95,
            "round": "NCAA_FIRST_FOUR",
            "is_first_four": 1,
            "site_type": "neutral",
            "venue_upset_rate_10y": 0.14,
            "weather_region_mismatch_flag": 1,
        },
    )
    assert game["tournament_structure"]["first_four_short_rest_flag"] == 1
    assert game["seed_historical_angles"]["upset_probability_cap_triggered"] == 1
    assert game["geography_region"]["team_b_proximity_score"] > 0.5
    assert game["round_1_style_clash"]["style_clash_tag"] == "SLOW_VS_TRANSITION"
