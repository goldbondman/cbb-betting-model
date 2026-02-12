#!/usr/bin/env python3
"""
home_away_analyzer.py

Dynamic Home Court Advantage (HCA) Calculator

Replaces static 2.7 point HCA with team-specific metrics:
- home_margin_lift: How much better at home vs neutral
- away_margin_penalty: How much worse on road vs neutral
- home_ortg_lift: Offensive boost at home
- home_drtg_lift: Defensive boost at home
- ha_consistency: How reliable is home/away split (sample size + variance)

Key principle: Duke at Cameron Indoor (+5.2) ≠ Nebraska at home (+1.8)

Integration: Add to espn_boxscore_builder.py PASS 5 (after opponent merge)
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

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

    # Lookback windows
    lookback_l20: int = 20

    # Minimum sample sizes for team-specific splits
    min_neutral_games: int = 3
    min_home_games: int = 5
    min_away_games: int = 5

    # Shrinkage controls (pull team estimates toward league priors when sample sizes are small)
    shrink_k: float = 10.0  # bigger = more pull to prior
    hca_clip: Tuple[float, float] = (-6.0, 8.0)  # sanity bounds on margin lift/penalty


class HomeAwayAnalyzer:
    """Computes team-specific home/away performance metrics with shrinkage priors."""

    def __init__(self, df_features: pd.DataFrame, config: HomeAwayConfig = HomeAwayConfig()):
        self.df = df_features.copy()
        self.config = config
        self._priors = self._compute_global_priors()

    def _compute_global_priors(self) -> Dict[str, float]:
        """
        Compute global priors for home/away effects from the dataset.

        We compute per-team deltas vs neutral baseline when available, then average across teams.
        Falls back to conventional priors if data is insufficient.
        """
        cfg = self.config
        df = self.df.copy()

        priors: Dict[str, float] = {
            "prior_home_margin_lift": 2.7,
            "prior_away_margin_penalty": -2.0,
            "prior_home_ortg_lift": 2.0,
            "prior_home_drtg_lift": -0.7,
        }

        # Required columns
        if cfg.team_id_col not in df.columns or cfg.margin_col not in df.columns:
            return priors

        # Coerce numeric
        for col in [cfg.neutral_site_col, cfg.margin_col, cfg.ortg_col, cfg.drtg_col]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # Clean location flags
        if cfg.home_away_col in df.columns:
            ha = df[cfg.home_away_col].astype(str).str.lower().str.strip()
        else:
            ha = pd.Series("", index=df.index)

        neutral = (df.get(cfg.neutral_site_col, 0).fillna(0) == 1) | ha.isin(["neutral", "n"])
        home = ha.eq("home")
        away = ha.eq("away")

        # Global neutral means (fallback)
        global_neutral_margin = df.loc[neutral, cfg.margin_col].mean()
        global_neutral_ortg = df.loc[neutral, cfg.ortg_col].mean() if cfg.ortg_col in df.columns else np.nan
        global_neutral_drtg = df.loc[neutral, cfg.drtg_col].mean() if cfg.drtg_col in df.columns else np.nan

        lifts: list[float] = []
        away_pen: list[float] = []
        ortg_lifts: list[float] = []
        drtg_lifts: list[float] = []

        for _, g in df.groupby(cfg.team_id_col):
            g_neu = g[neutral.loc[g.index]]
            if len(g_neu) >= cfg.min_neutral_games:
                neu_m = g_neu[cfg.margin_col].mean()
                neu_o = g_neu[cfg.ortg_col].mean() if cfg.ortg_col in g_neu.columns else global_neutral_ortg
                neu_d = g_neu[cfg.drtg_col].mean() if cfg.drtg_col in g_neu.columns else global_neutral_drtg
            else:
                neu_m = global_neutral_margin
                neu_o = global_neutral_ortg
                neu_d = global_neutral_drtg

            g_home = g[home.loc[g.index]]
            g_away = g[away.loc[g.index]]

            if len(g_home) >= cfg.min_home_games and pd.notna(neu_m):
                lifts.append(float(g_home[cfg.margin_col].mean() - neu_m))
                if cfg.ortg_col in g_home.columns and pd.notna(neu_o):
                    ortg_lifts.append(float(g_home[cfg.ortg_col].mean() - neu_o))
                if cfg.drtg_col in g_home.columns and pd.notna(neu_d):
                    drtg_lifts.append(float(g_home[cfg.drtg_col].mean() - neu_d))

            if len(g_away) >= cfg.min_away_games and pd.notna(neu_m):
                away_pen.append(float(g_away[cfg.margin_col].mean() - neu_m))

        # Only overwrite defaults if we have a healthy sample of teams
        if len(lifts) > 20:
            priors["prior_home_margin_lift"] = float(np.nanmean(lifts))
        if len(away_pen) > 20:
            priors["prior_away_margin_penalty"] = float(np.nanmean(away_pen))
        if len(ortg_lifts) > 20:
            priors["prior_home_ortg_lift"] = float(np.nanmean(ortg_lifts))
        if len(drtg_lifts) > 20:
            priors["prior_home_drtg_lift"] = float(np.nanmean(drtg_lifts))

        return priors

    def calculate_team_hca(
        self,
        team_id: str,
        lookback: Optional[int] = None,
        as_of_date: Optional[str] = None,
    ) -> Dict[str, float]:
        """Calculate home/away metrics for a single team (leak-free)."""
        cfg = self.config
        df = self.df[self.df[cfg.team_id_col] == team_id].copy()

        # Leak-free date filter
        if as_of_date and "game_date" in df.columns:
            df = df[df["game_date"] < as_of_date]
        if lookback:
            df = df.tail(lookback)

        if len(df) == 0:
            # Return priors when no data
            return {
                "home_margin_lift": float(self._priors["prior_home_margin_lift"]),
                "away_margin_penalty": float(self._priors["prior_away_margin_penalty"]),
                "home_ortg_lift": float(self._priors["prior_home_ortg_lift"]),
                "home_drtg_lift": float(self._priors["prior_home_drtg_lift"]),
                "ha_consistency": 0.25,
            }

        # Convert numeric fields (do NOT coerce home_away to numeric)
        for col in [cfg.neutral_site_col, cfg.margin_col, cfg.ortg_col, cfg.drtg_col]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # Clean location flags
        if cfg.home_away_col in df.columns:
            ha = df[cfg.home_away_col].astype(str).str.lower().str.strip()
        else:
            ha = pd.Series("", index=df.index)

        neutral_mask = (df.get(cfg.neutral_site_col, 0).fillna(0) == 1) | ha.isin(["neutral", "n"])
        home_mask = ha.eq("home")
        away_mask = ha.eq("away")

        neutral_games = df[neutral_mask]
        home_games = df[home_mask]
        away_games = df[away_mask]

        # Baseline (neutral): if not enough true neutral games, fallback to overall mean
        if len(neutral_games) >= cfg.min_neutral_games:
            neutral_margin = neutral_games[cfg.margin_col].mean()
            neutral_ortg = neutral_games[cfg.ortg_col].mean() if cfg.ortg_col in df.columns else df[cfg.ortg_col].mean()
            neutral_drtg = neutral_games[cfg.drtg_col].mean() if cfg.drtg_col in df.columns else df[cfg.drtg_col].mean()
        else:
            neutral_margin = df[cfg.margin_col].mean()
            neutral_ortg = df[cfg.ortg_col].mean() if cfg.ortg_col in df.columns else 0.0
            neutral_drtg = df[cfg.drtg_col].mean() if cfg.drtg_col in df.columns else 0.0

        # Team estimates (if enough samples)
        team_home_lift = np.nan
        team_home_ortg_lift = np.nan
        team_home_drtg_lift = np.nan
        team_away_pen = np.nan

        if len(home_games) >= cfg.min_home_games:
            team_home_lift = home_games[cfg.margin_col].mean() - neutral_margin
            if cfg.ortg_col in home_games.columns:
                team_home_ortg_lift = home_games[cfg.ortg_col].mean() - neutral_ortg
            if cfg.drtg_col in home_games.columns:
                team_home_drtg_lift = home_games[cfg.drtg_col].mean() - neutral_drtg

        if len(away_games) >= cfg.min_away_games:
            team_away_pen = away_games[cfg.margin_col].mean() - neutral_margin

        # Shrink toward global priors (avoid hardcoded, asymmetric defaults)
        k = float(cfg.shrink_k)
        prior_home = float(self._priors.get("prior_home_margin_lift", 2.7))
        prior_away = float(self._priors.get("prior_away_margin_penalty", -2.0))
        prior_ho = float(self._priors.get("prior_home_ortg_lift", 2.0))
        prior_hd = float(self._priors.get("prior_home_drtg_lift", -0.7))

        w_home = len(home_games) / (len(home_games) + k) if len(home_games) > 0 else 0.0
        w_away = len(away_games) / (len(away_games) + k) if len(away_games) > 0 else 0.0

        home_margin_lift = (w_home * team_home_lift + (1.0 - w_home) * prior_home) if pd.notna(team_home_lift) else prior_home
        away_margin_penalty = (w_away * team_away_pen + (1.0 - w_away) * prior_away) if pd.notna(team_away_pen) else prior_away

        home_ortg_lift = (w_home * team_home_ortg_lift + (1.0 - w_home) * prior_ho) if pd.notna(team_home_ortg_lift) else prior_ho
        home_drtg_lift = (w_home * team_home_drtg_lift + (1.0 - w_home) * prior_hd) if pd.notna(team_home_drtg_lift) else prior_hd

        # Sanity clips on margin effects
        lo, hi = cfg.hca_clip
        home_margin_lift = float(np.clip(home_margin_lift, lo, hi))
        away_margin_penalty = float(np.clip(away_margin_penalty, lo, hi))

        # Consistency as reliability of split estimate (sample size + variance)
        n_eff = min(len(home_games), len(away_games))
        if n_eff >= 3:
            v = np.nanstd(pd.concat([home_games[cfg.margin_col], away_games[cfg.margin_col]]))
            ha_consistency = float(np.clip((n_eff / (n_eff + 5.0)) * (1.0 / (1.0 + (v / 12.0))), 0.0, 1.0))
        else:
            ha_consistency = 0.4

        return {
            "home_margin_lift": float(home_margin_lift),
            "away_margin_penalty": float(away_margin_penalty),
            "home_ortg_lift": float(home_ortg_lift),
            "home_drtg_lift": float(home_drtg_lift),
            "ha_consistency": float(ha_consistency),
        }

    def get_matchup_hca(self, home_team_id: str, away_team_id: str, lookback: int = 20) -> float:
        """Net HCA = home_team['lift'] - away_team['penalty'] (penalty is typically negative)."""
        home_stats = self.calculate_team_hca(home_team_id, lookback=lookback)
        away_stats = self.calculate_team_hca(away_team_id, lookback=lookback)
        return float(home_stats["home_margin_lift"] - away_stats["away_margin_penalty"])


def add_home_away_features(
    df: pd.DataFrame,
    lookback_windows: Sequence[int] = (5, 10, 15),
    config: HomeAwayConfig = HomeAwayConfig(),
) -> pd.DataFrame:
    """
    Add HCA features to team-game dataframe (LEAK-FREE).

    Integration into espn_boxscore_builder.py PASS 5:
        from home_away_analyzer import add_home_away_features
        df = add_home_away_features(df, lookback_windows=[5, 10, 15])
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


def add_matchup_net_hca(
    df_matchups: pd.DataFrame,
    df_features: pd.DataFrame,
    config: HomeAwayConfig = HomeAwayConfig(),
    lookback: int = 20,
) -> pd.DataFrame:
    """Add net HCA to matchup-level dataframe."""
    analyzer = HomeAwayAnalyzer(df_features, config)
    out = df_matchups.copy()
    out["matchup_net_hca"] = np.nan

    for idx in out.index:
        h = out.loc[idx, "team_id_home"] if "team_id_home" in out.columns else out.loc[idx, "h_team_id"]
        a = out.loc[idx, "team_id_away"] if "team_id_away" in out.columns else out.loc[idx, "a_team_id"]
        if pd.notna(h) and pd.notna(a):
            out.loc[idx, "matchup_net_hca"] = analyzer.get_matchup_hca(str(h), str(a), lookback)

    return out


if __name__ == "__main__":
    # Smoke test
    data = {
        "team_id": ["Duke"] * 30 + ["UNC"] * 30,
        "game_date": ["2024-11-01"] * 60,
        "home_away": (["home"] * 10 + ["away"] * 10 + ["neutral"] * 10) * 2,
        "neutral_site": ([0] * 20 + [1] * 10) * 2,
        "margin": (
            [8, 7, 9, 8, 10, 7, 8, 9, 8, 7] +
            [2, 3, 1, 2, 3, 2, 1, 2, 3, 2] +
            [5, 4, 6, 5, 5, 4, 6, 5, 5, 4] +
            [6, 7, 5, 6, 7, 6, 5, 6, 7, 6] +
            [-1, 0, -2, -1, 0, -1, -2, -1, 0, -1] +
            [4, 3, 5, 4, 4, 3, 5, 4, 4, 3]
        ),
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
