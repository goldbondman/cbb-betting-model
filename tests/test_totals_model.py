import unittest

from ml.totals_model import TotalsBetRules, project_totals_contract, tournament_deflator


class TestTotalsModel(unittest.TestCase):
    def test_tournament_deflator_round_and_tier(self):
        self.assertLess(tournament_deflator("Semifinal", "power"), 0.0)
        self.assertLess(tournament_deflator("Championship", "group_of_5"), tournament_deflator("Opening", "power"))

    def test_rules_defaults_match_totals_execution(self):
        rules = TotalsBetRules()
        self.assertEqual(rules.min_edge_full_game, 1.5)
        self.assertEqual(rules.min_edge_first_half, 1.0)
        self.assertEqual(rules.min_edge_second_half_live, 1.5)
        self.assertTrue(rules.avoid_high_variance_teams)

    def test_totals_contract_shape(self):
        out = project_totals_contract(
            {
                "team_a": "A",
                "team_b": "B",
                "round": "Semifinal",
                "conference_tier": "power",
                "team_a_pace": 69.0,
                "team_b_pace": 66.0,
                "team_a_off_ppp": 1.04,
                "team_b_off_ppp": 1.01,
                "team_a_def_ppp": 0.96,
                "team_b_def_ppp": 0.98,
                "market_total": 134.5,
                "market_first_half": 65.5,
                "market_second_half": 67.0,
            }
        )
        self.assertIn("q2_possessions", out)
        self.assertIn("q3_efficiency", out)
        self.assertIn("q4_halves", out)
        self.assertIn("q5_final", out)
        self.assertIn("confidence_interval_80pct", out["q5_final"])


if __name__ == "__main__":
    unittest.main()
