#!/usr/bin/env python3
"""
Staking utilities (fractional Kelly + unit bands).
"""

from __future__ import annotations


def kelly_fraction(prob: float, odds: float) -> float:
    if odds == 0:
        return 0.0
    b = (odds / 100.0) if odds > 0 else (100.0 / abs(odds))
    return max(0.0, (prob * (b + 1) - 1) / b)


def unit_stake(kelly: float, fraction: float = 0.25, max_units: float = 3.0) -> float:
    return min(max_units, max(0.0, kelly * fraction * max_units))
