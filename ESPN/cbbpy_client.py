"""
CBBpy Client Wrapper
Provides ESPN API data access via the CBBpy library with fallback to direct ESPN API.
Maintains compatibility with existing ESPN HTTP client interface.
"""

import os
from typing import Dict, Any, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Configuration
ENABLE_CBBPY = os.getenv("ENABLE_CBBPY", "1").strip().lower() in ("1", "true", "yes")
CBBPY_FALLBACK_TO_ESPN = os.getenv("CBBPY_FALLBACK_TO_ESPN", "1").strip().lower() in ("1", "true", "yes")


def _convert_cbbpy_game_info_to_espn_format(game_info_df, game_id: str) -> Optional[Dict[str, Any]]:
    """
    Convert CBBpy game info DataFrame to ESPN API format.
    
    Args:
        game_info_df: DataFrame from CBBpy get_game_info()
        game_id: ESPN game ID
        
    Returns:
        Dictionary in ESPN summary format or None if conversion fails
    """
    try:
        if game_info_df is None or game_info_df.empty:
            return None
            
        row = game_info_df.iloc[0]
        
        # Build ESPN-compatible structure
        # This is a simplified version focusing on key fields
        espn_format = {
            "header": {
                "id": game_id,
                "competitions": [{
                    "id": game_id,
                    "date": row.get("game_date", ""),
                    "attendance": row.get("attendance", 0),
                    "venue": {
                        "fullName": row.get("venue", "")
                    },
                    "competitors": [],
                    "status": {
                        "type": {
                            "completed": row.get("game_status", "").lower() == "final"
                        }
                    }
                }]
            }
        }
        
        return espn_format
        
    except Exception as e:
        logger.error(f"Failed to convert CBBpy game info to ESPN format: {e}")
        return None


def _convert_cbbpy_boxscore_to_espn_format(boxscore_df, game_id: str) -> Optional[Dict[str, Any]]:
    """
    Convert CBBpy boxscore DataFrame to ESPN API format.
    
    Args:
        boxscore_df: DataFrame from CBBpy get_game_boxscore()
        game_id: ESPN game ID
        
    Returns:
        Dictionary in ESPN summary format or None if conversion fails
    """
    try:
        if boxscore_df is None or boxscore_df.empty:
            return None
        
        # Group by team to create team-level stats
        teams = []
        for team_name in boxscore_df['team'].unique():
            team_df = boxscore_df[boxscore_df['team'] == team_name]
            
            # Aggregate team stats from player stats
            team_stats = {
                "team": {
                    "id": str(team_df['team_id'].iloc[0]) if 'team_id' in team_df.columns else "",
                    "displayName": team_name
                },
                "statistics": []
            }
            
            # Add statistics if available
            if 'FGM' in team_df.columns and 'FGA' in team_df.columns:
                fgm = team_df['FGM'].sum()
                fga = team_df['FGA'].sum()
                team_stats["statistics"].append({
                    "name": "fieldGoalsMade-fieldGoalsAttempted",
                    "abbreviation": "FG",
                    "displayValue": f"{int(fgm)}-{int(fga)}"
                })
            
            if 'TPM' in team_df.columns and 'TPA' in team_df.columns:
                tpm = team_df['TPM'].sum()
                tpa = team_df['TPA'].sum()
                team_stats["statistics"].append({
                    "name": "threePointFieldGoalsMade-threePointFieldGoalsAttempted",
                    "abbreviation": "3PT",
                    "displayValue": f"{int(tpm)}-{int(tpa)}"
                })
            
            if 'FTM' in team_df.columns and 'FTA' in team_df.columns:
                ftm = team_df['FTM'].sum()
                fta = team_df['FTA'].sum()
                team_stats["statistics"].append({
                    "name": "freeThrowsMade-freeThrowsAttempted",
                    "abbreviation": "FT",
                    "displayValue": f"{int(ftm)}-{int(fta)}"
                })
            
            if 'TO' in team_df.columns:
                to = team_df['TO'].sum()
                team_stats["statistics"].append({
                    "name": "turnovers",
                    "abbreviation": "TO",
                    "displayValue": str(int(to))
                })
            
            if 'OREB' in team_df.columns:
                oreb = team_df['OREB'].sum()
                team_stats["statistics"].append({
                    "name": "offensiveRebounds",
                    "abbreviation": "OREB",
                    "displayValue": str(int(oreb))
                })
            
            if 'DREB' in team_df.columns:
                dreb = team_df['DREB'].sum()
                team_stats["statistics"].append({
                    "name": "defensiveRebounds",
                    "abbreviation": "DREB",
                    "displayValue": str(int(dreb))
                })
            
            if 'REB' in team_df.columns:
                reb = team_df['REB'].sum()
                team_stats["statistics"].append({
                    "name": "totalRebounds",
                    "abbreviation": "REB",
                    "displayValue": str(int(reb))
                })
            
            teams.append(team_stats)
        
        # Build ESPN-compatible structure
        espn_format = {
            "boxscore": {
                "teams": teams
            }
        }
        
        return espn_format
        
    except Exception as e:
        logger.error(f"Failed to convert CBBpy boxscore to ESPN format: {e}")
        return None


def fetch_summary_cbbpy(event_id: str) -> Optional[Dict[str, Any]]:
    """
    Fetch game summary/boxscore using CBBpy library.
    
    Args:
        event_id: ESPN event ID
        
    Returns:
        ESPN-compatible summary data or None if fetch fails
    """
    if not ENABLE_CBBPY:
        return None
    
    try:
        import cbbpy.mens_scraper as scraper
        
        logger.info(f"Fetching game {event_id} via CBBpy")
        
        # Get game info and boxscore
        game_info_df = scraper.get_game_info(event_id)
        boxscore_df = scraper.get_game_boxscore(event_id)
        
        # Convert to ESPN format
        info_dict = _convert_cbbpy_game_info_to_espn_format(game_info_df, event_id)
        box_dict = _convert_cbbpy_boxscore_to_espn_format(boxscore_df, event_id)
        
        if not info_dict or not box_dict:
            logger.warning(f"CBBpy returned incomplete data for game {event_id}")
            return None
        
        # Merge the two dictionaries
        result = {**info_dict, **box_dict}
        
        logger.info(f"Successfully fetched game {event_id} via CBBpy")
        return result
        
    except ImportError:
        logger.warning("CBBpy not installed, falling back to direct ESPN API")
        return None
    except Exception as e:
        logger.error(f"CBBpy fetch failed for game {event_id}: {e}")
        return None


def fetch_scoreboard_cbbpy(date_yyyymmdd: str) -> Optional[Dict[str, Any]]:
    """
    Fetch scoreboard data using CBBpy library.
    
    Note: CBBpy scoreboard scraping returns only game IDs, not the full
    competition/status/score data that the ESPN API provides. The downstream
    parser (parse_scoreboard_event) requires the full ESPN API format,
    so CBBpy should NOT be used for scoreboard fetches. Always return None
    to fall through to the direct ESPN API.
    
    Args:
        date_yyyymmdd: Date in YYYYMMDD format
        
    Returns:
        None – always defers to the direct ESPN API for scoreboard data
    """
    # CBBpy's scoreboard scraper (get_game_ids) only returns game IDs via
    # HTML scraping with a fixed seasontype=2 URL.  The minimal event dicts
    # it produces ({id, date}) lack the competitions/status/score structure
    # that parse_scoreboard_event() requires, so every event silently fails
    # to parse.  Returning None here ensures the caller falls back to the
    # direct ESPN JSON API which returns the full payload.
    return None


def fetch_summary_with_cbbpy_fallback(event_id: str, timeout: int = 25) -> Dict[str, Any]:
    """
    Fetch game summary with CBBpy as primary source and ESPN direct API as fallback.
    
    Args:
        event_id: ESPN event ID
        timeout: Request timeout for fallback ESPN API call
        
    Returns:
        Game summary data
        
    Raises:
        RuntimeError: If both CBBpy and ESPN direct API fail
    """
    # Try CBBpy first
    if ENABLE_CBBPY:
        result = fetch_summary_cbbpy(event_id)
        if result:
            return result
        
        if not CBBPY_FALLBACK_TO_ESPN:
            raise RuntimeError(f"CBBpy fetch failed for game {event_id} and fallback is disabled")
    
    # Fall back to direct ESPN API (call fetch_with_retry directly to
    # avoid infinite recursion through espn_http_client.fetch_summary
    # which would re-enter this function when ENABLE_CBBPY is True).
    logger.info(f"Falling back to direct ESPN API for game {event_id}")
    from espn_config import ESPN_SUMMARY_URL, DEFAULT_HEADERS as ESPN_HEADERS
    from espn_http_client import fetch_with_retry
    url = ESPN_SUMMARY_URL.format(event_id=event_id)
    return fetch_with_retry(url, headers=ESPN_HEADERS, timeout=timeout)


def fetch_scoreboard_with_cbbpy_fallback(date_yyyymmdd: str, timeout: int = 25) -> Dict[str, Any]:
    """
    Fetch scoreboard with CBBpy as primary source and ESPN direct API as fallback.
    
    Args:
        date_yyyymmdd: Date in YYYYMMDD format
        timeout: Request timeout for fallback ESPN API call
        
    Returns:
        Scoreboard data
        
    Raises:
        RuntimeError: If both CBBpy and ESPN direct API fail
    """
    # Try CBBpy first
    if ENABLE_CBBPY:
        result = fetch_scoreboard_cbbpy(date_yyyymmdd)
        if result:
            return result
        
        if not CBBPY_FALLBACK_TO_ESPN:
            raise RuntimeError(f"CBBpy scoreboard fetch failed for {date_yyyymmdd} and fallback is disabled")
    
    # Fall back to direct ESPN API (call fetch_with_retry directly to
    # avoid infinite recursion through espn_http_client.fetch_scoreboard
    # which would re-enter this function when ENABLE_CBBPY is True).
    logger.info(f"Falling back to direct ESPN API for scoreboard {date_yyyymmdd}")
    from espn_config import ESPN_SCOREBOARD_URL, DEFAULT_HEADERS as ESPN_HEADERS
    from espn_http_client import fetch_with_retry
    url = ESPN_SCOREBOARD_URL.format(date=date_yyyymmdd)
    return fetch_with_retry(url, headers=ESPN_HEADERS, timeout=timeout)
