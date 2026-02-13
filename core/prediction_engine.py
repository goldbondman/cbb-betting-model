"""Formula-based prediction engine backed by model_registry definitions."""

from __future__ import annotations

from typing import Any

from core.model_registry import get_active_model
from core.utils import safe_float


class PredictionEngine:
    """Formula-based prediction engine (registry-driven)."""

    def __init__(self, betting_config: dict[str, Any]) -> None:
        self.betting_config = betting_config
        self.active_model = self._load_active_model()

    def _load_active_model(self) -> dict[str, Any]:
        """Load active spread model from registry or fallback if unavailable."""
        model = get_active_model("spread")
        if not model:
            return {
                "model_id": "fallback",
                "model_name": "Fallback Weighted Model",
                "params": {
                    "weights": {
                        "torvik_adjem": 0.55,
                        "recent_netrtg": 0.25,
                        "four_factors": 0.20,
                        "sos_weighted": 0.00,
                    },
                    "hca_mode": "static",
                    "hca_static_value": 2.7,
                    "pace_adjustment": True,
                },
            }
        return model

    def predict_spread(self, home: dict[str, Any], away: dict[str, Any]) -> dict[str, Any]:
        """Generate spread prediction using weighted components and HCA settings."""
        params = self.active_model.get("params", {})
        weights = params.get("weights", {})

        torv_edge = safe_float(home.get("torvik_adj_em"), 0) - safe_float(away.get("torvik_adj_em"), 0)
        recent_edge = safe_float(home.get("netrtg_l7_pre"), 0) - safe_float(away.get("netrtg_l7_pre"), 0)
        ff_edge = self._compute_ff_edge(home, away)
        sos_edge = (safe_float(home.get("sos_weighted_margin_l10_pre"), 0) - safe_float(away.get("sos_weighted_margin_l10_pre"), 0)) / 10.0

        spread_points = (
            weights.get("torvik_adjem", 0) * torv_edge
            + weights.get("recent_netrtg", 0) * recent_edge
            + weights.get("four_factors", 0) * ff_edge
            + weights.get("sos_weighted", 0) * sos_edge
        )

        if params.get("hca_mode") == "dynamic":
            hca = safe_float(home.get("home_margin_lift_l20_pre"), 2.7) - safe_float(
                away.get("away_margin_penalty_l20_pre"), -2.0
            )
        else:
            hca = safe_float(params.get("hca_static_value"), 2.7)

        if params.get("pace_adjustment", True):
            pace = (safe_float(home.get("pace_l7_pre"), 70) + safe_float(away.get("pace_l7_pre"), 70)) / 2.0
            predicted_spread = (spread_points * (pace / 100.0)) + hca
        else:
            predicted_spread = spread_points + hca

        confidence = self._compute_confidence(home, away, params)

        return {
            "predicted_spread": predicted_spread,
            "confidence": confidence,
            "model_id": self.active_model.get("model_id", "fallback"),
            "breakdown": {
                "torvik_edge": torv_edge,
                "recent_edge": recent_edge,
                "ff_edge": ff_edge,
                "sos_edge": sos_edge,
                "hca": hca,
                "weights_used": weights,
            },
        }

    def _compute_ff_edge(self, home: dict[str, Any], away: dict[str, Any]) -> float:
        """Compute four-factors edge as home minus away composite."""
        h_ff = (
            safe_float(home.get("efg_l7_pre"), 0.5)
            - safe_float(home.get("tov_pct_l7_pre"), 0.15)
            + safe_float(home.get("orb_pct_l7_pre"), 0.3)
            + safe_float(home.get("ftr_l7_pre"), 0.3)
        )
        a_ff = (
            safe_float(away.get("efg_l7_pre"), 0.5)
            - safe_float(away.get("tov_pct_l7_pre"), 0.15)
            + safe_float(away.get("orb_pct_l7_pre"), 0.3)
            + safe_float(away.get("ftr_l7_pre"), 0.3)
        )
        return (h_ff - a_ff) * 10

    def _compute_confidence(self, home: dict[str, Any], away: dict[str, Any], params: dict[str, Any]) -> float:
        """Compute confidence using sample size method from model parameters."""
        _ = params
        games_a = safe_float(home.get("games_played_pre"), 0)
        games_b = safe_float(away.get("games_played_pre"), 0)
        sample_boost = min(0.15, (min(games_a, games_b) / 20) * 0.15)
        return max(0.5, min(0.95, 0.60 + sample_boost))
