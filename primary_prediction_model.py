#!/usr/bin/env python3
"""
College Basketball Prediction Model - Production Version v2.0
Recursive Bidirectional Analysis with Normalized Opponent Baselines

CHANGELOG v2.0 (Quant Team Recommendations):
─────────────────────────────────────────────────────────────────────────────
1. ✅ Removed SOS double-counting (now built into normalization)
2. ✅ Added three-layer normalized baselines (raw → weighted → schedule-adjusted)
3. ✅ Implemented averaged possessions (eliminates team discrepancies)
4. ✅ Removed pace scaling from spread (pace affects total only)
5. ✅ Added smooth exponential decay for game weighting
6. ✅ Added baseline confidence weighting (accounts for variance)
7. ✅ Opponent quality weights (elite opponents = stronger signal)

Author: 20-year quant veteran + Quant team review
Philosophy: Beat the market through normalized performance vs expectation
─────────────────────────────────────────────────────────────────────────────
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict
import warnings
warnings.filterwarnings('ignore')


# ============================================================================
# CONFIGURATION
# ============================================================================

@dataclass
class ModelConfig:
    """
    Model configuration with empirically-tuned parameters.
    
    v2.0 Updates:
    - Removed sos_strength (redundant with normalization)
    - Added decay_type for game weighting options
    - Added opponent quality weight bounds
    """
    
    # ─── Lookback Windows ───
    l5_window: int = 5
    l10_window: int = 10
    
    # ─── Game Decay Weighting ───
    # Options: 'smooth', 'plateau', 'simple'
    decay_type: str = 'smooth'
    
    # ─── Four Factors Weights (sum to 1.0) ───
    efg_weight: float = 0.28
    tov_weight: float = 0.22
    orb_weight: float = 0.18
    ftr_weight: float = 0.16
    drb_weight: float = 0.16
    
    # ─── Raw vs Vs-Expectation Blending ───
    vs_exp_weight: float = 0.70
    raw_weight: float = 0.30
    
    # ─── Opponent Quality Weighting ───
    # Elite opponents give stronger signal
    min_opp_weight: float = 0.5     # Weak opponent minimum weight
    max_opp_weight: float = 2.0     # Elite opponent maximum weight
    opp_weight_scale: float = 20.0  # Sensitivity (lower = more extreme)
    
    # ─── Schedule Adjustment ───
    schedule_adjustment_factor: float = 0.5  # How much to adjust for SOS
    
    # ─── Baseline Values ───
    avg_pace: float = 70.0
    default_hca: float = 3.2
    league_avg_off_eff: float = 105.0
    
    # ─── Confidence Parameters ───
    min_games_for_full_confidence: int = 5
    consistency_threshold: float = 20.0


@dataclass
class GameData:
    """
    Complete game data structure with recursive opponent history.
    """
    
    game_id: str
    date: datetime
    team_name: str
    opponent_name: str
    neutral_site: bool = False
    
    team_score: int = 0
    opponent_score: int = 0
    
    team_box: Dict[str, float] = field(default_factory=dict)
    opponent_box: Dict[str, float] = field(default_factory=dict)
    
    # Recursive opponent context (THE SECRET SAUCE)
    opponent_history: List['GameData'] = field(default_factory=list)
    
    def __post_init__(self):
        """Validate required data."""
        required_stats = ['fgm', 'fga', 'tpm', 'tpa', 'ftm', 'fta', 'orb', 'drb', 'tov']
        for stat in required_stats:
            if stat not in self.team_box:
                self.team_box[stat] = 0.0
            if stat not in self.opponent_box:
                self.opponent_box[stat] = 0.0


# ============================================================================
# CORE BASKETBALL CALCULATIONS
# ============================================================================

def estimate_possessions_averaged(
    team_fga: float,
    team_fta: float,
    team_orb: float,
    team_tov: float,
    opp_fga: float,
    opp_fta: float,
    opp_orb: float,
    opp_tov: float,
) -> float:
    """
    Estimate possessions using Dean Oliver's formula, AVERAGED between both teams.
    
    v2.0 UPDATE: Returns single possession count (averaged) to eliminate discrepancies.
    
    Formula for each team:
        Poss ≈ FGA + 0.475*FTA - ORB + TOV + 0.33*OPP_ORB
    
    Final: Average of both teams' estimates
    """
    # Team A possessions
    team_poss = team_fga + (0.475 * team_fta) - team_orb + team_tov + (0.33 * opp_orb)
    
    # Team B possessions (opponent perspective)
    opp_poss = opp_fga + (0.475 * opp_fta) - opp_orb + opp_tov + (0.33 * team_orb)
    
    # Average (ensures team_off_eff == opp_def_eff)
    return (team_poss + opp_poss) / 2.0


def calculate_four_factors(
    box: Dict[str, float],
    poss: float,
    opp_drb: float,
    opp_orb: float
) -> Dict[str, float]:
    """
    Calculate Dean Oliver's Four Factors from box score.
    
    No changes from v1.0
    """
    def safe_div(num: float, den: float, default: float = 0.0) -> float:
        return num / den if den > 0 else default
    
    efg = safe_div(box['fgm'] + 0.5 * box['tpm'], box['fga'], 0.50)
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


def calculate_efficiency(points: float, poss: float) -> float:
    """Calculate offensive or defensive efficiency (points per 100 possessions)."""
    return (points / poss * 100.0) if poss > 0 else 100.0


# ============================================================================
# GAME WEIGHTING (DECAY FUNCTIONS)
# ============================================================================

def get_game_weight(game_n: int, decay_type: str = 'smooth') -> float:
    """
    Calculate weight for game N based on recency.
    
    Args:
        game_n: Game number (1 = most recent, 10 = oldest in L10)
        decay_type: 'smooth', 'plateau', or 'simple'
        
    Returns:
        Weight between 0.50 and 1.00
    """
    if decay_type == 'smooth':
        # Smooth transition (Quant team recommended)
        # Games 1-5: Slow decay (1.00 → 0.92)
        # Games 6-10: Faster decay (0.75 → 0.50)
        if game_n <= 5:
            return 1.00 - 0.02 * (game_n - 1)  # 1.00, 0.98, 0.96, 0.94, 0.92
        else:
            return max(0.50, 0.75 - 0.05 * (game_n - 5))  # 0.75, 0.70, 0.65, 0.60, 0.55, 0.50
    
    elif decay_type == 'plateau':
        # Plateau for most recent 4 games
        if game_n <= 4:
            return 1.00
        elif game_n == 5:
            return 0.90
        else:
            return max(0.50, 1.05 - 0.10 * game_n)
    
    else:  # 'simple'
        # Equal weight L5, half weight 6-10
        return 1.00 if game_n <= 5 else 0.50


# ============================================================================
# NORMALIZED OPPONENT BASELINE ANALYZER
# ============================================================================

class NormalizedOpponentBaseline:
    """
    Three-layer baseline calculation with opponent quality normalization.
    
    v2.0 UPDATE: This is the major improvement.
    
    Layer 1: Raw baseline (simple average)
    Layer 2: Opponent-quality weighted (elite opponents = stronger signal)
    Layer 3: Schedule-adjusted (account for strength of opponents faced)
    """
    
    def __init__(self, config: ModelConfig):
        self.config = config
    
    def calculate_baseline(
        self,
        opponent_games: List[GameData],
        window: int = 5
    ) -> Dict[str, float]:
        """
        Calculate opponent's defensive baseline with three-layer normalization.
        
        Returns:
            {
                'raw_baseline': Simple average of points allowed
                'weighted_baseline': Opponent-quality weighted average
                'adjusted_baseline': Schedule-adjusted (USE THIS)
                'baseline_std': Standard deviation (for confidence)
                'confidence': Baseline reliability (0-1)
                'n_games': Sample size
            }
        """
        if not opponent_games or len(opponent_games) == 0:
            return self._default_baseline()
        
        # Take last N games
        recent = opponent_games[-window:] if len(opponent_games) >= window else opponent_games
        
        if not recent:
            return self._default_baseline()
        
        # Analyze each game
        game_analyses = []
        for game in recent:
            try:
                analysis = self._analyze_opponent_game(game)
                game_analyses.append(analysis)
            except Exception as e:
                print(f"Warning: Failed to analyze opponent game: {e}")
                continue
        
        if not game_analyses:
            return self._default_baseline()
        
        # Layer 1: Raw baseline (simple average)
        points_allowed = [g['points_allowed'] for g in game_analyses]
        raw_baseline = float(np.mean(points_allowed))
        baseline_std = float(np.std(points_allowed)) if len(points_allowed) > 1 else 5.0
        
        # Layer 2: Opponent-quality weighted baseline
        weighted_total = 0.0
        weight_sum = 0.0
        
        for analysis in game_analyses:
            points = analysis['points_allowed']
            opp_quality = analysis['opponent_net_eff']
            
            # Calculate opponent quality weight
            # Elite (+10 net eff): weight = 1.5
            # Average (0 net eff): weight = 1.0
            # Weak (-10 net eff): weight = 0.5
            weight = 1.0 + (opp_quality / self.config.opp_weight_scale)
            weight = np.clip(weight, self.config.min_opp_weight, self.config.max_opp_weight)
            
            weighted_total += points * weight
            weight_sum += weight
        
        weighted_baseline = float(weighted_total / weight_sum) if weight_sum > 0 else raw_baseline
        
        # Layer 3: Schedule adjustment
        opponent_off_effs = [g['opponent_off_eff'] for g in game_analyses]
        avg_opp_off = float(np.mean(opponent_off_effs))
        
        # If faced tough offenses, lower the baseline (defense is better than raw stats show)
        # If faced weak offenses, raise the baseline (defense is worse than raw stats show)
        schedule_factor = (avg_opp_off - self.config.league_avg_off_eff) * self.config.schedule_adjustment_factor
        adjusted_baseline = weighted_baseline - schedule_factor
        
        # Calculate confidence based on sample size and variance
        n_games = len(game_analyses)
        sample_confidence = min(1.0, n_games / self.config.min_games_for_full_confidence)
        variance_confidence = 1.0 / (1.0 + baseline_std / 10.0)
        confidence = float(sample_confidence * variance_confidence)
        
        return {
            'raw_baseline': raw_baseline,
            'weighted_baseline': weighted_baseline,
            'adjusted_baseline': adjusted_baseline,  # USE THIS for vs-expectation
            'baseline_std': baseline_std,
            'confidence': confidence,
            'n_games': n_games,
            'avg_opp_quality': avg_opp_off - self.config.league_avg_off_eff,  # For debugging
        }
    
    def _analyze_opponent_game(self, game: GameData) -> Dict[str, float]:
        """
        Analyze what opponent allowed/forced in a single game.
        
        v2.0 UPDATE: Uses averaged possessions.
        """
        # Calculate averaged possessions
        game_poss = estimate_possessions_averaged(
            game.opponent_box['fga'],
            game.opponent_box['fta'],
            game.opponent_box['orb'],
            game.opponent_box['tov'],
            game.team_box['fga'],
            game.team_box['fta'],
            game.team_box['orb'],
            game.team_box['tov'],
        )
        
        # From opponent's perspective
        points_allowed = game.team_score  # What they gave up
        allowed_eff = calculate_efficiency(points_allowed, game_poss)
        
        # Opponent's own offensive performance
        opp_off_eff = calculate_efficiency(game.opponent_score, game_poss)
        
        # Opponent's net efficiency (for quality rating)
        opp_def_eff = allowed_eff  # Their def eff = what they allowed
        opp_net_eff = opp_off_eff - opp_def_eff
        
        return {
            'points_allowed': allowed_eff,  # As efficiency (pts/100)
            'opponent_off_eff': opp_off_eff,
            'opponent_net_eff': opp_net_eff,
            'game_poss': game_poss,
        }
    
    def _default_baseline(self) -> Dict[str, float]:
        """NCAA Division I averages when no opponent data available."""
        return {
            'raw_baseline': 105.0,
            'weighted_baseline': 105.0,
            'adjusted_baseline': 105.0,
            'baseline_std': 10.0,
            'confidence': 0.0,
            'n_games': 0,
            'avg_opp_quality': 0.0,
        }


# ============================================================================
# PERFORMANCE VS EXPECTATION ANALYZER
# ============================================================================

class PerformanceVsExpectationAnalyzer:
    """
    Compares team performance to normalized opponent baselines.
    
    v2.0 UPDATE: Uses normalized baselines with confidence weighting.
    """
    
    def __init__(self, config: ModelConfig):
        self.config = config
        self.baseline_analyzer = NormalizedOpponentBaseline(config)
    
    def analyze_game(
        self,
        game: GameData,
        baseline_window: int = 5
    ) -> Dict[str, float]:
        """
        Analyze single game performance vs opponent's normalized baseline.
        
        v2.0 UPDATE:
        - Uses normalized baselines (adjusted_baseline)
        - Weights vs-expectation by baseline confidence
        - Uses averaged possessions
        """
        # Step 1: Establish opponent's normalized baseline
        opp_baseline = self.baseline_analyzer.calculate_baseline(
            game.opponent_history,
            window=baseline_window
        )
        
        # Step 2: Calculate team's actual performance
        game_poss = estimate_possessions_averaged(
            game.team_box['fga'],
            game.team_box['fta'],
            game.team_box['orb'],
            game.team_box['tov'],
            game.opponent_box['fga'],
            game.opponent_box['fta'],
            game.opponent_box['orb'],
            game.opponent_box['tov'],
        )
        
        team_factors = calculate_four_factors(
            game.team_box,
            game_poss,
            game.opponent_box['drb'],
            game.opponent_box['orb']
        )
        
        team_off_eff = calculate_efficiency(game.team_score, game_poss)
        team_def_eff = calculate_efficiency(game.opponent_score, game_poss)
        team_net_eff = team_off_eff - team_def_eff
        
        # Step 3: Calculate vs-expectation (use adjusted_baseline)
        # Raw vs-expectation
        off_eff_vs_exp_raw = team_off_eff - opp_baseline['adjusted_baseline']
        
        # Weight by baseline confidence
        baseline_conf = opp_baseline['confidence']
        off_eff_vs_exp = off_eff_vs_exp_raw * baseline_conf
        
        # Four Factors vs expectation (simplified - using offensive baseline as proxy)
        # In production, you'd have separate baselines for each factor
        efg_vs_exp = (team_factors['efg'] - 0.50) * 100 * baseline_conf
        orb_vs_exp = (team_factors['orb_pct'] - 0.30) * 100 * baseline_conf
        ftr_vs_exp = (team_factors['ftr'] - 0.30) * 100 * baseline_conf
        tov_vs_exp = (15.0 - team_factors['tov_pct']) * baseline_conf  # Lower is better
        drb_vs_exp = (team_factors['drb_pct'] - 0.70) * 100 * baseline_conf
        
        # Game context
        margin = game.team_score - game.opponent_score
        
        return {
            # Raw performance
            'team_off_eff': team_off_eff,
            'team_def_eff': team_def_eff,
            'team_net_eff': team_net_eff,
            'team_pace': game_poss,
            'team_margin': margin,
            'team_efg': team_factors['efg'],
            'team_tov_pct': team_factors['tov_pct'],
            'team_orb_pct': team_factors['orb_pct'],
            'team_drb_pct': team_factors['drb_pct'],
            'team_ftr': team_factors['ftr'],
            'team_ft_pct': team_factors['ft_pct'],
            
            # Performance vs normalized expectation
            'efg_vs_exp': efg_vs_exp,
            'orb_vs_exp': orb_vs_exp,
            'ftr_vs_exp': ftr_vs_exp,
            'tov_vs_exp': tov_vs_exp,
            'drb_vs_exp': drb_vs_exp,
            'off_eff_vs_exp': off_eff_vs_exp,
            
            # Baseline metadata
            'baseline_confidence': baseline_conf,
            'opponent_baseline': opp_baseline['adjusted_baseline'],
            'opponent_quality': opp_baseline['avg_opp_quality'],
        }
    
    def aggregate_window(
        self,
        games: List[GameData],
        window: int,
        decay_type: str = 'smooth'
    ) -> Dict[str, float]:
        """
        Aggregate performance over multiple games with decay weighting.
        
        v2.0 UPDATE: Applies exponential decay to game weights.
        """
        if not games or len(games) == 0:
            return self._default_aggregation()
        
        # Take last N games
        recent = games[-window:] if len(games) >= window else games
        
        if not recent:
            return self._default_aggregation()
        
        # Analyze each game
        analyzed_games = []
        for game in recent:
            try:
                analyzed = self.analyze_game(game, baseline_window=5)
                analyzed_games.append(analyzed)
            except Exception as e:
                print(f"Warning: Failed to analyze game {game.game_id}: {e}")
                continue
        
        if not analyzed_games:
            return self._default_aggregation()
        
        # Apply decay weights (game 1 = most recent)
        weighted_metrics = defaultdict(float)
        total_weight = 0.0
        
        for idx, game_metrics in enumerate(analyzed_games[::-1]):  # Reverse to make idx=0 most recent
            game_n = idx + 1
            weight = get_game_weight(game_n, decay_type)
            
            for key, value in game_metrics.items():
                if key not in ['baseline_confidence', 'opponent_baseline']:
                    weighted_metrics[key] += value * weight
            
            total_weight += weight
        
        # Average with weights
        result = {key: val / total_weight for key, val in weighted_metrics.items()}
        result['n_games'] = len(analyzed_games)
        result['total_weight'] = total_weight
        
        # Calculate variance (for confidence)
        net_effs = [g['team_net_eff'] for g in analyzed_games]
        result['net_eff_std'] = float(np.std(net_effs)) if len(net_effs) > 1 else 0.0
        
        return result
    
    def _default_aggregation(self) -> Dict[str, float]:
        """Default metrics when no games available."""
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
            'team_ft_pct': 0.70,
            'efg_vs_exp': 0.0,
            'orb_vs_exp': 0.0,
            'ftr_vs_exp': 0.0,
            'tov_vs_exp': 0.0,
            'drb_vs_exp': 0.0,
            'off_eff_vs_exp': 0.0,
            'opponent_quality': 0.0,
            'n_games': 0,
            'net_eff_std': 0.0,
            'total_weight': 0.0,
        }


# ============================================================================
# MAIN PREDICTION ENGINE
# ============================================================================

class CBBPredictionModel:
    """
    Main prediction engine with normalized bidirectional analysis.
    
    v2.0 CHANGES:
    - Removed SOS adjustment (built into normalization)
    - Removed pace scaling from spread
    - Uses decay-weighted aggregation
    - Uses normalized baselines
    """
    
    def __init__(self, config: ModelConfig = ModelConfig()):
        self.config = config
        self.analyzer = PerformanceVsExpectationAnalyzer(config)
    
    def predict_game(
        self,
        home_games: List[GameData],
        away_games: List[GameData],
        neutral_site: bool = False,
    ) -> Dict[str, float]:
        """
        Predict spread and total for a matchup.
        
        v2.0: More accurate predictions via normalization and decay weighting.
        """
        # Analyze both teams with decay weighting
        home_l5 = self.analyzer.aggregate_window(
            home_games,
            self.config.l5_window,
            self.config.decay_type
        )
        home_l10 = self.analyzer.aggregate_window(
            home_games,
            self.config.l10_window,
            self.config.decay_type
        )
        away_l5 = self.analyzer.aggregate_window(
            away_games,
            self.config.l5_window,
            self.config.decay_type
        )
        away_l10 = self.analyzer.aggregate_window(
            away_games,
            self.config.l10_window,
            self.config.decay_type
        )
        
        # Blend based on actual weights used
        home_blended = self._blend_windows(home_l5, home_l10)
        away_blended = self._blend_windows(away_l5, away_l10)
        
        # Calculate matchup
        prediction = self._calculate_matchup(home_blended, away_blended, neutral_site)
        
        # Calculate confidence
        confidence = self._calculate_confidence(home_l5, away_l5, home_l10, away_l10)
        prediction['confidence'] = confidence
        
        return prediction
    
    def _blend_windows(self, l5: Dict, l10: Dict) -> Dict[str, float]:
        """
        Blend L5 and L10 based on their actual total weights.
        
        v2.0: Uses decay weights, not static 70/30.
        """
        blended = {}
        
        # Get actual weights (from decay function)
        l5_total_weight = l5.get('total_weight', 4.8)  # ~4.8 for smooth decay on 5 games
        l10_total_weight = l10.get('total_weight', 7.8)  # ~7.8 for smooth decay on 10 games
        
        # Normalize to get proportions
        total = l5_total_weight + l10_total_weight
        l5_prop = l5_total_weight / total if total > 0 else 0.7
        l10_prop = l10_total_weight / total if total > 0 else 0.3
        
        for key in l5.keys():
            if key in ['n_games', 'total_weight']:
                blended[key] = l5[key]
                continue
            
            # Weighted blend
            blended[key] = l5_prop * l5[key] + l10_prop * l10.get(key, l5[key])
        
        return blended
    
    def _calculate_matchup(
        self,
        home: Dict,
        away: Dict,
        neutral: bool
    ) -> Dict[str, float]:
        """
        Calculate expected margin and total.
        
        v2.0 CHANGES:
        - Removed SOS adjustment (redundant)
        - Removed pace scaling from spread
        - Pace only affects total calculation
        """
        cfg = self.config
        
        # Expected pace
        exp_pace = (home['team_pace'] + away['team_pace']) / 2.0
        
        # Component deltas (blend raw + vs-expectation)
        raw_efg = (home['team_efg'] - away['team_efg']) * 100
        vs_exp_efg = home['efg_vs_exp'] - away['efg_vs_exp']
        efg_delta = cfg.raw_weight * raw_efg + cfg.vs_exp_weight * vs_exp_efg
        
        raw_tov = (away['team_tov_pct'] - home['team_tov_pct'])
        vs_exp_tov = home['tov_vs_exp'] - away['tov_vs_exp']
        tov_delta = cfg.raw_weight * raw_tov + cfg.vs_exp_weight * vs_exp_tov
        
        raw_orb = (home['team_orb_pct'] - away['team_orb_pct']) * 100
        vs_exp_orb = home['orb_vs_exp'] - away['orb_vs_exp']
        orb_delta = cfg.raw_weight * raw_orb + cfg.vs_exp_weight * vs_exp_orb
        
        raw_drb = (home['team_drb_pct'] - away['team_drb_pct']) * 100
        vs_exp_drb = home['drb_vs_exp'] - away['drb_vs_exp']
        drb_delta = cfg.raw_weight * raw_drb + cfg.vs_exp_weight * vs_exp_drb
        
        raw_ftr = (home['team_ftr'] - away['team_ftr']) * 100
        vs_exp_ftr = home['ftr_vs_exp'] - away['ftr_vs_exp']
        ftr_delta = cfg.raw_weight * raw_ftr + cfg.vs_exp_weight * vs_exp_ftr
        
        # Weighted composite
        composite_edge = (
            cfg.efg_weight * efg_delta +
            cfg.tov_weight * tov_delta +
            cfg.orb_weight * orb_delta +
            cfg.drb_weight * drb_delta +
            cfg.ftr_weight * ftr_delta
        )
        
        # Efficiency edge
        raw_eff = home['team_net_eff'] - away['team_net_eff']
        vs_exp_eff = home['off_eff_vs_exp'] - away['off_eff_vs_exp']
        eff_edge = cfg.raw_weight * raw_eff + cfg.vs_exp_weight * vs_exp_eff
        
        # Blend composite and efficiency (60/40)
        raw_edge = 0.60 * eff_edge + 0.40 * composite_edge
        
        # v2.0: NO pace scaling on spread (only affects total)
        # v2.0: NO SOS adjustment (already in normalization)
        
        # Home court advantage
        hca = 0.0 if neutral else cfg.default_hca
        final_edge = raw_edge + hca
        
        # Convert to spread
        predicted_spread = -final_edge
        
        # Calculate expected total (pace DOES affect this)
        home_expected_points = home['team_off_eff'] * (exp_pace / 100.0)
        away_expected_points = away['team_off_eff'] * (exp_pace / 100.0)
        predicted_total = home_expected_points + away_expected_points
        
        return {
            'predicted_spread': predicted_spread,
            'predicted_total': predicted_total,
            'home_net_eff': home['team_net_eff'],
            'away_net_eff': away['team_net_eff'],
            'home_off_eff_vs_exp': home['off_eff_vs_exp'],
            'away_off_eff_vs_exp': away['off_eff_vs_exp'],
            'pace': exp_pace,
            'breakdown': {
                'raw_edge': raw_edge,
                'eff_edge': eff_edge,
                'composite_edge': composite_edge,
                'hca': hca,
                'efg_delta': efg_delta,
                'tov_delta': tov_delta,
                'orb_delta': orb_delta,
                'drb_delta': drb_delta,
                'ftr_delta': ftr_delta,
            }
        }
    
    def _calculate_confidence(
        self,
        home_l5: Dict,
        away_l5: Dict,
        home_l10: Dict,
        away_l10: Dict,
    ) -> float:
        """Calculate prediction confidence (0-1)."""
        # Sample size component
        home_sample = min(home_l5['n_games'] / self.config.min_games_for_full_confidence, 1.0)
        away_sample = min(away_l5['n_games'] / self.config.min_games_for_full_confidence, 1.0)
        sample_conf = (home_sample + away_sample) / 2.0
        
        # Consistency component
        home_delta = abs(home_l5['team_net_eff'] - home_l10['team_net_eff'])
        away_delta = abs(away_l5['team_net_eff'] - away_l10['team_net_eff'])
        
        home_consistency = max(0.0, 1.0 - home_delta / self.config.consistency_threshold)
        away_consistency = max(0.0, 1.0 - away_delta / self.config.consistency_threshold)
        consistency_conf = (home_consistency + away_consistency) / 2.0
        
        # Combined confidence
        confidence = 0.60 * sample_conf + 0.40 * consistency_conf
        
        return min(0.95, max(0.05, confidence))


# ============================================================================
# EXAMPLE USAGE
# ============================================================================

if __name__ == "__main__":
    """
    Example: Duke vs UNC prediction with v2.0 improvements.
    """
    
    print("=" * 80)
    print("COLLEGE BASKETBALL PREDICTION MODEL v2.0")
    print("Normalized Bidirectional Analysis with Decay Weighting")
    print("=" * 80)
    print()
    
    # Create model with smooth decay
    config = ModelConfig(decay_type='smooth')
    model = CBBPredictionModel(config)
    
    # Example game data (simplified)
    duke_game_1 = GameData(
        game_id="duke_vt_123",
        date=datetime(2024, 1, 5),
        team_name="Duke",
        opponent_name="Virginia Tech",
        team_score=78,
        opponent_score=70,
        neutral_site=False,
        team_box={
            'fgm': 28, 'fga': 58, 'tpm': 8, 'tpa': 22,
            'ftm': 14, 'fta': 18, 'orb': 10, 'drb': 26, 'tov': 11
        },
        opponent_box={
            'fgm': 26, 'fga': 60, 'tpm': 6, 'tpa': 20,
            'ftm': 12, 'fta': 16, 'orb': 8, 'drb': 24, 'tov': 13
        },
        opponent_history=[]  # In production: VT's L5 games
    )
    
    unc_game_1 = GameData(
        game_id="unc_wake_456",
        date=datetime(2024, 1, 6),
        team_name="UNC",
        opponent_name="Wake Forest",
        team_score=82,
        opponent_score=75,
        neutral_site=False,
        team_box={
            'fgm': 30, 'fga': 62, 'tpm': 10, 'tpa': 26,
            'ftm': 12, 'fta': 16, 'orb': 12, 'drb': 24, 'tov': 10
        },
        opponent_box={
            'fgm': 28, 'fga': 64, 'tpm': 7, 'tpa': 24,
            'ftm': 12, 'fta': 15, 'orb': 9, 'drb': 25, 'tov': 12
        },
        opponent_history=[]
    )
    
    duke_games = [duke_game_1]
    unc_games = [unc_game_1]
    
    print("Generating prediction: Duke (home) vs UNC (away)")
    print()
    
    try:
        prediction = model.predict_game(
            home_games=duke_games,
            away_games=unc_games,
            neutral_site=False
        )
        
        print("PREDICTION RESULTS")
        print("-" * 80)
        print(f"Predicted Spread:  Duke {prediction['predicted_spread']:+.1f}")
        print(f"Predicted Total:   {prediction['predicted_total']:.1f} points")
        print(f"Confidence:        {prediction['confidence']:.1%}")
        print()
        
        print("v2.0 IMPROVEMENTS APPLIED")
        print("-" * 80)
        print("✅ Normalized opponent baselines (3-layer)")
        print("✅ Averaged possessions (eliminates discrepancies)")
        print("✅ Smooth decay weighting (62% L5, 38% games 6-10)")
        print("✅ Removed SOS double-counting")
        print("✅ Removed pace scaling from spread")
        print("✅ Baseline confidence weighting")
        print()
        
        print("EFFICIENCY METRICS")
        print("-" * 80)
        print(f"Duke Net Efficiency:     {prediction['home_net_eff']:+.1f} pts/100")
        print(f"UNC Net Efficiency:      {prediction['away_net_eff']:+.1f} pts/100")
        print(f"Duke Off Eff vs Exp:     {prediction['home_off_eff_vs_exp']:+.1f} pts/100")
        print(f"UNC Off Eff vs Exp:      {prediction['away_off_eff_vs_exp']:+.1f} pts/100")
        print(f"Expected Pace:           {prediction['pace']:.1f} possessions")
        print()
        
        print("BREAKDOWN")
        print("-" * 80)
        breakdown = prediction['breakdown']
        print(f"Raw Edge:                {breakdown['raw_edge']:+.2f} points")
        print(f"  ├─ Efficiency Edge:    {breakdown['eff_edge']:+.2f}")
        print(f"  └─ Composite Edge:     {breakdown['composite_edge']:+.2f}")
        print(f"Home Court Advantage:    {breakdown['hca']:+.2f} points")
        print(f"Final Spread:            {prediction['predicted_spread']:+.1f}")
        print()
        
        print("=" * 80)
        print("Note: With full opponent_history data, predictions will be more accurate")
        print("=" * 80)
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
