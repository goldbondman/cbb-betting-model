"""Tests for ESPN injury report parsing (ESPN/espn_injuries.py)."""

import sys
import os
from pathlib import Path
from unittest import mock

import pandas as pd
import pytest

# Ensure ESPN package is importable
_ESPN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ESPN"
)
if _ESPN_DIR not in sys.path:
    sys.path.insert(0, _ESPN_DIR)

from espn_injuries import (
    INJURY_COLUMNS,
    parse_injuries_from_team,
    fetch_injuries_for_teams,
)


# ---- Fixtures ----

SAMPLE_TEAM_JSON = {
    "team": {
        "id": "150",
        "displayName": "Duke Blue Devils",
        "injuries": [
            {
                "athlete": {
                    "id": "4433001",
                    "displayName": "John Smith",
                    "position": {"abbreviation": "PG"},
                },
                "status": "Out",
                "type": {"description": "Knee"},
                "longComment": "Expected to miss 2-3 weeks",
                "details": {
                    "detail": "Left ACL sprain",
                    "side": "Left",
                    "returnDate": "2026-03-01",
                },
            },
            {
                "athlete": {
                    "id": "4433002",
                    "displayName": "James Brown",
                    "position": {"abbreviation": "SG"},
                },
                "status": "Day-To-Day",
                "type": {"description": "Ankle"},
                "shortComment": "Ankle soreness",
                "details": {},
            },
        ],
    }
}

SAMPLE_TEAM_NO_INJURIES = {
    "team": {
        "id": "150",
        "displayName": "Duke Blue Devils",
    }
}

SAMPLE_TEAM_EMPTY_INJURIES = {
    "team": {
        "id": "150",
        "displayName": "Duke Blue Devils",
        "injuries": [],
    }
}


# ---- Tests for parse_injuries_from_team ----


class TestParseInjuriesFromTeam:
    def test_parses_two_injuries(self):
        rows = parse_injuries_from_team(SAMPLE_TEAM_JSON, "150")
        assert len(rows) == 2

    def test_first_injury_fields(self):
        rows = parse_injuries_from_team(SAMPLE_TEAM_JSON, "150")
        row = rows[0]
        assert row["team_id"] == "150"
        assert row["team"] == "Duke Blue Devils"
        assert row["athlete_id"] == "4433001"
        assert row["player"] == "John Smith"
        assert row["position"] == "PG"
        assert row["status"] == "Out"
        assert row["injury_type"] == "Knee"
        assert row["detail"] == "Left ACL sprain"
        assert row["side"] == "Left"
        assert row["return_date"] == "2026-03-01"
        assert row["source"] == "espn"

    def test_second_injury_day_to_day(self):
        rows = parse_injuries_from_team(SAMPLE_TEAM_JSON, "150")
        row = rows[1]
        assert row["status"] == "Day-To-Day"
        assert row["injury_type"] == "Ankle"
        assert row["player"] == "James Brown"
        # detail should fall back to shortComment when details.detail is empty
        assert row["detail"] == "Ankle soreness"

    def test_no_injuries_key_returns_empty(self):
        rows = parse_injuries_from_team(SAMPLE_TEAM_NO_INJURIES, "150")
        assert rows == []

    def test_empty_injuries_list_returns_empty(self):
        rows = parse_injuries_from_team(SAMPLE_TEAM_EMPTY_INJURIES, "150")
        assert rows == []

    def test_handles_missing_athlete(self):
        json_data = {
            "team": {
                "id": "99",
                "displayName": "Test U",
                "injuries": [{"status": "Out"}],
            }
        }
        rows = parse_injuries_from_team(json_data, "99")
        # Entry without athlete key still produces a row with defaults
        assert len(rows) == 1
        assert rows[0]["player"] == "Unknown"
        assert rows[0]["status"] == "Out"
        assert rows[0]["athlete_id"] == ""

    def test_handles_non_dict_entry(self):
        json_data = {
            "team": {
                "id": "99",
                "displayName": "Test U",
                "injuries": ["not a dict", None, 42],
            }
        }
        rows = parse_injuries_from_team(json_data, "99")
        assert rows == []

    def test_handles_empty_input(self):
        rows = parse_injuries_from_team({}, "0")
        assert rows == []

    def test_handles_none_input(self):
        rows = parse_injuries_from_team(None, "0")
        assert rows == []

    def test_type_as_string(self):
        json_data = {
            "team": {
                "id": "50",
                "displayName": "Team A",
                "injuries": [
                    {
                        "athlete": {"id": "1", "displayName": "Player X"},
                        "status": "Questionable",
                        "type": "Hamstring",
                    }
                ],
            }
        }
        rows = parse_injuries_from_team(json_data, "50")
        assert len(rows) == 1
        assert rows[0]["injury_type"] == "Hamstring"

    def test_position_as_string(self):
        json_data = {
            "team": {
                "id": "50",
                "displayName": "Team A",
                "injuries": [
                    {
                        "athlete": {
                            "id": "1",
                            "displayName": "Player Y",
                            "position": "C",
                        },
                        "status": "Out",
                    }
                ],
            }
        }
        rows = parse_injuries_from_team(json_data, "50")
        assert rows[0]["position"] == "C"


# ---- Tests for fetch_injuries_for_teams ----


class TestFetchInjuriesForTeams:
    def test_combines_multiple_teams(self):
        def mock_fetch(tid):
            if tid == "1":
                return SAMPLE_TEAM_JSON
            return SAMPLE_TEAM_NO_INJURIES

        with mock.patch(
            "espn_injuries.fetch_team_injuries", side_effect=mock_fetch
        ):
            df = fetch_injuries_for_teams(["1", "2"])

        assert isinstance(df, pd.DataFrame)
        assert len(df) == 2
        assert list(df.columns) == INJURY_COLUMNS

    def test_returns_empty_dataframe_on_no_injuries(self):
        with mock.patch(
            "espn_injuries.fetch_team_injuries",
            return_value=SAMPLE_TEAM_NO_INJURIES,
        ):
            df = fetch_injuries_for_teams(["1"])

        assert isinstance(df, pd.DataFrame)
        assert df.empty
        assert list(df.columns) == INJURY_COLUMNS

    def test_continues_on_fetch_error(self):
        call_count = {"n": 0}

        def mock_fetch(tid):
            call_count["n"] += 1
            if tid == "1":
                raise RuntimeError("network error")
            return SAMPLE_TEAM_JSON

        with mock.patch(
            "espn_injuries.fetch_team_injuries", side_effect=mock_fetch
        ):
            df = fetch_injuries_for_teams(["1", "150"])

        assert call_count["n"] == 2
        assert len(df) == 2  # injuries from team "150"

    def test_empty_team_ids(self):
        df = fetch_injuries_for_teams([])
        assert df.empty
        assert list(df.columns) == INJURY_COLUMNS


# ---- Tests for refresh_sources integration ----


class TestRefreshSourcesIntegration:
    def test_collect_team_ids_from_csv(self, tmp_path):
        csv_path = tmp_path / "logs.csv"
        pd.DataFrame(
            {"team_id": ["12", "150", "12", "2"]}
        ).to_csv(csv_path, index=False)

        sys.path.insert(
            0,
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "scripts",
            ),
        )
        from refresh_sources import _collect_team_ids_from_csv

        result = _collect_team_ids_from_csv(str(csv_path))
        assert result == ["12", "150", "2"]

    def test_collect_team_ids_missing_file(self):
        sys.path.insert(
            0,
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "scripts",
            ),
        )
        from refresh_sources import _collect_team_ids_from_csv

        assert _collect_team_ids_from_csv("/nonexistent/path.csv") == []
