#!/usr/bin/env python3
"""
Example script demonstrating NCAA Casablanca API usage.
This script shows how to use the NCAA Casablanca module without actually
calling the API (which may not be accessible in all environments).
"""

import sys
import os

# Add ESPN directory to path
_ESPN_DIR = os.path.dirname(os.path.abspath(__file__))
if _ESPN_DIR not in sys.path:
    sys.path.insert(0, _ESPN_DIR)

from ncaa_casablanca_parsers import (
    parse_scoreboard_game,
    parse_boxscore_json,
)


def example_parse_scoreboard():
    """Example: Parse a scoreboard game entry."""
    print("=" * 60)
    print("Example 1: Parsing Scoreboard Data")
    print("=" * 60)
    
    # Mock scoreboard game entry (based on expected NCAA Casablanca format)
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
    
    print("\nInput JSON (sample):")
    print(f"  Game ID: {game['game']['gameID']}")
    print(f"  Home: {game['home']['names']['short']} ({game['home']['score']})")
    print(f"  Away: {game['away']['names']['short']} ({game['away']['score']})")
    
    print("\nParsed Output:")
    print(f"  Game ID: {result['game_id']}")
    print(f"  Date: {result['date']}")
    print(f"  Venue: {result['venue']}")
    print(f"  Home Team: {result['home_team']} - {result['home_score']}")
    print(f"  Away Team: {result['away_team']} - {result['away_score']}")
    print(f"  Status: {result['status']}")
    print(f"  Source: {result['source']}")


def example_parse_boxscore():
    """Example: Parse a box score entry."""
    print("\n" + "=" * 60)
    print("Example 2: Parsing Box Score Data")
    print("=" * 60)
    
    # Mock box score entry (based on expected NCAA Casablanca format)
    boxscore = {
        "game": {
            "gameID": "12345",
            "startTime": "2024-02-15T19:00:00Z",
            "location": "Cameron Indoor Stadium"
        },
        "teams": [
            {
                "homeAway": "home",
                "names": {"short": "Duke", "full": "Duke Blue Devils"},
                "score": 85,
                "totals": {
                    "fieldGoalsMade": 30,
                    "fieldGoalsAttempted": 60,
                    "threePointsMade": 10,
                    "threePointsAttempted": 25,
                    "freeThrowsMade": 15,
                    "freeThrowsAttempted": 20,
                    "rebounds": 38,
                    "offensiveRebounds": 10,
                    "defensiveRebounds": 28,
                    "assists": 18,
                    "steals": 8,
                    "blocks": 5,
                    "turnovers": 12,
                    "fouls": 16
                },
                "players": [
                    {
                        "name": "Kyle Filipowski",
                        "playerID": "4432813",
                        "starter": True,
                        "stats": {
                            "minutes": 35.0,
                            "points": 22,
                            "fieldGoalsMade": 8,
                            "fieldGoalsAttempted": 14,
                            "threePointsMade": 2,
                            "threePointsAttempted": 5,
                            "freeThrowsMade": 4,
                            "freeThrowsAttempted": 5,
                            "rebounds": 10,
                            "offensiveRebounds": 3,
                            "defensiveRebounds": 7,
                            "assists": 3,
                            "steals": 2,
                            "blocks": 2,
                            "turnovers": 1,
                            "fouls": 2
                        }
                    }
                ]
            },
            {
                "homeAway": "away",
                "names": {"short": "UNC", "full": "North Carolina Tar Heels"},
                "score": 78,
                "totals": {
                    "fieldGoalsMade": 28,
                    "fieldGoalsAttempted": 58,
                    "threePointsMade": 8,
                    "threePointsAttempted": 22,
                    "freeThrowsMade": 14,
                    "freeThrowsAttempted": 18,
                    "rebounds": 35,
                    "offensiveRebounds": 8,
                    "defensiveRebounds": 27,
                    "assists": 15,
                    "steals": 7,
                    "blocks": 4,
                    "turnovers": 10,
                    "fouls": 18
                },
                "players": [
                    {
                        "name": "Armando Bacot",
                        "playerID": "4395694",
                        "starter": True,
                        "stats": {
                            "minutes": 32.0,
                            "points": 18,
                            "fieldGoalsMade": 7,
                            "fieldGoalsAttempted": 12,
                            "threePointsMade": 0,
                            "threePointsAttempted": 0,
                            "freeThrowsMade": 4,
                            "freeThrowsAttempted": 6,
                            "rebounds": 12,
                            "offensiveRebounds": 4,
                            "defensiveRebounds": 8,
                            "assists": 2,
                            "steals": 1,
                            "blocks": 2,
                            "turnovers": 2,
                            "fouls": 3
                        }
                    }
                ]
            }
        ]
    }
    
    result = parse_boxscore_json(boxscore, "12345")
    
    print("\nParsed Box Score:")
    print(f"  Game ID: {result['game_id']}")
    print(f"  Date: {result['game_date']}")
    print(f"  Venue: {result['venue']}")
    
    print("\n  Home Team: {team}".format(team=result['home']['team']))
    print(f"    Points: {result['home']['points_for']}")
    print(f"    FG: {result['home']['fgm']}/{result['home']['fga']} ({result['home']['fg_pct']:.1%})")
    print(f"    3PT: {result['home']['tpm']}/{result['home']['tpa']} ({result['home']['tp_pct']:.1%})")
    print(f"    FT: {result['home']['ftm']}/{result['home']['fta']} ({result['home']['ft_pct']:.1%})")
    print(f"    Rebounds: {result['home']['reb']} (Off: {result['home']['orb']}, Def: {result['home']['drb']})")
    print(f"    Assists: {result['home']['ast']}")
    print(f"    Turnovers: {result['home']['tov']}")
    
    print("\n  Away Team: {team}".format(team=result['away']['team']))
    print(f"    Points: {result['away']['points_for']}")
    print(f"    FG: {result['away']['fgm']}/{result['away']['fga']} ({result['away']['fg_pct']:.1%})")
    print(f"    3PT: {result['away']['tpm']}/{result['away']['tpa']} ({result['away']['tp_pct']:.1%})")
    print(f"    FT: {result['away']['ftm']}/{result['away']['fta']} ({result['away']['ft_pct']:.1%})")
    print(f"    Rebounds: {result['away']['reb']} (Off: {result['away']['orb']}, Def: {result['away']['drb']})")
    print(f"    Assists: {result['away']['ast']}")
    print(f"    Turnovers: {result['away']['tov']}")
    
    print("\n  Player Stats:")
    print(f"    Home: {len(result['players_home'])} players")
    if result['players_home']:
        player = result['players_home'][0]
        print(f"      Sample - {player['player_name']}: {player['points']} pts, {player['reb']} reb, {player['ast']} ast")
    
    print(f"    Away: {len(result['players_away'])} players")
    if result['players_away']:
        player = result['players_away'][0]
        print(f"      Sample - {player['player_name']}: {player['points']} pts, {player['reb']} reb, {player['ast']} ast")


def main():
    """Run all examples."""
    print("\nNCAA Casablanca Parser Examples")
    print("=" * 60)
    print("These examples demonstrate the parser functionality using")
    print("mock data that matches the expected NCAA Casablanca API format.")
    print()
    
    example_parse_scoreboard()
    example_parse_boxscore()
    
    print("\n" + "=" * 60)
    print("Examples Complete!")
    print("=" * 60)
    print("\nTo use with real data:")
    print("  1. Ensure NCAA Casablanca API is accessible")
    print("  2. Run: python ncaa_casablanca_builder.py")
    print("  3. Check ESPN/CSV/ directory for output files")
    print()


if __name__ == "__main__":
    main()
