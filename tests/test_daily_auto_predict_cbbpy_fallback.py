#!/usr/bin/env python3
"""Tests for CBBpy-first behavior in daily_auto_predict scoreboard ingestion."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts import daily_auto_predict


def _mock_game():
    return SimpleNamespace(
        game_id="401111111",
        home_team="Duke",
        away_team="UNC",
        home_score=80,
        away_score=75,
        venue="Cameron Indoor Stadium",
        game_datetime="2026-02-14T19:00:00Z",
        status="final",
    )


def test_fetch_scoreboard_uses_cbbpy_as_primary_source():
    """When CBBpy returns data, ESPN should not be called for that date."""
    mock_result = SimpleNamespace(success=True, games=[_mock_game()], error=None)

    with patch.object(daily_auto_predict, "DAYS_BACK", 0), patch.object(daily_auto_predict, "DAYS_AHEAD", 0):
        with patch("source_implementations.CBBpyDataSource.fetch_games", return_value=mock_result):
            with patch.object(daily_auto_predict.espn, "fetch_scoreboard_games_for_date", return_value=[]) as espn_fetch:
                df = daily_auto_predict.fetch_scoreboard()

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["source"] == "cbbpy"
    assert row["game_id"] == "401111111"
    assert bool(row["completed"]) is True
    espn_fetch.assert_not_called()


def test_fetch_scoreboard_falls_back_to_espn_when_cbbpy_unavailable():
    """If CBBpy fetch fails for a date, ESPN data should still be ingested."""
    espn_rows = [
        {
            "game_id": "401222222",
            "date": "20260214",
            "home_team": "Kansas",
            "away_team": "Baylor",
            "home_score": 70,
            "away_score": 65,
            "completed": True,
            "source": "espn",
        }
    ]
    failed_result = SimpleNamespace(success=False, games=[], error="cbbpy unavailable")

    with patch.object(daily_auto_predict, "DAYS_BACK", 0), patch.object(daily_auto_predict, "DAYS_AHEAD", 0):
        with patch("source_implementations.CBBpyDataSource.fetch_games", return_value=failed_result):
            with patch.object(daily_auto_predict.espn, "fetch_scoreboard_games_for_date", return_value=espn_rows):
                df = daily_auto_predict.fetch_scoreboard()

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["source"] == "espn"
    assert row["game_id"] == "401222222"


def test_fetch_scoreboard_cbbpy_fallback_returns_empty_when_unavailable():
    """Fallback helper should return empty rows when CBBpy fetch is unsuccessful."""
    mock_result = SimpleNamespace(success=False, games=[], error="cbbpy unavailable")

    with patch("source_implementations.CBBpyDataSource.fetch_games", return_value=mock_result):
        rows = daily_auto_predict._fetch_scoreboard_from_cbbpy("20260214")

    assert rows == []
