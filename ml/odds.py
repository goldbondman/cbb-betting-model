#!/usr/bin/env python3
"""
Odds conversion helpers.

Design goals (future-proofing):
- Backwards compatible function names/signatures:
    american_to_prob(odds) -> float
    prob_to_american(prob) -> float
    remove_vig(prob_a, prob_b) -> (float, float)
- Robust to strings/None/NaN and small numerical issues
- Clear, deterministic behavior on invalid inputs (return 0.0 or (0.0,0.0))
"""

from __future__ import annotations

from typing import Tuple, Union

import math

Number = Union[int, float]


def _to_float(x: object) -> float:
    try:
        v = float(x)  # handles int/float/str numerics
    except Exception:
        return float("nan")
    return v


def _is_finite(x: float) -> bool:
    return isinstance(x, float) and math.isfinite(x)


def american_to_prob(odds: Number) -> float:
    """
    Convert American odds to implied probability.

    Examples:
      +150 -> 0.4000
      -150 -> 0.6000

    Invalid / non-finite / 0 odds => 0.0
    """
    o = _to_float(odds)
    if not _is_finite(o) or o == 0.0:
        return 0.0

    if o > 0:
        return 100.0 / (o + 100.0)

    ao = abs(o)
    return ao / (ao + 100.0)


def prob_to_american(prob: Number) -> float:
    """
    Convert probability (0..1) to American odds.

    Examples:
      0.40 -> +150
      0.60 -> -150

    Invalid / non-finite / out of (0,1) => 0.0

    Note: returns float to preserve exactness; downstream can round/cast if desired.
    """
    p = _to_float(prob)
    if not _is_finite(p) or p <= 0.0 or p >= 1.0:
        return 0.0

    # Guard against division blowups at extremes
    p = min(max(p, 1e-12), 1.0 - 1e-12)

    if p < 0.5:
        return (100.0 / p) - 100.0

    return -(100.0 * p) / (1.0 - p)


def remove_vig(prob_a: Number, prob_b: Number) -> Tuple[float, float]:
    """
    Remove vig by normalizing a two-outcome market so probs sum to 1.

    Inputs are treated as implied probabilities (not odds).
    Non-finite or negative inputs are treated as 0.0.

    Returns:
      (fair_a, fair_b)

    If both inputs are invalid/zero => (0.0, 0.0)
    """
    a = _to_float(prob_a)
    b = _to_float(prob_b)

    if not _is_finite(a) or a < 0:
        a = 0.0
    if not _is_finite(b) or b < 0:
        b = 0.0

    total = a + b
    if total <= 0.0:
        return 0.0, 0.0

    return a / total, b / total
