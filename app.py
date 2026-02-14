#!/usr/bin/env python3
"""Main Streamlit orchestrator for CBB predictions."""

from __future__ import annotations

import logging

import pandas as pd
import streamlit as st

from core.betting_engine import BettingEngine
from core.config import APP_CONFIG, STRATEGY_PRESETS
from core.data_loader import DataLoader
from core.prediction_engine import PredictionEngine
from core.primary_prediction_engine import PrimaryPredictionEngine
from core.recursive_prediction_engine import RecursivePredictionEngine
from core.ui_components import PredictionUI

logger = logging.getLogger(__name__)

# Column name aliases for prediction spread values
# Maps from database column names to app internal format
SPREAD_COLUMN_ALIASES = ["pred_spread", "pred_margin_home", "ensemble_prediction"]

st.set_page_config(page_title="CBB Model", page_icon="🏀", layout="wide")

MODEL_OPTIONS = [
    "Primary (v2.0 Normalized Bidirectional)",
    "Recursive Bidirectional",
    "Formula",
]


def main() -> None:
    """Render main dashboard using active model and selected strategy."""
    data = DataLoader()
    ui = PredictionUI()

    strategy = st.sidebar.selectbox("Strategy", list(STRATEGY_PRESETS.keys()), index=1)
    config = STRATEGY_PRESETS[strategy].copy()

    model_choice = st.sidebar.selectbox("Prediction Model", MODEL_OPTIONS, index=0)

    pred_engine = PredictionEngine(config)
    primary_engine = PrimaryPredictionEngine(data) if model_choice == MODEL_OPTIONS[0] else None
    rec_engine = RecursivePredictionEngine(data) if model_choice == MODEL_OPTIONS[1] else None
    bet_engine = BettingEngine(config["betting"])

    if primary_engine is not None:
        active_model_id = primary_engine.active_model["model_id"]
    elif rec_engine is not None:
        active_model_id = rec_engine.active_model["model_id"]
    else:
        active_model_id = pred_engine.active_model["model_id"]
    st.title(f"🏀 CBB Betting Model {APP_CONFIG['version']}")
    st.caption(f"Strategy: {strategy} | Active Model: {active_model_id}")

    games = data.load_vegas_lines(date="today")
    try:
        daily_preds = data.load_todays_predictions()
        if not daily_preds.empty:
            logger.info("Loaded %d precomputed predictions", len(daily_preds))
        else:
            logger.info("No precomputed predictions available")
    except Exception as exc:
        logger.warning(
            "Failed to load today's predictions: %s; using live per-game predictions.",
            exc,
        )
        st.sidebar.info("Precomputed predictions unavailable; using live model predictions.")
        daily_preds = pd.DataFrame()

    if not isinstance(games, pd.DataFrame) or games.empty:
        st.info("No games today")
        return

    for _, game in games.iterrows():
        # Safe access to game_id with fallback
        if "game_id" not in game.index:
            logger.warning("Game row missing 'game_id' column, skipping")
            continue
        game_id = game["game_id"]
            
        # Handle potential key mismatch between game_id and event_id
        if not daily_preds.empty:
            if "event_id" in daily_preds.columns:
                existing = daily_preds[daily_preds["event_id"] == game_id]
            elif "game_id" in daily_preds.columns:
                existing = daily_preds[daily_preds["game_id"] == game_id]
            else:
                existing = pd.DataFrame()
        else:
            existing = daily_preds

        if not existing.empty:
            pred = existing.iloc[0].to_dict()
            # Handle various column name formats for predicted spread
            if "predicted_spread" not in pred:
                for alias in SPREAD_COLUMN_ALIASES:
                    if alias in pred:
                        pred["predicted_spread"] = pred[alias]
                        break
            pred.setdefault("confidence", 0.6)
            pred.setdefault("breakdown", {})
        else:
            # Safe access to team names with validation
            if "home_team" not in game.index or "away_team" not in game.index:
                logger.warning("Game row missing team columns, skipping game_id=%s", game_id)
                continue
                
            home = data.get_team_snapshot(game["home_team"])
            away = data.get_team_snapshot(game["away_team"])
            if primary_engine is not None:
                pred = primary_engine.predict_spread(game["home_team"], game["away_team"])
            elif rec_engine is not None:
                pred = rec_engine.predict_spread(game["home_team"], game["away_team"])
            elif not home or not away:
                logger.warning(
                    "Missing team snapshot for game: home=%s, away=%s",
                    game["home_team"],
                    game["away_team"],
                )
                continue
            else:
                pred = pred_engine.predict_spread(home, away)

        # Validate required prediction keys
        if "predicted_spread" not in pred:
            logger.error("Prediction missing 'predicted_spread' key for game_id=%s", game_id)
            continue
        if "confidence" not in pred:
            logger.warning("Prediction missing 'confidence' key for game_id=%s, using default", game_id)
            pred["confidence"] = 0.6

        # Extract market spread once to avoid duplication
        market_spread = game["market_spread"] if "market_spread" in game.index and pd.notna(game["market_spread"]) else None

        ui.render_prediction_card(
            home_team=game["home_team"],
            away_team=game["away_team"],
            prediction=pred,
            vegas_spread=market_spread,
        )

        bet = bet_engine.recommend_spread(
            predicted_spread=pred["predicted_spread"],
            market_spread=market_spread,
            confidence=pred["confidence"],
            home_team=game["home_team"],
            away_team=game["away_team"],
        )
        ui.render_bet_recommendation(
            bet,
            {
                "home": game["home_team"],
                "away": game["away_team"],
            },
        )
        st.divider()


if __name__ == "__main__":
    main()
