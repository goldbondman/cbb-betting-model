#!/usr/bin/env python3
"""
Diagnostic script to troubleshoot prediction loading issues.
Run this to check:
1. Supabase connectivity
2. RLS policy permissions
3. Data availability in various tables
4. Column schemas
"""

import os
import sys
from pathlib import Path

# Add repo root to path
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from core.supabase_utils import get_public_supabase_client, read_public_supabase_creds


def check_credentials():
    """Check if Supabase credentials are available."""
    print("\n" + "="*80)
    print("CHECKING CREDENTIALS")
    print("="*80)
    
    url, key = read_public_supabase_creds()
    
    if url:
        print(f"✓ SUPABASE_URL is set: {url[:30]}...")
    else:
        print("✗ SUPABASE_URL is NOT set")
        
    if key:
        print(f"✓ SUPABASE_ANON_KEY is set: {key[:20]}...")
    else:
        print("✗ SUPABASE_ANON_KEY is NOT set")
    
    return url and key


def check_client():
    """Check if Supabase client can be created."""
    print("\n" + "="*80)
    print("CHECKING CLIENT")
    print("="*80)
    
    try:
        client = get_public_supabase_client()
        if client:
            print("✓ Supabase client created successfully")
            return client
        else:
            print("✗ Failed to create Supabase client (returned None)")
            return None
    except Exception as e:
        print(f"✗ Exception creating Supabase client: {e}")
        return None


def check_table(client, table_name, schema="public"):
    """Check data availability in a table."""
    print(f"\n{'='*80}")
    print(f"CHECKING TABLE: {schema}.{table_name}")
    print('='*80)
    
    try:
        # Try to query the table
        if schema == "public":
            resp = client.table(table_name).select("*").limit(5).execute()
        else:
            resp = client.schema(schema).table(table_name).select("*").limit(5).execute()
        
        data = resp.data or []
        count = len(data)
        
        if count > 0:
            print(f"✓ Table exists and contains data ({count} rows returned, limit 5)")
            print(f"\nColumns available:")
            if data:
                cols = list(data[0].keys())
                for col in sorted(cols):
                    print(f"  - {col}")
            
            print(f"\nSample row (first row):")
            if data:
                for key, value in data[0].items():
                    val_str = str(value)[:50]
                    print(f"  {key}: {val_str}")
        else:
            print(f"⚠ Table exists but contains no data (empty result)")
        
        return True
        
    except Exception as e:
        error_msg = str(e)
        if "permission denied" in error_msg.lower() or "insufficient_privilege" in error_msg.lower():
            print(f"✗ Permission denied - RLS policy may be blocking anonymous access")
        elif "does not exist" in error_msg.lower() or "not found" in error_msg.lower():
            print(f"✗ Table does not exist")
        else:
            print(f"✗ Error querying table: {error_msg}")
        return False


def check_csv_files():
    """Check if CSV fallback files exist."""
    print(f"\n{'='*80}")
    print("CHECKING CSV FALLBACK FILES")
    print('='*80)
    
    csv_paths = [
        "data/predictions.csv",
        "ml/predictions_latest.csv",
        "ESPN/CSV/espn_games.csv",
        "ESPN/CSV/espn_matchups_model_ready.csv",
    ]
    
    for path in csv_paths:
        full_path = REPO_ROOT / path
        if full_path.exists():
            size_kb = full_path.stat().st_size / 1024
            print(f"✓ {path} exists ({size_kb:.1f} KB)")
        else:
            print(f"✗ {path} does not exist")


def main():
    """Run all diagnostic checks."""
    print("\n" + "="*80)
    print("SUPABASE PREDICTION LOADING DIAGNOSTICS")
    print("="*80)
    
    # Step 1: Check credentials
    creds_ok = check_credentials()
    if not creds_ok:
        print("\n⚠ Cannot proceed without credentials. Set SUPABASE_URL and SUPABASE_ANON_KEY")
        return
    
    # Step 2: Check client
    client = check_client()
    if not client:
        print("\n⚠ Cannot proceed without a working client")
        return
    
    # Step 3: Check tables
    tables_to_check = [
        ("predictions", "public"),
        ("predictions_latest", "raw"),
        ("games", "public"),
        ("teams", "public"),
    ]
    
    for table_name, schema in tables_to_check:
        check_table(client, table_name, schema)
    
    # Step 4: Check CSV files
    check_csv_files()
    
    # Summary
    print("\n" + "="*80)
    print("DIAGNOSTIC SUMMARY")
    print("="*80)
    print("""
If predictions are not loading:
1. Check that data exists in at least one of: public.predictions, raw.predictions_latest, or CSV files
2. Verify RLS policies allow anonymous reads (should see data above, not permission errors)
3. Ensure daily prediction pipeline has run (check GitHub Actions)
4. Check that game_datetime_utc dates match expected range

Next steps:
- If tables are empty: Run the daily prediction pipeline
- If permission denied: Apply the RLS policy migration
- If tables don't exist: Run Supabase migrations
- If all else fails: Use CSV fallback by placing files in data/ or ml/ directories
    """)


if __name__ == "__main__":
    main()
