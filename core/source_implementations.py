"""
Implementations of data sources: ESPN, NCAA Casablanca, and Henry API
"""

import sys
import os
from typing import List
from datetime import datetime, timezone
import logging
import requests

# Add ESPN directory to path
_ESPN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ESPN")
if _ESPN_DIR not in sys.path:
    sys.path.insert(0, _ESPN_DIR)

from data_sources import DataSource, SourceType, GameData, SourceResult, DataQuality

logger = logging.getLogger(__name__)


class ESPNDataSource(DataSource):
    """ESPN API data source"""
    
    def get_source_type(self) -> SourceType:
        return SourceType.ESPN
    
    def fetch_games(self, date: str) -> SourceResult:
        """
        Fetch games from ESPN API for a specific date.
        
        Args:
            date: Date in YYYY-MM-DD format
            
        Returns:
            SourceResult with ESPN game data
        """
        try:
            from espn_http_client import fetch_scoreboard
            from espn_parsers import parse_scoreboard_event
            
            # Convert date format: YYYY-MM-DD -> YYYYMMDD
            date_yyyymmdd = date.replace("-", "")
            
            # Fetch scoreboard data
            scoreboard_json = fetch_scoreboard(date_yyyymmdd)
            
            if not scoreboard_json or "events" not in scoreboard_json:
                return self._create_result(False, error="No events in ESPN response")
            
            # Parse each event
            games = []
            for event in scoreboard_json.get("events", []):
                try:
                    parsed = parse_scoreboard_event(event)
                    if parsed:
                        game_data = self._convert_to_game_data(parsed, date)
                        if game_data.is_complete_basic():
                            games.append(game_data)
                except Exception as e:
                    logger.warning(f"Failed to parse ESPN event: {e}")
                    continue
            
            if not games:
                return self._create_result(False, error="No valid games parsed from ESPN")
            
            return self._create_result(True, games=games)
            
        except Exception as e:
            logger.error(f"ESPN fetch failed: {e}")
            return self._create_result(False, error=str(e))
    
    def _convert_to_game_data(self, parsed: dict, date: str) -> GameData:
        """Convert ESPN parsed data to standardized GameData"""
        return GameData(
            game_id=str(parsed.get("event_id", "")),
            date=date,
            home_team=parsed.get("home_team", ""),
            away_team=parsed.get("away_team", ""),
            home_score=parsed.get("home_score"),
            away_score=parsed.get("away_score"),
            status=parsed.get("status_desc"),
            venue=parsed.get("venue"),
            game_datetime=parsed.get("game_datetime_utc"),
            market_spread=parsed.get("market_spread"),
            market_total=parsed.get("market_total"),
            market_home_ml=parsed.get("market_home_ml"),
            market_away_ml=parsed.get("market_away_ml"),
            source="espn",
            pulled_at=datetime.now(timezone.utc).isoformat(),
            raw_data=parsed
        )


class NCAADataSource(DataSource):
    """NCAA Casablanca API data source"""
    
    def get_source_type(self) -> SourceType:
        return SourceType.NCAA_CASABLANCA
    
    def fetch_games(self, date: str) -> SourceResult:
        """
        Fetch games from NCAA Casablanca API for a specific date.
        
        Args:
            date: Date in YYYY-MM-DD format
            
        Returns:
            SourceResult with NCAA game data
        """
        try:
            from ncaa_casablanca_http_client import fetch_scoreboard_by_date
            from ncaa_casablanca_parsers import parse_scoreboard_game
            
            # Fetch scoreboard data
            scoreboard_json = fetch_scoreboard_by_date(date)
            
            if not scoreboard_json:
                return self._create_result(False, error="No data in NCAA response")
            
            # NCAA Casablanca has different response structures
            games_list = scoreboard_json.get("games", [])
            if not games_list:
                # Try alternate structure
                games_list = scoreboard_json.get("scoreboard", {}).get("games", [])
            
            if not games_list:
                return self._create_result(False, error="No games in NCAA response")
            
            # Parse each game
            games = []
            for game in games_list:
                try:
                    parsed = parse_scoreboard_game(game)
                    if parsed:
                        game_data = self._convert_to_game_data(parsed, date)
                        if game_data.is_complete_basic():
                            games.append(game_data)
                except Exception as e:
                    logger.warning(f"Failed to parse NCAA game: {e}")
                    continue
            
            if not games:
                return self._create_result(False, error="No valid games parsed from NCAA")
            
            return self._create_result(True, games=games)
            
        except Exception as e:
            logger.error(f"NCAA fetch failed: {e}")
            return self._create_result(False, error=str(e))
    
    def _convert_to_game_data(self, parsed: dict, date: str) -> GameData:
        """Convert NCAA parsed data to standardized GameData"""
        return GameData(
            game_id=str(parsed.get("game_id", "")),
            date=date,
            home_team=parsed.get("home_team", ""),
            away_team=parsed.get("away_team", ""),
            home_score=parsed.get("home_score"),
            away_score=parsed.get("away_score"),
            status=parsed.get("status"),
            venue=parsed.get("venue"),
            game_datetime=parsed.get("game_datetime"),
            source="ncaa_casablanca",
            pulled_at=datetime.now(timezone.utc).isoformat(),
            raw_data=parsed
        )


class HenryAPIDataSource(DataSource):
    """Henry API data source (alternative NCAA endpoint)"""
    
    def get_source_type(self) -> SourceType:
        return SourceType.HENRY_API
    
    def fetch_games(self, date: str) -> SourceResult:
        """
        Fetch games from Henry API for a specific date.
        
        Args:
            date: Date in YYYY-MM-DD format
            
        Returns:
            SourceResult with Henry API game data
        """
        # Henry API is essentially NCAA Casablanca proxy
        # Use same implementation as NCAA but mark as henry_api source
        try:
            from ncaa_casablanca_http_client import fetch_scoreboard_by_date
            from ncaa_casablanca_parsers import parse_scoreboard_game
            
            scoreboard_json = fetch_scoreboard_by_date(date)
            
            if not scoreboard_json:
                return self._create_result(False, error="No data in Henry API response")
            
            games_list = scoreboard_json.get("games", [])
            if not games_list:
                games_list = scoreboard_json.get("scoreboard", {}).get("games", [])
            
            if not games_list:
                return self._create_result(False, error="No games in Henry API response")
            
            games = []
            for game in games_list:
                try:
                    parsed = parse_scoreboard_game(game)
                    if parsed:
                        game_data = self._convert_to_game_data(parsed, date)
                        if game_data.is_complete_basic():
                            games.append(game_data)
                except Exception as e:
                    logger.warning(f"Failed to parse Henry API game: {e}")
                    continue
            
            if not games:
                return self._create_result(False, error="No valid games parsed from Henry API")
            
            return self._create_result(True, games=games)
            
        except Exception as e:
            logger.error(f"Henry API fetch failed: {e}")
            return self._create_result(False, error=str(e))
    
    def _convert_to_game_data(self, parsed: dict, date: str) -> GameData:
        """Convert Henry API parsed data to standardized GameData"""
        return GameData(
            game_id=str(parsed.get("game_id", "")),
            date=date,
            home_team=parsed.get("home_team", ""),
            away_team=parsed.get("away_team", ""),
            home_score=parsed.get("home_score"),
            away_score=parsed.get("away_score"),
            status=parsed.get("status"),
            venue=parsed.get("venue"),
            game_datetime=parsed.get("game_datetime"),
            source="henry_api",
            pulled_at=datetime.now(timezone.utc).isoformat(),
            raw_data=parsed
        )


class CBBpyDataSource(DataSource):
    """CBBpy data source - Python-based NCAA basketball web scraper"""

    def get_source_type(self) -> SourceType:
        return SourceType.CBBPY

    def fetch_games(self, date: str) -> SourceResult:
        """
        Fetch games from CBBpy for a specific date.

        Args:
            date: Date in YYYY-MM-DD format

        Returns:
            SourceResult with CBBpy game data
        """
        try:
            import cbbpy.mens_scraper as scraper

            # Convert YYYY-MM-DD to MM-DD-YYYY for cbbpy
            dt = datetime.strptime(date, "%Y-%m-%d")
            cbbpy_date = dt.strftime("%m-%d-%Y")

            game_ids = scraper.get_game_ids(cbbpy_date)
            if not game_ids:
                return self._create_result(False, error=f"No game IDs returned from CBBpy for {date}")

            games = []
            for game_id in game_ids:
                try:
                    info_df = scraper.get_game_info(game_id)
                    if info_df is None or info_df.empty:
                        logger.warning(f"CBBpy game info empty for game {game_id}")
                        continue

                    boxscore_df = None
                    try:
                        boxscore_df = scraper.get_game_boxscore(game_id)
                    except Exception as box_err:
                        logger.warning(f"CBBpy boxscore unavailable for game {game_id}: {box_err}")

                    game_data = self._convert_df_to_game_data(info_df, boxscore_df, date)
                    if game_data and game_data.is_complete_basic():
                        games.append(game_data)
                except Exception as e:
                    logger.warning(f"Failed to fetch CBBpy game {game_id}: {e}")
                    continue

            if not games:
                return self._create_result(
                    False,
                    error=f"No valid games parsed from CBBpy (attempted {len(game_ids)} games)"
                )

            return self._create_result(True, games=games)

        except ImportError:
            logger.error("cbbpy package is not installed")
            return self._create_result(False, error="cbbpy package not installed")
        except Exception as e:
            logger.error(f"CBBpy fetch failed: {e}")
            return self._create_result(False, error=str(e))

    @staticmethod
    def _to_int_or_none(value):
        """Safely parse an integer-like value."""
        if value is None:
            return None
        try:
            text = str(value).strip()
            if not text:
                return None
            return int(float(text))
        except (ValueError, TypeError):
            return None

    def _extract_scores_from_boxscore(self, boxscore_df, home_team: str, away_team: str):
        """Extract team scores from CBBpy game boxscore DataFrame when available."""
        if boxscore_df is None or getattr(boxscore_df, "empty", True):
            return None, None
        if "team" not in boxscore_df.columns or "PTS" not in boxscore_df.columns:
            return None, None

        team_points = boxscore_df.groupby("team", dropna=True)["PTS"].sum(min_count=1).to_dict()
        if len(team_points) < 2:
            return None, None

        home_score = self._to_int_or_none(team_points.get(home_team))
        away_score = self._to_int_or_none(team_points.get(away_team))
        return home_score, away_score

    def _convert_df_to_game_data(self, info_df, boxscore_df, date: str) -> GameData:
        """Convert CBBpy game info/boxscore to standardized GameData."""
        row = info_df.iloc[0]

        home_score = self._to_int_or_none(row.get("home_score"))
        away_score = self._to_int_or_none(row.get("away_score"))

        if home_score is None or away_score is None:
            bs_home, bs_away = self._extract_scores_from_boxscore(
                boxscore_df, str(row.get("home_team", "")), str(row.get("away_team", ""))
            )
            home_score = home_score if home_score is not None else bs_home
            away_score = away_score if away_score is not None else bs_away

        raw_data = {
            "game_info": row.to_dict() if hasattr(row, "to_dict") else None,
            "boxscore": boxscore_df.to_dict(orient="records") if boxscore_df is not None and not boxscore_df.empty else None,
        }

        return GameData(
            game_id=str(row.get("game_id", "")),
            date=date,
            home_team=str(row.get("home_team", "")),
            away_team=str(row.get("away_team", "")),
            home_score=home_score,
            away_score=away_score,
            status="final" if row.get("home_win") is not None else None,
            venue=str(row.get("arena", "")) if row.get("arena") else None,
            game_datetime=f"{row.get('game_day', '')} {row.get('game_time', '')}".strip() or None,
            source="cbbpy",
            pulled_at=datetime.now(timezone.utc).isoformat(),
            raw_data=raw_data,
        )


class CBBDDataSource(DataSource):
    """CollegeBasketballData API source."""

    BASE_URL = os.getenv("CBBD_BASE_URL", "https://api.collegebasketballdata.com")

    def get_source_type(self) -> SourceType:
        return SourceType.CBBD

    def fetch_games(self, date: str) -> SourceResult:
        """Fetch games from CollegeBasketballData API for a specific date."""
        api_key = (os.getenv("CBBD_API_KEY") or "").strip()
        if not api_key:
            return self._create_result(False, error="CBBD_API_KEY not set")

        start_iso = f"{date}T00:00:00Z"
        end_iso = f"{date}T23:59:59Z"
        params = {
            "startDateRange": start_iso,
            "endDateRange": end_iso,
        }
        headers = {"Authorization": f"Bearer {api_key}"}

        try:
            resp = requests.get(f"{self.BASE_URL}/games", params=params, headers=headers, timeout=30)
            resp.raise_for_status()
            payload = resp.json()

            games_payload = payload if isinstance(payload, list) else payload.get("games", [])
            if not isinstance(games_payload, list) or not games_payload:
                return self._create_result(False, error=f"No games in CBBD response for {date}")

            games = []
            for item in games_payload:
                game = self._convert_to_game_data(item, date)
                if game and game.is_complete_basic():
                    games.append(game)

            if not games:
                return self._create_result(False, error=f"No valid CBBD games parsed for {date}")

            return self._create_result(True, games=games)

        except Exception as exc:
            logger.error(f"CBBD fetch failed: {exc}")
            return self._create_result(False, error=str(exc))

    @staticmethod
    def _safe_int(value):
        if value is None:
            return None
        try:
            return int(float(str(value).strip()))
        except Exception:
            return None

    @staticmethod
    def _first_nonempty(payload: dict, keys: List[str]):
        for key in keys:
            val = payload.get(key)
            if val not in (None, ""):
                return val
        return None

    def _convert_to_game_data(self, payload: dict, date: str):
        if not isinstance(payload, dict):
            return None

        game_id = self._first_nonempty(payload, ["id", "gameId", "game_id", "espnGameId", "espn_game_id"])
        if game_id is None:
            return None

        home_team = self._first_nonempty(payload, ["homeTeam", "home_team", "homeTeamName", "home_team_name"])
        away_team = self._first_nonempty(payload, ["awayTeam", "away_team", "awayTeamName", "away_team_name"])
        if not home_team or not away_team:
            return None

        return GameData(
            game_id=str(game_id),
            date=date,
            home_team=str(home_team),
            away_team=str(away_team),
            home_score=self._safe_int(self._first_nonempty(payload, ["homeScore", "home_score", "homePoints", "home_points"])),
            away_score=self._safe_int(self._first_nonempty(payload, ["awayScore", "away_score", "awayPoints", "away_points"])),
            status=str(self._first_nonempty(payload, ["status", "gameStatus", "game_status"]) or "").lower() or None,
            venue=self._first_nonempty(payload, ["venue", "location", "arena"]),
            game_datetime=self._first_nonempty(payload, ["startDate", "start_date", "startTime", "start_time"]),
            source="cbbd",
            pulled_at=datetime.now(timezone.utc).isoformat(),
            raw_data=payload,
        )
