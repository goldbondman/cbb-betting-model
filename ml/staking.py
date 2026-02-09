#!/usr/bin/env python3
"""
Staking utilities (fractional Kelly + unit bands).

Design goals:
- Robust odds handling (American odds in, decimal conversion internally)
- Safe behavior for edge cases (bad inputs, odds=0, prob out of range)
- Backward compatible function names/signatures
"""

from __future__ import annotations

from typing import Optional

import math


def _to_float(x: object, default: float = 0.0) -> float:
    try:
        v = float(x)  # type: ignore[arg-type]
        if not math.isfinite(v):
            return default
        return v
    except Exception:
        return default


def _clamp_prob(p: float) -> float:
    # Keep within open interval for stability
    if not math.isfinite(p):
        return 0.0
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    return p


def _american_to_decimal(odds: float) -> Optional[float]:
    """
    Convert American odds to decimal odds.
    Returns None if odds are invalid (0 or non-finite).
    """
    o = _to_float(odds, default=0.0)
    if o == 0.0:
        return None
    # Decimal odds: 1 + profit per 1 staked
    if o > 0:
        return 1.0 + (o / 100.0)
    return 1.0 + (100.0 / abs(o))


def kelly_fraction(prob: float, odds: float) -> float:
    """
    Kelly fraction for a single bet using American odds.
    Returns a fraction of bankroll to wager (0..inf), but we clamp below at 0.

    Formula with decimal odds d:
      b = d - 1
      f* = (p*b - (1-p)) / b = (p*d - 1) / (d - 1)
    """
    p = _clamp_prob(_to_float(prob, default=0.0))
    dec = _american_to_decimal(odds)
    if dec is None:
        return 0.0

    b = dec - 1.0
    if b <= 0.0:
        return 0.0

    f = (p * dec - 1.0) / b
    if not math.isfinite(f):
        return 0.0
    return max(0.0, f)


def unit_stake(kelly: float, fraction: float = 0.25, max_units: float = 3.0) -> float:
    """
    Convert Kelly fraction into "units" with a fractional Kelly multiplier.

    Interpretation:
      - kelly is a bankroll fraction (0..)
      - fraction scales aggressiveness (0.25 = quarter Kelly)
      - max_units caps exposure in your unit system

    We map:
      units = clamp( kelly * fraction * max_units, 0, max_units )
    """
    k = _to_float(kelly, default=0.0)
    frac = _to_float(fraction, default=0.25)
    cap = _to_float(max_units, default=3.0)

    if cap <= 0.0 or frac <= 0.0 or k <= 0.0:
        return 0.0

    u = k * frac * cap
    if not math.isfinite(u):
        return 0.0
    return min(cap, max(0.0, u))
