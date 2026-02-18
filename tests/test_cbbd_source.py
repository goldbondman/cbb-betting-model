"""Tests for CBBD data source integration."""

import os
import sys
from unittest.mock import patch, Mock

# Add core directory to path
_CORE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core")
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

from data_sources import SourceType
from source_implementations import CBBDDataSource


def test_cbbd_source_type():
    source = CBBDDataSource()
    assert source.get_source_type() == SourceType.CBBD


def test_cbbd_fetch_requires_api_key():
    source = CBBDDataSource()
    with patch.dict(os.environ, {}, clear=True):
        result = source.fetch_games("2026-02-18")
    assert result.success is False
    assert "CBBD_API_KEY" in (result.error or "")


def test_cbbd_fetch_games_parses_payload():
    source = CBBDDataSource()
    payload = [
        {
            "id": "cbbd-123",
            "startDate": "2026-02-18T20:00:00Z",
            "status": "final",
            "homeTeam": "Duke",
            "awayTeam": "UNC",
            "homeScore": 82,
            "awayScore": 74,
            "venue": "Cameron Indoor Stadium",
        }
    ]

    resp = Mock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None

    with patch.dict(os.environ, {"CBBD_API_KEY": "test-key"}, clear=False):
        with patch("source_implementations.requests.get", return_value=resp) as mock_get:
            result = source.fetch_games("2026-02-18")

    assert result.success is True
    assert len(result.games) == 1
    game = result.games[0]
    assert game.game_id == "cbbd-123"
    assert game.home_team == "Duke"
    assert game.away_team == "UNC"
    assert game.home_score == 82
    assert game.away_score == 74
    assert game.source == "cbbd"
    assert result.source == SourceType.CBBD
    assert mock_get.called
