"""Data loading layer for files and Supabase-backed views."""

from __future__ import annotations

import os
from datetime import datetime
from functools import lru_cache
from typing import Any

import pandas as pd
from supabase import Client, create_client

from core.config import APP_CONFIG


class DataLoader:
    """Access game, feature, and prediction data with light caching."""

    def __init__(self) -> None:
        self._feature_store_path = str(APP_CONFIG.get("data", {}).get("feature_store_path", "espn_team_game_features.csv"))

    @staticmethod
    @lru_cache(maxsize=1)
    def _supabase_client() -> Client | None:
        url = (os.getenv("SUPABASE_URL") or "").strip()
        key = (os.getenv("SUPABASE_ANON_KEY") or "").strip()
        if not url or not key:
            return None
        try:
            return create_client(url, key)
        except Exception:
            return None

    @staticmethod
    @lru_cache(maxsize=4)
    def _load_csv(path: str) -> pd.DataFrame:
        if not os.path.exists(path):
            return pd.DataFrame()
        try:
            return pd.read_csv(path)
        except Exception:
            return pd.DataFrame()

    def load_feature_store(self) -> pd.DataFrame:
        """Load the feature store CSV with game-date coercion."""
        df = self._load_csv(self._feature_store_path).copy()
        if "game_date" in df.columns:
            df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce")
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
        games_df = self._load_csv("espn_games.csv").copy()
        if games_df.empty:
            games_df = self._load_csv("ESPN/CSV/espn_games.csv").copy()
        if games_df.empty:
            return pd.DataFrame()
        games_df["game_date"] = pd.to_datetime(games_df.get("date"), format="%Y%m%d", errors="coerce")
        if date == "today":
            today = pd.Timestamp(datetime.utcnow().date())
            return games_df[games_df["game_date"].dt.date == today.date()].copy()
        return games_df

    def load_todays_predictions(self) -> pd.DataFrame:
        """Load current predictions_latest rows from Supabase if configured."""
        client = self._supabase_client()
        if client is None:
            return pd.DataFrame()
        try:
            response = client.table("predictions_latest").select("*").execute()
            return pd.DataFrame(response.data or [])
        except Exception:
            return pd.DataFrame()

    def load_historical_games(self, start_date: str, end_date: str) -> pd.DataFrame:
        """Load completed historical games in range with actual margin field."""
        games_df = self._load_csv("espn_games.csv").copy()
        if games_df.empty:
            games_df = self._load_csv("ESPN/CSV/espn_games.csv").copy()
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
