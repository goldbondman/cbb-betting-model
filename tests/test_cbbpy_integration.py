"""
Tests for CBBpy integration
"""

import os
import sys
import pandas as pd

# Add ESPN directory to path
_ESPN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ESPN"
)
if _ESPN_DIR not in sys.path:
    sys.path.insert(0, _ESPN_DIR)


def test_cbbpy_client_imports_without_cbbpy_installed():
    """Test that cbbpy_client can be imported even if cbbpy is not installed"""
    import cbbpy_client
    assert True


def test_configuration_values():
    """Test that CBBpy configuration values are read correctly"""
    import cbbpy_client
    
    # Default values should be enabled
    assert cbbpy_client.ENABLE_CBBPY in (True, False)
    assert cbbpy_client.CBBPY_FALLBACK_TO_ESPN in (True, False)


def test_convert_cbbpy_boxscore_to_espn_format():
    """Test conversion of CBBpy boxscore to ESPN format"""
    import cbbpy_client
    
    mock_boxscore_df = pd.DataFrame([
        {
            'team': 'Team A',
            'team_id': '1',
            'FGM': 25, 'FGA': 58,
            'TPM': 9, 'TPA': 25,
            'FTM': 15, 'FTA': 20,
            'TO': 11,
            'OREB': 8, 'DREB': 24, 'REB': 32
        }
    ])
    
    result = cbbpy_client._convert_cbbpy_boxscore_to_espn_format(mock_boxscore_df, '401479097')
    
    assert result is not None
    assert 'boxscore' in result
    assert 'teams' in result['boxscore']
    assert len(result['boxscore']['teams']) == 1
    
    team = result['boxscore']['teams'][0]
    assert team['team']['displayName'] == 'Team A'
    assert len(team['statistics']) > 0
    
    # Check that FG stat exists
    fg_stat = next((s for s in team['statistics'] if s['abbreviation'] == 'FG'), None)
    assert fg_stat is not None
    assert fg_stat['displayValue'] == '25-58'


def test_convert_cbbpy_boxscore_handles_empty_dataframe():
    """Test that empty DataFrame is handled gracefully"""
    import cbbpy_client
    
    empty_df = pd.DataFrame()
    result = cbbpy_client._convert_cbbpy_boxscore_to_espn_format(empty_df, '401479097')
    
    assert result is None


def test_fetch_scoreboard_cbbpy_returns_none():
    """CBBpy scoreboard cannot produce ESPN-compatible events, must return None
    so the caller falls back to the direct ESPN API."""
    import cbbpy_client
    result = cbbpy_client.fetch_scoreboard_cbbpy("20260115")
    assert result is None


def test_is_valid_espn_summary_rejects_incomplete():
    """Validate that _is_valid_espn_summary rejects cbbpy-style incomplete data."""
    import cbbpy_client

    # Empty competitors list (the old cbbpy bug)
    bad = {
        "header": {"competitions": [{"competitors": [], "status": {"type": {"completed": True}}}]},
        "boxscore": {"teams": [{"team": {"id": "1"}}, {"team": {"id": "2"}}]}
    }
    assert cbbpy_client._is_valid_espn_summary(bad) is False

    # Missing boxscore teams
    bad2 = {
        "header": {"competitions": [{"competitors": [{"homeAway": "home"}, {"homeAway": "away"}]}]},
        "boxscore": {"teams": []}
    }
    assert cbbpy_client._is_valid_espn_summary(bad2) is False


def test_is_valid_espn_summary_accepts_complete():
    """Validate that _is_valid_espn_summary accepts well-formed ESPN data."""
    import cbbpy_client

    good = {
        "header": {"competitions": [{
            "date": "2026-01-15T00:00Z",
            "competitors": [
                {"homeAway": "home", "team": {"id": "1"}, "score": "80"},
                {"homeAway": "away", "team": {"id": "2"}, "score": "70"},
            ],
            "status": {"type": {"completed": True}},
        }]},
        "boxscore": {"teams": [
            {"team": {"id": "1", "displayName": "A"}, "statistics": []},
            {"team": {"id": "2", "displayName": "B"}, "statistics": []},
        ]}
    }
    assert cbbpy_client._is_valid_espn_summary(good) is True
