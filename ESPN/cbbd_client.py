"""
CBBD (College Basketball Data) Client Wrapper
Provides game data access via the cbbd Python SDK as a parallel test data source.
Converts CBBD responses to the standardised GameData format used by the multi-source layer.
"""

import os
from typing import Dict, List, Optional, Any
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Configuration (mirrors espn_config values but kept local so the module can
# be imported independently during testing)
ENABLE_CBBD = os.getenv("ENABLE_CBBD", "0").strip().lower() in ("1", "true", "yes")
CBBD_API_TOKEN = os.getenv("CBBD_API_TOKEN", "")


def _get_api_client():
    """
    Create and return a configured cbbd ApiClient.

    Returns:
        cbbd.ApiClient or None if cbbd is not installed or token is missing.
    """
    token = CBBD_API_TOKEN or os.getenv("CBBD_API_TOKEN", "")
    if not token:
        logger.warning("CBBD_API_TOKEN not set – skipping CBBD fetch")
        return None

    try:
        import cbbd
    except ImportError:
        logger.warning("cbbd package is not installed")
        return None

    configuration = cbbd.Configuration(access_token=token)
    return cbbd.ApiClient(configuration)


def fetch_games_for_date(date_str: str) -> Optional[List[Dict[str, Any]]]:
    """
    Fetch completed games for a specific date via the CBBD GamesApi.

    Args:
        date_str: Date in YYYY-MM-DD format.

    Returns:
        List of game dicts in a normalised format, or None on failure.
    """
    if not ENABLE_CBBD:
        return None

    client = _get_api_client()
    if client is None:
        return None

    try:
        import cbbd

        with client:
            games_api = cbbd.GamesApi(client)

            dt = datetime.strptime(date_str, "%Y-%m-%d")
            season = dt.year if dt.month >= 10 else dt.year - 1

            raw_games = games_api.get_games(season=season)

            target_date = dt.strftime("%Y-%m-%d")
            day_games = [
                g for g in raw_games
                if _game_matches_date(g, target_date)
            ]

            if not day_games:
                logger.info(f"CBBD: no games found for {date_str}")
                return []

            results = []
            for g in day_games:
                converted = _convert_game(g)
                if converted:
                    results.append(converted)

            logger.info(f"CBBD: fetched {len(results)} games for {date_str}")
            return results

    except ImportError:
        logger.warning("cbbd package is not installed")
        return None
    except Exception as e:
        logger.error(f"CBBD fetch failed for {date_str}: {e}")
        return None


def _game_matches_date(game, target_date: str) -> bool:
    """Check whether a CBBD game object falls on *target_date* (YYYY-MM-DD)."""
    start = getattr(game, "start_date", None) or getattr(game, "date", None)
    if start is None:
        return False
    start_str = str(start)
    return start_str.startswith(target_date)


def _convert_game(game) -> Optional[Dict[str, Any]]:
    """
    Convert a single CBBD game object to a normalised dict.

    Returns None when required fields are missing.
    """
    try:
        game_id = str(getattr(game, "id", "") or "")
        home_team = getattr(game, "home_team", None) or ""
        away_team = getattr(game, "away_team", None) or ""
        home_points = getattr(game, "home_points", None)
        away_points = getattr(game, "away_points", None)

        if not game_id or not home_team or not away_team:
            return None

        start = getattr(game, "start_date", None) or getattr(game, "date", None)
        venue = getattr(game, "venue", None) or ""
        status = getattr(game, "status", None) or ""
        completed = str(status).lower() in ("final", "completed", "complete")

        # Normalize to "final" for consistency with ESPN/CBBpy/NCAA pipelines
        return {
            "game_id": game_id,
            "home_team": str(home_team),
            "away_team": str(away_team),
            "home_score": int(home_points) if home_points is not None else None,
            "away_score": int(away_points) if away_points is not None else None,
            "status": "final" if completed else str(status),
            "venue": str(venue) if venue else None,
            "game_datetime": str(start) if start else None,
            "completed": completed,
        }
    except Exception as e:
        logger.warning(f"Failed to convert CBBD game: {e}")
        return None
