"""Tests for core/primary_prediction_engine.py."""

import unittest
from datetime import datetime
from unittest.mock import MagicMock

import numpy as np
import pandas as pd

from core.primary_prediction_engine import (
    PrimaryPredictionEngine,
    _extract_box,
    _parse_date,
    _row_has_box,
)
from primary_prediction_model import (
    CBBPredictionModel,
    GameData,
    ModelConfig,
    NormalizedOpponentBaseline,
    PerformanceVsExpectationAnalyzer,
    calculate_four_factors,
    estimate_possessions_averaged,
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
# Core v2.0 model tests
# ---------------------------------------------------------------------------


class TestEstimatePossessionsAveraged(unittest.TestCase):
    def test_basic(self):
        poss = estimate_possessions_averaged(
            team_fga=60, team_fta=16, team_orb=12, team_tov=10,
            opp_fga=62, opp_fta=17, opp_orb=7, opp_tov=12,
        )
        self.assertGreater(poss, 0)

    def test_zero_inputs(self):
        poss = estimate_possessions_averaged(0, 0, 0, 0, 0, 0, 0, 0)
        self.assertEqual(poss, 0.0)

    def test_symmetric(self):
        """Averaged possessions should be same regardless of team perspective."""
        poss1 = estimate_possessions_averaged(60, 16, 12, 10, 62, 17, 7, 12)
        poss2 = estimate_possessions_averaged(62, 17, 7, 12, 60, 16, 12, 10)
        self.assertAlmostEqual(poss1, poss2)


class TestCalculateFourFactorsV2(unittest.TestCase):
    def test_returns_all_keys(self):
        poss = 70.0
        factors = calculate_four_factors(_SAMPLE_BOX, poss, opp_drb=27, opp_orb=7)
        for key in ("efg", "tov_pct", "orb_pct", "drb_pct", "ftr", "ft_pct"):
            self.assertIn(key, factors)


class TestNormalizedOpponentBaseline(unittest.TestCase):
    def setUp(self):
        self.analyzer = NormalizedOpponentBaseline(ModelConfig())

    def test_default_baseline_for_empty(self):
        baseline = self.analyzer.calculate_baseline([], window=5)
        self.assertEqual(baseline["n_games"], 0)
        self.assertEqual(baseline["confidence"], 0.0)
        self.assertAlmostEqual(baseline["adjusted_baseline"], 105.0)

    def test_baseline_from_single_game(self):
        game = _make_game()
        baseline = self.analyzer.calculate_baseline([game], window=5)
        self.assertIsInstance(baseline["raw_baseline"], float)
        self.assertIsInstance(baseline["weighted_baseline"], float)
        self.assertIsInstance(baseline["adjusted_baseline"], float)
        self.assertGreater(baseline["n_games"], 0)

    def test_three_layer_baselines(self):
        games = [_make_game(team_score=80, opp_score=70) for _ in range(5)]
        baseline = self.analyzer.calculate_baseline(games, window=5)
        # All three layers should be populated
        self.assertIn("raw_baseline", baseline)
        self.assertIn("weighted_baseline", baseline)
        self.assertIn("adjusted_baseline", baseline)
        self.assertGreater(baseline["confidence"], 0)


class TestPerformanceVsExpectationV2(unittest.TestCase):
    def setUp(self):
        self.analyzer = PerformanceVsExpectationAnalyzer(ModelConfig())

    def test_analyze_game_returns_keys(self):
        game = _make_game()
        result = self.analyzer.analyze_game(game)
        for key in ("efg_vs_exp", "orb_vs_exp", "ftr_vs_exp", "tov_vs_exp",
                     "drb_vs_exp", "off_eff_vs_exp"):
            self.assertIn(key, result)

    def test_aggregate_defaults(self):
        agg = self.analyzer.aggregate_window([], window=5)
        self.assertEqual(agg["n_games"], 0)

    def test_aggregate_multiple_games(self):
        games = [_make_game() for _ in range(5)]
        agg = self.analyzer.aggregate_window(games, window=5)
        self.assertEqual(agg["n_games"], 5)


class TestCBBPredictionModelV2(unittest.TestCase):
    def setUp(self):
        self.model = CBBPredictionModel(ModelConfig())

    def test_predict_returns_required_keys(self):
        home = [_make_game(team_score=80, opp_score=70)]
        away = [_make_game(team_score=72, opp_score=65)]
        pred = self.model.predict_game(home, away)
        self.assertIn("predicted_spread", pred)
        self.assertIn("predicted_total", pred)
        self.assertIn("confidence", pred)
        self.assertIn("breakdown", pred)

    def test_predicted_total_is_positive(self):
        home = [_make_game() for _ in range(5)]
        away = [_make_game() for _ in range(5)]
        pred = self.model.predict_game(home, away)
        self.assertGreater(pred["predicted_total"], 0)

    def test_confidence_range(self):
        home = [_make_game() for _ in range(5)]
        away = [_make_game() for _ in range(5)]
        pred = self.model.predict_game(home, away)
        self.assertGreaterEqual(pred["confidence"], 0.05)
        self.assertLessEqual(pred["confidence"], 0.95)

    def test_neutral_site_removes_hca(self):
        games = [_make_game()]
        with_hca = self.model.predict_game(games, games, neutral_site=False)
        without_hca = self.model.predict_game(games, games, neutral_site=True)
        self.assertNotAlmostEqual(with_hca["predicted_spread"], without_hca["predicted_spread"])

    def test_empty_games_uses_defaults(self):
        pred = self.model.predict_game([], [])
        self.assertIn("predicted_spread", pred)
        self.assertIn("predicted_total", pred)


# ---------------------------------------------------------------------------
# PrimaryPredictionEngine adapter tests
# ---------------------------------------------------------------------------


class TestPrimaryPredictionEngine(unittest.TestCase):
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
        engine = PrimaryPredictionEngine(self._mock_loader(df))
        pred = engine.predict_spread("TeamA", "TeamB")
        self.assertIn("predicted_spread", pred)
        self.assertIn("predicted_total", pred)
        self.assertIn("confidence", pred)
        self.assertIn("model_id", pred)
        self.assertEqual(pred["model_id"], "primary_v2_normalized_bidirectional")

    def test_missing_teams_returns_default(self):
        df = pd.DataFrame(columns=["team", "opponent", "event_id", "game_date",
                                     "home_away", "completed", "fgm", "fga", "tpm",
                                     "tpa", "ftm", "fta", "orb", "drb", "tov",
                                     "points_for", "points_against"])
        engine = PrimaryPredictionEngine(self._mock_loader(df))
        pred = engine.predict_spread("NoTeam", "NoOpponent")
        self.assertEqual(pred["confidence"], 0.50)

    def test_active_model_property(self):
        engine = PrimaryPredictionEngine(self._mock_loader(pd.DataFrame()))
        self.assertEqual(engine.active_model["model_id"], "primary_v2_normalized_bidirectional")
        self.assertIn("Primary", engine.active_model["model_name"])

    def test_empty_dataframe_handled(self):
        engine = PrimaryPredictionEngine(self._mock_loader(pd.DataFrame()))
        pred = engine.predict_spread("A", "B")
        self.assertIn("predicted_spread", pred)
        self.assertIn("predicted_total", pred)

    def test_multiple_games_per_team(self):
        """Engine should handle multiple games per team."""
        rows = []
        for i in range(5):
            rows.append({
                "team": "TeamA", "opponent": "TeamB", "event_id": str(i),
                "game_date": f"2024-01-{10+i:02d}", "home_away": "home", "completed": True,
                "fgm": 28, "fga": 60, "tpm": 7, "tpa": 20, "ftm": 12, "fta": 16,
                "orb": 12, "drb": 25, "tov": 10, "points_for": 75, "points_against": 68,
            })
            rows.append({
                "team": "TeamB", "opponent": "TeamA", "event_id": str(i),
                "game_date": f"2024-01-{10+i:02d}", "home_away": "away", "completed": True,
                "fgm": 25, "fga": 62, "tpm": 5, "tpa": 22, "ftm": 13, "fta": 17,
                "orb": 7, "drb": 27, "tov": 12, "points_for": 68, "points_against": 75,
            })
        df = pd.DataFrame(rows)
        engine = PrimaryPredictionEngine(self._mock_loader(df))
        pred = engine.predict_spread("TeamA", "TeamB")
        self.assertIn("predicted_spread", pred)
        self.assertGreater(pred["confidence"], 0.0)


# ---------------------------------------------------------------------------
# Utility tests
# ---------------------------------------------------------------------------


class TestDataAdapterUtils(unittest.TestCase):
    def test_row_has_box_true(self):
        row = pd.Series({"fga": 60})
        self.assertTrue(_row_has_box(row))

    def test_row_has_box_false(self):
        row = pd.Series({"fga": 0})
        self.assertFalse(_row_has_box(row))

    def test_extract_box_with_data(self):
        row = pd.Series({
            "fgm": 28, "fga": 60, "tpm": 7, "tpa": 20,
            "ftm": 12, "fta": 16, "orb": 12, "drb": 25, "tov": 10,
        })
        box = _extract_box(row)
        self.assertEqual(box["fgm"], 28)

    def test_extract_box_defaults_when_no_fga(self):
        row = pd.Series({"points_for": 72})
        box = _extract_box(row)
        self.assertIn("fgm", box)
        self.assertGreater(box["fga"], 0)

    def test_parse_date_valid(self):
        dt = _parse_date("2024-01-10")
        self.assertEqual(dt.year, 2024)

    def test_parse_date_none(self):
        dt = _parse_date(None)
        self.assertEqual(dt.year, 2000)

    def test_parse_date_nan(self):
        dt = _parse_date(float("nan"))
        self.assertEqual(dt.year, 2000)


if __name__ == "__main__":
    unittest.main()
