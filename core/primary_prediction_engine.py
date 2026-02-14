#!/usr/bin/env python3
"""
Primary Prediction Engine adapter.

Wraps the production v2.0 CBBPredictionModel from primary_prediction_model.py
and feeds it ESPN game-log data via DataLoader, following the same interface as
RecursivePredictionEngine so it can be swapped into app.py / streamlit_app.py.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from primary_prediction_model import (
    CBBPredictionModel,
    GameData,
    ModelConfig,
)

logger = logging.getLogger(__name__)

# ============================================================================
# DATA ADAPTER HELPERS (shared with recursive engine)
# ============================================================================

_BOX_KEYS = ('fgm', 'fga', 'tpm', 'tpa', 'ftm', 'fta', 'orb', 'drb', 'tov')

_DEFAULT_BOX: Dict[str, float] = {
    'fgm': 27.0, 'fga': 62.0, 'tpm': 8.0, 'tpa': 22.0,
    'ftm': 14.0, 'fta': 20.0, 'orb': 10.0, 'drb': 25.0, 'tov': 12.0,
}


def _row_has_box(row: pd.Series) -> bool:
    return float(row.get('fga', 0) or 0) > 0


def _extract_box(row: pd.Series) -> Dict[str, float]:
    if _row_has_box(row):
        return {k: float(row.get(k, 0) or 0) for k in _BOX_KEYS}
    score = float(row.get('points_for', 0) or 0)
    box = dict(_DEFAULT_BOX)
    if score > 0:
        ratio = score / 72.0
        box = {k: round(v * ratio, 1) for k, v in _DEFAULT_BOX.items()}
    return box


def _parse_date(raw: object) -> datetime:
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return datetime(2000, 1, 1)
    try:
        return pd.Timestamp(raw).to_pydatetime()
    except Exception:
        return datetime(2000, 1, 1)


# ============================================================================
# PRIMARY PREDICTION ENGINE
# ============================================================================

class PrimaryPredictionEngine:
    """
    Adapter that reads ESPN game logs via DataLoader, builds GameData
    histories with recursive opponent context, and predicts spreads
    using the production v2.0 CBBPredictionModel from
    primary_prediction_model.py.

    Exposes the same return signature as PredictionEngine.predict_spread()
    and RecursivePredictionEngine.predict_spread() so it can be used
    anywhere in the app.
    """

    MODEL_ID = "primary_v2_normalized_bidirectional"

    def __init__(self, data_loader: Any, config: Optional[ModelConfig] = None) -> None:
        self.data_loader = data_loader
        self.config = config or ModelConfig()
        self.model = CBBPredictionModel(self.config)
        self._game_log_df: Optional[pd.DataFrame] = None
        self._team_index: Dict[str, pd.DataFrame] = {}

    # -- public interface -----------------------------------------------------

    @property
    def active_model(self) -> Dict[str, Any]:
        return {"model_id": self.MODEL_ID, "model_name": "Primary v2.0 (Normalized Bidirectional)"}

    def predict_spread(self, home_team: str, away_team: str, neutral_site: bool = False) -> Dict[str, Any]:
        """
        Predict spread for *home_team* vs *away_team*.

        Returns dict compatible with app.py expectations::

            {
                "predicted_spread": float,
                "confidence": float,
                "model_id": str,
                "breakdown": dict,
            }
        """
        self._ensure_loaded()

        home_games = self._build_team_games(home_team, n=self.config.l10_window)
        away_games = self._build_team_games(away_team, n=self.config.l10_window)

        if not home_games and not away_games:
            logger.warning("No game data for either team: %s vs %s", home_team, away_team)
            return {
                "predicted_spread": -self.config.default_hca if not neutral_site else 0.0,
                "confidence": 0.50,
                "model_id": self.MODEL_ID,
                "breakdown": {},
            }

        raw = self.model.predict_game(home_games, away_games, neutral_site=neutral_site)

        return {
            "predicted_spread": raw["predicted_spread"],
            "predicted_total": raw.get("predicted_total"),
            "confidence": raw["confidence"],
            "model_id": self.MODEL_ID,
            "breakdown": raw.get("breakdown", {}),
        }

    # -- data loading ---------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._game_log_df is not None:
            return

        df = self.data_loader.load_feature_store()
        if df.empty:
            logger.warning("Game log data is empty; primary model will use defaults.")
            self._game_log_df = pd.DataFrame()
            return

        if "game_date" not in df.columns and "game_datetime_utc" in df.columns:
            df["game_date"] = pd.to_datetime(df["game_datetime_utc"], errors="coerce")
        elif "game_date" in df.columns:
            df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce")

        if "completed" in df.columns:
            df = df[df["completed"].astype(str).str.lower().isin(["true", "1"])].copy()

        self._game_log_df = df.sort_values("game_date", ascending=True).reset_index(drop=True)

        if "team" in self._game_log_df.columns:
            for team_name, grp in self._game_log_df.groupby("team"):
                self._team_index[str(team_name).lower()] = grp

    def _get_opponent_row(self, event_id: str, opponent_name: str) -> Optional[pd.Series]:
        assert self._game_log_df is not None
        opp_key = str(opponent_name).lower()
        opp_df = self._team_index.get(opp_key)
        if opp_df is not None:
            match = opp_df[opp_df["event_id"].astype(str) == str(event_id)]
            if not match.empty:
                return match.iloc[0]
        return None

    # -- GameData construction ------------------------------------------------

    def _build_team_games(self, team_name: str, n: int = 10) -> List[GameData]:
        self._ensure_loaded()
        if self._game_log_df is None or self._game_log_df.empty:
            return []

        team_key = team_name.lower()
        team_df = self._team_index.get(team_key)
        if team_df is None or team_df.empty:
            return []

        recent = team_df.tail(n)
        games: List[GameData] = []

        for _, row in recent.iterrows():
            gd = self._row_to_game_data(row, populate_opp_history=True)
            if gd is not None:
                games.append(gd)

        return games

    def _row_to_game_data(self, row: pd.Series, populate_opp_history: bool = False) -> Optional[GameData]:
        try:
            team_name = str(row.get("team", ""))
            opponent_name = str(row.get("opponent", ""))
            event_id = str(row.get("event_id", ""))
            game_date = _parse_date(row.get("game_date"))

            team_score = int(float(row.get("points_for", 0) or 0))
            opp_score = int(float(row.get("points_against", 0) or 0))

            home_away = str(row.get("home_away", "")).lower()
            neutral_site = home_away not in ("home", "away")

            team_box = _extract_box(row)

            opp_row = self._get_opponent_row(event_id, opponent_name)
            if opp_row is not None:
                opp_box = _extract_box(opp_row)
                if opp_score == 0:
                    opp_score = int(float(opp_row.get("points_for", 0) or 0))
                if team_score == 0:
                    team_score = int(float(opp_row.get("points_against", 0) or 0))
            else:
                opp_box = _extract_box(pd.Series(dtype="float64"))

            opp_history: List[GameData] = []
            if populate_opp_history:
                opp_history = self._build_opponent_history(opponent_name, before=game_date)

            return GameData(
                game_id=event_id,
                date=game_date,
                team_name=team_name,
                opponent_name=opponent_name,
                team_score=team_score,
                opponent_score=opp_score,
                neutral_site=neutral_site,
                team_box=team_box,
                opponent_box=opp_box,
                opponent_history=opp_history,
            )
        except Exception:
            logger.debug("Failed to parse game-log row", exc_info=True)
            return None

    def _build_opponent_history(self, opponent_name: str, before: datetime, n: int = 5) -> List[GameData]:
        assert self._game_log_df is not None
        opp_key = opponent_name.lower()
        opp_df = self._team_index.get(opp_key)
        if opp_df is None or opp_df.empty:
            return []

        before_ts = pd.Timestamp(before)
        prior = opp_df[opp_df["game_date"] < before_ts]
        if prior.empty:
            return []

        recent = prior.tail(n)
        games: List[GameData] = []
        for _, row in recent.iterrows():
            gd = self._row_to_game_data(row, populate_opp_history=False)
            if gd is not None:
                games.append(gd)
        return games
