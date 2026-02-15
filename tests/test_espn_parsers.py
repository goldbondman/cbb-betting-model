import os
import sys


_ESPN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ESPN"
)
if _ESPN_DIR not in sys.path:
    sys.path.insert(0, _ESPN_DIR)

from espn_parsers import parse_team_from_summary, _extract_players


def test_parse_team_from_summary_supports_abbreviation_stat_names():
    team_entry = {
        "team": {"id": "1", "displayName": "Team A"},
        "statistics": [
            {"abbreviation": "FG", "displayValue": "25-58"},
            {"abbreviation": "3PT", "displayValue": "9-25"},
            {"abbreviation": "FT", "displayValue": "15-20"},
            {"abbreviation": "TO", "displayValue": "11"},
            {"abbreviation": "OREB", "displayValue": "8"},
            {"abbreviation": "DREB", "displayValue": "24"},
            {"abbreviation": "REB", "displayValue": "32"},
        ],
    }

    row = parse_team_from_summary(team_entry)

    assert row["fgm"] == 25
    assert row["fga"] == 58
    assert row["tpm"] == 9
    assert row["tpa"] == 25
    assert row["ftm"] == 15
    assert row["fta"] == 20
    assert row["tov"] == 11
    assert row["orb"] == 8
    assert row["drb"] == 24
    assert row["reb"] == 32


def test_extract_players_supports_keys_when_labels_missing():
    summary_json = {
        "boxscore": {
            "players": [
                {
                    "team": {"id": "1"},
                    "statistics": [
                        {
                            "keys": ["MIN", "FG", "3PT", "FT", "REB", "AST", "TO", "PTS"],
                            "athletes": [
                                {
                                    "athlete": {"displayName": "Player One", "id": "99"},
                                    "stats": ["34", "7-14", "2-6", "3-4", "8", "5", "2", "19"],
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    }

    players = _extract_players(summary_json, "1")

    assert len(players) == 1
    assert players[0]["player"] == "Player One"
    assert players[0]["athlete_id"] == "99"
    assert players[0]["minutes"] == 34.0
    assert players[0]["points"] == 19
    assert players[0]["fgm"] == 7
    assert players[0]["fga"] == 14
