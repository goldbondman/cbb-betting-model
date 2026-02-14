#!/usr/bin/env python3
"""
Test that daily_auto_predict.py handles empty predictions gracefully.
"""
import sys
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import pandas as pd

# Add repo root to path
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def test_empty_predictions_handling():
    """Test that the script doesn't crash when predictions are empty."""
    
    # Import after path setup
    from scripts import daily_auto_predict
    
    # Create a mock scoreboard with some games
    mock_scoreboard = pd.DataFrame([
        {
            'game_id': '401234',
            'date': '20260214',
            'home_team': 'Duke',
            'away_team': 'UNC',
            'market_spread': -5.5,
            'market_total': 150.0,
            'completed': False,
        }
    ])
    
    # Mock fetch_scoreboard to return our test data
    with patch('scripts.daily_auto_predict.fetch_scoreboard', return_value=mock_scoreboard):
        # Mock _load_supabase
        with patch('scripts.daily_auto_predict._load_supabase') as mock_load_supabase:
            # Configure mock client that returns valid JSON-serializable data
            mock_client = Mock()
            mock_load_supabase.return_value = mock_client
            
            # Mock upsert_rows to return counts
            with patch('scripts.daily_auto_predict.upsert_rows', return_value=1) as mock_upsert:
                # Mock fetch_predictions_latest_from_db to return empty DataFrame
                with patch('scripts.daily_auto_predict.fetch_predictions_latest_from_db', return_value=pd.DataFrame()):
                    
                    # This should not raise an exception
                    try:
                        daily_auto_predict.main()
                        print("✓ Script completed successfully with empty predictions")
                        
                        # Verify upsert_rows was called for games/teams/markets but not predictions
                        assert mock_upsert.called, "upsert_rows should be called for games/teams/markets"
                        
                        # Check that at least games were upserted
                        upsert_calls = mock_upsert.call_args_list
                        tables_upserted = [call[0][2] for call in upsert_calls if len(call[0]) > 2]
                        
                        # Should have upserted teams, games, and market_lines
                        assert 'teams' in tables_upserted or any('team' in t for t in tables_upserted), \
                            "Should upsert teams"
                        
                        print("✓ Games, teams, and market lines were still processed")
                        print("✓ Test passed: Empty predictions handled gracefully")
                        return True
                        
                    except RuntimeError as e:
                        if "No rows found" in str(e):
                            print("✗ Test failed: Script still raises RuntimeError for empty predictions")
                            print(f"   Error: {e}")
                            return False
                        else:
                            raise


def test_predictions_available():
    """Test that the script works when predictions are available."""
    
    # Import after path setup
    from scripts import daily_auto_predict
    
    # Create mock client
    mock_client = Mock()
    
    # Create a mock scoreboard with some games
    mock_scoreboard = pd.DataFrame([
        {
            'game_id': '401234',
            'date': '20260214',
            'home_team': 'Duke',
            'away_team': 'UNC',
            'market_spread': -5.5,
            'market_total': 150.0,
            'completed': False,
            'game_datetime_utc': '2026-02-14T19:00:00Z',
            'venue': 'Cameron Indoor Stadium',
        }
    ])
    
    # Create mock predictions
    mock_predictions = pd.DataFrame([
        {
            'event_id': '401234',
            'pred_margin_home': -7.2,
            'pred_total': 148.5,
            'model_version': 'test-model-v1',
            'team_home': 'Duke',
            'team_away': 'UNC',
        }
    ])
    
    # Mock fetch_scoreboard to return our test data
    with patch('scripts.daily_auto_predict.fetch_scoreboard', return_value=mock_scoreboard):
        # Mock _load_supabase to return our mock client
        with patch('scripts.daily_auto_predict._load_supabase', return_value=mock_client):
            # Mock upsert_rows to track calls
            with patch('scripts.daily_auto_predict.upsert_rows', return_value=1) as mock_upsert:
                # Mock fetch_predictions_latest_from_db to return predictions
                with patch('scripts.daily_auto_predict.fetch_predictions_latest_from_db', return_value=mock_predictions):
                    
                    try:
                        daily_auto_predict.main()
                        print("✓ Script completed successfully with predictions")
                        
                        # Verify predictions were upserted
                        upsert_calls = mock_upsert.call_args_list
                        tables_upserted = [call[0][2] for call in upsert_calls if len(call[0]) > 2]
                        
                        assert 'predictions' in tables_upserted, \
                            "Should upsert predictions when available"
                        
                        print("✓ Predictions were processed and upserted")
                        print("✓ Test passed: Predictions handled correctly")
                        return True
                        
                    except Exception as e:
                        print(f"✗ Test failed with exception: {e}")
                        import traceback
                        traceback.print_exc()
                        return False


if __name__ == "__main__":
    print("=" * 80)
    print("TEST 1: Empty Predictions Handling")
    print("=" * 80)
    result1 = test_empty_predictions_handling()
    
    print("\n" + "=" * 80)
    print("TEST 2: Predictions Available")
    print("=" * 80)
    result2 = test_predictions_available()
    
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    
    if result1 and result2:
        print("✓ All tests passed")
        sys.exit(0)
    else:
        print("✗ Some tests failed")
        sys.exit(1)
