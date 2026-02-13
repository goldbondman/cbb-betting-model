"""Data loading layer for files and Supabase-backed views."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from functools import lru_cache
from typing import Any

import pandas as pd

from core.config import APP_CONFIG

logger = logging.getLogger(__name__)


class DataLoader:
    """Access game, feature, and prediction data with light caching."""

    def __init__(self) -> None:
        data_cfg = APP_CONFIG.get("data", {})
        self._feature_store_path = str(data_cfg.get("feature_store_path", "ESPN/CSV/espn_team_game_features.csv"))
        self._feature_store_fallback_path = str(data_cfg.get("feature_store_fallback_path", "ESPN/CSV/espn_team_game_logs.csv"))
        self._predictions_csv_paths: list[str] = list(
            data_cfg.get("predictions_csv_paths", ["data/predictions.csv", "ml/predictions_latest.csv"])
        )

    @staticmethod
    @lru_cache(maxsize=1)
    def _supabase_client() -> "Any | None":
        url = (os.getenv("SUPABASE_URL") or "").strip()
        key = (os.getenv("SUPABASE_ANON_KEY") or "").strip()
        if not url or not key:
            logger.info("Supabase credentials not set; using CSV fallback.")
            return None
        try:
            from supabase import create_client
            return create_client(url, key)
        except Exception as exc:
            logger.warning("Failed to create Supabase client: %s", exc)
            return None

    @staticmethod
    @lru_cache(maxsize=8)
    def _load_csv(path: str) -> pd.DataFrame:
        if not os.path.exists(path):
            return pd.DataFrame()
        try:
            return pd.read_csv(path)
        except Exception:
            return pd.DataFrame()

    def load_feature_store(self) -> pd.DataFrame:
        """Load feature store from primary CSV, then fallback logs CSV."""
        df = self._load_csv(self._feature_store_path).copy()
        if df.empty:
            df = self._load_csv(self._feature_store_fallback_path).copy()
        if df.empty:
            return pd.DataFrame()

        if "game_date" in df.columns:
            df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce")
        elif "game_datetime_utc" in df.columns:
            df["game_date"] = pd.to_datetime(df["game_datetime_utc"], errors="coerce")

        if "event_id" not in df.columns:
            df["event_id"] = df.index.astype(str)

        return df

    def get_team_snapshot(self, team_name: str) -> dict[str, Any]:
        """Return latest pregame row for a team from feature store."""
        df = self.load_feature_store()
        if df.empty or "team" not in df.columns:
            return {}
        team_df = df[df["team"].astype(str).str.lower() == team_name.lower()].copy()
        if team_df.empty:
            return {}
        sort_col = "game_date" if "game_date" in team_df.columns else team_df.columns[0]
        team_df = team_df.sort_values(sort_col, ascending=False)
        return team_df.iloc[0].to_dict()

    def load_vegas_lines(self, date: str = "today") -> pd.DataFrame:
        """Load game lines from espn_games.csv, optionally filtered to date."""
        games_df = self._load_csv("ESPN/CSV/espn_games.csv").copy()
        if games_df.empty:
            games_df = self._load_csv("espn_games.csv").copy()
        if games_df.empty:
            return pd.DataFrame()
        games_df["game_date"] = pd.to_datetime(games_df.get("date"), format="%Y%m%d", errors="coerce")
        if date == "today":
            today = pd.Timestamp(datetime.utcnow().date())
            return games_df[games_df["game_date"].dt.date == today.date()].copy()
        return games_df

    def _load_predictions_from_csv(self) -> pd.DataFrame:
        """Try each configured CSV path until one returns data."""
        for path in self._predictions_csv_paths:
            df = self._load_csv(path)
            if not df.empty:
                logger.info("Loaded predictions from CSV: %s", path)
                return df.copy()
        return pd.DataFrame()

    def load_todays_predictions(self) -> pd.DataFrame:
        """Load predictions from Supabase if configured, else fall back to CSV."""
        client = self._supabase_client()
        if client is not None:
            try:
                response = client.table("predictions_latest").select("*").execute()
                data = pd.DataFrame(response.data or [])
                if not data.empty:
                    return data
                logger.info("Supabase predictions_latest returned no rows; trying CSV fallback.")
            except Exception as exc:
                logger.warning("Supabase predictions query failed: %s; trying CSV fallback.", exc)

        return self._load_predictions_from_csv()

    def load_historical_games(self, start_date: str, end_date: str) -> pd.DataFrame:
        """Load completed historical games in range with actual margin field."""
        games_df = self._load_csv("ESPN/CSV/espn_games.csv").copy()
        if games_df.empty:
            games_df = self._load_csv("espn_games.csv").copy()
        if games_df.empty:
            return pd.DataFrame()

        games_df["game_date"] = pd.to_datetime(games_df.get("date"), format="%Y%m%d", errors="coerce")
        start_ts = pd.to_datetime(start_date, errors="coerce")
        end_ts = pd.to_datetime(end_date, errors="coerce")
        if pd.isna(start_ts) or pd.isna(end_ts):
            return pd.DataFrame()

        completed = games_df.get("completed", False)
        mask = (games_df["game_date"] >= start_ts) & (games_df["game_date"] <= end_ts) & (completed == True)
        result = games_df.loc[mask].copy()

        if "margin" not in result.columns and {"home_score", "away_score"}.issubset(result.columns):
            result["margin"] = result["home_score"] - result["away_score"]

        return result
