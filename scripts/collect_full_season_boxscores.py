#!/usr/bin/env python3
"""
Full Season Box Score Collection Script

This script collects college basketball box score data for every game played
between November 1, 2025 and April 1, 2026.

Key requirements:
1. Iterate through each individual day (no bulk queries)
2. Log progress for each day processed
3. Track comprehensive audit data (games found, status, errors)
4. Store results in structured format
5. Generate summary audit report showing all days and their status

The goal is full confidence that no day in the season is missing.
"""

import os
import sys
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional
import pandas as pd

# Add parent directories to path to import ESPN modules
script_dir = Path(__file__).parent
repo_root = script_dir.parent
espn_dir = repo_root / "ESPN"
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(espn_dir))

# Import ESPN fetching utilities
from ESPN.espn_boxscore_builder import (
    fetch_scoreboard_games,
    fetch_and_parse_espn_summary,
    _utc_now_iso,
)

# Configuration
START_DATE = datetime(2025, 11, 1)
END_DATE = datetime(2026, 4, 1)
OUTPUT_DIR = repo_root / "data" / "season_boxscores"
AUDIT_FILE = OUTPUT_DIR / "season_collection_audit.csv"
FULL_DATASET_FILE = OUTPUT_DIR / "season_boxscores_full.csv"
ERROR_LOG_FILE = OUTPUT_DIR / "season_collection_errors.json"

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(OUTPUT_DIR / "season_collection.log" if OUTPUT_DIR.exists() else "season_collection.log")
    ]
)
logger = logging.getLogger(__name__)


class DayAuditRecord:
    """Track audit information for a single day."""
    
    def __init__(self, date: datetime):
        self.date = date
        self.date_str = date.strftime("%Y%m%d")
        self.games_found = 0
        self.games_completed = 0
        self.games_in_progress = 0
        self.status = "pending"  # pending, success, empty, error
        self.error_message: Optional[str] = None
        self.fetch_timestamp: Optional[str] = None
        
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "date": self.date.strftime("%Y-%m-%d"),
            "date_yyyymmdd": self.date_str,
            "games_found": self.games_found,
            "games_completed": self.games_completed,
            "games_in_progress": self.games_in_progress,
            "status": self.status,
            "error_message": self.error_message or "",
            "fetch_timestamp": self.fetch_timestamp or "",
        }


class SeasonBoxScoreCollector:
    """Collect box score data for entire season, day by day."""
    
    def __init__(self, start_date: datetime, end_date: datetime, output_dir: Path):
        self.start_date = start_date
        self.end_date = end_date
        self.output_dir = output_dir
        self.audit_records: List[DayAuditRecord] = []
        self.all_games_data: List[Dict[str, Any]] = []
        self.error_log: List[Dict[str, Any]] = []
        
        # Create output directory
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Initialized SeasonBoxScoreCollector")
        logger.info(f"  Start date: {start_date.strftime('%Y-%m-%d')}")
        logger.info(f"  End date: {end_date.strftime('%Y-%m-%d')}")
        logger.info(f"  Output directory: {output_dir}")
        
    def generate_date_range(self) -> List[datetime]:
        """Generate list of all dates in the season (inclusive)."""
        dates = []
        current = self.start_date
        while current <= self.end_date:
            dates.append(current)
            current += timedelta(days=1)
        logger.info(f"Generated {len(dates)} days to process")
        return dates
    
    def fetch_day_data(self, date: datetime) -> DayAuditRecord:
        """
        Fetch box score data for a single day.
        
        Args:
            date: The date to fetch data for
            
        Returns:
            DayAuditRecord with audit information
        """
        audit = DayAuditRecord(date)
        audit.fetch_timestamp = _utc_now_iso()
        
        date_str = date.strftime("%Y%m%d")
        logger.info(f"Processing {date.strftime('%Y-%m-%d')} ({date_str})...")
        
        try:
            # Fetch scoreboard data for this day
            games = fetch_scoreboard_games(date_str)
            
            if not games:
                audit.status = "empty"
                audit.games_found = 0
                logger.info(f"  ✓ No games found for {date_str}")
            else:
                audit.games_found = len(games)
                audit.games_completed = sum(1 for g in games if g.get("completed", False))
                audit.games_in_progress = audit.games_found - audit.games_completed
                audit.status = "success"
                
                # Store the games data
                for game in games:
                    game["collection_date"] = date.strftime("%Y-%m-%d")
                    game["collection_timestamp"] = audit.fetch_timestamp
                    self.all_games_data.append(game)
                
                logger.info(f"  ✓ Found {audit.games_found} games "
                          f"({audit.games_completed} completed, "
                          f"{audit.games_in_progress} in progress)")
                
        except Exception as e:
            audit.status = "error"
            audit.error_message = str(e)
            logger.error(f"  ✗ Error fetching data for {date_str}: {e}")
            
            # Log error for later review
            self.error_log.append({
                "date": date.strftime("%Y-%m-%d"),
                "date_yyyymmdd": date_str,
                "error_type": type(e).__name__,
                "error_message": str(e),
                "timestamp": audit.fetch_timestamp,
            })
        
        return audit
    
    def collect_all_days(self):
        """Iterate through all days and collect data."""
        dates = self.generate_date_range()
        total_days = len(dates)
        
        logger.info(f"\n{'='*60}")
        logger.info(f"Starting collection for {total_days} days")
        logger.info(f"{'='*60}\n")
        
        for i, date in enumerate(dates, 1):
            logger.info(f"[Day {i}/{total_days}]")
            
            audit = self.fetch_day_data(date)
            self.audit_records.append(audit)
            
            # Save incremental audit after every day (for resume capability)
            if i % 10 == 0 or i == total_days:
                self.save_audit_report()
                logger.info(f"  → Incremental audit saved ({i}/{total_days} days processed)")
        
        logger.info(f"\n{'='*60}")
        logger.info(f"Collection complete!")
        logger.info(f"{'='*60}\n")
    
    def save_audit_report(self):
        """Save audit report to CSV."""
        if not self.audit_records:
            logger.warning("No audit records to save")
            return
        
        audit_file = self.output_dir / "season_collection_audit.csv"
        audit_df = pd.DataFrame([record.to_dict() for record in self.audit_records])
        audit_df.to_csv(audit_file, index=False)
        logger.debug(f"Audit report saved to {audit_file}")
    
    def save_full_dataset(self):
        """Save all collected games to a CSV file."""
        if not self.all_games_data:
            logger.warning("No games data collected")
            return
        
        dataset_file = self.output_dir / "season_boxscores_full.csv"
        games_df = pd.DataFrame(self.all_games_data)
        games_df.to_csv(dataset_file, index=False)
        logger.info(f"\nFull dataset saved to {dataset_file}")
        logger.info(f"  Total games: {len(games_df)}")
    
    def save_error_log(self):
        """Save error log to JSON file."""
        if not self.error_log:
            logger.info("No errors to log")
            return
        
        error_file = self.output_dir / "season_collection_errors.json"
        with open(error_file, 'w') as f:
            json.dump(self.error_log, f, indent=2)
        logger.warning(f"\nError log saved to {error_file}")
        logger.warning(f"  Total errors: {len(self.error_log)}")
    
    def print_summary_report(self):
        """Print comprehensive summary report."""
        if not self.audit_records:
            logger.warning("No audit records available for summary")
            return
        
        total_days = len(self.audit_records)
        success_days = sum(1 for r in self.audit_records if r.status == "success")
        empty_days = sum(1 for r in self.audit_records if r.status == "empty")
        error_days = sum(1 for r in self.audit_records if r.status == "error")
        
        total_games = sum(r.games_found for r in self.audit_records)
        completed_games = sum(r.games_completed for r in self.audit_records)
        
        logger.info("\n" + "="*60)
        logger.info("SEASON COLLECTION SUMMARY")
        logger.info("="*60)
        logger.info(f"Date range: {self.start_date.strftime('%Y-%m-%d')} to {self.end_date.strftime('%Y-%m-%d')}")
        logger.info(f"Total days processed: {total_days}")
        logger.info(f"")
        logger.info(f"Status breakdown:")
        logger.info(f"  ✓ Success: {success_days} days ({success_days/total_days*100:.1f}%)")
        logger.info(f"  ○ Empty:   {empty_days} days ({empty_days/total_days*100:.1f}%)")
        logger.info(f"  ✗ Error:   {error_days} days ({error_days/total_days*100:.1f}%)")
        logger.info(f"")
        logger.info(f"Games collected:")
        logger.info(f"  Total games found: {total_games}")
        logger.info(f"  Completed games: {completed_games}")
        logger.info(f"")
        
        if error_days > 0:
            logger.warning(f"Days with errors:")
            for record in self.audit_records:
                if record.status == "error":
                    logger.warning(f"  - {record.date.strftime('%Y-%m-%d')}: {record.error_message}")
        
        # Check for gaps in coverage
        expected_days = (self.end_date - self.start_date).days + 1
        if total_days < expected_days:
            logger.error(f"\n⚠ WARNING: Missing {expected_days - total_days} days from expected range!")
        else:
            logger.info(f"✓ All {expected_days} days in date range were processed")
        
        logger.info("="*60)
        logger.info(f"Audit report: {self.output_dir / 'season_collection_audit.csv'}")
        logger.info(f"Full dataset: {self.output_dir / 'season_boxscores_full.csv'}")
        if self.error_log:
            logger.info(f"Error log: {self.output_dir / 'season_collection_errors.json'}")
        logger.info("="*60 + "\n")


def main():
    """Main entry point."""
    logger.info("="*60)
    logger.info("FULL SEASON BOX SCORE COLLECTION")
    logger.info("="*60)
    logger.info(f"Script: {__file__}")
    logger.info(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("")
    
    # Create collector
    collector = SeasonBoxScoreCollector(
        start_date=START_DATE,
        end_date=END_DATE,
        output_dir=OUTPUT_DIR
    )
    
    # Collect all data
    collector.collect_all_days()
    
    # Save outputs
    collector.save_audit_report()
    collector.save_full_dataset()
    collector.save_error_log()
    
    # Print summary
    collector.print_summary_report()
    
    logger.info(f"Script completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
