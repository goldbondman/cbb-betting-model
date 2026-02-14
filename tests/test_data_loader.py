"""Tests for core.data_loader CSV fallback and path resolution."""

import os
from datetime import datetime
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest

from core.data_loader import DataLoader


@pytest.fixture(autouse=True)
def _clear_caches():
    """Clear LRU caches between tests so stale results don't leak."""
    DataLoader._supabase_client.cache_clear()
    DataLoader._load_csv.cache_clear()
    yield
    DataLoader._supabase_client.cache_clear()
    DataLoader._load_csv.cache_clear()


def _write_csv(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


class TestLoadTodaysPredictions:
    """Predictions should fall back to CSV when Supabase is unavailable."""

    def test_returns_csv_when_no_supabase(self, tmp_path: Path) -> None:
        pred_csv = tmp_path / "data" / "predictions.csv"
        _write_csv(
            pred_csv,
            pd.DataFrame(
                {
                    "event_id": ["100"],
                    "pred_margin_home": [5.0],
                    "pred_total": [140.0],
                }
            ),
        )

        with mock.patch.dict(os.environ, {"SUPABASE_URL": "", "SUPABASE_ANON_KEY": ""}):
            loader = DataLoader()
            loader._predictions_csv_paths = [str(pred_csv)]
            result = loader.load_todays_predictions()

        assert not result.empty
        assert float(result.iloc[0]["event_id"]) == 100.0

    def test_returns_empty_when_no_csv_and_no_supabase(self) -> None:
        with mock.patch.dict(os.environ, {"SUPABASE_URL": "", "SUPABASE_ANON_KEY": ""}):
            loader = DataLoader()
            loader._predictions_csv_paths = ["/nonexistent/path.csv"]
            result = loader.load_todays_predictions()

        assert result.empty

    def test_csv_fallback_order(self, tmp_path: Path) -> None:
        """First existing CSV in the list wins."""
        first_csv = tmp_path / "first.csv"
        second_csv = tmp_path / "second.csv"
        _write_csv(first_csv, pd.DataFrame({"x": [1]}))
        _write_csv(second_csv, pd.DataFrame({"x": [2]}))

        with mock.patch.dict(os.environ, {"SUPABASE_URL": "", "SUPABASE_ANON_KEY": ""}):
            loader = DataLoader()
            loader._predictions_csv_paths = [str(first_csv), str(second_csv)]
            result = loader.load_todays_predictions()

        assert result.iloc[0]["x"] == 1


class TestLoadFeatureStore:
    """Feature store should chain primary → fallback CSV."""

    def test_loads_from_primary_path(self, tmp_path: Path) -> None:
        primary = tmp_path / "primary.csv"
        _write_csv(
            primary,
            pd.DataFrame(
                {
                    "team": ["Duke"],
                    "game_datetime_utc": ["2026-02-01T00:00:00Z"],
                    "event_id": ["200"],
                }
            ),
        )

        with mock.patch.dict(os.environ, {"SUPABASE_URL": "", "SUPABASE_ANON_KEY": ""}):
            loader = DataLoader()
            loader._feature_store_path = str(primary)
            loader._feature_store_fallback_path = "/nonexistent.csv"
            result = loader.load_feature_store()

        assert not result.empty
        assert result.iloc[0]["team"] == "Duke"

    def test_falls_back_when_primary_missing(self, tmp_path: Path) -> None:
        fallback = tmp_path / "fallback.csv"
        _write_csv(
            fallback,
            pd.DataFrame(
                {
                    "team": ["UNC"],
                    "game_datetime_utc": ["2026-02-01T00:00:00Z"],
                }
            ),
        )

        with mock.patch.dict(os.environ, {"SUPABASE_URL": "", "SUPABASE_ANON_KEY": ""}):
            loader = DataLoader()
            loader._feature_store_path = "/nonexistent.csv"
            loader._feature_store_fallback_path = str(fallback)
            result = loader.load_feature_store()

        assert not result.empty
        assert result.iloc[0]["team"] == "UNC"


class TestLoadVegasLines:
    """Vegas lines should prefer ESPN/CSV/ path."""

    def test_loads_from_espn_csv(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "espn_games.csv"
        _write_csv(
            csv_path,
            pd.DataFrame(
                {
                    "date": ["20260201"],
                    "game_id": ["300"],
                    "home_team": ["Kansas"],
                    "away_team": ["Kentucky"],
                }
            ),
        )

        with mock.patch.dict(os.environ, {"SUPABASE_URL": "", "SUPABASE_ANON_KEY": ""}):
            loader = DataLoader()
            # Patch the static method's cache to use our temp file
            result = DataLoader._load_csv(str(csv_path))

        assert not result.empty
        assert result.iloc[0]["home_team"] == "Kansas"

    def test_falls_back_to_latest_date_when_today_missing(self) -> None:
        loader = DataLoader()
        stale_games = pd.DataFrame(
            {
                "date": ["20250120", "20250201"],
                "game_id": ["300", "301"],
                "home_team": ["UCLA", "Arizona"],
                "away_team": ["USC", "UCLA"],
                "market_spread": [1.5, -2.5],
            }
        )

        with mock.patch.object(DataLoader, "_load_csv", return_value=stale_games):
            result = loader.load_vegas_lines(date="today")

        assert not result.empty
        assert str(result.iloc[0]["game_id"]) == "301"
        assert str(result.iloc[0]["game_date"].date()) == "2025-02-01"

    def test_maps_spread_alias_to_market_spread(self) -> None:
        loader = DataLoader()
        today = datetime.utcnow().strftime("%Y%m%d")
        games = pd.DataFrame(
            {
                "date": [today],
                "game_id": ["302"],
                "home_team": ["Purdue"],
                "away_team": ["Illinois"],
                "spread": [-4.0],
            }
        )

        with mock.patch.object(DataLoader, "_load_csv", return_value=games):
            result = loader.load_vegas_lines(date="today")

        assert "market_spread" in result.columns
        assert float(result.iloc[0]["market_spread"]) == -4.0
