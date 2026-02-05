#!/usr/bin/env python3
"""
Odds conversion helpers.
"""

from __future__ import annotations

from typing import Tuple


def american_to_prob(odds: float) -> float:
    if odds == 0:
        return 0.0
    return 100.0 / (odds + 100.0) if odds > 0 else abs(odds) / (abs(odds) + 100.0)


def prob_to_american(prob: float) -> float:
    if prob <= 0 or prob >= 1:
        return 0.0
    return (100.0 / prob) - 100.0 if prob < 0.5 else -(100.0 * prob) / (1.0 - prob)


def remove_vig(prob_a: float, prob_b: float) -> Tuple[float, float]:
    total = prob_a + prob_b
    if total == 0:
        return 0.0, 0.0
    return prob_a / total, prob_b / total
