#!/usr/bin/env python3
"""
Edge calculations for betting decisions.

Edits / hardening:
- Input validation + clipping for probabilities.
- Adds common edge representations you’ll want later:
  - prob edge (model_prob - market_prob)
  - implied EV (expected value) given odds
  - fair odds (American) from a probability
  - market probability from American odds
- Keeps the original function names for compatibility, but makes them safer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


def _clip_prob(p: float, *, eps: float = 1e-6) -> float:
    try:
        x = float(p)
    except Exception as e:
        raise ValueError(f"Invalid probability: {p!r}") from e
    if x != x:  # NaN
        raise ValueError("Probability is NaN.")
    # allow slightly out of bounds, but clamp
    if x < 0.0:
        x = 0.0
    if x > 1.0:
        x = 1.0
    return max(eps, min(1.0 - eps, x))


def edge_prob(model_prob: float, market_prob: float) -> float:
    """
    Probability edge: model - market.
    Positive => value on the modeled side.
    """
    mp = _clip_prob(model_prob)
    mk = _clip_prob(market_prob)
    return float(mp - mk)


def american_to_prob(odds: float) -> float:
    """
    Convert American odds to implied probability (no vig removal).
    odds must be non-zero.
    """
    o = float(odds)
    if o == 0:
        raise ValueError("American odds cannot be 0.")
    if o > 0:
        return 100.0 / (o + 100.0)
    return (-o) / ((-o) + 100.0)


def prob_to_american(p: float) -> int:
    """
    Convert probability to fair American odds.
    Returns an int (rounded).
    """
    p = _clip_prob(p)
    if p >= 0.5:
        odds = - (p / (1.0 - p)) * 100.0
    else:
        odds = ((1.0 - p) / p) * 100.0
    # sportsbook odds are integer-ish
    return int(round(odds))


def expected_value(model_prob: float, odds: float, *, stake: float = 1.0) -> float:
    """
    Expected profit (not ROI) per stake unit, assuming payout per American odds.

    stake=1.0 means "per 1 unit risked".
    """
    p = _clip_prob(model_prob)
    o = float(odds)
    if o == 0:
        raise ValueError("American odds cannot be 0.")

    # profit if win, per 1 unit staked
    if o > 0:
        win_profit = (o / 100.0) * stake
        lose_loss = stake
    else:
        win_profit = (100.0 / (-o)) * stake
        lose_loss = stake

    return float(p * win_profit - (1.0 - p) * lose_loss)


def expected_roi(model_prob: float, odds: float) -> float:
    """
    Expected ROI per 1 unit risked.
    """
    return expected_value(model_prob, odds, stake=1.0)


def fair_line(edge: float) -> float:
    """
    Legacy placeholder.

    Historically you returned edge directly, which isn't a "line".
    Keep it for backward compatibility, but clarify semantics:
    - This returns the edge unchanged.
    """
    return float(edge)
