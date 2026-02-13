"""Bet sizing and recommendation logic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class BetRecommendation:
    """Recommendation payload for rendering and tracking."""

    should_bet: bool
    side: str
    edge: float
    confidence: float
    ev: float
    kelly_units: float
    reason: str


class BettingEngine:
    """Compute expected value, Kelly stake, and gating thresholds."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def recommend_spread(
        self,
        predicted_spread: float,
        market_spread: float | None,
        confidence: float,
        home_team: str,
        away_team: str,
    ) -> BetRecommendation:
        """Recommend home/away spread bet if edge + confidence pass gates."""
        if market_spread is None:
            return BetRecommendation(False, "none", 0.0, confidence, 0.0, 0.0, "No market spread available")

        edge_pts = predicted_spread - float(market_spread)
        side = "home" if edge_pts > 0 else "away"
        edge = abs(edge_pts) / 10.0

        min_edge = float(self.config.get("min_edge", 0.03))
        min_conf = float(self.config.get("min_confidence", 0.60))
        if edge < min_edge:
            return BetRecommendation(False, side, edge, confidence, 0.0, 0.0, "Edge below threshold")
        if confidence < min_conf:
            return BetRecommendation(False, side, edge, confidence, 0.0, 0.0, "Confidence below threshold")

        win_prob = min(0.95, max(0.05, 0.5 + edge / 2.0))
        ev = (win_prob * 0.91) - ((1 - win_prob) * 1.0)
        units = self._kelly_units(win_prob)

        reason = f"{home_team if side == 'home' else away_team} edge {edge_pts:+.2f}"
        return BetRecommendation(True, side, edge, confidence, ev, units, reason)

    def _kelly_units(self, win_prob: float) -> float:
        """Return fractional Kelly units with max cap."""
        b = 0.91
        q = 1 - win_prob
        raw_kelly = ((b * win_prob) - q) / b
        frac = float(self.config.get("kelly_fraction", 0.25))
        max_units = float(self.config.get("max_units", 3.0))
        return max(0.0, min(max_units, raw_kelly * frac * 10.0))
