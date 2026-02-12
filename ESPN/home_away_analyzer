#!/usr/bin/env python3
"""
home_away_analyzer.py

Dynamic Home Court Advantage (HCA) Calculator

Replaces static 2.7 point HCA with team-specific metrics:
- home_margin_lift: How much better at home vs neutral
- away_margin_penalty: How much worse on road vs neutral  
- home_ortg_lift: Offensive boost at home
- home_drtg_lift: Defensive boost at home
- ha_consistency: How consistent is home/away split

Key principle: Duke at Cameron Indoor (+5.2) ≠ Nebraska at home (+1.8)

Integration: Add to espn_boxscore_builder.py PASS 5 (after opponent merge)
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Optional, Sequence
import numpy as np
import pandas as pd

EPS = 1e-9


@dataclass(frozen=True)
class HomeAwayConfig:
    """Configuration for home/away analysis."""
    team_id_col: str = "team_id"
    home_away_col: str = "home_away"
    neutral_site_col: str = "neutral_site"
    margin_col: str = "margin"
    ortg_col: str = "ortg"
    drtg_col: str = "drtg"
    lookback_l20: int = 20
    min_neutral_games: int = 3
    min_home_games: int = 5
    min_away_games: int = 5


class HomeAwayAnalyzer:
    """Computes team-specific home/away performance metrics."""
    
    def __init__(self, df_features: pd.DataFrame, config: HomeAwayConfig = HomeAwayConfig()):
        self.df = df_features.copy()
        self.config = config
    
    def calculate_team_hca(self, team_id: str, lookback: Optional[int] = None,
                          as_of_date: Optional[str] = None) -> Dict[str, float]:
        """Calculate home/away metrics for a single team (leak-free)."""
        cfg = self.config
        df = self.df[self.df[cfg.team_id_col] == team_id].copy()
        
        if as_of_date and "game_date" in df.columns:
            df = df[df["game_date"] < as_of_date]
        if lookback:
            df = df.tail(lookback)
        
        # Convert to numeric
        for col in [cfg.neutral_site_col, cfg.margin_col, cfg.ortg_col, cfg.drtg_col]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        
        # Split by location
        neutral_mask = (df[cfg.neutral_site_col].fillna(0) == 1) | (df[cfg.home_away_col].isna())
        home_mask = df[cfg.home_away_col].astype(str).str.lower() == "home"
        away_mask = df[cfg.home_away_col].astype(str).str.lower() == "away"
        
        neutral_games = df[neutral_mask]
        home_games = df[home_mask]
        away_games = df[away_mask]
        
        # Baseline (neutral)
        if len(neutral_games) >= cfg.min_neutral_games:
            neutral_margin = neutral_games[cfg.margin_col].mean()
            neutral_ortg = neutral_games[cfg.ortg_col].mean()
            neutral_drtg = neutral_games[cfg.drtg_col].mean()
        else:
            neutral_margin = df[cfg.margin_col].mean()
            neutral_ortg = df[cfg.ortg_col].mean()
            neutral_drtg = df[cfg.drtg_col].mean()
        
        # Home lift
        if len(home_games) >= cfg.min_home_games:
            home_margin_lift = home_games[cfg.margin_col].mean() - neutral_margin
            home_ortg_lift = home_games[cfg.ortg_col].mean() - neutral_ortg
            home_drtg_lift = home_games[cfg.drtg_col].mean() - neutral_drtg
        else:
            home_margin_lift, home_ortg_lift, home_drtg_lift = 2.7, 2.0, -0.7
        
        # Away penalty
        if len(away_games) >= cfg.min_away_games:
            away_margin_penalty = away_games[cfg.margin_col].mean() - neutral_margin
        else:
            away_margin_penalty = -2.0
        
        # Consistency
        if len(home_games) > 0 and len(away_games) > 0:
            std_combined = (home_games[cfg.margin_col].std() + away_games[cfg.margin_col].std()) / 2
            mean_margin = abs(df[cfg.margin_col].mean())
            ha_consistency = max(0.0, min(1.0, 1.0 - std_combined / (mean_margin + EPS)))
        else:
            ha_consistency = 0.5
        
        return {
            "home_margin_lift": float(home_margin_lift),
            "away_margin_penalty": float(away_margin_penalty),
            "home_ortg_lift": float(home_ortg_lift),
            "home_drtg_lift": float(home_drtg_lift),
            "ha_consistency": float(ha_consistency),
        }
    
    def get_matchup_hca(self, home_team_id: str, away_team_id: str, lookback: int = 20) -> float:
        """Net HCA = home_team['lift'] - away_team['penalty']"""
        home_stats = self.calculate_team_hca(home_team_id, lookback=lookback)
        away_stats = self.calculate_team_hca(away_team_id, lookback=lookback)
        return float(home_stats["home_margin_lift"] - away_stats["away_margin_penalty"])


def add_home_away_features(df: pd.DataFrame, lookback_windows: Sequence[int] = (10, 20),
                           config: HomeAwayConfig = HomeAwayConfig()) -> pd.DataFrame:
    """
    Add HCA features to team-game dataframe (LEAK-FREE).
    
    Integration into espn_boxscore_builder.py PASS 5:
        from home_away_analyzer import add_home_away_features
        df = add_home_away_features(df, lookback_windows=[10, 20])
    """
    out = df.copy()
    analyzer = HomeAwayAnalyzer(out, config)
    
    # Sort chronologically
    if "game_date" in out.columns:
        out = out.sort_values(["game_date", config.team_id_col])
    
    for lookback in lookback_windows:
        suffix = f"_l{lookback}_pre"
        out[f"home_margin_lift{suffix}"] = np.nan
        out[f"away_margin_penalty{suffix}"] = np.nan
        out[f"home_ortg_lift{suffix}"] = np.nan
        out[f"home_drtg_lift{suffix}"] = np.nan
        out[f"ha_consistency{suffix}"] = np.nan
        
        # Calculate for each row (leak-free: only uses prior games)
        for idx in out.index:
            team_id = out.loc[idx, config.team_id_col]
            game_date = out.loc[idx, "game_date"] if "game_date" in out.columns else None
            
            hca = analyzer.calculate_team_hca(team_id, lookback=lookback, as_of_date=game_date)
            
            out.loc[idx, f"home_margin_lift{suffix}"] = hca["home_margin_lift"]
            out.loc[idx, f"away_margin_penalty{suffix}"] = hca["away_margin_penalty"]
            out.loc[idx, f"home_ortg_lift{suffix}"] = hca["home_ortg_lift"]
            out.loc[idx, f"home_drtg_lift{suffix}"] = hca["home_drtg_lift"]
            out.loc[idx, f"ha_consistency{suffix}"] = hca["ha_consistency"]
    
    return out


def add_matchup_net_hca(df_matchups: pd.DataFrame, df_features: pd.DataFrame,
                       config: HomeAwayConfig = HomeAwayConfig(), lookback: int = 20) -> pd.DataFrame:
    """Add net HCA to matchup-level dataframe."""
    analyzer = HomeAwayAnalyzer(df_features, config)
    out = df_matchups.copy()
    out["matchup_net_hca"] = np.nan
    
    for idx in out.index:
        h = out.loc[idx, "team_id_home"] if "team_id_home" in out.columns else out.loc[idx, "h_team_id"]
        a = out.loc[idx, "team_id_away"] if "team_id_away" in out.columns else out.loc[idx, "a_team_id"]
        if pd.notna(h) and pd.notna(a):
            out.loc[idx, "matchup_net_hca"] = analyzer.get_matchup_hca(h, a, lookback)
    
    return out


if __name__ == "__main__":
    # Smoke test
    data = {
        "team_id": ["Duke"] * 30 + ["UNC"] * 30,
        "game_date": ["2024-11-01"] * 60,
        "home_away": (["home"] * 10 + ["away"] * 10 + [""] * 10) * 2,
        "neutral_site": ([0] * 20 + [1] * 10) * 2,
        "margin": ([8,7,9,8,10,7,8,9,8,7] + [2,3,1,2,3,2,1,2,3,2] + [5,4,6,5,5,4,6,5,5,4] +
                  [6,7,5,6,7,6,5,6,7,6] + [-1,0,-2,-1,0,-1,-2,-1,0,-1] + [4,3,5,4,4,3,5,4,4,3]),
        "ortg": [105] * 60,
        "drtg": [95] * 60,
    }
    
    df = pd.DataFrame(data)
    analyzer = HomeAwayAnalyzer(df)
    
    duke = analyzer.calculate_team_hca("Duke")
    unc = analyzer.calculate_team_hca("UNC")
    
    print(f"Duke home lift: {duke['home_margin_lift']:+.1f}, away penalty: {duke['away_margin_penalty']:+.1f}")
    print(f"UNC home lift: {unc['home_margin_lift']:+.1f}, away penalty: {unc['away_margin_penalty']:+.1f}")
    print(f"Duke @ home vs UNC: net HCA = {analyzer.get_matchup_hca('Duke', 'UNC'):+.1f}")
    print("✅ Smoke test complete!")
