"""
Tests for CBBpy data source integration.
"""

import sys
import os
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

# Add core directory to path
_CORE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core")
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

import pandas as pd
from data_sources import GameData, SourceResult, SourceType, DataQuality
from source_implementations import CBBpyDataSource


def test_cbbpy_source_type():
    """Test CBBpyDataSource returns correct source type"""
    source = CBBpyDataSource()
    assert source.get_source_type() == SourceType.CBBPY
    print("✓ test_cbbpy_source_type passed")


def test_cbbpy_source_type_enum():
    """Test CBBPY is a valid SourceType enum value"""
    assert SourceType.CBBPY.value == "cbbpy"
    print("✓ test_cbbpy_source_type_enum passed")


def test_cbbpy_convert_df_to_game_data():
    """Test conversion of CBBpy DataFrame to GameData"""
    source = CBBpyDataSource()

    # Create a mock DataFrame resembling CBBpy get_game_info output
    info_df = pd.DataFrame([{
        "game_id": "401522202",
        "home_team": "UConn Huskies",
        "away_team": "San Diego State Aztecs",
        "home_score": 76,
        "away_score": 59,
        "home_win": True,
        "arena": "NRG Stadium",
        "game_day": "April 03, 2023",
        "game_time": "06:20 PM PDT",
    }])

    game_data = source._convert_df_to_game_data(info_df, None, "2023-04-03")

    assert game_data.game_id == "401522202"
    assert game_data.home_team == "UConn Huskies"
    assert game_data.away_team == "San Diego State Aztecs"
    assert game_data.home_score == 76
    assert game_data.away_score == 59
    assert game_data.status == "final"
    assert game_data.venue == "NRG Stadium"
    assert game_data.source == "cbbpy"
    assert game_data.date == "2023-04-03"
    assert game_data.is_complete_basic()
    print("✓ test_cbbpy_convert_df_to_game_data passed")


def test_cbbpy_convert_df_minimal():
    """Test conversion handles minimal data"""
    source = CBBpyDataSource()

    info_df = pd.DataFrame([{
        "game_id": "401522203",
        "home_team": "Duke Blue Devils",
        "away_team": "UNC Tar Heels",
        "home_score": None,
        "away_score": None,
        "home_win": None,
        "arena": None,
        "game_day": "",
        "game_time": "",
    }])

    game_data = source._convert_df_to_game_data(info_df, None, "2023-03-15")

    assert game_data.game_id == "401522203"
    assert game_data.home_team == "Duke Blue Devils"
    assert game_data.away_team == "UNC Tar Heels"
    assert game_data.home_score is None
    assert game_data.away_score is None
    assert game_data.status is None  # home_win is None
    assert game_data.is_complete_basic()
    print("✓ test_cbbpy_convert_df_minimal passed")


def test_cbbpy_convert_df_uses_boxscore_when_info_scores_missing():
    """Scores should be filled from boxscore PTS totals when game_info scores are missing."""
    source = CBBpyDataSource()

    info_df = pd.DataFrame([{
        "game_id": "401522204",
        "home_team": "Duke Blue Devils",
        "away_team": "UNC Tar Heels",
        "home_score": None,
        "away_score": None,
        "home_win": True,
    }])

    boxscore_df = pd.DataFrame([
        {"team": "UNC Tar Heels", "PTS": 75},
        {"team": "Duke Blue Devils", "PTS": 80},
    ])

    game_data = source._convert_df_to_game_data(info_df, boxscore_df, "2023-03-15")

    assert game_data.home_score == 80
    assert game_data.away_score == 75
    assert game_data.raw_data is not None
    assert game_data.raw_data.get("boxscore") is not None
    print("✓ test_cbbpy_convert_df_uses_boxscore_when_info_scores_missing passed")


@patch("source_implementations.CBBpyDataSource.fetch_games")
def test_cbbpy_fetch_returns_source_result(mock_fetch):
    """Test that CBBpy fetch returns a valid SourceResult"""
    mock_fetch.return_value = SourceResult(
        source=SourceType.CBBPY,
        success=True,
        games=[
            GameData(
                game_id="401522202",
                date="2023-04-03",
                home_team="UConn Huskies",
                away_team="San Diego State Aztecs",
                home_score=76,
                away_score=59,
                source="cbbpy"
            )
        ],
        fetch_time=datetime.now(timezone.utc)
    )

    source = CBBpyDataSource()
    result = source.fetch_games("2023-04-03")

    assert result.success
    assert result.source == SourceType.CBBPY
    assert len(result.games) == 1
    assert result.games[0].game_id == "401522202"
    print("✓ test_cbbpy_fetch_returns_source_result passed")


def test_cbbpy_fetch_import_error():
    """Test CBBpy handles missing cbbpy package gracefully"""
    source = CBBpyDataSource()

    with patch.dict("sys.modules", {"cbbpy": None, "cbbpy.mens_scraper": None}):
        result = source.fetch_games("2023-04-03")

    assert not result.success
    assert result.source == SourceType.CBBPY
    assert result.error is not None
    print("✓ test_cbbpy_fetch_import_error passed")


def test_cbbpy_integration_with_merger():
    """Test CBBpy data integrates with IntegrityMerger"""
    from integrity_merger import IntegrityMerger

    # Create a CBBpy game result
    cbbpy_game = GameData(
        game_id="401829197",
        date="2024-01-15",
        home_team="Duke",
        away_team="UNC",
        home_score=80,
        away_score=75,
        status="final",
        source="cbbpy"
    )

    espn_game = GameData(
        game_id="401829197",
        date="2024-01-15",
        home_team="Duke",
        away_team="UNC",
        home_score=80,
        away_score=75,
        status="final",
        source="espn"
    )

    results = [
        SourceResult(SourceType.ESPN, True, [espn_game]),
        SourceResult(SourceType.CBBPY, True, [cbbpy_game]),
    ]

    merger = IntegrityMerger()
    merged_games, report = merger.merge(results)

    assert len(merged_games) == 1
    assert merged_games[0].source_count == 2
    assert not merged_games[0].has_conflicts
    assert "cbbpy" in merged_games[0].sources
    assert "espn" in merged_games[0].sources
    print("✓ test_cbbpy_integration_with_merger passed")


def test_cbbpy_conflict_resolution():
    """Test CBBpy conflicts are resolved correctly (ESPN takes priority)"""
    from integrity_merger import IntegrityMerger

    cbbpy_game = GameData(
        game_id="401829197",
        date="2024-01-15",
        home_team="Duke",
        away_team="UNC",
        home_score=80,
        away_score=76,  # Different from ESPN
        status="final",
        source="cbbpy"
    )

    espn_game = GameData(
        game_id="401829197",
        date="2024-01-15",
        home_team="Duke",
        away_team="UNC",
        home_score=80,
        away_score=75,  # ESPN value
        status="final",
        source="espn"
    )

    results = [
        SourceResult(SourceType.ESPN, True, [espn_game]),
        SourceResult(SourceType.CBBPY, True, [cbbpy_game]),
    ]

    merger = IntegrityMerger()
    merged_games, report = merger.merge(results)

    assert merged_games[0].has_conflicts
    # ESPN has higher priority, so its value should be used
    assert merged_games[0].game.away_score == 75
    print("✓ test_cbbpy_conflict_resolution passed")


def test_cbbpy_date_conversion():
    """Test that CBBpy date conversion works correctly"""
    source = CBBpyDataSource()

    # The fetch_games method converts YYYY-MM-DD to MM-DD-YYYY internally
    # We test this indirectly through the _convert_df_to_game_data method
    info_df = pd.DataFrame([{
        "game_id": "401522202",
        "home_team": "Team A",
        "away_team": "Team B",
        "home_score": 70,
        "away_score": 65,
        "home_win": True,
        "arena": "Arena",
        "game_day": "January 15, 2024",
        "game_time": "07:00 PM EST",
    }])

    game_data = source._convert_df_to_game_data(info_df, None, "2024-01-15")
    assert game_data.date == "2024-01-15"
    assert game_data.game_datetime == "January 15, 2024 07:00 PM EST"
    print("✓ test_cbbpy_date_conversion passed")


def run_all_tests():
    """Run all CBBpy tests"""
    print("\nRunning CBBpy Data Source Tests")
    print("=" * 50)

    test_cbbpy_source_type()
    test_cbbpy_source_type_enum()
    test_cbbpy_convert_df_to_game_data()
    test_cbbpy_convert_df_minimal()
    test_cbbpy_fetch_returns_source_result()
    test_cbbpy_fetch_import_error()
    test_cbbpy_integration_with_merger()
    test_cbbpy_conflict_resolution()
    test_cbbpy_date_conversion()

    print("=" * 50)
    print("All CBBpy tests passed! ✓")


if __name__ == "__main__":
    run_all_tests()
