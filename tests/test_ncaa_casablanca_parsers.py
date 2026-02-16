"""
Tests for NCAA Casablanca Parsers
"""

import os
import sys

# Add ESPN directory to path
_ESPN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ESPN"
)
if _ESPN_DIR not in sys.path:
    sys.path.insert(0, _ESPN_DIR)

from ncaa_casablanca_parsers import (
    parse_scoreboard_game,
    parse_team_stats,
    parse_player_stats,
    parse_boxscore_json,
)


def test_parse_scoreboard_game_basic():
    """Test parsing a basic scoreboard game entry."""
    game = {
        "game": {
            "gameID": "12345",
            "startTime": "2024-02-15T19:00:00Z",
            "location": "Cameron Indoor Stadium",
            "gameState": "final"
        },
        "home": {
            "names": {"short": "Duke", "full": "Duke Blue Devils"},
            "score": 85
        },
        "away": {
            "names": {"short": "UNC", "full": "North Carolina Tar Heels"},
            "score": 78
        }
    }
    
    result = parse_scoreboard_game(game)
    
    assert result is not None
    assert result["game_id"] == "12345"
    assert result["home_team"] == "Duke"
    assert result["away_team"] == "UNC"
    assert result["home_score"] == 85
    assert result["away_score"] == 78
    assert result["status"] == "final"
    assert result["venue"] == "Cameron Indoor Stadium"


def test_parse_scoreboard_game_missing_game_id():
    """Test that games without game ID are skipped."""
    game = {
        "home": {"score": 80},
        "away": {"score": 75}
    }
    
    result = parse_scoreboard_game(game)
    assert result is None


def test_parse_team_stats_basic():
    """Test parsing team statistics."""
    team_data = {
        "totals": {
            "fieldGoalsMade": 30,
            "fieldGoalsAttempted": 60,
            "threePointsMade": 10,
            "threePointsAttempted": 25,
            "freeThrowsMade": 15,
            "freeThrowsAttempted": 20,
            "rebounds": 35,
            "offensiveRebounds": 10,
            "defensiveRebounds": 25,
            "assists": 18,
            "steals": 8,
            "blocks": 5,
            "turnovers": 12,
            "fouls": 18
        }
    }
    
    result = parse_team_stats(team_data, is_home=True)
    
    assert result["fgm"] == 30
    assert result["fga"] == 60
    assert result["tpm"] == 10
    assert result["tpa"] == 25
    assert result["ftm"] == 15
    assert result["fta"] == 20
    assert result["reb"] == 35
    assert result["orb"] == 10
    assert result["drb"] == 25
    assert result["ast"] == 18
    assert result["stl"] == 8
    assert result["blk"] == 5
    assert result["tov"] == 12
    assert result["pf"] == 18


def test_parse_team_stats_calculates_percentages():
    """Test that percentages are calculated when not provided."""
    team_data = {
        "totals": {
            "fieldGoalsMade": 30,
            "fieldGoalsAttempted": 60,
            "threePointsMade": 10,
            "threePointsAttempted": 25,
        }
    }
    
    result = parse_team_stats(team_data, is_home=True)
    
    assert result["fg_pct"] == 0.5  # 30/60
    assert result["tp_pct"] == 0.4  # 10/25


def test_parse_player_stats_basic():
    """Test parsing player statistics."""
    player_data = {
        "name": "John Doe",
        "playerID": "98765",
        "starter": True,
        "stats": {
            "minutes": 32.5,
            "points": 25,
            "fieldGoalsMade": 9,
            "fieldGoalsAttempted": 15,
            "threePointsMade": 3,
            "threePointsAttempted": 7,
            "freeThrowsMade": 4,
            "freeThrowsAttempted": 5,
            "rebounds": 8,
            "offensiveRebounds": 2,
            "defensiveRebounds": 6,
            "assists": 5,
            "steals": 2,
            "blocks": 1,
            "turnovers": 3,
            "fouls": 2
        }
    }
    
    result = parse_player_stats(player_data)
    
    assert result["player_name"] == "John Doe"
    assert result["player_id"] == "98765"
    assert result["starter"] == 1
    assert result["minutes"] == 32.5
    assert result["points"] == 25
    assert result["fgm"] == 9
    assert result["fga"] == 15
    assert result["tpm"] == 3
    assert result["tpa"] == 7
    assert result["ftm"] == 4
    assert result["fta"] == 5
    assert result["reb"] == 8
    assert result["orb"] == 2
    assert result["drb"] == 6
    assert result["ast"] == 5
    assert result["stl"] == 2
    assert result["blk"] == 1
    assert result["tov"] == 3
    assert result["pf"] == 2


def test_parse_boxscore_json_basic():
    """Test parsing a complete box score JSON."""
    boxscore = {
        "game": {
            "gameID": "12345",
            "startTime": "2024-02-15T19:00:00Z",
            "location": "Test Arena"
        },
        "teams": [
            {
                "homeAway": "home",
                "names": {"short": "Home Team"},
                "score": 85,
                "totals": {
                    "fieldGoalsMade": 30,
                    "fieldGoalsAttempted": 60,
                    "rebounds": 35
                },
                "players": [
                    {
                        "name": "Player 1",
                        "playerID": "1",
                        "starter": True,
                        "stats": {"points": 20, "minutes": 30}
                    }
                ]
            },
            {
                "homeAway": "away",
                "names": {"short": "Away Team"},
                "score": 78,
                "totals": {
                    "fieldGoalsMade": 28,
                    "fieldGoalsAttempted": 58,
                    "rebounds": 32
                },
                "players": [
                    {
                        "name": "Player 2",
                        "playerID": "2",
                        "starter": True,
                        "stats": {"points": 18, "minutes": 32}
                    }
                ]
            }
        ]
    }
    
    result = parse_boxscore_json(boxscore, "12345")
    
    assert result["game_id"] == "12345"
    assert result["venue"] == "Test Arena"
    assert result["home"]["team"] == "Home Team"
    assert result["away"]["team"] == "Away Team"
    assert result["home"]["points_for"] == 85
    assert result["away"]["points_for"] == 78
    assert result["home"]["home_away"] == "home"
    assert result["away"]["home_away"] == "away"
    assert len(result["players_home"]) == 1
    assert len(result["players_away"]) == 1
    assert result["players_home"][0]["player_name"] == "Player 1"
    assert result["players_away"][0]["player_name"] == "Player 2"


def test_parse_boxscore_json_missing_teams():
    """Test that box score with missing teams raises ValueError."""
    boxscore = {
        "game": {"gameID": "12345"},
        "teams": []
    }
    
    try:
        parse_boxscore_json(boxscore, "12345")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "teams missing or too short" in str(e)
