from unittest.mock import Mock

import pandas as pd

from backtesting.backtest_engine import BacktestEngine


def test_backtest_model_value_scan_finds_best_threshold() -> None:
    engine = BacktestEngine()
    engine.data_loader = Mock()
    engine.data_loader.load_historical_games.return_value = pd.DataFrame(
        [
            {"game_id": "g1", "game_date": "2026-01-01", "home_team": "A", "away_team": "B", "completed": True, "margin": 6.0, "market_spread": 0.0},
            {"game_id": "g2", "game_date": "2026-01-02", "home_team": "A", "away_team": "B", "completed": True, "margin": -2.0, "market_spread": 0.0},
            {"game_id": "g3", "game_date": "2026-01-03", "home_team": "A", "away_team": "B", "completed": True, "margin": 3.0, "market_spread": 0.0},
            {"game_id": "g4", "game_date": "2026-01-04", "home_team": "A", "away_team": "B", "completed": True, "margin": -1.0, "market_spread": 0.0},
        ]
    )
    engine._get_snapshot_at_date = Mock(return_value={"team": "ok"})  # type: ignore[method-assign]
    engine._predict_with_params = Mock(  # type: ignore[method-assign]
        side_effect=[
            {"predicted_spread": 5.0},
            {"predicted_spread": 4.0},
            {"predicted_spread": 2.0},
            {"predicted_spread": 1.0},
        ]
    )

    results = engine.backtest_model({"params": {}}, days_back=30)

    assert results["best_value_threshold"] == "2+"
    assert results["value_scan"]["2+"]["games"] == 3
    assert results["value_scan"]["2+"]["roi"] > results["value_scan"]["0+"]["roi"]
