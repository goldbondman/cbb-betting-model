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

# Initialize session state for new features
if "bet_history" not in st.session_state:
    st.session_state.bet_history = []
if "bankroll" not in st.session_state:
    st.session_state.bankroll = 10000.0  # Default starting bankroll
if "show_analytics" not in st.session_state:
    st.session_state.show_analytics = False


def main() -> None:
    """Render main dashboard using active model and selected strategy."""
    data = DataLoader()
    ui = PredictionUI()

    # Sidebar configuration
    st.sidebar.title("⚙️ Configuration")
    
    strategy = st.sidebar.selectbox("Strategy", list(STRATEGY_PRESETS.keys()), index=1)
    config = STRATEGY_PRESETS[strategy].copy()

    model_choice = st.sidebar.selectbox("Prediction Model", MODEL_OPTIONS, index=0)
    
    # New: Bankroll Management Section
    st.sidebar.divider()
    st.sidebar.subheader("💰 Bankroll")
    
    col1, col2 = st.sidebar.columns(2)
    with col1:
        current_bankroll = st.number_input(
            "Current", 
            min_value=0.0, 
            value=st.session_state.bankroll,
            step=100.0,
            key="bankroll_input"
        )
        st.session_state.bankroll = current_bankroll
    
    with col2:
        unit_size = st.number_input(
            "Unit Size",
            min_value=1.0,
            value=current_bankroll / 100,
            step=10.0
        )
    
    # Show bankroll analytics
    if st.session_state.bet_history:
        total_wagered = sum(b.get("stake", 0) for b in st.session_state.bet_history)
        total_profit = sum(b.get("profit", 0) for b in st.session_state.bet_history)
        roi = (total_profit / total_wagered * 100) if total_wagered > 0 else 0
        
        st.sidebar.metric("Total P&L", f"${total_profit:+,.0f}", f"{roi:+.1f}% ROI")
    
    # New: Quick Stats Toggle
    st.sidebar.divider()
    show_analytics = st.sidebar.checkbox("Show Performance Analytics", value=st.session_state.show_analytics)
    st.session_state.show_analytics = show_analytics
    
    if show_analytics and st.session_state.bet_history:
        st.sidebar.subheader("📊 Quick Stats")
        wins = sum(1 for b in st.session_state.bet_history if b.get("result") == "win")
        total = len(st.session_state.bet_history)
        win_rate = wins / total if total > 0 else 0
        st.sidebar.metric("Win Rate", f"{win_rate:.1%}", f"{wins}/{total}")

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
    
    # New: Performance Analytics Dashboard
    if show_analytics and st.session_state.bet_history:
        with st.expander("📊 Performance Analytics", expanded=False):
            col1, col2, col3, col4, col5 = st.columns(5)
            
            total_bets = len(st.session_state.bet_history)
            wins = sum(1 for b in st.session_state.bet_history if b.get("result") == "win")
            losses = sum(1 for b in st.session_state.bet_history if b.get("result") == "loss")
            total_wagered = sum(b.get("stake", 0) for b in st.session_state.bet_history)
            total_profit = sum(b.get("profit", 0) for b in st.session_state.bet_history)
            
            col1.metric("Total Bets", total_bets)
            col2.metric("Record", f"{wins}-{losses}")
            col3.metric("Win Rate", f"{wins/total_bets:.1%}" if total_bets > 0 else "N/A")
            col4.metric("Total Wagered", f"${total_wagered:,.0f}")
            col5.metric("Net P&L", f"${total_profit:+,.0f}")
            
            # Recent bet history
            if st.session_state.bet_history:
                st.subheader("Recent Bets")
                recent = st.session_state.bet_history[-10:]  # Last 10 bets
                df = pd.DataFrame(recent)
                st.dataframe(df, use_container_width=True)
                
                if st.button("Clear Bet History"):
                    st.session_state.bet_history = []
                    st.rerun()

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
        
        # New: Track Bet Button
        if bet.should_bet:
            col1, col2, col3 = st.columns([2, 1, 1])
            with col1:
                st.write("")  # Spacer
            with col2:
                stake_amount = st.number_input(
                    "Stake ($)", 
                    min_value=0.0, 
                    value=bet.kelly_units * unit_size,
                    step=10.0,
                    key=f"stake_{game_id}"
                )
            with col3:
                if st.button("📝 Track Bet", key=f"track_{game_id}"):
                    # Format bet side display string
                    team = game['home_team'] if bet.side == 'home' else game['away_team']
                    spread_str = f"{market_spread:+.1f}" if market_spread else ""
                    side_display = f"{team} {spread_str}" if spread_str else bet.side
                    
                    bet_record = {
                        "game": f"{game['away_team']} @ {game['home_team']}",
                        "side": side_display,
                        "stake": stake_amount,
                        "edge": bet.edge,
                        "confidence": bet.confidence,
                        "ev": bet.ev,
                        "result": None,  # To be filled in later
                        "profit": 0.0,  # To be filled in later
                    }
                    st.session_state.bet_history.append(bet_record)
                    st.success(f"✅ Bet tracked: ${stake_amount:.0f} on {bet_record['side']}")
        
        st.divider()


if __name__ == "__main__":
    main()
