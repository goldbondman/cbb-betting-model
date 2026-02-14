"""Tests for core/recursive_prediction_engine.py."""

import unittest
from datetime import datetime
from unittest.mock import MagicMock

import numpy as np
import pandas as pd

from core.recursive_prediction_engine import (
    CBBPredictionModel,
    GameData,
    ModelConfig,
    OpponentBaselineAnalyzer,
    PerformanceVsExpectationAnalyzer,
    RecursivePredictionEngine,
    calculate_four_factors,
    estimate_possessions,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_BOX = {
    "fgm": 28, "fga": 60, "tpm": 7, "tpa": 20,
    "ftm": 12, "fta": 16, "orb": 12, "drb": 25, "tov": 10,
}

_OPP_BOX = {
    "fgm": 25, "fga": 62, "tpm": 5, "tpa": 22,
    "ftm": 13, "fta": 17, "orb": 7, "drb": 27, "tov": 12,
}


def _make_game(
    team="UNC", opponent="Virginia", team_score=75, opp_score=68,
    team_box=None, opp_box=None, date=None, opp_history=None,
):
    return GameData(
        game_id="test",
        date=date or datetime(2024, 1, 10),
        team_name=team,
        opponent_name=opponent,
        team_score=team_score,
        opponent_score=opp_score,
        neutral_site=False,
        team_box=team_box or dict(_SAMPLE_BOX),
        opponent_box=opp_box or dict(_OPP_BOX),
        opponent_history=opp_history or [],
    )


# ---------------------------------------------------------------------------
# Core calculation tests
# ---------------------------------------------------------------------------


class TestEstimatePossessions(unittest.TestCase):
    def test_basic(self):
        poss = estimate_possessions(fga=60, fta=16, orb=12, tov=10, opp_orb=7)
        self.assertGreater(poss, 0)
        # fga + 0.475*fta - orb + tov + opp_orb*0.33
        expected = 60 + 0.475 * 16 - 12 + 10 + 7 * 0.33
        self.assertAlmostEqual(poss, expected)

    def test_zero_inputs(self):
        poss = estimate_possessions(0, 0, 0, 0, 0)
        self.assertEqual(poss, 0.0)


class TestCalculateFourFactors(unittest.TestCase):
    def test_returns_all_keys(self):
        poss = 70.0
        factors = calculate_four_factors(_SAMPLE_BOX, poss, opp_drb=27, opp_orb=7)
        for key in ("efg", "tov_pct", "orb_pct", "drb_pct", "ftr", "ft_pct"):
            self.assertIn(key, factors)

    def test_zero_poss_uses_defaults(self):
        factors = calculate_four_factors(_SAMPLE_BOX, 0.0, opp_drb=27, opp_orb=7)
        self.assertEqual(factors["tov_pct"], 0.15 * 100)  # default * 100


# ---------------------------------------------------------------------------
# Opponent baseline tests
# ---------------------------------------------------------------------------


class TestOpponentBaselineAnalyzer(unittest.TestCase):
    def setUp(self):
        self.analyzer = OpponentBaselineAnalyzer(ModelConfig())

    def test_default_baseline_returned_for_empty(self):
        baseline = self.analyzer.establish_opponent_baseline([], window=5)
        self.assertAlmostEqual(baseline["allowed_efg"], 0.50)
        self.assertIn("avg_margin_allowed", baseline)
        self.assertIn("avg_total", baseline)

    def test_baseline_from_single_game(self):
        game = _make_game()
        baseline = self.analyzer.establish_opponent_baseline([game], window=5)
        self.assertIsInstance(baseline["allowed_efg"], float)
        self.assertIsInstance(baseline["avg_margin_allowed"], float)
        self.assertIsInstance(baseline["avg_total"], float)

    def test_margin_and_total_baselines(self):
        g1 = _make_game(team_score=80, opp_score=70)
        g2 = _make_game(team_score=90, opp_score=85)
        baseline = self.analyzer.establish_opponent_baseline([g1, g2], window=5)
        # avg_margin_allowed = mean([80-70, 90-85]) = mean([10, 5]) = 7.5
        self.assertAlmostEqual(baseline["avg_margin_allowed"], 7.5)
        # avg_total = mean([150, 175]) = 162.5
        self.assertAlmostEqual(baseline["avg_total"], 162.5)


# ---------------------------------------------------------------------------
# Performance vs expectation tests
# ---------------------------------------------------------------------------


class TestPerformanceVsExpectationAnalyzer(unittest.TestCase):
    def setUp(self):
        self.analyzer = PerformanceVsExpectationAnalyzer(ModelConfig())

    def test_analyze_game_returns_vs_exp_keys(self):
        game = _make_game()
        result = self.analyzer.analyze_game_vs_expectation(game)
        for key in ("efg_vs_exp", "orb_vs_exp", "ftr_vs_exp", "tov_vs_exp",
                     "drb_vs_exp", "off_eff_vs_exp", "margin_vs_exp", "total_vs_exp"):
            self.assertIn(key, result, f"Missing key: {key}")

    def test_margin_vs_exp_calculation(self):
        # Opponent baseline: avg_margin_allowed defaults to 0.0 (no history)
        game = _make_game(team_score=80, opp_score=70)
        result = self.analyzer.analyze_game_vs_expectation(game)
        # With default baseline avg_margin_allowed=0.0: margin_vs_exp = (80-70) - 0.0 = 10
        self.assertAlmostEqual(result["margin_vs_exp"], 10.0)

    def test_total_vs_exp_calculation(self):
        game = _make_game(team_score=80, opp_score=70)
        result = self.analyzer.analyze_game_vs_expectation(game)
        # With default baseline avg_total=144.0: total_vs_exp = 150 - 144 = 6
        self.assertAlmostEqual(result["total_vs_exp"], 6.0)

    def test_aggregate_defaults(self):
        agg = self.analyzer.aggregate_vs_expectation([], window=5)
        self.assertEqual(agg["n_games"], 0)
        self.assertIn("margin_vs_exp", agg)
        self.assertIn("total_vs_exp", agg)

    def test_aggregate_multiple_games(self):
        games = [_make_game(team_score=80, opp_score=70), _make_game(team_score=90, opp_score=85)]
        agg = self.analyzer.aggregate_vs_expectation(games, window=5)
        self.assertEqual(agg["n_games"], 2)
        self.assertIn("margin_vs_exp", agg)
        self.assertIn("total_vs_exp", agg)


# ---------------------------------------------------------------------------
# Full model tests
# ---------------------------------------------------------------------------


class TestCBBPredictionModel(unittest.TestCase):
    def setUp(self):
        self.model = CBBPredictionModel(ModelConfig())

    def test_predict_returns_required_keys(self):
        home = [_make_game(team_score=80, opp_score=70)]
        away = [_make_game(team_score=72, opp_score=65)]
        pred = self.model.predict_game(home, away)
        self.assertIn("predicted_spread", pred)
        self.assertIn("confidence", pred)
        self.assertIn("breakdown", pred)

    def test_confidence_range(self):
        home = [_make_game() for _ in range(5)]
        away = [_make_game() for _ in range(5)]
        pred = self.model.predict_game(home, away)
        self.assertGreaterEqual(pred["confidence"], 0.0)
        self.assertLessEqual(pred["confidence"], 0.95)

    def test_neutral_site_no_hca(self):
        games = [_make_game()]
        with_hca = self.model.predict_game(games, games, neutral_site=False)
        without_hca = self.model.predict_game(games, games, neutral_site=True)
        self.assertNotAlmostEqual(with_hca["predicted_spread"], without_hca["predicted_spread"])

    def test_empty_games_uses_defaults(self):
        pred = self.model.predict_game([], [])
        self.assertIn("predicted_spread", pred)


# ---------------------------------------------------------------------------
# RecursivePredictionEngine adapter tests
# ---------------------------------------------------------------------------


class TestRecursivePredictionEngine(unittest.TestCase):
    def _mock_loader(self, df):
        loader = MagicMock()
        loader.load_feature_store.return_value = df
        return loader

    def test_predict_returns_app_compatible_dict(self):
        df = pd.DataFrame([
            {
                "team": "TeamA", "opponent": "TeamB", "event_id": "1",
                "game_date": "2024-01-10", "home_away": "home", "completed": True,
                "fgm": 28, "fga": 60, "tpm": 7, "tpa": 20, "ftm": 12, "fta": 16,
                "orb": 12, "drb": 25, "tov": 10, "points_for": 75, "points_against": 68,
            },
            {
                "team": "TeamB", "opponent": "TeamA", "event_id": "1",
                "game_date": "2024-01-10", "home_away": "away", "completed": True,
                "fgm": 25, "fga": 62, "tpm": 5, "tpa": 22, "ftm": 13, "fta": 17,
                "orb": 7, "drb": 27, "tov": 12, "points_for": 68, "points_against": 75,
            },
        ])
        engine = RecursivePredictionEngine(self._mock_loader(df))
        pred = engine.predict_spread("TeamA", "TeamB")
        self.assertIn("predicted_spread", pred)
        self.assertIn("confidence", pred)
        self.assertIn("model_id", pred)
        self.assertEqual(pred["model_id"], "recursive_bidirectional_v1")

    def test_missing_teams_returns_default(self):
        df = pd.DataFrame(columns=["team", "opponent", "event_id", "game_date",
                                     "home_away", "completed", "fgm", "fga", "tpm",
                                     "tpa", "ftm", "fta", "orb", "drb", "tov",
                                     "points_for", "points_against"])
        engine = RecursivePredictionEngine(self._mock_loader(df))
        pred = engine.predict_spread("NoTeam", "NoOpponent")
        self.assertEqual(pred["confidence"], 0.50)

    def test_active_model_property(self):
        engine = RecursivePredictionEngine(self._mock_loader(pd.DataFrame()))
        self.assertEqual(engine.active_model["model_id"], "recursive_bidirectional_v1")

    def test_empty_dataframe_handled(self):
        engine = RecursivePredictionEngine(self._mock_loader(pd.DataFrame()))
        pred = engine.predict_spread("A", "B")
        self.assertIn("predicted_spread", pred)


if __name__ == "__main__":
    unittest.main()
