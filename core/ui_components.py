"""Reusable Streamlit UI components for predictions and betting output."""

from __future__ import annotations

from typing import Any

import streamlit as st

from core.betting_engine import BetRecommendation
from core.utils import format_spread


class PredictionUI:
    """DraftKings-style UI blocks for daily prediction cards."""

    def render_prediction_card(
        self,
        home_team: str,
        away_team: str,
        prediction: dict[str, Any],
        vegas_spread: float | None,
    ) -> None:
        """Render matchup card including formula transparency breakdown."""
        st.subheader(f"{away_team} @ {home_team}")
        col1, col2, col3 = st.columns(3)
        col1.metric("Model Spread", format_spread(prediction.get("predicted_spread", 0), home_team))
        col2.metric("Market Spread", "N/A" if vegas_spread is None else format_spread(vegas_spread, home_team))
        col3.metric("Confidence", f"{prediction.get('confidence', 0):.1%}")

        breakdown = prediction.get("breakdown", {})
        with st.expander("Formula Breakdown"):
            st.json(breakdown)

    def render_bet_recommendation(self, recommendation: BetRecommendation, teams: dict[str, str]) -> None:
        """Render actionable recommendation + stake details."""
        if not recommendation.should_bet:
            st.info(f"No bet: {recommendation.reason}")
            return

        team = teams.get(recommendation.side, recommendation.side)
        st.success(
            (
                f"Bet {team} | Edge: {recommendation.edge:.2%} | "
                f"EV: {recommendation.ev:.3f} | Units: {recommendation.kelly_units:.2f}"
            )
        )

    def render_model_studio(self, models: list[dict[str, Any]]) -> None:
        """Render compact model table for quick inspection."""
        if not models:
            st.caption("No models available")
            return
        st.dataframe(models, use_container_width=True)
