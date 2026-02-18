"""Data loading layer for files and Supabase-backed views."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

import pandas as pd

from core.config import APP_CONFIG
from core.supabase_schema import (
    MARKET_SPREAD_ALIASES,
    RAW_PREDICTIONS_SCHEMA,
    RAW_PREDICTIONS_TABLE,
)
from core.supabase_utils import get_public_supabase_client

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
        client = get_public_supabase_client()
        if client is None:
            logger.info("Supabase credentials not set; using CSV fallback.")
        return client

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
        if "market_spread" not in games_df.columns:
            for col in MARKET_SPREAD_ALIASES:
                if col in games_df.columns:
                    games_df["market_spread"] = pd.to_numeric(games_df[col], errors="coerce")
                    break
        elif games_df["market_spread"].dtype == object:
            games_df["market_spread"] = pd.to_numeric(games_df["market_spread"], errors="coerce")

        if "market_spread" not in games_df.columns:
            games_df["market_spread"] = pd.NA

        games_df["game_date"] = pd.to_datetime(games_df.get("date"), format="%Y%m%d", errors="coerce")
        if games_df["game_date"].isna().all() and "game_datetime_utc" in games_df.columns:
            games_df["game_date"] = pd.to_datetime(games_df["game_datetime_utc"], errors="coerce")
        if date == "today":
            today = pd.Timestamp(datetime.now(timezone.utc).date())
            today_games = games_df[games_df["game_date"].dt.date == today.date()].copy()
            if not today_games.empty:
                return today_games
            latest_date = games_df["game_date"].dropna().max()
            if pd.notna(latest_date):
                return games_df[games_df["game_date"].dt.date == latest_date.date()].copy()
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
        """Load predictions from Supabase with multiple fallbacks, else fall back to CSV."""
        client = self._supabase_client()
        if client is not None:
            # REDUNDANCY 1: Try public.predictions (primary table)
            try:
                logger.info("Attempting to load predictions from public.predictions")
                response = client.table("predictions").select("*").execute()
                data = pd.DataFrame(response.data or [])
                if not data.empty:
                    logger.info("✓ Loaded %d predictions from public.predictions", len(data))
                    return data
                logger.info("No predictions in public.predictions")
            except Exception as exc:
                logger.warning("Failed to query public.predictions: %s", exc)

            # REDUNDANCY 2: Try raw.predictions_latest (source table)
            try:
                logger.info("Attempting fallback to %s.%s", RAW_PREDICTIONS_SCHEMA, RAW_PREDICTIONS_TABLE)
                response = client.schema(RAW_PREDICTIONS_SCHEMA).table(RAW_PREDICTIONS_TABLE).select("*").execute()
                data = pd.DataFrame(response.data or [])
                if not data.empty:
                    logger.info("✓ Loaded %d predictions from raw.predictions_latest", len(data))
                    return data
                logger.info("No predictions in raw.predictions_latest")
            except Exception as exc:
                logger.warning("Failed to query raw.predictions_latest: %s", exc)

            logger.info("All Supabase queries returned empty; trying CSV fallback")

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
