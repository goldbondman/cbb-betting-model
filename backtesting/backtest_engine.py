"""Backtest Engine - Test formula models on historical data."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from core.data_loader import DataLoader
from core.utils import safe_float


class BacktestEngine:
    """Run historical accuracy tests on formula models."""

    def __init__(self) -> None:
        self.data_loader = DataLoader()

    def backtest_model(self, model: dict[str, Any], days_back: int = 30) -> dict[str, Any]:
        """Backtest model on completed games and return summary metrics."""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days_back)

        games = self.data_loader.load_historical_games(start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))
        if games.empty:
            return self._empty_results()

        results: list[dict[str, Any]] = []
        for _, game in games.iterrows():
            if not game.get("completed") or pd.isna(game.get("margin")):
                continue

            home_snap = self._get_snapshot_at_date(str(game.get("home_team", "")), game["game_date"])
            away_snap = self._get_snapshot_at_date(str(game.get("away_team", "")), game["game_date"])
            if not home_snap or not away_snap:
                continue

            pred = self._predict_with_params(home_snap, away_snap, model.get("params", {}))
            actual_margin = safe_float(game.get("margin"), 0)
            predicted_spread = pred["predicted_spread"]
            error = abs(predicted_spread - actual_margin)
            correct_side = (predicted_spread > 0) == (actual_margin > 0)

            if pd.notna(game.get("market_spread")):
                market_spread = safe_float(game.get("market_spread"), 0)
                edge = abs(predicted_spread - market_spread)
                bet_side = "home" if predicted_spread > market_spread else "away"
                bet_won = actual_margin > market_spread if bet_side == "home" else actual_margin < market_spread
                ats_result = 1.0 if bet_won else 0.0
            else:
                edge = None
                ats_result = None

            results.append(
                {
                    "game_id": game.get("game_id"),
                    "date": game.get("game_date"),
                    "home_team": game.get("home_team"),
                    "away_team": game.get("away_team"),
                    "predicted_spread": predicted_spread,
                    "actual_margin": actual_margin,
                    "error": error,
                    "correct_side": correct_side,
                    "market_spread": game.get("market_spread"),
                    "edge": edge,
                    "ats_result": ats_result,
                }
            )

        df = pd.DataFrame(results)
        if df.empty:
            return self._empty_results()

        mae = float(df["error"].mean())
        win_pct = float(df["correct_side"].mean())

        ats_games = df[df["ats_result"].notna()]
        if not ats_games.empty:
            wins = int((ats_games["ats_result"] == 1.0).sum())
            losses = int((ats_games["ats_result"] == 0.0).sum())
            roi = (wins * 0.91 - losses) / len(ats_games)
            edge_dist = {
                "0-3": int(((ats_games["edge"] >= 0) & (ats_games["edge"] < 3)).sum()),
                "3-6": int(((ats_games["edge"] >= 3) & (ats_games["edge"] < 6)).sum()),
                "6-9": int(((ats_games["edge"] >= 6) & (ats_games["edge"] < 9)).sum()),
                "9+": int((ats_games["edge"] >= 9).sum()),
            }
        else:
            roi = 0.0
            edge_dist = {}

        return {
            "mae": mae,
            "win_pct": win_pct,
            "roi": float(roi),
            "total_games": len(df),
            "edge_distribution": edge_dist,
            "details": df,
        }

    def _predict_with_params(self, home: dict[str, Any], away: dict[str, Any], params: dict[str, Any]) -> dict[str, float]:
        """Generate prediction using explicit params payload.
        
        Supports both legacy (4 features) and enhanced (8 features) models.
        
        Legacy features:
        - torvik_adjem: Adjusted efficiency margin differential
        - recent_netrtg: Last 7 games net rating differential
        - four_factors: Composite of eFG%, TOV%, ORB%, FTR
        - sos_weighted: Strength of schedule (L10) weighted margin
        
        Enhanced features (v2):
        - def_efficiency: Defensive rating differential (lower DRTG is better)
        - off_efficiency: Offensive rating differential (higher ORTG is better)
        - tempo_advantage: Pace differential scaled by impact factor
        - three_rate: 3-point attempt rate differential
        
        All features use pre-game stats (L7 or L10) to avoid data leakage.
        Missing data is handled gracefully with reasonable defaults.
        
        Args:
            home: Team snapshot with pre-game stats
            away: Team snapshot with pre-game stats
            params: Model configuration with weights and HCA settings
            
        Returns:
            Dict with predicted_spread key (positive favors home team)
        """
        weights = params.get("weights", {})
        
        # Core metrics
        torv_edge = safe_float(home.get("torvik_adj_em"), 0) - safe_float(away.get("torvik_adj_em"), 0)
        recent_edge = safe_float(home.get("netrtg_l7_pre"), 0) - safe_float(away.get("netrtg_l7_pre"), 0)

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
        ff_edge = (h_ff - a_ff) * 10
        sos_edge = (safe_float(home.get("sos_weighted_margin_l10_pre"), 0) - safe_float(away.get("sos_weighted_margin_l10_pre"), 0)) / 10.0

        # Advanced metrics
        # Defensive efficiency: Home DRTG vs Away ORTG (lower DRTG is better)
        def_eff_edge = (safe_float(away.get("drtg_l7_pre"), 100) - safe_float(home.get("drtg_l7_pre"), 100))
        
        # Offensive efficiency: Home ORTG vs Away ORTG (higher ORTG is better)
        off_eff_edge = (safe_float(home.get("ortg_l7_pre"), 100) - safe_float(away.get("ortg_l7_pre"), 100))
        
        # Tempo advantage: Pace differential scaled by impact
        pace_home = safe_float(home.get("pace_l7_pre"), 70)
        pace_away = safe_float(away.get("pace_l7_pre"), 70)
        tempo_edge = (pace_home - pace_away) * 0.15  # Scale factor for tempo impact
        
        # Three-point rate differential
        three_rate_edge = (safe_float(home.get("3par_l7_pre"), 0.35) - safe_float(away.get("3par_l7_pre"), 0.35)) * 20

        spread_points = (
            weights.get("torvik_adjem", 0) * torv_edge
            + weights.get("recent_netrtg", 0) * recent_edge
            + weights.get("four_factors", 0) * ff_edge
            + weights.get("sos_weighted", 0) * sos_edge
            + weights.get("def_efficiency", 0) * def_eff_edge
            + weights.get("off_efficiency", 0) * off_eff_edge
            + weights.get("tempo_advantage", 0) * tempo_edge
            + weights.get("three_rate", 0) * three_rate_edge
        )

        if params.get("hca_mode") == "dynamic":
            hca = safe_float(home.get("home_margin_lift_l20_pre"), 2.7) - safe_float(away.get("away_margin_penalty_l20_pre"), -2.0)
        else:
            hca = safe_float(params.get("hca_static_value"), 2.7)

        if params.get("pace_adjustment", True):
            pace = (safe_float(home.get("pace_l7_pre"), 70) + safe_float(away.get("pace_l7_pre"), 70)) / 2.0
            predicted_spread = (spread_points * (pace / 100.0)) + hca
        else:
            predicted_spread = spread_points + hca

        return {"predicted_spread": float(predicted_spread)}

    def _get_snapshot_at_date(self, team_name: str, game_date: Any) -> dict[str, Any] | None:
        """Get leak-free team snapshot before the game date."""
        df = self.data_loader.load_feature_store()
        if df.empty or "team" not in df.columns or "game_date" not in df.columns:
            return None
        date_val = pd.to_datetime(game_date, errors="coerce")
        if pd.isna(date_val):
            return None

        team_df = df[(df["team"].astype(str).str.lower() == team_name.lower()) & (df["game_date"] < date_val)].copy()
        if team_df.empty:
            return None
        return team_df.sort_values("game_date", ascending=False).iloc[0].to_dict()

    def _empty_results(self) -> dict[str, Any]:
        """Return empty results structure."""
        return {
            "mae": 0.0,
            "win_pct": 0.0,
            "roi": 0.0,
            "total_games": 0,
            "edge_distribution": {},
            "details": pd.DataFrame(),
        }
