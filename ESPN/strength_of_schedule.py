#!/usr/bin/env python3
"""
strength_of_schedule.py

Strength of Schedule (SOS) Calculator

Computes opponent-adjusted performance metrics:
- sos_avg_opp_netrtg: Average opponent rating
- sos_weighted_margin: Margin weighted by opponent quality
- sos_quality_wins: Wins vs top teams
- sos_bad_losses: Losses to weak teams
- game_quality_score: Importance of each game (0-100)

Key principle: Beating Gonzaga (+15 netrtg) ≠ beating Portland (-10 netrtg)

Integration: Add to espn_boxscore_builder.py PASS 5 (after opponent merge)
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Sequence
import numpy as np
import pandas as pd

EPS = 1e-9


@dataclass(frozen=True)
class SOSConfig:
    """Configuration for SOS analysis."""
    team_id_col: str = "team_id"
    opp_netrtg_col: str = "opp_netrtg_l7_pre"  # Opponent's pregame rating
    margin_col: str = "margin"
    is_tournament_col: str = "is_tournament"
    is_conference_col: str = "conference_game"
    neutral_site_col: str = "neutral_site"

    # Thresholds for quality classification
    elite_opp_threshold: float = 15.0  # Top teams
    good_opp_threshold: float = 5.0
    weak_opp_threshold: float = -10.0  # Cupcakes

    # Lookback windows (updated)
    lookback_l5: int = 5
    lookback_l10: int = 10
    lookback_l15: int = 15

    # Quality win/loss thresholds
    quality_win_margin: float = 0.0  # Win by any margin vs elite
    bad_loss_threshold: float = -10.0  # Opponent netrtg threshold


class StrengthOfSchedule:
    """Computes opponent-adjusted performance metrics."""

    def __init__(self, df_features: pd.DataFrame, config: SOSConfig = SOSConfig()):
        self.df = df_features.copy()
        self.config = config

    def calculate_sos_metrics(
        self,
        team_id: str,
        lookback: Optional[int] = None,
        as_of_date: Optional[str] = None
    ) -> dict:
        """
        Calculate SOS metrics for a single team (leak-free).

        Returns:
            sos_avg_opp_netrtg: Average opponent rating
            sos_weighted_margin: Margin weighted by opponent strength
            sos_quality_wins: Number of wins vs elite opponents
            sos_bad_losses: Number of losses to weak opponents
            sos_toughness_score: 0-100 (higher = tougher schedule)
        """
        cfg = self.config
        df = self.df[self.df[cfg.team_id_col] == team_id].copy()

        # Filter by date (leak-free)
        if as_of_date and "game_date" in df.columns:
            df = df[df["game_date"] < as_of_date]
        if lookback:
            df = df.tail(lookback)

        if len(df) == 0:
            return {
                "sos_avg_opp_netrtg": 0.0,
                "sos_weighted_margin": 0.0,
                "sos_quality_wins": 0,
                "sos_bad_losses": 0,
                "sos_toughness_score": 50.0,
            }

        # Convert to numeric
        for col in [cfg.opp_netrtg_col, cfg.margin_col]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # Average opponent rating
        sos_avg = df[cfg.opp_netrtg_col].mean() if cfg.opp_netrtg_col in df.columns else 0.0

        # Weighted margin (weight by opponent strength)
        if cfg.opp_netrtg_col in df.columns and cfg.margin_col in df.columns:
            # Weight = (opp_rating / 10) + 1.0, so elite (+15) gets 2.5x, weak (-10) gets 0.0x
            weights = (df[cfg.opp_netrtg_col].fillna(0) / 10.0) + 1.0
            weights = weights.clip(lower=0.5, upper=3.0)  # Bound weights
            weighted_margin = (df[cfg.margin_col] * weights).sum() / weights.sum()
        else:
            weighted_margin = df[cfg.margin_col].mean() if cfg.margin_col in df.columns else 0.0

        # Quality wins (vs elite opponents)
        if cfg.opp_netrtg_col in df.columns and cfg.margin_col in df.columns:
            quality_wins = len(df[
                (df[cfg.opp_netrtg_col] >= cfg.elite_opp_threshold) &
                (df[cfg.margin_col] > cfg.quality_win_margin)
            ])
        else:
            quality_wins = 0

        # Bad losses (to weak opponents)
        if cfg.opp_netrtg_col in df.columns and cfg.margin_col in df.columns:
            bad_losses = len(df[
                (df[cfg.opp_netrtg_col] <= cfg.weak_opp_threshold) &
                (df[cfg.margin_col] < 0)
            ])
        else:
            bad_losses = 0

        # Toughness score (0-100)
        base_score = 50.0
        base_score += (sos_avg / 15.0) * 30.0  # Max +30 for elite schedule
        base_score += quality_wins * 5.0       # +5 per quality win
        base_score -= bad_losses * 10.0        # -10 per bad loss
        toughness_score = max(0.0, min(100.0, base_score))

        return {
            "sos_avg_opp_netrtg": float(sos_avg),
            "sos_weighted_margin": float(weighted_margin),
            "sos_quality_wins": int(quality_wins),
            "sos_bad_losses": int(bad_losses),
            "sos_toughness_score": float(toughness_score),
        }

    def calculate_game_quality_score(self, row: dict) -> float:
        """
        Score individual game importance (0-100).

        Factors:
        - Tournament game: +40
        - Conference game: +10
        - Elite opponent: +20
        - Weak opponent: -30
        - Neutral site: +5
        """
        cfg = self.config
        base = 50.0

        # Tournament bonus
        if row.get(cfg.is_tournament_col, False):
            base += 40.0

        # Conference bonus
        if row.get(cfg.is_conference_col, False):
            base += 10.0

        # Opponent quality
        opp_rating = float(row.get(cfg.opp_netrtg_col, 0) or 0)
        if opp_rating >= cfg.elite_opp_threshold:
            base += 20.0
        elif opp_rating >= cfg.good_opp_threshold:
            base += 10.0
        elif opp_rating <= cfg.weak_opp_threshold:
            base -= 30.0

        # Neutral site (higher stakes)
        if row.get(cfg.neutral_site_col, False):
            base += 5.0

        return float(max(10.0, min(100.0, base)))


def add_sos_features(
    df: pd.DataFrame,
    lookback_windows: Sequence[int] = (5, 10, 15),
    config: SOSConfig = SOSConfig()
) -> pd.DataFrame:
    """
    Add SOS features to team-game dataframe (LEAK-FREE).

    Integration into espn_boxscore_builder.py PASS 5:
        from strength_of_schedule import add_sos_features
        df = add_sos_features(df, lookback_windows=[5, 10, 15])

    Creates features for each lookback window:
        - sos_avg_opp_netrtg_l5_pre
        - sos_weighted_margin_l5_pre
        - sos_quality_wins_l5_pre
        - sos_bad_losses_l5_pre
        - sos_toughness_score_l5_pre
      (and same for l10, l15)
    """
    out = df.copy()
    analyzer = StrengthOfSchedule(out, config)

    # Sort chronologically
    if "game_date" in out.columns:
        out = out.sort_values(["game_date", config.team_id_col])

    # Add game quality score (per-game, not rolling)
    out["game_quality_score"] = out.apply(
        lambda row: analyzer.calculate_game_quality_score(row.to_dict()),
        axis=1
    )

    # Add rolling SOS features
    for lookback in lookback_windows:
        suffix = f"_l{lookback}_pre"
        out[f"sos_avg_opp_netrtg{suffix}"] = np.nan
        out[f"sos_weighted_margin{suffix}"] = np.nan
        out[f"sos_quality_wins{suffix}"] = np.nan
        out[f"sos_bad_losses{suffix}"] = np.nan
        out[f"sos_toughness_score{suffix}"] = np.nan

        # Calculate for each row (leak-free)
        for idx in out.index:
            team_id = out.loc[idx, config.team_id_col]
            game_date = out.loc[idx, "game_date"] if "game_date" in out.columns else None

            sos = analyzer.calculate_sos_metrics(team_id, lookback=lookback, as_of_date=game_date)

            out.loc[idx, f"sos_avg_opp_netrtg{suffix}"] = sos["sos_avg_opp_netrtg"]
            out.loc[idx, f"sos_weighted_margin{suffix}"] = sos["sos_weighted_margin"]
            out.loc[idx, f"sos_quality_wins{suffix}"] = sos["sos_quality_wins"]
            out.loc[idx, f"sos_bad_losses{suffix}"] = sos["sos_bad_losses"]
            out.loc[idx, f"sos_toughness_score{suffix}"] = sos["sos_toughness_score"]

    return out


def add_matchup_quality_score(
    df_matchups: pd.DataFrame,
    df_features: pd.DataFrame,
    config: SOSConfig = SOSConfig()
) -> pd.DataFrame:
    """
    Add game quality score to matchup-level dataframe.

    Uses average of home and away team's opponent rating to determine game importance.
    """
    out = df_matchups.copy()

    # If matchup has opp ratings, use them
    if "h_opp_netrtg_l7_pre" in out.columns and "a_opp_netrtg_l7_pre" in out.columns:
        h_rating = pd.to_numeric(out["h_opp_netrtg_l7_pre"], errors="coerce").fillna(0)
        a_rating = pd.to_numeric(out["a_opp_netrtg_l7_pre"], errors="coerce").fillna(0)
        avg_opp_rating = (h_rating + a_rating) / 2
    else:
        avg_opp_rating = 0

    analyzer = StrengthOfSchedule(df_features, config)

    out["matchup_quality_score"] = out.apply(
        lambda row: analyzer.calculate_game_quality_score({
            **row.to_dict(),
            config.opp_netrtg_col: avg_opp_rating.loc[row.name] if hasattr(avg_opp_rating, "loc") else 0,
        }),
        axis=1
    )

    return out


if __name__ == "__main__":
    # Smoke test
    data = {
        "team_id": ["Duke"] * 20,
        "game_date": ["2024-11-01"] * 20,
        "opp_netrtg_l7_pre": [18, 16, 20, 15, 12, 8, 5, 3, 0, -2,
                              -5, -8, -12, -15, -18, -10, -8, 4, 6, 10],
        "margin": [5, 3, -2, 8, 6, 10, 12, 15, 18, 20,
                   22, 25, 28, 30, 32, 18, 15, 12, 8, 5],
        "is_tournament": [False] * 18 + [True] * 2,
        "conference_game": [False] * 10 + [True] * 10,
        "neutral_site": [False] * 15 + [True] * 5,
    }

    df = pd.DataFrame(data)
    analyzer = StrengthOfSchedule(df)

    # Full season SOS
    sos_all = analyzer.calculate_sos_metrics("Duke", lookback=None)
    print("Duke full season SOS:")
    print(f"  Avg opponent rating: {sos_all['sos_avg_opp_netrtg']:+.1f}")
    print(f"  Weighted margin: {sos_all['sos_weighted_margin']:+.1f}")
    print(f"  Quality wins: {sos_all['sos_quality_wins']}")
    print(f"  Bad losses: {sos_all['sos_bad_losses']}")
    print(f"  Toughness score: {sos_all['sos_toughness_score']:.1f}/100")

    # Last 5 / 10 / 15 games SOS
    for n in (5, 10, 15):
        sos_n = analyzer.calculate_sos_metrics("Duke", lookback=n)
        print(f"\nDuke last {n} games SOS:")
        print(f"  Avg opponent rating: {sos_n['sos_avg_opp_netrtg']:+.1f}")
        print(f"  Weighted margin: {sos_n['sos_weighted_margin']:+.1f}")

    # Game quality scores
    tournament_game = analyzer.calculate_game_quality_score({
        "opp_netrtg_l7_pre": 18,
        "is_tournament": True,
        "conference_game": True,
        "neutral_site": True,
    })

    cupcake_game = analyzer.calculate_game_quality_score({
        "opp_netrtg_l7_pre": -15,
        "is_tournament": False,
        "conference_game": False,
        "neutral_site": False,
    })

    print("\nGame quality scores:")
    print(f"  Tournament vs elite: {tournament_game:.0f}/100")
    print(f"  Home vs cupcake: {cupcake_game:.0f}/100")

    print("\n✅ Smoke test complete!")
