#!/usr/bin/env python3
"""Main Streamlit orchestrator for formula-based CBB predictions."""

from __future__ import annotations

import logging

import pandas as pd
import streamlit as st

from core.betting_engine import BettingEngine
from core.config import APP_CONFIG, STRATEGY_PRESETS
from core.data_loader import DataLoader
from core.prediction_engine import PredictionEngine
from core.ui_components import PredictionUI

logger = logging.getLogger(__name__)

st.set_page_config(page_title="CBB Model", page_icon="🏀", layout="wide")


def main() -> None:
    """Render main dashboard using active formula model and selected strategy."""
    data = DataLoader()
    ui = PredictionUI()

    strategy = st.sidebar.selectbox("Strategy", list(STRATEGY_PRESETS.keys()), index=1)
    config = STRATEGY_PRESETS[strategy].copy()

    pred_engine = PredictionEngine(config)
    bet_engine = BettingEngine(config["betting"])

    st.title(f"🏀 CBB Betting Model {APP_CONFIG['version']}")
    st.caption(f"Strategy: {strategy} | Active Model: {pred_engine.active_model['model_id']}")

    games = data.load_vegas_lines(date="today")
    daily_preds = data.load_todays_predictions()

    if not isinstance(games, pd.DataFrame) or games.empty:
        st.info("No games today")
        return

    for _, game in games.iterrows():
        game_id = game.get("game_id")
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
            if "predicted_spread" not in pred and "pred_spread" in pred:
                pred["predicted_spread"] = pred["pred_spread"]
            pred.setdefault("confidence", 0.6)
            pred.setdefault("breakdown", {})
        else:
            home = data.get_team_snapshot(game.get("home_team", ""))
            away = data.get_team_snapshot(game.get("away_team", ""))
            if not home or not away:
                logger.warning(
                    "Missing team snapshot for game: home=%s, away=%s",
                    game.get("home_team"),
                    game.get("away_team"),
                )
                continue
            pred = pred_engine.predict_spread(home, away)

        # Validate required prediction keys
        if "predicted_spread" not in pred:
            logger.error("Prediction missing 'predicted_spread' key for game_id=%s", game_id)
            continue
        if "confidence" not in pred:
            logger.warning("Prediction missing 'confidence' key for game_id=%s, using default", game_id)
            pred["confidence"] = 0.6

        ui.render_prediction_card(
            home_team=game["home_team"],
            away_team=game["away_team"],
            prediction=pred,
            vegas_spread=game.get("market_spread"),
        )

        bet = bet_engine.recommend_spread(
            predicted_spread=pred["predicted_spread"],
            market_spread=game.get("market_spread"),
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
