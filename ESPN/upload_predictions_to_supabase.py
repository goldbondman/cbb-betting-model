#!/usr/bin/env python3
"""
Upload Predictions to Supabase
Bridges the gap: reads predictions from game_predictor.py and uploads to Supabase.

This script should be added to your GitHub Actions workflow AFTER espn_boxscore_builder.py runs.
"""

import os
import sys
from datetime import datetime
from typing import Optional

import pandas as pd

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from game_predictor import GamePredictor, predict_today, predict_tomorrow
from espn_config import OUT_MATCHUPS


def upload_to_supabase(df: pd.DataFrame, table_name: str = "predictions_latest") -> bool:
    """
    Upload predictions to Supabase.
    
    Args:
        df: Predictions DataFrame
        table_name: Target table name (default: "predictions_latest")
        
    Returns:
        True if successful, False otherwise
    """
    db_url = os.getenv("SUPABASE_DB_URL")
    if not db_url:
        print("[ERROR] SUPABASE_DB_URL not set. Cannot upload.")
        return False
    
    # Try using psycopg2 (PostgreSQL driver)
    try:
        import psycopg2
        from psycopg2.extras import execute_values
        
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        
        # Prepare data for insertion
        columns = df.columns.tolist()
        values = [tuple(row) for row in df.values]
        
        # Build upsert query (on conflict update)
        cols_str = ", ".join(columns)
        placeholders = ", ".join(["%s"] * len(columns))
        
        # Identify conflict column (usually event_id)
        conflict_col = "event_id" if "event_id" in columns else columns[0]
        
        # Update clause (update all columns except conflict key)
        update_cols = [c for c in columns if c != conflict_col]
        update_str = ", ".join([f"{c} = EXCLUDED.{c}" for c in update_cols])
        
        query = f"""
            INSERT INTO raw.{table_name} ({cols_str})
            VALUES %s
            ON CONFLICT ({conflict_col})
            DO UPDATE SET {update_str}
        """
        
        execute_values(cur, query, values)
        conn.commit()
        
        cur.close()
        conn.close()
        
        print(f"✓ Uploaded {len(df)} predictions to {table_name}")
        return True
        
    except ImportError:
        print("[ERROR] psycopg2 not installed. Install with: pip install psycopg2-binary")
        return False
    except Exception as e:
        print(f"[ERROR] Failed to upload to Supabase: {e}")
        return False


def generate_and_upload_predictions(
    model_type: str = "formula",
    model_path: Optional[str] = None,
    days_ahead: int = 2
) -> bool:
    """
    Generate predictions and upload to Supabase.
    
    Args:
        model_type: "ml" or "formula"
        model_path: Path to ML model (if using ml)
        days_ahead: Number of days to predict (default: 2 for today + tomorrow)
        
    Returns:
        True if successful, False otherwise
    """
    print(f"\n{'='*80}")
    print(f"GENERATING PREDICTIONS: {model_type.upper()}")
    print(f"{'='*80}\n")
    
    # Check if matchups file exists
    if not os.path.exists(OUT_MATCHUPS):
        print(f"[ERROR] Matchups file not found: {OUT_MATCHUPS}")
        print("Run espn_boxscore_builder.py first to generate features.")
        return False
    
    # Initialize predictor
    predictor = GamePredictor(model_type=model_type, model_path=model_path)
    
    # Generate predictions for upcoming games
    try:
        predictions = predictor.predict_upcoming_games(days_ahead=days_ahead)
    except Exception as e:
        print(f"[ERROR] Prediction generation failed: {e}")
        return False
    
    if predictions.empty:
        print("No games to predict (no upcoming games or all completed)")
        return False
    
    print(f"Generated {len(predictions)} predictions")
    print(f"\nSample predictions:")
    print(predictions[["event_id", "h_team", "a_team", "home_win_prob", "predicted_spread"]].head())
    
    # Add metadata
    predictions["uploaded_at"] = pd.Timestamp.now(tz="UTC")
    predictions["model_version"] = model_type
    
    # Rename columns to match Supabase schema (if needed)
    # Adjust this based on your actual schema
    column_mapping = {
        "event_id": "game_id",  # Adjust if your schema uses different names
        "home_win_prob": "home_win_probability",
        # Add more mappings as needed
    }
    predictions = predictions.rename(columns=column_mapping)
    
    # Upload to Supabase
    success = upload_to_supabase(predictions, table_name="predictions_latest")
    
    if success:
        print(f"\n✓ Successfully uploaded {len(predictions)} predictions to Supabase")
    else:
        print("\n✗ Upload failed")
    
    return success


def main():
    """
    Main entry point for script.
    
    Usage:
        # Formula-based predictions (default)
        python upload_predictions_to_supabase.py
        
        # ML model predictions
        python upload_predictions_to_supabase.py --model-type ml --model-path ml/models/best_model.pkl
    """
    import argparse
    
    parser = argparse.ArgumentParser(description="Generate and upload predictions to Supabase")
    parser.add_argument(
        "--model-type",
        type=str,
        default="formula",
        choices=["ml", "formula"],
        help="Prediction model type (default: formula)"
    )
    parser.add_argument(
        "--model-path",
        type=str,
        default=None,
        help="Path to trained ML model (required if --model-type ml)"
    )
    parser.add_argument(
        "--days-ahead",
        type=int,
        default=2,
        help="Number of days to predict (default: 2 for today + tomorrow)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate predictions but don't upload (for testing)"
    )
    
    args = parser.parse_args()
    
    # Validate arguments
    if args.model_type == "ml" and not args.model_path:
        parser.error("--model-path is required when using --model-type ml")
    
    # Check environment
    if not args.dry_run and not os.getenv("SUPABASE_DB_URL"):
        print("[ERROR] SUPABASE_DB_URL environment variable not set")
        sys.exit(1)
    
    # Generate predictions
    if args.dry_run:
        print("\n[DRY RUN MODE - No upload will occur]\n")
        predictor = GamePredictor(model_type=args.model_type, model_path=args.model_path)
        predictions = predictor.predict_upcoming_games(days_ahead=args.days_ahead)
        print(f"\nGenerated {len(predictions)} predictions:")
        print(predictions)
        success = True
    else:
        success = generate_and_upload_predictions(
            model_type=args.model_type,
            model_path=args.model_path,
            days_ahead=args.days_ahead
        )
    
    # Exit with appropriate code
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
