#!/usr/bin/env python3
"""
Test column normalization for predictions.
"""
import pandas as pd


def normalize_prediction_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize column names to handle variations between tables."""
    if df.empty:
        return df
    
    # Column aliases - map various names to standard names
    column_mappings = {
        # Margin/spread predictions
        "pred_spread": "pred_margin_home",
        "ensemble_prediction": "pred_margin_home",
        "predicted_spread": "pred_margin_home",
        # Total predictions
        "predicted_total": "pred_total",
        # Team names
        "team_a": "home_team",
        "team_b": "away_team",
        "team_home": "home_team",
        "team_away": "away_team",
        # Game identifiers
        "game_id": "event_id",
    }
    
    # Apply mappings only if source column exists and target doesn't
    for source_col, target_col in column_mappings.items():
        if source_col in df.columns and target_col not in df.columns:
            df[target_col] = df[source_col]
    
    return df


def test_column_normalization():
    """Test that column normalization works correctly."""
    
    # Test case 1: Basic mapping
    test_df = pd.DataFrame({
        'pred_spread': [5.0, -3.0],
        'predicted_total': [150.0, 145.0],
        'team_a': ['Duke', 'UNC'],
        'team_b': ['Kansas', 'Virginia'],
        'game_id': ['401234', '401235']
    })
    
    normalized = normalize_prediction_columns(test_df)
    
    assert 'pred_margin_home' in normalized.columns, "Should map pred_spread to pred_margin_home"
    assert 'pred_total' in normalized.columns, "Should map predicted_total to pred_total"
    assert 'home_team' in normalized.columns, "Should map team_a to home_team"
    assert 'away_team' in normalized.columns, "Should map team_b to away_team"
    assert 'event_id' in normalized.columns, "Should map game_id to event_id"
    
    # Verify values were copied correctly
    assert normalized['pred_margin_home'].tolist() == [5.0, -3.0]
    assert normalized['pred_total'].tolist() == [150.0, 145.0]
    assert normalized['home_team'].tolist() == ['Duke', 'UNC']
    
    # Test case 2: Don't overwrite existing columns
    test_df2 = pd.DataFrame({
        'pred_spread': [5.0],
        'pred_margin_home': [6.0],  # Already exists
    })
    
    normalized2 = normalize_prediction_columns(test_df2)
    
    # Should keep the existing pred_margin_home value
    assert normalized2['pred_margin_home'].tolist() == [6.0], "Should not overwrite existing columns"
    
    # Test case 3: Empty dataframe
    empty_df = pd.DataFrame()
    normalized_empty = normalize_prediction_columns(empty_df)
    assert normalized_empty.empty, "Should handle empty dataframes"
    
    print("✓ All column normalization tests passed")


if __name__ == "__main__":
    test_column_normalization()
