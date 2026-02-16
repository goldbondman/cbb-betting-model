#!/usr/bin/env python3
"""
Standalone script to fetch ESPN injury data for all teams.
Reads team IDs from espn_teams.csv and outputs to CSV/espn_injuries.csv
"""

import sys
import os
import logging
import pandas as pd

# Add ESPN directory to path so we can import modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from espn_injuries import fetch_injuries_for_teams
from espn_config import OUT_INJURIES, OUT_TEAMS
from file_io import _atomic_csv_write

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """
    Main entry point for fetching ESPN injury data.
    """
    logger.info("Starting ESPN injury data fetch...")
    
    # Read team IDs from espn_teams.csv
    if not os.path.exists(OUT_TEAMS):
        logger.error(f"Teams CSV not found: {OUT_TEAMS}")
        logger.error("Please run espn_boxscore_builder_modular.py first to generate teams data.")
        sys.exit(1)
    
    logger.info(f"Reading team IDs from {OUT_TEAMS}...")
    teams_df = pd.read_csv(OUT_TEAMS)
    
    if "espn_id" not in teams_df.columns:
        logger.error(f"Expected 'espn_id' column not found in {OUT_TEAMS}")
        sys.exit(1)
    
    team_ids = teams_df["espn_id"].unique().astype(str).tolist()
    logger.info(f"Found {len(team_ids)} teams to fetch injuries for")
    
    # Fetch injuries for all teams
    logger.info("Fetching injury data from ESPN API...")
    injuries_df = fetch_injuries_for_teams(team_ids)
    
    logger.info(f"Fetched {len(injuries_df)} injury records")
    
    # Write to CSV using atomic write operation
    logger.info(f"Writing injury data to {OUT_INJURIES}...")
    _atomic_csv_write(injuries_df, OUT_INJURIES)
    
    logger.info(f"Successfully wrote {len(injuries_df)} injury records to {OUT_INJURIES}")
    
    # Print summary statistics
    if len(injuries_df) > 0:
        logger.info("=== Injury Summary ===")
        logger.info(f"Total injuries: {len(injuries_df)}")
        logger.info(f"Unique teams with injuries: {injuries_df['team_id'].nunique()}")
        
        status_counts = injuries_df['status'].value_counts()
        logger.info("Injury status breakdown:")
        for status, count in status_counts.items():
            logger.info(f"  {status}: {count}")


if __name__ == "__main__":
    main()
