"""
Tests for CBBD (College Basketball Data) integration
"""

import os
import sys
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

# Add ESPN and core directories to path
_ESPN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ESPN"
)
_CORE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"
)
if _ESPN_DIR not in sys.path:
    sys.path.insert(0, _ESPN_DIR)
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)


# ---------------------------------------------------------------------------
# cbbd_client module-level tests
# ---------------------------------------------------------------------------


def test_cbbd_client_imports():
    """cbbd_client module can be imported regardless of cbbd installation"""
    import cbbd_client
    assert hasattr(cbbd_client, "fetch_games_for_date")
    assert hasattr(cbbd_client, "ENABLE_CBBD")


def test_cbbd_disabled_by_default():
    """CBBD should be disabled by default (ENABLE_CBBD=0)"""
    import cbbd_client
    # Re-evaluate with clean env to guarantee default
    with patch.dict(os.environ, {"ENABLE_CBBD": "0"}, clear=False):
        import importlib
        importlib.reload(cbbd_client)
        assert cbbd_client.ENABLE_CBBD is False


def test_fetch_games_returns_none_when_disabled():
    """fetch_games_for_date returns None when CBBD is disabled"""
    import cbbd_client
    with patch.dict(os.environ, {"ENABLE_CBBD": "0"}, clear=False):
        import importlib
        importlib.reload(cbbd_client)
        result = cbbd_client.fetch_games_for_date("2024-01-15")
        assert result is None


def test_convert_game_extracts_fields():
    """_convert_game correctly normalises a mock CBBD game object"""
    import cbbd_client

    mock_game = SimpleNamespace(
        id=401829197,
        home_team="Duke",
        away_team="UNC",
        home_points=80,
        away_points=75,
        start_date="2024-01-15T19:00:00Z",
        venue="Cameron Indoor Stadium",
        status="final",
    )

    result = cbbd_client._convert_game(mock_game)

    assert result is not None
    assert result["game_id"] == "401829197"
    assert result["home_team"] == "Duke"
    assert result["away_team"] == "UNC"
    assert result["home_score"] == 80
    assert result["away_score"] == 75
    assert result["status"] == "final"
    assert result["venue"] == "Cameron Indoor Stadium"
    assert result["completed"] is True


def test_convert_game_handles_incomplete():
    """_convert_game returns None for games missing required fields"""
    import cbbd_client

    incomplete = SimpleNamespace(
        id=None,
        home_team="",
        away_team="",
        home_points=None,
        away_points=None,
        start_date=None,
        venue=None,
        status=None,
    )

    result = cbbd_client._convert_game(incomplete)
    assert result is None


def test_game_matches_date():
    """_game_matches_date correctly filters by date string"""
    import cbbd_client

    game_match = SimpleNamespace(start_date="2024-01-15T19:00:00Z")
    game_no_match = SimpleNamespace(start_date="2024-01-16T01:00:00Z")
    game_missing = SimpleNamespace(start_date=None)

    assert cbbd_client._game_matches_date(game_match, "2024-01-15") is True
    assert cbbd_client._game_matches_date(game_no_match, "2024-01-15") is False
    assert cbbd_client._game_matches_date(game_missing, "2024-01-15") is False


def test_convert_game_non_final_status():
    """_convert_game preserves non-final status"""
    import cbbd_client

    mock_game = SimpleNamespace(
        id=12345,
        home_team="Kentucky",
        away_team="Tennessee",
        home_points=None,
        away_points=None,
        start_date="2024-01-15T19:00:00Z",
        venue="Rupp Arena",
        status="scheduled",
    )

    result = cbbd_client._convert_game(mock_game)
    assert result is not None
    assert result["status"] == "scheduled"
    assert result["completed"] is False
    assert result["home_score"] is None


# ---------------------------------------------------------------------------
# SourceType / data_sources tests
# ---------------------------------------------------------------------------


def test_source_type_includes_cbbd():
    """SourceType enum includes CBBD"""
    from data_sources import SourceType
    assert hasattr(SourceType, "CBBD")
    assert SourceType.CBBD.value == "cbbd"


# ---------------------------------------------------------------------------
# CBBDDataSource tests (core/source_implementations.py)
# ---------------------------------------------------------------------------


def test_cbbd_data_source_type():
    """CBBDDataSource reports correct SourceType"""
    from source_implementations import CBBDDataSource
    from data_sources import SourceType

    ds = CBBDDataSource()
    assert ds.get_source_type() == SourceType.CBBD


def test_cbbd_data_source_handles_disabled():
    """CBBDDataSource returns failed result when cbbd_client returns None"""
    from source_implementations import CBBDDataSource

    with patch.dict(os.environ, {"ENABLE_CBBD": "0"}, clear=False):
        ds = CBBDDataSource()
        result = ds.fetch_games("2024-01-15")
        assert result.success is False


def test_cbbd_data_source_converts_games():
    """CBBDDataSource converts CBBD dicts to GameData correctly"""
    from source_implementations import CBBDDataSource

    mock_raw = [
        {
            "game_id": "401829197",
            "home_team": "Duke",
            "away_team": "UNC",
            "home_score": 80,
            "away_score": 75,
            "status": "final",
            "venue": "Cameron Indoor Stadium",
            "game_datetime": "2024-01-15T19:00:00Z",
            "completed": True,
        }
    ]

    with patch("source_implementations.CBBDDataSource.fetch_games") as mock_fetch:
        # Actually call the real _convert_to_game_data
        ds = CBBDDataSource()
        game_data = ds._convert_to_game_data(mock_raw[0], "2024-01-15")

        assert game_data.game_id == "401829197"
        assert game_data.home_team == "Duke"
        assert game_data.away_team == "UNC"
        assert game_data.source == "cbbd"
        assert game_data.is_complete_basic()


# ---------------------------------------------------------------------------
# espn_config tests
# ---------------------------------------------------------------------------


def test_espn_config_has_cbbd_settings():
    """espn_config exports CBBD configuration constants"""
    import espn_config

    assert hasattr(espn_config, "ENABLE_CBBD")
    assert hasattr(espn_config, "CBBD_API_TOKEN")
    assert hasattr(espn_config, "CBBD_BASE_URL")
    assert espn_config.CBBD_BASE_URL == "https://api.collegebasketballdata.com"


# ---------------------------------------------------------------------------
# MultiSourceFetcher integration
# ---------------------------------------------------------------------------


def test_multi_source_fetcher_accepts_cbbd_flag():
    """MultiSourceFetcher accepts enable_cbbd parameter"""
    from multi_source_fetcher import MultiSourceFetcher
    from data_sources import SourceType

    fetcher = MultiSourceFetcher(
        enable_espn=False,
        enable_ncaa=False,
        enable_henry=False,
        enable_cbbpy=False,
        enable_cbbd=True,
    )

    source_types = [s.get_source_type() for s in fetcher.sources]
    assert SourceType.CBBD in source_types


def test_multi_source_fetcher_cbbd_off_by_default():
    """MultiSourceFetcher does not include CBBD when flag omitted"""
    from multi_source_fetcher import MultiSourceFetcher
    from data_sources import SourceType

    # CBBD should not be in sources when enable_cbbd is not set (default False)
    fetcher = MultiSourceFetcher(
        enable_espn=True,
        enable_ncaa=False,
        enable_henry=False,
        enable_cbbpy=False,
        # enable_cbbd defaults to False
    )
    source_types = [s.get_source_type() for s in fetcher.sources]
    assert SourceType.CBBD not in source_types


def test_integrity_merger_default_priority_includes_cbbd():
    """IntegrityMerger default source priority includes CBBD"""
    from integrity_merger import IntegrityMerger
    from data_sources import SourceType

    merger = IntegrityMerger()
    assert SourceType.CBBD in merger.source_priority
