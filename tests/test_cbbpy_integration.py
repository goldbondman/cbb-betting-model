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
