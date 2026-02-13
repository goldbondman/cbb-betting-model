#!/usr/bin/env python3
"""Main Streamlit orchestrator for formula-based CBB predictions."""

from __future__ import annotations

import streamlit as st

from core.betting_engine import BettingEngine
from core.config import APP_CONFIG, STRATEGY_PRESETS
from core.data_loader import DataLoader
from core.prediction_engine import PredictionEngine
from core.ui_components import PredictionUI

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

    if games.empty:
        st.info("No games today")
        return

    for _, game in games.iterrows():
        existing = daily_preds[daily_preds["event_id"] == game.get("game_id")] if not daily_preds.empty else daily_preds

        if not existing.empty:
            pred = existing.iloc[0].to_dict()
            if "predicted_spread" not in pred and "pred_spread" in pred:
                pred["predicted_spread"] = pred["pred_spread"]
            pred.setdefault("confidence", 0.6)
            pred.setdefault("breakdown", {})
        else:
            home = data.get_team_snapshot(game["home_team"])
            away = data.get_team_snapshot(game["away_team"])
            if not home or not away:
                continue
            pred = pred_engine.predict_spread(home, away)

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
