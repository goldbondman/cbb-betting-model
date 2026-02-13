"""Utility helpers shared across prediction, betting, and UI modules."""

from __future__ import annotations

from typing import Any


def safe_float(value: Any, default: float = 0.0) -> float:
    """Return float(value) and gracefully fall back to default."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def american_to_prob(odds: Any) -> float:
    """Convert American odds to implied probability in [0, 1]."""
    value = safe_float(odds, 0.0)
    if value == 0:
        return 0.0
    if value > 0:
        return 100.0 / (value + 100.0)
    return abs(value) / (abs(value) + 100.0)


def prob_to_american(prob: Any) -> float:
    """Convert win probability in [0,1] to American odds."""
    p = min(max(safe_float(prob, 0.0), 0.0001), 0.9999)
    if p >= 0.5:
        return -round((p / (1 - p)) * 100)
    return round(((1 - p) / p) * 100)


def remove_vig(prob_a: Any, prob_b: Any) -> tuple[float, float]:
    """Normalize two implied probabilities so they sum to 1."""
    a = max(safe_float(prob_a, 0.0), 0.0)
    b = max(safe_float(prob_b, 0.0), 0.0)
    total = a + b
    if total <= 0:
        return 0.5, 0.5
    return a / total, b / total


def format_spread(spread: Any, team: str = "") -> str:
    """Format spread with sign and optional team label."""
    value = safe_float(spread, 0.0)
    sign = "+" if value > 0 else ""
    core = f"{team} {sign}{value:.1f}".strip()
    return core


def format_odds(odds: Any) -> str:
    """Format American odds with sign."""
    value = int(round(safe_float(odds, 0.0)))
    sign = "+" if value > 0 else ""
    return f"{sign}{value}"
