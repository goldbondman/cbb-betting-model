"""
Multi-Source Data Refresh Script
Replaces single-source fetching with multi-source integrity merge.

This script fetches data from ESPN, NCAA Casablanca, and Henry API,
performs integrity checks, and outputs merged results to the standard
CSV location for backward compatibility with existing pipeline.
"""

import sys
import os
from datetime import datetime, timedelta
import argparse
import logging

# Add core directory to path
_CORE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core")
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

from multi_source_fetcher import MultiSourceFetcher

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description='Fetch college basketball data from multiple sources with integrity checks'
    )
    parser.add_argument(
        '--date', 
        type=str,
        help='Date to fetch (YYYY-MM-DD), default: today'
    )
    parser.add_argument(
        '--days-back',
        type=int,
        default=7,
        help='Number of days to fetch (from today backwards), default: 7'
    )
    parser.add_argument(
        '--output',
        type=str,
        default='ESPN/CSV/espn_games_merged.csv',
        help='Output CSV file path, default: ESPN/CSV/espn_games_merged.csv'
    )
    parser.add_argument(
        '--conflict-log',
        type=str,
        default='ESPN/CSV/data_conflicts.csv',
        help='Output file for conflict log, default: ESPN/CSV/data_conflicts.csv'
    )
    parser.add_argument(
        '--disable-espn',
        action='store_true',
        help='Disable ESPN source'
    )
    parser.add_argument(
        '--disable-ncaa',
        action='store_true',
        help='Disable NCAA source'
    )
    parser.add_argument(
        '--disable-henry',
        action='store_true',
        help='Disable Henry API source'
    )
    parser.add_argument(
        '--disable-cbbpy',
        action='store_true',
        help='Disable CBBpy source'
    )
    parser.add_argument(
        '--enable-cbbd',
        action='store_true',
        help='Enable CBBD source (requires CBBD_API_KEY)'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )
    parser.add_argument(
        '--fail-on-conflicts',
        action='store_true',
        help='Exit with error if conflicts are detected'
    )
    
    args = parser.parse_args()
    
    # Configure verbose logging if requested
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Initialize multi-source fetcher
    try:
        fetcher = MultiSourceFetcher(
            enable_espn=not args.disable_espn,
            enable_ncaa=not args.disable_ncaa,
            enable_henry=not args.disable_henry,
            enable_cbbpy=not args.disable_cbbpy,
            enable_cbbd=args.enable_cbbd
        )
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        return 1
    
    # Determine dates to fetch
    if args.date:
        dates = [args.date]
        logger.info(f"Fetching single date: {args.date}")
    else:
        # Fetch last N days
        dates = []
        for i in range(args.days_back):
            date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            dates.append(date)
        logger.info(f"Fetching last {args.days_back} days: {dates[0]} to {dates[-1]}")
    
    # Fetch and merge data
    all_games = []
    all_conflicts = []
    total_conflicts = 0
    
    for date in dates:
        try:
            logger.info(f"Processing {date}...")
            games, report = fetcher.fetch_date(date, allow_partial=True)
            
            # Log report
            logger.info(f"{date}: {len(games)} games, {report.total_conflicts} conflicts")
            
            if report.failed_sources:
                logger.warning(f"{date}: Failed sources: {', '.join(report.failed_sources)}")
            
            all_games.extend(games)
            total_conflicts += report.total_conflicts
            
            # Collect conflicts for logging
            for game in games:
                for conflict in game.conflicts:
                    all_conflicts.append({
                        'date': date,
                        'game_id': conflict.game_id,
                        'field': conflict.field_name,
                        'values': str(conflict.values),
                        'resolved_value': conflict.resolved_value,
                        'resolution_method': conflict.resolution_method
                    })
        
        except Exception as e:
            logger.error(f"Failed to process {date}: {e}")
            continue
    
    # Save merged games to CSV
    if all_games:
        logger.info(f"Saving {len(all_games)} games to {args.output}")
        fetcher.save_to_csv(all_games, args.output, include_metadata=True)
        logger.info(f"Successfully wrote {args.output}")
    else:
        logger.warning("No games to save")
        return 1
    
    # Save conflict log if there are conflicts
    if all_conflicts and args.conflict_log:
        import pandas as pd
        conflict_df = pd.DataFrame(all_conflicts)
        conflict_df.to_csv(args.conflict_log, index=False)
        logger.info(f"Saved {len(all_conflicts)} conflicts to {args.conflict_log}")
    
    # Print summary
    print("\n" + "=" * 60)
    print("Multi-Source Data Refresh Summary")
    print("=" * 60)
    print(f"Dates processed: {len(dates)}")
    print(f"Total games: {len(all_games)}")
    print(f"Total conflicts detected: {total_conflicts}")
    
    single_source_count = sum(1 for g in all_games if g.source_count == 1)
    multi_source_count = sum(1 for g in all_games if g.source_count > 1)
    print(f"Games from single source: {single_source_count}")
    print(f"Games from multiple sources: {multi_source_count}")
    
    if total_conflicts > 0:
        print(f"\nConflicts logged to: {args.conflict_log}")
    
    print("=" * 60)
    
    # Exit with error if conflicts detected and flag set
    if total_conflicts > 0 and args.fail_on_conflicts:
        logger.error("Exiting with error due to conflicts (--fail-on-conflicts)")
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
