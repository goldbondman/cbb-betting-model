#!/usr/bin/env python3
"""
Example usage of the full season box score collection script.

This demonstrates how to use the collector for different scenarios.
"""

from datetime import datetime
from pathlib import Path
from scripts.collect_full_season_boxscores import SeasonBoxScoreCollector


def example_full_season():
    """Example: Collect the full 2025-2026 season."""
    print("Example 1: Full Season Collection")
    print("-" * 60)
    
    collector = SeasonBoxScoreCollector(
        start_date=datetime(2025, 11, 1),
        end_date=datetime(2026, 4, 1),
        output_dir=Path("data/season_boxscores")
    )
    
    # Run the collection
    collector.collect_all_days()
    
    # Save all outputs
    collector.save_audit_report()
    collector.save_full_dataset()
    collector.save_error_log()
    
    # Print summary
    collector.print_summary_report()


def example_custom_date_range():
    """Example: Collect a custom date range."""
    print("\nExample 2: Custom Date Range")
    print("-" * 60)
    
    # Collect just November 2025
    collector = SeasonBoxScoreCollector(
        start_date=datetime(2025, 11, 1),
        end_date=datetime(2025, 11, 30),
        output_dir=Path("data/november_2025")
    )
    
    collector.collect_all_days()
    collector.save_audit_report()
    collector.save_full_dataset()
    collector.save_error_log()
    collector.print_summary_report()


def example_analyze_audit():
    """Example: Analyze the audit report after collection."""
    print("\nExample 3: Audit Analysis")
    print("-" * 60)
    
    import pandas as pd
    
    # Load the audit report
    audit_file = Path("data/season_boxscores/season_collection_audit.csv")
    
    if not audit_file.exists():
        print(f"Audit file not found: {audit_file}")
        print("Run the collection first!")
        return
    
    audit_df = pd.read_csv(audit_file)
    
    # Analysis
    print(f"\nTotal days: {len(audit_df)}")
    print(f"\nStatus counts:")
    print(audit_df['status'].value_counts())
    
    print(f"\nTotal games collected: {audit_df['games_found'].sum()}")
    print(f"Average games per day: {audit_df['games_found'].mean():.1f}")
    
    # Days with most games
    print(f"\nTop 5 days with most games:")
    top_days = audit_df.nlargest(5, 'games_found')[['date', 'games_found']]
    print(top_days.to_string(index=False))
    
    # Check for errors
    errors = audit_df[audit_df['status'] == 'error']
    if len(errors) > 0:
        print(f"\n⚠ Found {len(errors)} days with errors:")
        print(errors[['date', 'error_message']].to_string(index=False))
    else:
        print("\n✓ No errors found!")


def example_verify_coverage():
    """Example: Verify complete coverage of date range."""
    print("\nExample 4: Coverage Verification")
    print("-" * 60)
    
    import pandas as pd
    from datetime import timedelta
    
    audit_file = Path("data/season_boxscores/season_collection_audit.csv")
    
    if not audit_file.exists():
        print(f"Audit file not found: {audit_file}")
        return
    
    audit_df = pd.read_csv(audit_file)
    audit_df['date'] = pd.to_datetime(audit_df['date'])
    
    # Expected date range
    start_date = datetime(2025, 11, 1)
    end_date = datetime(2026, 4, 1)
    expected_days = (end_date - start_date).days + 1
    
    print(f"Expected date range: {start_date.date()} to {end_date.date()}")
    print(f"Expected days: {expected_days}")
    print(f"Actual days in audit: {len(audit_df)}")
    
    # Check for gaps
    dates_in_audit = set(audit_df['date'].dt.date)
    all_expected_dates = {
        (start_date + timedelta(days=i)).date()
        for i in range(expected_days)
    }
    
    missing_dates = all_expected_dates - dates_in_audit
    
    if missing_dates:
        print(f"\n⚠ WARNING: {len(missing_dates)} days missing from audit:")
        for date in sorted(missing_dates):
            print(f"  - {date}")
    else:
        print("\n✓ Complete coverage - all expected days present in audit!")
    
    # Check for duplicates
    duplicates = audit_df[audit_df['date'].duplicated()]
    if len(duplicates) > 0:
        print(f"\n⚠ WARNING: {len(duplicates)} duplicate dates found:")
        print(duplicates[['date']].to_string(index=False))
    else:
        print("✓ No duplicate dates found!")


if __name__ == "__main__":
    print("="*60)
    print("FULL SEASON BOX SCORE COLLECTION - USAGE EXAMPLES")
    print("="*60)
    print()
    
    # Uncomment the example you want to run:
    
    # Full season collection (takes ~7-8 minutes)
    # example_full_season()
    
    # Custom date range (faster, for testing)
    # example_custom_date_range()
    
    # Analyze existing audit file
    example_analyze_audit()
    
    # Verify coverage
    example_verify_coverage()
    
    print("\n" + "="*60)
    print("Examples complete!")
    print("="*60)
