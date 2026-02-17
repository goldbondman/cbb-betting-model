import unittest

from ml.conf_tournament_model import (
    ConfTourneyBetRules,
    build_conf_bet_card,
    build_conf_tourney_composite_edge,
    conference_tier,
)


class TestConfTournamentModel(unittest.TestCase):
    def test_conference_tier_mapping(self):
        self.assertEqual(conference_tier("Big Ten"), "power6")
        self.assertEqual(conference_tier(" big ten "), "power6")
        self.assertEqual(conference_tier("A-10"), "high_major_mid_major")
        self.assertEqual(conference_tier("MVC"), "true_mid_major")
        self.assertEqual(conference_tier("Big Sky"), "low_major")

    def test_composite_edge_respects_clv_gate(self):
        edge = build_conf_tourney_composite_edge(
            archetype_alignment_conf=2.0,
            conf_upset_dog_dna_score=2.0,
            conf_fragility_index=2.0,
            situational_adjustment_conf=2.0,
            third_meeting_adjustment=2.0,
            load_management_probability=2.0,
            market_implied_edge=0.5,
            clv_validation_years=4,
        )
        self.assertEqual(edge, 0.0)

    def test_build_conf_bet_card_contract(self):
        game = {
            "game_id": "g-1",
            "conference": "Big Ten",
            "tournament_round": "Quarterfinal",
            "game_number_in_tournament": {"team_a": 2, "team_b": 1},
            "hours_rest": {"team_a": 42, "team_b": 66},
            "team_a": {
                "team": "Team A",
                "seed": 3,
                "kenpom_rank": 18,
                "load_management_risk": 0.61,
                "bracket_motivation_score": 0.3,
            },
            "team_b": {
                "team": "Team B",
                "seed": 6,
                "kenpom_rank": 31,
                "motivation_matrix_label": "SEASON_ON_THE_LINE",
                "motivation_clarity_score": 9,
                "revenge_score": 0.78,
            },
            "archetype_alignment_conf": 3.2,
            "dog_dna_score_conf": 0.81,
            "third_meeting_adjustment": 0.4,
            "fatigue_differential": 0.6,
            "market_implied_edge": 0.2,
            "market_spread": -6.5,
            "situational_adjustment_points": 2.4,
            "motivation_asymmetry": "HIGH",
            "crowd_advantage_label": "team_b +1.8pts",
            "best_book": "DraftKings",
            "correlation_group": "Big_Ten_Top_Half",
        }

        card = build_conf_bet_card(game, rules=ConfTourneyBetRules(min_edge_power6=0.1))
        self.assertEqual(card["game_id"], "g-1")
        self.assertIn("q1_archetypes", card)
        self.assertIn("q2_upset", card)
        self.assertIn("q3_fragility", card)
        self.assertIn("q4_situational", card)
        self.assertIn("q5_final", card)
        self.assertIn("composite_edge", card["q5_final"])
        self.assertEqual(card["q5_final"]["best_book"], "DraftKings")
        self.assertEqual(card["q5_final"]["recommended_bet"], "Team B +6.5")


if __name__ == "__main__":
    unittest.main()
