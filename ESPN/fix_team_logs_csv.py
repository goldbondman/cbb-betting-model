#!/usr/bin/env python3
"""
Fix espn_team_game_logs.csv issues:
1. Reorder columns to match schema with game_date in the far left
2. Populate missing game_date values from game_datetime_utc
3. Ensure all data is properly aligned
"""

import pandas as pd
import os
import shutil
from datetime import datetime
from zoneinfo import ZoneInfo

# Configuration
CSV_PATH = "CSV/espn_team_game_logs.csv"
BACKUP_PATH = "CSV/espn_team_game_logs.csv.backup"
TZ_PST = ZoneInfo("America/Los_Angeles")

def calculate_game_date(game_datetime_utc):
    """Calculate game_date (PST) from game_datetime_utc"""
    if pd.isna(game_datetime_utc):
        return None
    try:
        dt = pd.to_datetime(game_datetime_utc, utc=True, errors="coerce")
        if pd.isna(dt):
            return None
        dt_pst = dt.tz_convert(TZ_PST)
        return dt_pst.date().isoformat()
    except Exception:
        return None

def calculate_game_date_utc(game_datetime_utc):
    """Calculate game_date_utc from game_datetime_utc"""
    if pd.isna(game_datetime_utc):
        return None
    try:
        dt = pd.to_datetime(game_datetime_utc, utc=True, errors="coerce")
        if pd.isna(dt):
            return None
        return dt.date().isoformat()
    except Exception:
        return None

def main():
    print("="*60)
    print("ESPN Team Game Logs CSV Fix Script")
    print("="*60)
    
    if not os.path.exists(CSV_PATH):
        print(f"ERROR: {CSV_PATH} not found!")
        return
    
    # Backup existing file
    print(f"\n1. Creating backup: {BACKUP_PATH}")
    shutil.copy2(CSV_PATH, BACKUP_PATH)
    
    # Read CSV
    print(f"\n2. Reading {CSV_PATH}")
    df = pd.read_csv(CSV_PATH)
    print(f"   Total rows: {len(df)}")
    
    # Analyze current state
    print(f"\n3. Analyzing data quality")
    print(f"   Rows with non-zero fga: {(df['fga'] > 0).sum()}")
    print(f"   Rows with null game_date: {df['game_date'].isna().sum()}")
    print(f"   Rows with null game_datetime_utc: {df['game_datetime_utc'].isna().sum()}")
    
    # Fix game_date and game_date_utc fields
    print(f"\n4. Fixing game_date fields from game_datetime_utc")
    
    # For rows where game_date is NaN but game_datetime_utc exists
    needs_fix = df['game_date'].isna() & df['game_datetime_utc'].notna()
    print(f"   Rows needing game_date fix: {needs_fix.sum()}")
    
    if needs_fix.sum() > 0:
        df.loc[needs_fix, 'game_date'] = df.loc[needs_fix, 'game_datetime_utc'].apply(calculate_game_date)
        df.loc[needs_fix, 'game_date_utc'] = df.loc[needs_fix, 'game_datetime_utc'].apply(calculate_game_date_utc)
    
    # For rows where game_date contains a datetime string (misalignment issue)
    # Check if game_date looks like a datetime
    def looks_like_datetime(val):
        if pd.isna(val):
            return False
        return isinstance(val, str) and ('T' in val or ':' in val)
    
    datetime_in_date = df['game_date'].apply(looks_like_datetime)
    if datetime_in_date.sum() > 0:
        print(f"   Found {datetime_in_date.sum()} rows with datetime in game_date field")
        print(f"   These appear to be from older data with misaligned columns")
        # For these rows, game_date actually contains the datetime, so use it
        df.loc[datetime_in_date, 'game_datetime_utc'] = df.loc[datetime_in_date, 'game_date']
        df.loc[datetime_in_date, 'game_date'] = df.loc[datetime_in_date, 'game_date'].apply(calculate_game_date)
        df.loc[datetime_in_date, 'game_date_utc'] = df.loc[datetime_in_date, 'game_datetime_utc'].apply(calculate_game_date_utc)
    
    # Define the correct column order with game_date far left (after basic identifiers)
    # Per user request, game_date should be "far left" 
    # We'll put it right after the primary identifiers
    print(f"\n5. Reordering columns to match schema (game_date far left)")
    
    desired_order = [
        # Primary identifiers first
        "event_id", "team_id", "team", "opponent", "home_away", 
        # Game date/time - game_date moved to far left per user request
        "game_date", "game_date_utc", "game_datetime_utc", "venue",
        # Score data
        "points_for", "points_against", "margin",
        # Box score raw stats
        "fgm", "fga", "tpm", "tpa", "ftm", "fta", "tov", "orb", "drb", "reb",
        # Derived metrics
        "poss", "efg", "ftr", "3par", "3p_pct", "ft_pct", "tov_pct", "orb_pct", "drb_pct",
        "ortg", "drtg", "netrtg", "pace",
        # Game metadata
        "neutral_site", "is_ot", "num_ot", "noise_flag",
        "data_ok", "completed", "state", "status_desc", "status_detail",
        # Technical metadata
        "pulled_at_utc", "source", "parse_version",
        # Additional fields
        "home_team", "away_team", "blowout", "row_hash"
    ]
    
    # Add any columns that exist in df but not in desired_order (at the end)
    existing_cols = df.columns.tolist()
    for col in existing_cols:
        if col not in desired_order:
            desired_order.append(col)
    
    # Only include columns that actually exist
    final_order = [col for col in desired_order if col in df.columns]
    
    df = df[final_order]
    
    # Verify results
    print(f"\n6. Verification after fixes")
    print(f"   Rows with null game_date: {df['game_date'].isna().sum()}")
    print(f"   Rows with null game_datetime_utc: {df['game_datetime_utc'].isna().sum()}")
    print(f"   First 10 columns: {', '.join(df.columns[:10].tolist())}")
    
    # Show sample
    print(f"\n7. Sample of fixed data (first 3 rows)")
    sample_cols = ['event_id', 'team', 'game_date', 'game_datetime_utc', 'fgm', 'fga', 'points_for']
    sample_cols = [c for c in sample_cols if c in df.columns]
    print(df[sample_cols].head(3).to_string(index=False))
    
    # Write fixed CSV
    print(f"\n8. Writing fixed CSV to {CSV_PATH}")
    df.to_csv(CSV_PATH, index=False)
    
    print(f"\n" + "="*60)
    print("DONE! CSV has been fixed.")
    print(f"Backup saved to: {BACKUP_PATH}")
    print("="*60)

if __name__ == "__main__":
    main()
