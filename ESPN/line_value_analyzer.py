"""
Line Value Analyzer
Compare model predictions to market lines to identify value bets.
"""

from typing import Dict, List, Optional, Tuple
from datetime import datetime

import pandas as pd
import numpy as np

from espn_config import OUT_GAMES, OUT_MATCHUPS
from data_utils import _normalize_id_series


class LineValueAnalyzer:
    """
    Betting analytics: find edges vs market.
    
    Compares model predictions to actual betting lines to identify
    games where the model has an opinion that differs from the market.
    """
    
    def __init__(self, edge_threshold: float = 0.03, confidence_min: float = 0.6):
        """
        Initialize analyzer.
        
        Args:
            edge_threshold: Minimum edge to flag as value (default 3%)
            confidence_min: Minimum model confidence to consider (default 0.6)
        """
        self.edge_threshold = edge_threshold
        self.confidence_min = confidence_min
    
    def find_value_bets(
        self,
        predictions: pd.DataFrame,
        min_edge: Optional[float] = None,
        min_confidence: Optional[float] = None
    ) -> pd.DataFrame:
        """
        Find games where model disagrees with market.
        
        Args:
            predictions: DataFrame with columns: event_id, home_win_prob, predicted_spread
            min_edge: Override default edge threshold
            min_confidence: Override default confidence threshold
            
        Returns:
            DataFrame with value bet opportunities
        """
        edge_thresh = min_edge if min_edge is not None else self.edge_threshold
        conf_thresh = min_confidence if min_confidence is not None else self.confidence_min
        
        # Load games with market lines
        if not os.path.exists(OUT_GAMES):
            raise FileNotFoundError(f"Games file not found: {OUT_GAMES}")
        
        df_games = pd.read_csv(OUT_GAMES)
        df_games["game_id"] = _normalize_id_series(df_games["game_id"])
        
        # Merge predictions with market lines
        df = predictions.merge(
            df_games[["game_id", "market_spread", "market_total", "market_home_ml", "market_away_ml"]],
            left_on="event_id",
            right_on="game_id",
            how="inner"
        )
        
        # Calculate edges
        df = self._calculate_spread_edge(df)
        df = self._calculate_total_edge(df)
        df = self._calculate_moneyline_edge(df)
        
        # Filter to valuable opportunities
        mask = (
            (df["confidence"].fillna(0) >= conf_thresh) &
            (
                (df["spread_edge"].abs() >= edge_thresh) |
                (df["total_edge"].abs() >= edge_thresh) |
                (df["ml_edge"].abs() >= edge_thresh)
            )
        )
        
        value_bets = df[mask].copy()
        
        if value_bets.empty:
            print(f"No value bets found (edge >= {edge_thresh:.1%}, confidence >= {conf_thresh:.1%})")
            return value_bets
        
        # Sort by biggest edge
        value_bets["max_edge"] = value_bets[["spread_edge", "total_edge", "ml_edge"]].abs().max(axis=1)
        value_bets = value_bets.sort_values("max_edge", ascending=False)
        
        return value_bets
    
    def _calculate_spread_edge(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate spread betting edge.
        
        Edge = difference between predicted spread and market spread.
        Positive edge = bet home (market undervalues home team).
        Negative edge = bet away (market overvalues home team).
        """
        df = df.copy()
        
        predicted_spread = df.get("predicted_spread", np.nan)
        market_spread = df.get("market_spread", np.nan)
        
        # Edge: how much better is our line than market?
        # If we predict -3 and market is -6, edge = +3 (bet home, they're undervalued)
        df["spread_edge"] = market_spread - predicted_spread
        
        # Recommendation
        df["spread_rec"] = np.where(
            df["spread_edge"] > 0,
            "BET_HOME",
            np.where(df["spread_edge"] < 0, "BET_AWAY", "PASS")
        )
        
        return df
    
    def _calculate_total_edge(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate totals (over/under) edge.
        
        Requires predicted total points (sum of predicted team scores).
        """
        df = df.copy()
        
        # We need to estimate predicted total
        # Simple approach: use pace * avg efficiency
        # Better approach: sum predicted team scores (if available)
        
        if "predicted_total" in df.columns:
            predicted_total = df["predicted_total"]
        else:
            # Estimate from pace and average ORtg
            # This is rough - better to use team-specific predictions
            predicted_total = np.nan
        
        market_total = df.get("market_total", np.nan)
        
        # Edge: difference between predicted and market total
        df["total_edge"] = predicted_total - market_total
        
        # Recommendation
        df["total_rec"] = np.where(
            df["total_edge"] > 0,
            "BET_OVER",
            np.where(df["total_edge"] < 0, "BET_UNDER", "PASS")
        )
        
        return df
    
    def _calculate_moneyline_edge(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate moneyline betting edge.
        
        Convert win probability to fair odds, compare to market odds.
        """
        df = df.copy()
        
        home_win_prob = df.get("home_win_prob", 0.5)
        market_home_ml = df.get("market_home_ml", np.nan)
        market_away_ml = df.get("market_away_ml", np.nan)
        
        # Convert probability to fair odds (American format)
        fair_home_ml = self._prob_to_american_odds(home_win_prob)
        fair_away_ml = self._prob_to_american_odds(1 - home_win_prob)
        
        # Market implied probability (with vig removed)
        market_home_prob = self._american_odds_to_prob(market_home_ml)
        market_away_prob = self._american_odds_to_prob(market_away_ml)
        
        # Edge = our probability - market probability
        df["ml_edge"] = home_win_prob - market_home_prob
        
        # Expected value (EV)
        # EV = (win_prob * profit) - (lose_prob * stake)
        home_ev = self._calculate_ev(home_win_prob, market_home_ml, stake=100)
        away_ev = self._calculate_ev(1 - home_win_prob, market_away_ml, stake=100)
        
        df["home_ml_ev"] = home_ev
        df["away_ml_ev"] = away_ev
        
        # Recommendation (bet side with positive EV)
        df["ml_rec"] = np.where(
            home_ev > away_ev,
            "BET_HOME_ML",
            np.where(away_ev > 0, "BET_AWAY_ML", "PASS")
        )
        
        return df
    
    def _prob_to_american_odds(self, prob: float) -> float:
        """
        Convert probability to American odds.
        
        Examples:
        - 0.50 → +100 (even money)
        - 0.60 → -150 (favorite)
        - 0.40 → +150 (underdog)
        """
        if pd.isna(prob) or prob <= 0 or prob >= 1:
            return np.nan
        
        if prob >= 0.5:
            # Favorite
            return -100 * prob / (1 - prob)
        else:
            # Underdog
            return 100 * (1 - prob) / prob
    
    def _american_odds_to_prob(self, odds: float) -> float:
        """
        Convert American odds to implied probability.
        
        Examples:
        - +100 → 0.50
        - -150 → 0.60
        - +150 → 0.40
        """
        if pd.isna(odds):
            return np.nan
        
        if odds > 0:
            # Underdog
            return 100 / (odds + 100)
        else:
            # Favorite
            return -odds / (-odds + 100)
    
    def _calculate_ev(self, win_prob: float, odds: float, stake: float = 100) -> float:
        """
        Calculate expected value of a bet.
        
        EV = (win_prob * profit) - (lose_prob * stake)
        
        Returns:
            Expected value in units of stake
        """
        if pd.isna(odds) or pd.isna(win_prob):
            return np.nan
        
        lose_prob = 1 - win_prob
        
        if odds > 0:
            # Underdog: profit = stake * (odds / 100)
            profit = stake * (odds / 100)
        else:
            # Favorite: profit = stake / (-odds / 100)
            profit = stake / (-odds / 100)
        
        ev = (win_prob * profit) - (lose_prob * stake)
        return ev
    
    def track_line_movement(
        self,
        event_id: str,
        history: List[Dict[str, any]]
    ) -> pd.DataFrame:
        """
        Track how betting lines move over time for a game.
        
        Args:
            event_id: Game identifier
            history: List of dicts with keys: timestamp, market_spread, market_total, etc.
            
        Returns:
            DataFrame with line movement analysis
        """
        if not history:
            return pd.DataFrame()
        
        df = pd.DataFrame(history)
        df["event_id"] = event_id
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp")
        
        # Calculate movement (change from opening line)
        if "market_spread" in df.columns:
            df["spread_movement"] = df["market_spread"] - df["market_spread"].iloc[0]
        
        if "market_total" in df.columns:
            df["total_movement"] = df["market_total"] - df["market_total"].iloc[0]
        
        return df
    
    def calculate_roi(
        self,
        predictions: pd.DataFrame,
        actual_results: pd.DataFrame,
        bet_size: float = 100,
        bet_type: str = "spread"
    ) -> Dict[str, float]:
        """
        Calculate return on investment for betting strategy.
        
        Args:
            predictions: DataFrame with model predictions
            actual_results: DataFrame with game outcomes
            bet_size: Dollar amount per bet
            bet_type: "spread", "total", or "moneyline"
            
        Returns:
            Dictionary with ROI metrics
        """
        # Merge predictions with results
        df = predictions.merge(
            actual_results[["event_id", "home_win", "home_points", "away_points", "total_points"]],
            on="event_id",
            how="inner"
        )
        
        if df.empty:
            return {"error": "No matching games found"}
        
        # Calculate outcomes based on bet type
        if bet_type == "spread":
            # Did recommended bet win?
            df["bet_won"] = (
                ((df["spread_rec"] == "BET_HOME") & (df["home_win"] == 1)) |
                ((df["spread_rec"] == "BET_AWAY") & (df["home_win"] == 0))
            )
        elif bet_type == "total":
            df["bet_won"] = (
                ((df["total_rec"] == "BET_OVER") & (df["total_points"] > df["market_total"])) |
                ((df["total_rec"] == "BET_UNDER") & (df["total_points"] < df["market_total"]))
            )
        elif bet_type == "moneyline":
            df["bet_won"] = (
                ((df["ml_rec"] == "BET_HOME_ML") & (df["home_win"] == 1)) |
                ((df["ml_rec"] == "BET_AWAY_ML") & (df["home_win"] == 0))
            )
        
        # Only count games where we made a bet
        df = df[df.get(f"{bet_type}_rec", "PASS") != "PASS"].copy()
        
        n_bets = len(df)
        if n_bets == 0:
            return {"error": "No bets made"}
        
        # Calculate P&L (assume -110 odds for spread/totals)
        wins = df["bet_won"].sum()
        losses = n_bets - wins
        
        # Standard bet: risk $110 to win $100
        profit = (wins * 100) - (losses * 110)
        total_risked = n_bets * 110
        
        roi = profit / total_risked if total_risked > 0 else 0
        win_rate = wins / n_bets if n_bets > 0 else 0
        
        return {
            "n_bets": n_bets,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "profit": profit,
            "total_risked": total_risked,
            "roi": roi,
            "bet_type": bet_type,
        }
    
    def format_value_report(self, value_bets: pd.DataFrame) -> str:
        """
        Format value bets as human-readable report.
        
        Args:
            value_bets: Output from find_value_bets()
            
        Returns:
            Formatted string report
        """
        if value_bets.empty:
            return "No value bets found."
        
        lines = ["=" * 80, "VALUE BETTING OPPORTUNITIES", "=" * 80, ""]
        
        for _, row in value_bets.iterrows():
            lines.append(f"Game: {row.get('h_team', 'Unknown')} vs {row.get('a_team', 'Unknown')}")
            lines.append(f"Time: {row.get('game_datetime_utc', 'Unknown')}")
            lines.append(f"Confidence: {row.get('confidence', 0):.1%}")
            lines.append("")
            
            # Spread recommendation
            if abs(row.get("spread_edge", 0)) >= self.edge_threshold:
                lines.append(f"  SPREAD: {row['spread_rec']}")
                lines.append(f"    Market: {row.get('market_spread', 'N/A')}")
                lines.append(f"    Model:  {row.get('predicted_spread', 'N/A'):.1f}")
                lines.append(f"    Edge:   {row.get('spread_edge', 0):.1f} points")
                lines.append("")
            
            # Total recommendation
            if abs(row.get("total_edge", 0)) >= self.edge_threshold:
                lines.append(f"  TOTAL: {row['total_rec']}")
                lines.append(f"    Market: {row.get('market_total', 'N/A')}")
                lines.append(f"    Model:  {row.get('predicted_total', 'N/A'):.1f}")
                lines.append(f"    Edge:   {row.get('total_edge', 0):.1f} points")
                lines.append("")
            
            # Moneyline recommendation
            if abs(row.get("ml_edge", 0)) >= self.edge_threshold:
                lines.append(f"  MONEYLINE: {row['ml_rec']}")
                lines.append(f"    Home ML: {row.get('market_home_ml', 'N/A')}")
                lines.append(f"    Away ML: {row.get('market_away_ml', 'N/A')}")
                lines.append(f"    Edge:    {row.get('ml_edge', 0):.1%}")
                lines.append(f"    Home EV: ${row.get('home_ml_ev', 0):.2f}")
                lines.append(f"    Away EV: ${row.get('away_ml_ev', 0):.2f}")
                lines.append("")
            
            lines.append("-" * 80)
            lines.append("")
        
        return "\n".join(lines)
