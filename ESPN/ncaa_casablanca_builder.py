#!/usr/bin/env python3
"""
NCAA Casablanca Box Score Builder
Fetches and parses NCAA Casablanca JSON feeds to generate CSV files.

Outputs:
- ncaa_games.csv                  (scoreboard snapshot, one row per game)
- ncaa_team_game_logs.csv         (team-game rows with box score stats)
- ncaa_player_boxscores.csv       (player box score rows)
"""

import sys
import os
import time
from datetime import datetime, timedelta
from typing import List, Optional

import pandas as pd

# Add ESPN directory to path for imports
_ESPN_DIR = os.path.dirname(os.path.abspath(__file__))
if _ESPN_DIR not in sys.path:
    sys.path.insert(0, _ESPN_DIR)

from ncaa_casablanca_config import (
    PARSE_VERSION,
    SOURCE_NAME,
    OUT_NCAA_GAMES,
    OUT_NCAA_TEAM_LOGS,
    OUT_NCAA_PLAYER_BOX,
    CSV_SCHEMAS,
)

from ncaa_casablanca_http_client import (
    fetch_scoreboard,
    fetch_scoreboard_by_date,
    fetch_boxscore,
)

from ncaa_casablanca_parsers import (
    parse_scoreboard_game,
    parse_boxscore_json,
    _utc_now_iso,
)


def _ensure_csv_exists(filepath: str, columns: List[str]) -> None:
    """Create empty CSV with headers if it doesn't exist."""
    if not os.path.exists(filepath):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        df = pd.DataFrame(columns=columns)
        df.to_csv(filepath, index=False)


def _append_dedupe_write(filepath: str, new_data: pd.DataFrame, subset_keys: List[str]) -> pd.DataFrame:
    """
    Append new data to existing CSV, deduplicate, and write back.
    
    Args:
        filepath: Path to CSV file
        new_data: New data to append
        subset_keys: Columns to use for deduplication
        
    Returns:
        Combined DataFrame after deduplication
    """
    if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
        existing = pd.read_csv(filepath)
        combined = pd.concat([existing, new_data], ignore_index=True)
    else:
        combined = new_data.copy()
    
    # Deduplicate - keep last occurrence
    combined = combined.drop_duplicates(subset=subset_keys, keep="last")
    
    # Write back
    combined.to_csv(filepath, index=False)
    return combined


def fetch_scoreboard_games_for_date(date_str: str, verbose: bool = True) -> List[dict]:
    """
    Fetch and parse scoreboard for a single date.
    
    Args:
        date_str: Date in YYYY-MM-DD format
        verbose: Print progress messages
        
    Returns:
        List of parsed game dictionaries
    """
    try:
        data = fetch_scoreboard_by_date(date_str)
    except Exception as e:
        if verbose:
            print(f"[ERROR] Failed to fetch scoreboard for {date_str}: {e}")
        return []
    
    # NCAA Casablanca returns games in various formats - be flexible
    games = data.get("games", []) or data.get("scoreboard", {}).get("games", []) or []
    
    rows = []
    for game in games:
        try:
            parsed = parse_scoreboard_game(game)
            if parsed:
                parsed["parse_version"] = PARSE_VERSION
                rows.append(parsed)
        except Exception as e:
            if verbose:
                print(f"[WARN] Failed to parse game: {e}")
    
    return rows


def build_ncaa_games_csv(days_back: int = 3, verbose: bool = True) -> pd.DataFrame:
    """
    Build games CSV from NCAA Casablanca scoreboard data.
    
    Args:
        days_back: Number of days to fetch (including today)
        verbose: Print progress messages
        
    Returns:
        DataFrame with all games
    """
    # Ensure CSV exists
    _ensure_csv_exists(OUT_NCAA_GAMES, CSV_SCHEMAS["games"])
    
    # Build date range
    today = datetime.now().date()
    dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days_back)]
    
    all_rows = []
    for date_str in dates:
        rows = fetch_scoreboard_games_for_date(date_str, verbose)
        all_rows.extend(rows)
        if verbose:
            print(f"{date_str}: {len(rows)} games fetched")
        
        # Rate limiting
        time.sleep(0.5)
    
    if not all_rows:
        if verbose:
            print("No games returned from scoreboard.")
        return pd.DataFrame(columns=CSV_SCHEMAS["games"])
    
    df_new = pd.DataFrame(all_rows)
    df_all = _append_dedupe_write(OUT_NCAA_GAMES, df_new, subset_keys=["game_id"])
    
    if verbose:
        print(f"{OUT_NCAA_GAMES} total rows: {len(df_all)}")
    
    return df_all


def fetch_and_parse_boxscore(game_id: str, verbose: bool = True) -> Optional[dict]:
    """
    Fetch and parse box score for a single game.
    
    Args:
        game_id: NCAA game ID
        verbose: Print progress messages
        
    Returns:
        Parsed box score dictionary or None if failed
    """
    try:
        data = fetch_boxscore(game_id)
        parsed = parse_boxscore_json(data, game_id)
        return parsed
    except Exception as e:
        if verbose:
            print(f"[WARN] Failed to fetch/parse boxscore for game {game_id}: {e}")
        return None


def build_ncaa_boxscore_csvs(game_ids: List[str], verbose: bool = True) -> None:
    """
    Build team logs and player boxscore CSVs from NCAA Casablanca box score data.
    
    Args:
        game_ids: List of NCAA game IDs to fetch
        verbose: Print progress messages
    """
    # Ensure CSVs exist
    _ensure_csv_exists(OUT_NCAA_TEAM_LOGS, CSV_SCHEMAS["team_logs"])
    _ensure_csv_exists(OUT_NCAA_PLAYER_BOX, CSV_SCHEMAS["player_box"])
    
    team_rows = []
    player_rows = []
    
    for i, game_id in enumerate(game_ids, 1):
        parsed = fetch_and_parse_boxscore(game_id, verbose)
        if not parsed:
            continue
        
        # Extract team logs
        home = parsed["home"].copy()
        away = parsed["away"].copy()
        
        for team in [home, away]:
            team.update({
                "game_id": parsed["game_id"],
                "game_date": parsed["game_date"],
                "game_datetime": parsed["game_datetime"],
                "venue": parsed["venue"],
                "pulled_at_utc": _utc_now_iso(),
                "source": SOURCE_NAME,
                "parse_version": PARSE_VERSION,
            })
        
        team_rows.append(home)
        team_rows.append(away)
        
        # Extract player box scores
        for player in parsed["players_home"]:
            player.update({
                "game_id": parsed["game_id"],
                "pulled_at_utc": _utc_now_iso(),
                "source": SOURCE_NAME,
                "parse_version": PARSE_VERSION,
            })
            player_rows.append(player)
        
        for player in parsed["players_away"]:
            player.update({
                "game_id": parsed["game_id"],
                "pulled_at_utc": _utc_now_iso(),
                "source": SOURCE_NAME,
                "parse_version": PARSE_VERSION,
            })
            player_rows.append(player)
        
        if verbose and i % 10 == 0:
            print(f"Processed {i}/{len(game_ids)} box scores...")
        
        # Rate limiting
        time.sleep(0.5)
    
    # Write team logs
    if team_rows:
        df_team = pd.DataFrame(team_rows)
        df_team_all = _append_dedupe_write(
            OUT_NCAA_TEAM_LOGS, 
            df_team, 
            subset_keys=["game_id", "team"]
        )
        if verbose:
            print(f"{OUT_NCAA_TEAM_LOGS} total rows: {len(df_team_all)}")
    
    # Write player box scores
    if player_rows:
        df_player = pd.DataFrame(player_rows)
        df_player_all = _append_dedupe_write(
            OUT_NCAA_PLAYER_BOX,
            df_player,
            subset_keys=["game_id", "team", "player_name"]
        )
        if verbose:
            print(f"{OUT_NCAA_PLAYER_BOX} total rows: {len(df_player_all)}")


def run_pipeline(days_back: int = 3, verbose: bool = True) -> None:
    """
    Main pipeline: Fetch scoreboard, then fetch box scores for all games.
    
    Args:
        days_back: Number of days to fetch (including today)
        verbose: Print progress messages
    """
    print(f"NCAA Casablanca Pipeline Started | DAYS_BACK={days_back} | VERSION={PARSE_VERSION}")
    print(f"Started at: {_utc_now_iso()}")
    
    # Step 1: Fetch scoreboard data
    print("\n=== Step 1: Fetching Scoreboard ===")
    games_df = build_ncaa_games_csv(days_back=days_back, verbose=verbose)
    
    if games_df.empty:
        print("No games found. Exiting.")
        return
    
    # Step 2: Fetch box scores for all games
    print("\n=== Step 2: Fetching Box Scores ===")
    game_ids = games_df["game_id"].unique().tolist()
    print(f"Found {len(game_ids)} unique games to fetch")
    
    build_ncaa_boxscore_csvs(game_ids, verbose=verbose)
    
    print("\n=== Pipeline Complete ===")
    print(f"Completed at: {_utc_now_iso()}")


def main():
    """Main entry point."""
    # Allow DAYS_BACK to be set via environment variable
    days_back = int(os.getenv("DAYS_BACK", "3"))
    run_pipeline(days_back=days_back, verbose=True)


if __name__ == "__main__":
    main()
