#!/usr/bin/env python3
"""
College Basketball Prediction Model - Recursive Bidirectional Analysis
Built by: 20-year gambling quant veteran

ENHANCED PHILOSOPHY:
- When UNC beats Virginia by 8 with +5 ORB, we need context
- What does Virginia typically allow in ORB? (+2 or +8?)
- UNC's +5 ORB is only impressive if Virginia normally allows +2
- This requires analyzing opponent's opponent's games (recursive)

Example Flow:
1. UNC's L5: vs Virginia, vs Florida St, vs Duke, vs NC State, vs Wake
2. For Virginia game: Get Virginia's L5/L10 pre-dating UNC matchup
3. Compare UNC's stats vs Virginia's typical allowances
4. UNC had 12 ORB vs Virginia's avg of 8 allowed = +4 vs expectation
5. Repeat for all 5 games
6. Aggregate UNC's "vs expectation" performance

This is where the market gets beaten.

Integration:
- Data sourced from ESPN/CSV/espn_team_game_logs.csv via DataLoader
- Exposes RecursivePredictionEngine.predict_spread(home_team, away_team)
  returning the same dict shape as PredictionEngine.predict_spread()
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class GameData:
    """
    Single game data structure.

    CRITICAL: Must include opponent's full game history for context.
    """
    game_id: str
    date: datetime
    team_name: str
    opponent_name: str
    team_score: int
    opponent_score: int
    neutral_site: bool

    # Team box score
    team_box: Dict[str, float]  # {fgm, fga, tpm, tpa, ftm, fta, orb, drb, tov}

    # Opponent box score
    opponent_box: Dict[str, float]

    # Opponent's recent games (for baseline establishment)
    opponent_history: List["GameData"] = field(default_factory=list)


@dataclass
class ModelConfig:
    """Model parameters."""

    # Lookback windows
    l5_window: int = 5
    l10_window: int = 10

    # Weighting
    l5_weight: float = 0.70
    l10_weight: float = 0.30

    # Component weights (sum to 1.0)
    efg_weight: float = 0.28
    tov_weight: float = 0.22
    orb_weight: float = 0.18
    ftr_weight: float = 0.16
    drb_weight: float = 0.16

    # Performance vs expectation weight
    # 70% vs expectation, 30% raw stats
    vs_exp_weight: float = 0.70
    raw_weight: float = 0.30

    # SOS adjustment
    sos_strength: float = 0.35

    # Baseline
    avg_pace: float = 70.0
    default_hca: float = 3.2


# ============================================================================
# CORE CALCULATIONS
# ============================================================================

def estimate_possessions(fga: float, fta: float, orb: float, tov: float, opp_orb: float) -> float:
    """Dean Oliver's possessions formula."""
    return fga + (0.475 * fta) - orb + tov + (opp_orb * 0.33)


def calculate_four_factors(box: Dict, poss: float, opp_drb: float, opp_orb: float) -> Dict[str, float]:
    """Calculate Four Factors from box score."""
    def safe_div(num: float, den: float, default: float = 0.0) -> float:
        return num / den if den > 0 else default

    efg = safe_div(box['fgm'] + 0.5 * box['tpm'], box['fga'], 0.5)
    tov_pct = safe_div(box['tov'], poss, 0.15) * 100
    orb_pct = safe_div(box['orb'], box['orb'] + opp_drb, 0.30)
    drb_pct = safe_div(box['drb'], box['drb'] + opp_orb, 0.70)
    ftr = safe_div(box['fta'], box['fga'], 0.30)
    ft_pct = safe_div(box['ftm'], box['fta'], 0.70)

    return {
        'efg': efg,
        'tov_pct': tov_pct,
        'orb_pct': orb_pct,
        'drb_pct': drb_pct,
        'ftr': ftr,
        'ft_pct': ft_pct,
    }


# ============================================================================
# RECURSIVE OPPONENT BASELINE ANALYZER
# ============================================================================

class OpponentBaselineAnalyzer:
    """
    Analyzes opponent's typical performance to establish baselines.

    This is the SECRET SAUCE.

    When Virginia allows UNC to shoot 55% eFG, is that good?
    - If Virginia typically allows 48% eFG: UNC overperformed (+7%)
    - If Virginia typically allows 58% eFG: UNC underperformed (-3%)

    The market often misses this context.
    """

    def __init__(self, config: ModelConfig):
        self.config = config

    def establish_opponent_baseline(
        self,
        opponent_history: List[GameData],
        window: int = 5
    ) -> Dict[str, float]:
        """
        Establish what opponent typically allows/forces.

        Args:
            opponent_history: Opponent's recent games (pre-dating the matchup)
            window: How many games to look back

        Returns:
            Baseline metrics (what opponent typically allows)
        """
        if not opponent_history or len(opponent_history) == 0:
            return self._default_baseline()

        # Take last N games
        recent = opponent_history[-window:] if len(opponent_history) >= window else opponent_history

        if not recent:
            return self._default_baseline()

        # Aggregate what opponent has allowed/forced
        baselines: Dict[str, list] = defaultdict(list)

        for game in recent:
            # Analyze the game
            analyzed = self._analyze_single_game(game)

            # What did opponent ALLOW (defensive baselines)
            baselines['allowed_efg'].append(analyzed['opp_allowed_efg'])
            baselines['allowed_orb_pct'].append(analyzed['opp_allowed_orb_pct'])
            baselines['allowed_ftr'].append(analyzed['opp_allowed_ftr'])
            baselines['allowed_off_eff'].append(analyzed['opp_allowed_off_eff'])

            # What did opponent FORCE (offensive baselines)
            baselines['forced_tov_pct'].append(analyzed['opp_forced_tov_pct'])
            baselines['forced_drb_pct'].append(analyzed['opp_forced_drb_pct'])

            # Opponent's own performance
            baselines['opp_off_eff'].append(analyzed['opp_off_eff'])
            baselines['opp_def_eff'].append(analyzed['opp_def_eff'])

            # Margin & total context
            baselines['avg_margin_allowed'].append(analyzed['opp_margin_allowed'])
            baselines['avg_total'].append(analyzed['opp_game_total'])

        # Average across games
        return {key: float(np.mean(values)) for key, values in baselines.items()}

    def _analyze_single_game(self, game: GameData) -> Dict[str, float]:
        """
        Analyze what opponent allowed/forced in a single game.

        From opponent's perspective:
        - allowed_efg: What they gave up on defense
        - forced_tov_pct: What they created on defense
        """
        # Calculate possessions
        opp_poss = estimate_possessions(
            game.opponent_box['fga'],
            game.opponent_box['fta'],
            game.opponent_box['orb'],
            game.opponent_box['tov'],
            game.team_box['orb']
        )

        team_poss = estimate_possessions(
            game.team_box['fga'],
            game.team_box['fta'],
            game.team_box['orb'],
            game.team_box['tov'],
            game.opponent_box['orb']
        )

        # What opponent allowed (their opponent's stats = what they allowed)
        opp_factors = calculate_four_factors(
            game.opponent_box,
            opp_poss,
            game.team_box['drb'],
            game.team_box['orb']
        )

        team_factors = calculate_four_factors(
            game.team_box,
            team_poss,
            game.opponent_box['drb'],
            game.opponent_box['orb']
        )

        # From opponent's defensive perspective (what they allowed)
        allowed_efg = team_factors['efg']  # Their opponent shot this
        allowed_orb_pct = team_factors['orb_pct']
        allowed_ftr = team_factors['ftr']
        allowed_off_eff = (game.team_score / team_poss * 100) if team_poss > 0 else 100

        # From opponent's defensive perspective (what they forced)
        forced_tov_pct = team_factors['tov_pct']
        forced_drb_pct = opp_factors['drb_pct']

        # Opponent's own performance
        opp_off_eff = (game.opponent_score / opp_poss * 100) if opp_poss > 0 else 100
        opp_def_eff = allowed_off_eff

        # Margin allowed & game total (for margin_vs_exp / total_vs_exp)
        margin_allowed = game.team_score - game.opponent_score
        game_total = game.team_score + game.opponent_score

        return {
            'opp_allowed_efg': allowed_efg,
            'opp_allowed_orb_pct': allowed_orb_pct,
            'opp_allowed_ftr': allowed_ftr,
            'opp_allowed_off_eff': allowed_off_eff,
            'opp_forced_tov_pct': forced_tov_pct,
            'opp_forced_drb_pct': forced_drb_pct,
            'opp_off_eff': opp_off_eff,
            'opp_def_eff': opp_def_eff,
            'opp_margin_allowed': margin_allowed,
            'opp_game_total': game_total,
        }

    def _default_baseline(self) -> Dict[str, float]:
        """NCAA averages when no data available."""
        return {
            'allowed_efg': 0.50,
            'allowed_orb_pct': 0.30,
            'allowed_ftr': 0.30,
            'allowed_off_eff': 105.0,
            'forced_tov_pct': 15.0,
            'forced_drb_pct': 0.70,
            'opp_off_eff': 105.0,
            'opp_def_eff': 105.0,
            'avg_margin_allowed': 0.0,
            'avg_total': 144.0,
        }


# ============================================================================
# PERFORMANCE VS EXPECTATION ANALYZER
# ============================================================================

class PerformanceVsExpectationAnalyzer:
    """
    Compares team's actual performance to opponent's baseline.

    This is the CORE INSIGHT that beats the market.
    """

    def __init__(self, config: ModelConfig):
        self.config = config
        self.baseline_analyzer = OpponentBaselineAnalyzer(config)

    def analyze_game_vs_expectation(
        self,
        game: GameData,
        window: int = 5
    ) -> Dict[str, float]:
        """
        Analyze how team performed vs what opponent typically allows.

        Returns both:
        1. Raw performance
        2. Performance vs expectation
        3. Opponent quality
        """
        # Establish opponent's baseline (what they typically allow/force)
        opp_baseline = self.baseline_analyzer.establish_opponent_baseline(
            game.opponent_history,
            window=window
        )

        # Calculate team's actual performance
        team_poss = estimate_possessions(
            game.team_box['fga'],
            game.team_box['fta'],
            game.team_box['orb'],
            game.team_box['tov'],
            game.opponent_box['orb']
        )

        opp_poss = estimate_possessions(
            game.opponent_box['fga'],
            game.opponent_box['fta'],
            game.opponent_box['orb'],
            game.opponent_box['tov'],
            game.team_box['orb']
        )

        team_factors = calculate_four_factors(
            game.team_box,
            team_poss,
            game.opponent_box['drb'],
            game.opponent_box['orb']
        )

        team_off_eff = (game.team_score / team_poss * 100) if team_poss > 0 else 100
        team_def_eff = (game.opponent_score / team_poss * 100) if team_poss > 0 else 100

        # Calculate performance vs expectation
        # Positive = better than opponent typically allows
        efg_vs_exp = (team_factors['efg'] - opp_baseline['allowed_efg']) * 100
        orb_vs_exp = (team_factors['orb_pct'] - opp_baseline['allowed_orb_pct']) * 100
        ftr_vs_exp = (team_factors['ftr'] - opp_baseline['allowed_ftr']) * 100
        tov_vs_exp = (opp_baseline['forced_tov_pct'] - team_factors['tov_pct'])  # Lower is better
        drb_vs_exp = (team_factors['drb_pct'] - (1 - opp_baseline['forced_drb_pct'])) * 100
        off_eff_vs_exp = team_off_eff - opp_baseline['allowed_off_eff']
        margin_vs_exp = (game.team_score - game.opponent_score) - opp_baseline['avg_margin_allowed']
        total_vs_exp = (game.team_score + game.opponent_score) - opp_baseline['avg_total']

        # Opponent quality (for SOS)
        opp_quality = opp_baseline['opp_off_eff'] - opp_baseline['opp_def_eff']

        return {
            # Raw performance
            'team_off_eff': team_off_eff,
            'team_def_eff': team_def_eff,
            'team_net_eff': team_off_eff - team_def_eff,
            'team_pace': team_poss,
            'team_margin': float(game.team_score - game.opponent_score),
            'team_efg': team_factors['efg'],
            'team_tov_pct': team_factors['tov_pct'],
            'team_orb_pct': team_factors['orb_pct'],
            'team_drb_pct': team_factors['drb_pct'],
            'team_ftr': team_factors['ftr'],

            # Performance vs expectation (THE EDGE)
            'efg_vs_exp': efg_vs_exp,
            'orb_vs_exp': orb_vs_exp,
            'ftr_vs_exp': ftr_vs_exp,
            'tov_vs_exp': tov_vs_exp,
            'drb_vs_exp': drb_vs_exp,
            'off_eff_vs_exp': off_eff_vs_exp,
            'margin_vs_exp': margin_vs_exp,
            'total_vs_exp': total_vs_exp,

            # Opponent quality
            'opp_quality': opp_quality,
            'opp_baseline': opp_baseline,
        }

    def aggregate_vs_expectation(
        self,
        games: List[GameData],
        window: int
    ) -> Dict[str, float]:
        """
        Aggregate performance vs expectation over multiple games.

        This gives us context-adjusted metrics.
        """
        if not games:
            return self._default_aggregation()

        recent = games[-window:] if len(games) >= window else games

        if not recent:
            return self._default_aggregation()

        # Analyze each game
        analyzed_games = []
        for game in recent:
            analyzed = self.analyze_game_vs_expectation(game, window=5)
            analyzed_games.append(analyzed)

        # Aggregate
        agg: Dict[str, list] = defaultdict(list)
        for game_result in analyzed_games:
            for key, value in game_result.items():
                if key != 'opp_baseline':  # Skip nested dict
                    agg[key].append(value)

        # Average
        result: Dict[str, float] = {key: float(np.mean(values)) for key, values in agg.items()}
        result['n_games'] = float(len(analyzed_games))

        return result

    def _default_aggregation(self) -> Dict[str, float]:
        """Default when no games available."""
        return {
            'team_off_eff': 105.0,
            'team_def_eff': 105.0,
            'team_net_eff': 0.0,
            'team_pace': 70.0,
            'team_margin': 0.0,
            'team_efg': 0.50,
            'team_tov_pct': 15.0,
            'team_orb_pct': 0.30,
            'team_drb_pct': 0.70,
            'team_ftr': 0.30,
            'efg_vs_exp': 0.0,
            'orb_vs_exp': 0.0,
            'ftr_vs_exp': 0.0,
            'tov_vs_exp': 0.0,
            'drb_vs_exp': 0.0,
            'off_eff_vs_exp': 0.0,
            'margin_vs_exp': 0.0,
            'total_vs_exp': 0.0,
            'opp_quality': 0.0,
            'n_games': 0,
        }


# ============================================================================
# MAIN PREDICTION ENGINE
# ============================================================================

class CBBPredictionModel:
    """
    Enhanced prediction engine with recursive opponent context.
    """

    def __init__(self, config: ModelConfig = ModelConfig()):
        self.config = config
        self.vs_exp_analyzer = PerformanceVsExpectationAnalyzer(config)

    def predict_game(
        self,
        home_games: List[GameData],
        away_games: List[GameData],
        neutral_site: bool = False,
    ) -> Dict[str, Any]:
        """
        Predict spread with recursive opponent context.

        Args:
            home_games: List of GameData objects (each with opponent_history populated)
            away_games: Same for away team
            neutral_site: If True, no HCA

        Returns:
            Prediction dict with spread, confidence, breakdown
        """
        # Analyze both teams with opponent context
        home_l5 = self.vs_exp_analyzer.aggregate_vs_expectation(
            home_games,
            self.config.l5_window
        )
        home_l10 = self.vs_exp_analyzer.aggregate_vs_expectation(
            home_games,
            self.config.l10_window
        )

        away_l5 = self.vs_exp_analyzer.aggregate_vs_expectation(
            away_games,
            self.config.l5_window
        )
        away_l10 = self.vs_exp_analyzer.aggregate_vs_expectation(
            away_games,
            self.config.l10_window
        )

        # Blend L5 (70%) and L10 (30%)
        home_blended = self._blend_windows(home_l5, home_l10)
        away_blended = self._blend_windows(away_l5, away_l10)

        # Calculate matchup
        prediction = self._calculate_matchup(home_blended, away_blended, neutral_site)

        # Confidence
        confidence = self._calculate_confidence(home_l5, away_l5, home_l10, away_l10)
        prediction['confidence'] = confidence

        return prediction

    def _blend_windows(self, l5: Dict, l10: Dict) -> Dict[str, float]:
        """Blend L5 (70%) and L10 (30%)."""
        blended: Dict[str, float] = {}
        for key in l5.keys():
            if key == 'n_games':
                blended[key] = l5[key]
                continue
            blended[key] = (
                self.config.l5_weight * l5[key]
                + self.config.l10_weight * l10.get(key, l5[key])
            )
        return blended

    def _calculate_matchup(
        self,
        home: Dict,
        away: Dict,
        neutral: bool
    ) -> Dict[str, Any]:
        """
        Calculate expected margin using vs-expectation metrics.

        KEY: Use both raw stats and vs-expectation stats.
        """
        cfg = self.config

        # Expected pace
        exp_pace = (home['team_pace'] + away['team_pace']) / 2.0

        # RAW component deltas
        raw_efg_delta = (home['team_efg'] - away['team_efg']) * 100
        raw_tov_delta = (away['team_tov_pct'] - home['team_tov_pct'])
        raw_orb_delta = (home['team_orb_pct'] - away['team_orb_pct']) * 100
        raw_drb_delta = (home['team_drb_pct'] - away['team_drb_pct']) * 100
        raw_ftr_delta = (home['team_ftr'] - away['team_ftr']) * 100

        # VS EXPECTATION deltas (THE EDGE)
        vs_exp_efg_delta = home['efg_vs_exp'] - away['efg_vs_exp']
        vs_exp_tov_delta = home['tov_vs_exp'] - away['tov_vs_exp']
        vs_exp_orb_delta = home['orb_vs_exp'] - away['orb_vs_exp']
        vs_exp_drb_delta = home['drb_vs_exp'] - away['drb_vs_exp']
        vs_exp_ftr_delta = home['ftr_vs_exp'] - away['ftr_vs_exp']

        # Blend raw and vs-expectation (30% raw, 70% vs-exp)
        efg_delta = cfg.raw_weight * raw_efg_delta + cfg.vs_exp_weight * vs_exp_efg_delta
        tov_delta = cfg.raw_weight * raw_tov_delta + cfg.vs_exp_weight * vs_exp_tov_delta
        orb_delta = cfg.raw_weight * raw_orb_delta + cfg.vs_exp_weight * vs_exp_orb_delta
        drb_delta = cfg.raw_weight * raw_drb_delta + cfg.vs_exp_weight * vs_exp_drb_delta
        ftr_delta = cfg.raw_weight * raw_ftr_delta + cfg.vs_exp_weight * vs_exp_ftr_delta

        # Weighted composite
        composite_edge = (
            cfg.efg_weight * efg_delta
            + cfg.tov_weight * tov_delta
            + cfg.orb_weight * orb_delta
            + cfg.drb_weight * drb_delta
            + cfg.ftr_weight * ftr_delta
        )

        # Efficiency edge (also blend raw and vs-exp)
        raw_eff_edge = home['team_net_eff'] - away['team_net_eff']
        vs_exp_eff_edge = home['off_eff_vs_exp'] - away['off_eff_vs_exp']
        eff_edge = cfg.raw_weight * raw_eff_edge + cfg.vs_exp_weight * vs_exp_eff_edge

        # Blend efficiency and composite (60/40)
        raw_edge = 0.60 * eff_edge + 0.40 * composite_edge

        # Pace adjustment
        pace_factor = exp_pace / cfg.avg_pace
        adjusted_edge = raw_edge * pace_factor

        # SOS adjustment
        sos_factor = (home['opp_quality'] - away['opp_quality']) * cfg.sos_strength

        final_edge = adjusted_edge + sos_factor

        # Home court advantage
        hca = 0 if neutral else cfg.default_hca

        # Final prediction
        predicted_spread = -(final_edge + hca)

        return {
            'predicted_spread': predicted_spread,
            'home_net_eff': home['team_net_eff'],
            'away_net_eff': away['team_net_eff'],
            'home_off_eff_vs_exp': home['off_eff_vs_exp'],
            'away_off_eff_vs_exp': away['off_eff_vs_exp'],
            'pace': exp_pace,
            'breakdown': {
                'raw_edge': raw_edge,
                'eff_edge': eff_edge,
                'composite_edge': composite_edge,
                'sos_factor': sos_factor,
                'hca': hca,
                'efg_delta': efg_delta,
                'tov_delta': tov_delta,
                'orb_delta': orb_delta,
                'drb_delta': drb_delta,
                'ftr_delta': ftr_delta,
            }
        }

    def _calculate_confidence(self, home_l5: Dict, away_l5: Dict, home_l10: Dict, away_l10: Dict) -> float:
        """Calculate prediction confidence."""
        home_sample = min(home_l5['n_games'] / 5.0, 1.0)
        away_sample = min(away_l5['n_games'] / 5.0, 1.0)
        sample_conf = (home_sample + away_sample) / 2.0

        home_consistency = 1.0 - abs(home_l5['team_net_eff'] - home_l10['team_net_eff']) / 20.0
        away_consistency = 1.0 - abs(away_l5['team_net_eff'] - away_l10['team_net_eff']) / 20.0
        consistency_conf = (max(0, home_consistency) + max(0, away_consistency)) / 2.0

        return min(0.95, (0.6 * sample_conf + 0.4 * consistency_conf))


# ============================================================================
# DATA ADAPTER: ESPN GAME LOGS -> GameData
# ============================================================================

_BOX_KEYS = ('fgm', 'fga', 'tpm', 'tpa', 'ftm', 'fta', 'orb', 'drb', 'tov')

# Default box score approximated from NCAA D1 averages per game
_DEFAULT_BOX: Dict[str, float] = {
    'fgm': 27.0, 'fga': 62.0, 'tpm': 8.0, 'tpa': 22.0,
    'ftm': 14.0, 'fta': 20.0, 'orb': 10.0, 'drb': 25.0, 'tov': 12.0,
}


def _row_has_box(row: pd.Series) -> bool:
    """Return True if the row has usable box-score data."""
    return float(row.get('fga', 0) or 0) > 0


def _extract_box(row: pd.Series) -> Dict[str, float]:
    """Extract box-score dict from a game-log row, using defaults if missing."""
    if _row_has_box(row):
        return {k: float(row.get(k, 0) or 0) for k in _BOX_KEYS}

    # Estimate box score from available aggregate stats and score
    score = float(row.get('points_for', 0) or 0)
    box = dict(_DEFAULT_BOX)
    if score > 0:
        # Scale defaults proportionally to actual score vs average (~72)
        ratio = score / 72.0
        box = {k: round(v * ratio, 1) for k, v in _DEFAULT_BOX.items()}
    return box


def _parse_date(raw: object) -> datetime:
    """Best-effort parse of date from game-log row."""
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return datetime(2000, 1, 1)
    try:
        return pd.Timestamp(raw).to_pydatetime()
    except Exception:
        return datetime(2000, 1, 1)


class RecursivePredictionEngine:
    """
    Adapter that reads ESPN game logs via DataLoader, builds recursive
    GameData histories, and predicts spreads using CBBPredictionModel.

    Exposes the same return signature as PredictionEngine.predict_spread()
    so it can be swapped in app.py.
    """

    MODEL_ID = "recursive_bidirectional_v1"

    def __init__(self, data_loader: Any, config: Optional[ModelConfig] = None) -> None:
        self.data_loader = data_loader
        self.config = config or ModelConfig()
        self.model = CBBPredictionModel(self.config)
        self._game_log_df: Optional[pd.DataFrame] = None
        self._team_index: Dict[str, pd.DataFrame] = {}

    # -- public interface -----------------------------------------------------

    @property
    def active_model(self) -> Dict[str, Any]:
        return {"model_id": self.MODEL_ID, "model_name": "Recursive Bidirectional"}

    def predict_spread(self, home_team: str, away_team: str, neutral_site: bool = False) -> Dict[str, Any]:
        """
        Predict spread for *home_team* vs *away_team*.

        Returns dict compatible with app.py expectations::

            {
                "predicted_spread": float,
                "confidence": float,
                "model_id": str,
                "breakdown": dict,
            }
        """
        self._ensure_loaded()

        home_games = self._build_team_games(home_team, n=self.config.l10_window)
        away_games = self._build_team_games(away_team, n=self.config.l10_window)

        if not home_games and not away_games:
            logger.warning("No game data for either team: %s vs %s", home_team, away_team)
            return {
                "predicted_spread": -self.config.default_hca if not neutral_site else 0.0,
                "confidence": 0.50,
                "model_id": self.MODEL_ID,
                "breakdown": {},
            }

        raw = self.model.predict_game(home_games, away_games, neutral_site=neutral_site)

        return {
            "predicted_spread": raw["predicted_spread"],
            "confidence": raw["confidence"],
            "model_id": self.MODEL_ID,
            "breakdown": raw.get("breakdown", {}),
        }

    # -- data loading ---------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._game_log_df is not None:
            return

        df = self.data_loader.load_feature_store()
        if df.empty:
            logger.warning("Game log data is empty; recursive model will use defaults.")
            self._game_log_df = pd.DataFrame()
            return

        # Normalise date column
        if "game_date" not in df.columns and "game_datetime_utc" in df.columns:
            df["game_date"] = pd.to_datetime(df["game_datetime_utc"], errors="coerce")
        elif "game_date" in df.columns:
            df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce")

        # Keep only completed games with some score data
        if "completed" in df.columns:
            df = df[df["completed"].astype(str).str.lower().isin(["true", "1"])].copy()

        self._game_log_df = df.sort_values("game_date", ascending=True).reset_index(drop=True)

        # Build per-team index for fast lookups
        if "team" in self._game_log_df.columns:
            for team_name, grp in self._game_log_df.groupby("team"):
                self._team_index[str(team_name).lower()] = grp

    def _get_opponent_row(self, event_id: str, opponent_name: str) -> Optional[pd.Series]:
        """Find the opponent's matching row for the same event."""
        assert self._game_log_df is not None
        opp_key = str(opponent_name).lower()
        opp_df = self._team_index.get(opp_key)
        if opp_df is not None:
            match = opp_df[opp_df["event_id"].astype(str) == str(event_id)]
            if not match.empty:
                return match.iloc[0]
        return None

    # -- GameData construction ------------------------------------------------

    def _build_team_games(self, team_name: str, n: int = 10) -> List[GameData]:
        """Build the last *n* GameData objects for a team, each with opponent history."""
        self._ensure_loaded()
        if self._game_log_df is None or self._game_log_df.empty:
            return []

        team_key = team_name.lower()
        team_df = self._team_index.get(team_key)
        if team_df is None or team_df.empty:
            return []

        recent = team_df.tail(n)
        games: List[GameData] = []

        for _, row in recent.iterrows():
            gd = self._row_to_game_data(row, populate_opp_history=True)
            if gd is not None:
                games.append(gd)

        return games

    def _row_to_game_data(self, row: pd.Series, populate_opp_history: bool = False) -> Optional[GameData]:
        """Convert one game-log row into a GameData, optionally with opponent history."""
        try:
            team_name = str(row.get("team", ""))
            opponent_name = str(row.get("opponent", ""))
            event_id = str(row.get("event_id", ""))
            game_date = _parse_date(row.get("game_date"))

            team_score = int(float(row.get("points_for", 0) or 0))
            opp_score = int(float(row.get("points_against", 0) or 0))

            # Determine neutral site (heuristic: if home_away is empty or "neutral")
            home_away = str(row.get("home_away", "")).lower()
            neutral_site = home_away not in ("home", "away")

            # Team box score
            team_box = _extract_box(row)

            # Opponent box score (from their matching row)
            opp_row = self._get_opponent_row(event_id, opponent_name)
            if opp_row is not None:
                opp_box = _extract_box(opp_row)
                if opp_score == 0:
                    opp_score = int(float(opp_row.get("points_for", 0) or 0))
                if team_score == 0:
                    team_score = int(float(opp_row.get("points_against", 0) or 0))
            else:
                opp_box = _extract_box(pd.Series(dtype="float64"))

            # Opponent history (one level of recursion)
            opp_history: List[GameData] = []
            if populate_opp_history:
                opp_history = self._build_opponent_history(opponent_name, before=game_date)

            return GameData(
                game_id=event_id,
                date=game_date,
                team_name=team_name,
                opponent_name=opponent_name,
                team_score=team_score,
                opponent_score=opp_score,
                neutral_site=neutral_site,
                team_box=team_box,
                opponent_box=opp_box,
                opponent_history=opp_history,
            )
        except Exception:
            logger.debug("Failed to parse game-log row", exc_info=True)
            return None

    def _build_opponent_history(self, opponent_name: str, before: datetime, n: int = 5) -> List[GameData]:
        """
        Get the opponent's last *n* games before *before* date.

        These are built WITHOUT further recursive history to avoid
        infinite recursion (depth limited to 1).
        """
        assert self._game_log_df is not None
        opp_key = opponent_name.lower()
        opp_df = self._team_index.get(opp_key)
        if opp_df is None or opp_df.empty:
            return []

        before_ts = pd.Timestamp(before)
        prior = opp_df[opp_df["game_date"] < before_ts]
        if prior.empty:
            return []

        recent = prior.tail(n)
        games: List[GameData] = []
        for _, row in recent.iterrows():
            gd = self._row_to_game_data(row, populate_opp_history=False)
            if gd is not None:
                games.append(gd)
        return games
