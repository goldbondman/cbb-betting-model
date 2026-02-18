import os
import sys


_ESPN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ESPN"
)
if _ESPN_DIR not in sys.path:
    sys.path.insert(0, _ESPN_DIR)

from espn_parsers import parse_team_from_summary, _extract_players, parse_scoreboard_event


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


def test_parse_team_from_summary_supports_labels_stats_table_shape():
    team_entry = {
        "team": {"id": "1", "displayName": "Team A"},
        "statistics": [
            {
                "labels": ["FG", "3PT", "FT", "TO", "OREB", "DREB", "REB"],
                "stats": ["25-58", "9-25", "15-20", "11", "8", "24", "32"],
            }
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


def test_extract_players_uses_partial_header_mapping_when_lengths_mismatch():
    summary_json = {
        "boxscore": {
            "players": [
                {
                    "team": {"id": "1"},
                    "statistics": [
                        {
                            "labels": ["MIN", "FG", "3PT", "FT", "REB", "AST", "STL", "BLK", "TO", "PF", "EXTRA"],
                            "athletes": [
                                {
                                    "athlete": {"displayName": "Player Two", "id": "100"},
                                    "stats": ["30", "6-12", "1-4", "2-2", "7", "4", "2", "1", "3", "2"],
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
    assert players[0]["athlete_id"] == "100"
    assert players[0]["fgm"] == 6
    assert players[0]["fga"] == 12
    assert players[0]["stl"] == 2
    assert players[0]["blk"] == 1
    assert players[0]["pf"] == 2


def test_parse_scoreboard_event_rejects_minimal_cbbpy_format():
    """Events with only id/date (no competitions) must return None."""
    event = {"id": "401829197", "date": "02-18-2026"}
    result = parse_scoreboard_event(event)
    assert result is None


def test_parse_scoreboard_event_accepts_full_espn_format():
    """Full ESPN scoreboard events with competitions/competitors must parse."""
    event = {
        "id": "401829197",
        "date": "2026-02-18T00:00Z",
        "competitions": [
            {
                "date": "2026-02-18T00:00Z",
                "venue": {"fullName": "Test Arena"},
                "competitors": [
                    {
                        "homeAway": "home",
                        "score": "75",
                        "winner": True,
                        "team": {"displayName": "Home Team"},
                    },
                    {
                        "homeAway": "away",
                        "score": "68",
                        "winner": False,
                        "team": {"displayName": "Away Team"},
                    },
                ],
                "status": {
                    "type": {
                        "completed": True,
                        "state": "post",
                        "detail": "Final",
                        "description": "Final",
                    }
                },
            }
        ],
    }
    result = parse_scoreboard_event(event)
    assert result is not None
    assert result["game_id"] == "401829197"
    assert result["home_team"] == "Home Team"
    assert result["away_team"] == "Away Team"
    assert result["home_score"] == 75
    assert result["away_score"] == 68
    assert result["completed"] is True
