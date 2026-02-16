"""
NCAA Casablanca JSON Parsers
Transform NCAA Casablanca API responses into structured data.
Pure transformation logic - no I/O or side effects.
"""

from typing import Any, Dict, List, Optional
from datetime import datetime

import pandas as pd
import numpy as np


def _to_int(value: Any, default: int = 0) -> int:
    """Safely convert value to int."""
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    """Safely convert value to float."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _utc_now_iso() -> str:
    """Return current UTC timestamp as ISO string."""
    from datetime import timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_scoreboard_game(game: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Parse a single game from NCAA Casablanca scoreboard API.
    
    Args:
        game: Game dictionary from scoreboard response
        
    Returns:
        Dictionary with game data, or None if game is invalid/incomplete
    """
    # NCAA Casablanca JSON structure varies, so we need to be defensive
    game_id = game.get("game", {}).get("gameID") or game.get("gameID")
    if not game_id:
        return None

    game_info = game.get("game", {})
    home = game.get("home", {})
    away = game.get("away", {})

    # Extract basic game info
    game_datetime = game_info.get("startTime") or game_info.get("startDate")
    venue = game_info.get("location") or game_info.get("venue")
    status = game_info.get("gameState") or game_info.get("status") or ""
    
    # Extract team names
    home_team = home.get("names", {}).get("short") or home.get("names", {}).get("full") or home.get("name", "")
    away_team = away.get("names", {}).get("short") or away.get("names", {}).get("full") or away.get("name", "")
    
    # Extract scores
    home_score = _to_int(home.get("score"), 0)
    away_score = _to_int(away.get("score"), 0)
    
    # Parse date
    game_date = None
    if game_datetime:
        try:
            dt = pd.to_datetime(game_datetime, errors="coerce")
            if not pd.isna(dt):
                game_date = dt.date().isoformat()
        except Exception:
            pass

    return {
        "game_id": str(game_id),
        "date": game_date,
        "game_datetime": game_datetime,
        "home_team": home_team,
        "away_team": away_team,
        "home_score": home_score,
        "away_score": away_score,
        "status": status,
        "venue": venue,
        "pulled_at_utc": _utc_now_iso(),
        "source": "ncaa_casablanca",
    }


def parse_team_stats(team_data: Dict[str, Any], is_home: bool) -> Dict[str, Any]:
    """
    Parse team statistics from NCAA Casablanca box score.
    
    Args:
        team_data: Team dictionary from box score response
        is_home: Whether this is the home team
        
    Returns:
        Dictionary with team statistics
    """
    stats = team_data.get("totals", {}) or team_data.get("teamStats", {})
    
    # Extract field goal stats
    fgm = _to_int(stats.get("fieldGoalsMade") or stats.get("fgm"), 0)
    fga = _to_int(stats.get("fieldGoalsAttempted") or stats.get("fga"), 0)
    fg_pct = _to_float(stats.get("fieldGoalPct") or stats.get("fgPct"), 0.0)
    
    # Extract three-point stats
    tpm = _to_int(stats.get("threePointsMade") or stats.get("3pm") or stats.get("tpm"), 0)
    tpa = _to_int(stats.get("threePointsAttempted") or stats.get("3pa") or stats.get("tpa"), 0)
    tp_pct = _to_float(stats.get("threePointPct") or stats.get("3pPct") or stats.get("tpPct"), 0.0)
    
    # Extract free throw stats
    ftm = _to_int(stats.get("freeThrowsMade") or stats.get("ftm"), 0)
    fta = _to_int(stats.get("freeThrowsAttempted") or stats.get("fta"), 0)
    ft_pct = _to_float(stats.get("freeThrowPct") or stats.get("ftPct"), 0.0)
    
    # Extract rebound stats
    reb = _to_int(stats.get("rebounds") or stats.get("reb"), 0)
    orb = _to_int(stats.get("offensiveRebounds") or stats.get("oreb") or stats.get("orb"), 0)
    drb = _to_int(stats.get("defensiveRebounds") or stats.get("dreb") or stats.get("drb"), 0)
    
    # Extract other stats
    ast = _to_int(stats.get("assists") or stats.get("ast"), 0)
    stl = _to_int(stats.get("steals") or stats.get("stl"), 0)
    blk = _to_int(stats.get("blocks") or stats.get("blk"), 0)
    tov = _to_int(stats.get("turnovers") or stats.get("tov") or stats.get("to"), 0)
    pf = _to_int(stats.get("fouls") or stats.get("pf"), 0)
    
    # Calculate percentages if not provided
    if fg_pct == 0.0 and fga > 0:
        fg_pct = fgm / fga
    if tp_pct == 0.0 and tpa > 0:
        tp_pct = tpm / tpa
    if ft_pct == 0.0 and fta > 0:
        ft_pct = ftm / fta
    
    # Calculate rebounds if not provided
    if reb == 0 and (orb > 0 or drb > 0):
        reb = orb + drb
    
    return {
        "fgm": fgm, "fga": fga, "fg_pct": fg_pct,
        "tpm": tpm, "tpa": tpa, "tp_pct": tp_pct,
        "ftm": ftm, "fta": fta, "ft_pct": ft_pct,
        "reb": reb, "orb": orb, "drb": drb,
        "ast": ast, "stl": stl, "blk": blk,
        "tov": tov, "pf": pf,
    }


def parse_player_stats(player_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse player statistics from NCAA Casablanca box score.
    
    Args:
        player_data: Player dictionary from box score response
        
    Returns:
        Dictionary with player statistics
    """
    # Extract player info
    player_name = player_data.get("name") or player_data.get("playerName") or "Unknown"
    player_id = str(player_data.get("playerID") or player_data.get("playerId") or "")
    starter = bool(player_data.get("starter") or player_data.get("isStarter") or False)
    
    # Extract stats
    stats = player_data.get("stats", {}) or player_data
    
    minutes = _to_float(stats.get("minutes") or stats.get("min"), 0.0)
    points = _to_int(stats.get("points") or stats.get("pts"), 0)
    
    # Field goals
    fgm = _to_int(stats.get("fieldGoalsMade") or stats.get("fgm"), 0)
    fga = _to_int(stats.get("fieldGoalsAttempted") or stats.get("fga"), 0)
    fg_pct = _to_float(stats.get("fieldGoalPct") or stats.get("fgPct"), 0.0)
    
    # Three-pointers
    tpm = _to_int(stats.get("threePointsMade") or stats.get("3pm") or stats.get("tpm"), 0)
    tpa = _to_int(stats.get("threePointsAttempted") or stats.get("3pa") or stats.get("tpa"), 0)
    tp_pct = _to_float(stats.get("threePointPct") or stats.get("3pPct") or stats.get("tpPct"), 0.0)
    
    # Free throws
    ftm = _to_int(stats.get("freeThrowsMade") or stats.get("ftm"), 0)
    fta = _to_int(stats.get("freeThrowsAttempted") or stats.get("fta"), 0)
    ft_pct = _to_float(stats.get("freeThrowPct") or stats.get("ftPct"), 0.0)
    
    # Rebounds
    reb = _to_int(stats.get("rebounds") or stats.get("reb"), 0)
    orb = _to_int(stats.get("offensiveRebounds") or stats.get("oreb") or stats.get("orb"), 0)
    drb = _to_int(stats.get("defensiveRebounds") or stats.get("dreb") or stats.get("drb"), 0)
    
    # Other stats
    ast = _to_int(stats.get("assists") or stats.get("ast"), 0)
    stl = _to_int(stats.get("steals") or stats.get("stl"), 0)
    blk = _to_int(stats.get("blocks") or stats.get("blk"), 0)
    tov = _to_int(stats.get("turnovers") or stats.get("tov") or stats.get("to"), 0)
    pf = _to_int(stats.get("fouls") or stats.get("pf"), 0)
    
    # Calculate percentages if not provided
    if fg_pct == 0.0 and fga > 0:
        fg_pct = fgm / fga
    if tp_pct == 0.0 and tpa > 0:
        tp_pct = tpm / tpa
    if ft_pct == 0.0 and fta > 0:
        ft_pct = ftm / fta
    
    # Calculate rebounds if not provided
    if reb == 0 and (orb > 0 or drb > 0):
        reb = orb + drb
    
    return {
        "player_name": player_name,
        "player_id": player_id,
        "starter": int(starter),
        "minutes": minutes,
        "points": points,
        "fgm": fgm, "fga": fga, "fg_pct": fg_pct,
        "tpm": tpm, "tpa": tpa, "tp_pct": tp_pct,
        "ftm": ftm, "fta": fta, "ft_pct": ft_pct,
        "reb": reb, "orb": orb, "drb": drb,
        "ast": ast, "stl": stl, "blk": blk,
        "tov": tov, "pf": pf,
    }


def parse_boxscore_json(boxscore_json: Dict[str, Any], game_id: str) -> Dict[str, Any]:
    """
    Parse NCAA Casablanca box score JSON into structured game data.
    
    Args:
        boxscore_json: Raw NCAA Casablanca box score API response
        game_id: Game ID for this box score
        
    Returns:
        Dictionary with keys: game_id, game_datetime, venue, 
        home (team dict), away (team dict), players_home, players_away
        
    Raises:
        ValueError: If box score structure is unexpected
    """
    # Extract game metadata
    game_info = boxscore_json.get("game", {})
    game_datetime = game_info.get("startTime") or game_info.get("startDate")
    venue = game_info.get("location") or game_info.get("venue")
    
    # Parse date
    game_date = None
    if game_datetime:
        try:
            dt = pd.to_datetime(game_datetime, errors="coerce")
            if not pd.isna(dt):
                game_date = dt.date().isoformat()
        except Exception:
            pass
    
    # Extract teams data
    teams = boxscore_json.get("teams", [])
    if not isinstance(teams, list) or len(teams) < 2:
        raise ValueError("Unexpected NCAA box score format: teams missing or too short")
    
    # Find home and away teams
    home_team = None
    away_team = None
    for team in teams:
        if team.get("homeAway") == "home" or team.get("isHome"):
            home_team = team
        elif team.get("homeAway") == "away" or not team.get("isHome"):
            away_team = team
    
    if not home_team or not away_team:
        # If homeAway flag not present, assume first is home, second is away
        home_team = teams[0]
        away_team = teams[1]
    
    # Parse team names
    home_name = (home_team.get("names", {}).get("short") or 
                 home_team.get("names", {}).get("full") or 
                 home_team.get("name", "Home"))
    away_name = (away_team.get("names", {}).get("short") or 
                 away_team.get("names", {}).get("full") or 
                 away_team.get("name", "Away"))
    
    # Parse scores
    home_score = _to_int(home_team.get("score"), 0)
    away_score = _to_int(away_team.get("score"), 0)
    
    # Parse team stats
    home_stats = parse_team_stats(home_team, is_home=True)
    away_stats = parse_team_stats(away_team, is_home=False)
    
    home_stats.update({
        "team": home_name,
        "opponent": away_name,
        "home_away": "home",
        "points_for": home_score,
        "points_against": away_score,
        "margin": home_score - away_score,
    })
    
    away_stats.update({
        "team": away_name,
        "opponent": home_name,
        "home_away": "away",
        "points_for": away_score,
        "points_against": home_score,
        "margin": away_score - home_score,
    })
    
    # Parse player box scores
    players_home = []
    players_away = []
    
    # Extract players for home team
    home_players = home_team.get("players", []) or []
    for player in home_players:
        player_stats = parse_player_stats(player)
        player_stats["team"] = home_name
        players_home.append(player_stats)
    
    # Extract players for away team
    away_players = away_team.get("players", []) or []
    for player in away_players:
        player_stats = parse_player_stats(player)
        player_stats["team"] = away_name
        players_away.append(player_stats)
    
    return {
        "game_id": str(game_id),
        "game_date": game_date,
        "game_datetime": game_datetime,
        "venue": venue,
        "home": home_stats,
        "away": away_stats,
        "players_home": players_home,
        "players_away": players_away,
    }
