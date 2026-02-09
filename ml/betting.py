#!/usr/bin/env python3
"""
Betting rules and sizing utilities.

Notes:
- This module intentionally stays thin and delegates logic to:
  odds.py (prob conversions, vig removal)
  edge.py (edge computation)
  staking.py (kelly + unit sizing)
  rules.py (gating rules)

Hardening added:
- Input validation and clamping to avoid garbage-in (NaNs, negatives, odds=0)
- More explicit naming and return shape
- Optional return of "fair_prob" + "market_prob_implied" helpers

Assumptions:
- odds is American odds (e.g., -110, +150) where applicable
- model_prob/market_prob/conf are probabilities in [0,1]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import math

from odds import american_to_prob, remove_vig
from edge import edge_prob
from staking import kelly_fraction, unit_stake
from rules import should_bet


def _is_finite(x: float) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(float(x))


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _require_prob(name: str, x: float) -> float:
    if not _is_finite(x):
        raise ValueError(f"{name} must be finite, got {x!r}")
    x = float(x)
    if x < 0.0 or x > 1.0:
        raise ValueError(f"{name} must be in [0,1], got {x}")
    return x


def _require_odds(name: str, odds: float) -> float:
    if not _is_finite(odds):
        raise ValueError(f"{name} must be finite, got {odds!r}")
    odds = float(odds)
    # American odds cannot be 0; small magnitudes are invalid in practice too, but we just block 0.
    if odds == 0.0:
        raise ValueError(f"{name} cannot be 0 (invalid American odds).")
    return odds


def compute_edge(model_prob: float, market_prob: float) -> float:
    """
    Edge definition is delegated to edge_prob().
    Expects probabilities in [0,1].
    """
    mp = _require_prob("model_prob", model_prob)
    kp = _require_prob("market_prob", market_prob)
    return float(edge_prob(mp, kp))


def fair_prob_from_market(odds_home: float, odds_away: float) -> float:
    """
    Returns fair home probability after removing vig, given two-sided American odds.
    """
    oh = _require_odds("odds_home", odds_home)
    oa = _require_odds("odds_away", odds_away)
    p_home = float(american_to_prob(oh))
    p_away = float(american_to_prob(oa))

    # Clamp, just in case upstream returns tiny float noise
    p_home = _clamp01(p_home)
    p_away = _clamp01(p_away)

    p_home_fair, _ = remove_vig(p_home, p_away)
    return _clamp01(float(p_home_fair))


def market_prob_implied(odds: float) -> float:
    """
    One-sided implied probability from American odds (includes vig when applicable).
    """
    o = _require_odds("odds", odds)
    return _clamp01(float(american_to_prob(o)))


def recommend_bet(
    model_prob: float,
    market_prob: float,
    odds: float,
    conf: float,
    *,
    extra: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    """
    Returns a normalized recommendation dict.

    Required inputs:
      - model_prob: model win probability in [0,1]
      - market_prob: market implied or fair probability in [0,1]
      - odds: American odds for the side you're betting (e.g., -110 or +150)
      - conf: confidence score in [0,1] (whatever your rules.py expects)

    extra:
      - optional passthrough fields (event_id, market, side, timestamp, etc.)
    """
    mp = _require_prob("model_prob", model_prob)
    kp = _require_prob("market_prob", market_prob)
    c = _require_prob("conf", conf)
    o = _require_odds("odds", odds)

    edge = compute_edge(mp, kp)

    # kelly_fraction should handle mp + odds; we just ensure finite output
    kelly = float(kelly_fraction(mp, o))
    if not _is_finite(kelly):
        kelly = 0.0

    units = float(unit_stake(kelly))
    if not _is_finite(units) or units < 0:
        units = 0.0

    play = bool(should_bet(edge, c))

    rec: Dict[str, object] = {
        "edge": float(edge),
        "kelly": float(kelly),
        "units": float(units),
        "play": play,
        "model_prob": float(mp),
        "market_prob": float(kp),
        "odds": float(o),
        "conf": float(c),
    }

    if extra:
        # avoid overwriting core keys
        for k, v in extra.items():
            if k not in rec:
                rec[k] = v

    return rec
