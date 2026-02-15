"""
ESPN Injury Report Scraper
Fetches injury data from ESPN's college basketball team API.

ESPN embeds injury information in team endpoints at:
  https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/teams/{team_id}

Each team response may contain an "injuries" array with player-level
status entries (e.g., Out, Day-To-Day, Questionable).

This module:
  1. Accepts a list of team IDs (sourced from scoreboard or CSV)
  2. Fetches each team's page from the ESPN API
  3. Extracts injury entries into a flat list of dicts
  4. Returns a DataFrame suitable for CSV export
"""

import logging
from typing import Any, Dict, List, Optional

import pandas as pd

from espn_config import ESPN_INJURIES_URL, PARSE_VERSION, SOURCE_NAME
from espn_http_client import fetch_with_retry
from data_utils import _utc_now_iso

logger = logging.getLogger(__name__)

# Column order for injury CSV output
INJURY_COLUMNS = [
    "team_id",
    "team",
    "athlete_id",
    "player",
    "position",
    "status",
    "injury_type",
    "detail",
    "side",
    "return_date",
    "pulled_at_utc",
    "source",
    "parse_version",
]


def fetch_team_injuries(team_id: str) -> Dict[str, Any]:
    """
    Fetch team page from ESPN API (which may include injury data).

    Args:
        team_id: ESPN numeric team ID (e.g., "12" for Duke)

    Returns:
        Raw ESPN team JSON response

    Raises:
        RuntimeError: If fetch fails after retries
    """
    url = ESPN_INJURIES_URL.format(team_id=team_id)
    return fetch_with_retry(url)


def parse_injuries_from_team(
    team_json: Dict[str, Any], team_id: str
) -> List[Dict[str, Any]]:
    """
    Extract injury entries from an ESPN team JSON response.

    ESPN structures injury data under:
        team_json["team"]["injuries"] -> list of injury entries

    Each entry may contain:
        - athlete: {id, displayName, position}
        - status: "Out" | "Day-To-Day" | "Questionable" | etc.
        - type: {id, description, abbreviation}
        - details: {detail, side, returnDate}

    Args:
        team_json: Raw ESPN team API response
        team_id: Team ID for metadata

    Returns:
        List of flat dicts, one per injured player
    """
    rows: List[Dict[str, Any]] = []

    team_obj = team_json.get("team", {}) if isinstance(team_json, dict) else {}
    team_name = team_obj.get("displayName") or team_obj.get("name") or "Unknown"

    injuries = team_obj.get("injuries")
    if not isinstance(injuries, list):
        injuries = team_json.get("injuries") if isinstance(team_json, dict) else []
    if not isinstance(injuries, list):
        injuries = []
    if not injuries and isinstance(team_json, dict):
        athletes = team_json.get("athletes") or []
        if isinstance(athletes, list):
            for athlete_entry in athletes:
                if not isinstance(athlete_entry, dict):
                    continue
                athlete_obj = athlete_entry.get("athlete") or athlete_entry
                athlete_injuries = athlete_entry.get("injuries") or []
                if not isinstance(athlete_injuries, list):
                    continue
                for injury_entry in athlete_injuries:
                    if isinstance(injury_entry, dict):
                        merged = dict(injury_entry)
                        merged.setdefault("athlete", athlete_obj)
                        injuries.append(merged)

    for entry in injuries:
        if not isinstance(entry, dict):
            continue

        athlete = entry.get("athlete")
        if not isinstance(athlete, dict):
            continue

        athlete_id = str(athlete.get("id", ""))
        player_name = (
            athlete.get("displayName")
            or athlete.get("shortName")
            or athlete.get("fullName")
            or "Unknown"
        )
        position = ""
        pos_obj = athlete.get("position") or {}
        if isinstance(pos_obj, dict):
            position = pos_obj.get("abbreviation") or pos_obj.get("name") or ""
        elif isinstance(pos_obj, str):
            position = pos_obj

        status = entry.get("status") or ""
        long_comment = entry.get("longComment") or ""
        short_comment = entry.get("shortComment") or ""

        # Type info (e.g., "Knee", "Illness")
        type_obj = entry.get("type") or {}
        injury_type = ""
        if isinstance(type_obj, dict):
            injury_type = (
                type_obj.get("description")
                or type_obj.get("abbreviation")
                or type_obj.get("name")
                or ""
            )
        elif isinstance(type_obj, str):
            injury_type = type_obj

        # Details (optional sub-object)
        details = entry.get("details") or {}
        detail_text = ""
        side = ""
        return_date = ""
        if isinstance(details, dict):
            detail_text = details.get("detail") or ""
            side = details.get("side") or ""
            return_date = details.get("returnDate") or ""

        rows.append(
            {
                "team_id": str(team_id),
                "team": team_name,
                "athlete_id": athlete_id,
                "player": player_name,
                "position": position,
                "status": status,
                "injury_type": injury_type,
                "detail": detail_text or long_comment or short_comment,
                "side": side,
                "return_date": return_date,
                "pulled_at_utc": _utc_now_iso(),
                "source": SOURCE_NAME,
                "parse_version": PARSE_VERSION,
            }
        )

    return rows


def fetch_injuries_for_teams(
    team_ids: List[str],
) -> pd.DataFrame:
    """
    Fetch injury reports for multiple teams and return a combined DataFrame.

    Args:
        team_ids: List of ESPN team ID strings

    Returns:
        DataFrame with one row per injured player across all teams.
        Empty DataFrame (with correct columns) if no injuries found.
    """
    all_rows: List[Dict[str, Any]] = []

    for tid in team_ids:
        try:
            team_json = fetch_team_injuries(tid)
            rows = parse_injuries_from_team(team_json, tid)
            all_rows.extend(rows)
        except Exception as exc:
            logger.warning("Failed to fetch injuries for team %s: %s", tid, exc)
            continue

    if not all_rows:
        return pd.DataFrame(columns=INJURY_COLUMNS)

    return pd.DataFrame(all_rows, columns=INJURY_COLUMNS)
