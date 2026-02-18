"""
Multi-Source Data Fetcher
Orchestrates fetching from ESPN, NCAA, and Henry API with integrity merge.
"""

import logging
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
import pandas as pd

from data_sources import SourceType, GameData
from source_implementations import ESPNDataSource, NCAADataSource, HenryAPIDataSource, CBBpyDataSource, CBBDDataSource
from integrity_merger import IntegrityMerger, MergedGame, IntegrityReport

logger = logging.getLogger(__name__)


class MultiSourceFetcher:
    """
    Orchestrates data fetching from multiple sources with integrity checks.
    
    Usage:
        fetcher = MultiSourceFetcher()
        games, report = fetcher.fetch_date("2024-01-15")
        
        # Check for issues
        if report.failed_sources:
            print(f"Warning: {report.failed_sources} failed")
        
        # Access merged data
        for merged_game in games:
            print(f"{merged_game.game.home_team} vs {merged_game.game.away_team}")
            if merged_game.has_conflicts:
                print(f"  Conflicts: {len(merged_game.conflicts)}")
    """
    
    def __init__(
        self,
        enable_espn: bool = True,
        enable_ncaa: bool = True,
        enable_henry: bool = True,
        enable_cbbpy: bool = True,
        enable_cbbd: bool = False,
        source_priority: List[SourceType] = None
    ):
        """
        Initialize multi-source fetcher.
        
        Args:
            enable_espn: Enable ESPN data source
            enable_ncaa: Enable NCAA Casablanca data source
            enable_henry: Enable Henry API data source
            enable_cbbpy: Enable CBBpy data source
            enable_cbbd: Enable CBBD (College Basketball Data) test source
            source_priority: Source priority for conflict resolution
        """
        self.sources = []
        
        if enable_espn:
            self.sources.append(ESPNDataSource())
        if enable_ncaa:
            self.sources.append(NCAADataSource())
        if enable_henry:
            self.sources.append(HenryAPIDataSource())
        if enable_cbbpy:
            self.sources.append(CBBpyDataSource())
        if enable_cbbd:
            self.sources.append(CBBDDataSource())
        
        if not self.sources:
            raise ValueError("At least one data source must be enabled")
        
        self.merger = IntegrityMerger(source_priority=source_priority)
        logger.info(f"Initialized with {len(self.sources)} sources: {[s.get_source_type().value for s in self.sources]}")
    
    def fetch_date(
        self,
        date: str,
        allow_partial: bool = True
    ) -> Tuple[List[MergedGame], IntegrityReport]:
        """
        Fetch and merge games for a specific date.
        
        Args:
            date: Date in YYYY-MM-DD format
            allow_partial: If True, returns data even if some sources fail
            
        Returns:
            Tuple of (merged games, integrity report)
            
        Raises:
            RuntimeError: If all sources fail and allow_partial=False
        """
        logger.info(f"Fetching games for {date} from {len(self.sources)} sources")
        
        # Fetch from all sources in parallel (could be optimized with threading)
        results = []
        for source in self.sources:
            try:
                result = source.fetch_games(date)
                results.append(result)
                
                if result.success:
                    logger.info(f"{source.get_source_type().value}: {len(result.games)} games")
                else:
                    logger.warning(f"{source.get_source_type().value}: FAILED - {result.error}")
            except Exception as e:
                logger.error(f"{source.get_source_type().value}: EXCEPTION - {e}")
        
        # Check if we have any successful results
        successful = [r for r in results if r.success]
        if not successful and not allow_partial:
            raise RuntimeError(f"All data sources failed for {date}")
        
        # Merge results
        merged_games, report = self.merger.merge(results)
        
        logger.info(f"Merge complete: {len(merged_games)} games, {report.total_conflicts} conflicts")
        
        return merged_games, report
    
    def fetch_date_range(
        self,
        start_date: str,
        end_date: str,
        allow_partial: bool = True
    ) -> Dict[str, Tuple[List[MergedGame], IntegrityReport]]:
        """
        Fetch and merge games for a date range.
        
        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format (inclusive)
            allow_partial: If True, continues even if some dates fail
            
        Returns:
            Dict mapping date -> (merged games, report)
        """
        results = {}
        
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        
        current = start
        while current <= end:
            date_str = current.strftime("%Y-%m-%d")
            try:
                merged_games, report = self.fetch_date(date_str, allow_partial=allow_partial)
                results[date_str] = (merged_games, report)
            except Exception as e:
                logger.error(f"Failed to fetch {date_str}: {e}")
                if not allow_partial:
                    raise
            
            current += timedelta(days=1)
        
        return results
    
    def to_dataframe(self, merged_games: List[MergedGame]) -> pd.DataFrame:
        """
        Convert merged games to a pandas DataFrame.
        
        Args:
            merged_games: List of MergedGame objects
            
        Returns:
            DataFrame with game data and metadata
        """
        rows = []
        for merged in merged_games:
            game = merged.game
            row = {
                'game_id': game.game_id,
                'date': game.date,
                'home_team': game.home_team,
                'away_team': game.away_team,
                'home_score': game.home_score,
                'away_score': game.away_score,
                'status': game.status,
                'venue': game.venue,
                'game_datetime': game.game_datetime,
                'market_spread': game.market_spread,
                'market_total': game.market_total,
                'market_home_ml': game.market_home_ml,
                'market_away_ml': game.market_away_ml,
                'source': game.source,
                'pulled_at': game.pulled_at,
                # Metadata
                'source_count': merged.source_count,
                'has_conflicts': merged.has_conflicts,
                'conflict_count': len(merged.conflicts),
                'quality_score': merged.quality_score,
            }
            rows.append(row)
        
        return pd.DataFrame(rows)
    
    def save_to_csv(
        self,
        merged_games: List[MergedGame],
        output_path: str,
        include_metadata: bool = True
    ):
        """
        Save merged games to CSV file.
        
        Args:
            merged_games: List of MergedGame objects
            output_path: Path to output CSV file
            include_metadata: Include merge metadata columns
        """
        df = self.to_dataframe(merged_games)
        
        if not include_metadata:
            # Drop metadata columns
            metadata_cols = ['source_count', 'has_conflicts', 'conflict_count', 'quality_score']
            df = df.drop(columns=[c for c in metadata_cols if c in df.columns])
        
        df.to_csv(output_path, index=False)
        logger.info(f"Saved {len(df)} games to {output_path}")


def main():
    """Command-line interface for testing the multi-source fetcher"""
    import sys
    import argparse
    
    parser = argparse.ArgumentParser(description='Fetch college basketball data from multiple sources')
    parser.add_argument('--date', type=str, help='Date to fetch (YYYY-MM-DD), default: today')
    parser.add_argument('--start-date', type=str, help='Start date for range (YYYY-MM-DD)')
    parser.add_argument('--end-date', type=str, help='End date for range (YYYY-MM-DD)')
    parser.add_argument('--output', type=str, help='Output CSV file path')
    parser.add_argument('--disable-espn', action='store_true', help='Disable ESPN source')
    parser.add_argument('--disable-ncaa', action='store_true', help='Disable NCAA source')
    parser.add_argument('--disable-henry', action='store_true', help='Disable Henry API source')
    parser.add_argument('--disable-cbbpy', action='store_true', help='Disable CBBpy source')
    parser.add_argument('--enable-cbbd', action='store_true', help='Enable CBBD test source')
    parser.add_argument('--verbose', action='store_true', help='Enable verbose logging')
    
    args = parser.parse_args()
    
    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # Initialize fetcher
    fetcher = MultiSourceFetcher(
        enable_espn=not args.disable_espn,
        enable_ncaa=not args.disable_ncaa,
        enable_henry=not args.disable_henry,
        enable_cbbpy=not args.disable_cbbpy,
        enable_cbbd=args.enable_cbbd
    )
    
    # Determine date(s) to fetch
    if args.start_date and args.end_date:
        # Date range
        results = fetcher.fetch_date_range(args.start_date, args.end_date)
        
        # Combine all games
        all_games = []
        for date, (games, report) in results.items():
            print(f"\n{date}:")
            print(report.summary())
            all_games.extend(games)
        
        if args.output:
            fetcher.save_to_csv(all_games, args.output)
        else:
            df = fetcher.to_dataframe(all_games)
            print(f"\nTotal games: {len(df)}")
            print(df.head(10))
    
    else:
        # Single date
        date = args.date or datetime.now().strftime("%Y-%m-%d")
        
        games, report = fetcher.fetch_date(date)
        
        print(f"\n{report.summary()}")
        
        if args.output:
            fetcher.save_to_csv(games, args.output)
        else:
            df = fetcher.to_dataframe(games)
            print(f"\nGames for {date}:")
            print(df[['game_id', 'home_team', 'away_team', 'home_score', 'away_score', 'source_count', 'has_conflicts']])


if __name__ == "__main__":
    main()
