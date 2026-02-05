#!/usr/bin/env python3
"""
Betting rules and sizing utilities.
"""

from __future__ import annotations

from odds import american_to_prob, remove_vig
from edge import edge_prob
from staking import kelly_fraction, unit_stake
from rules import should_bet


def compute_edge(model_prob: float, market_prob: float) -> float:
    return edge_prob(model_prob, market_prob)


def fair_prob_from_market(odds_home: float, odds_away: float) -> float:
    p_home = american_to_prob(odds_home)
    p_away = american_to_prob(odds_away)
    p_home_fair, _ = remove_vig(p_home, p_away)
    return p_home_fair


def recommend_bet(model_prob: float, market_prob: float, odds: float, conf: float) -> dict:
    edge = compute_edge(model_prob, market_prob)
    kelly = kelly_fraction(model_prob, odds)
    units = unit_stake(kelly)
    return {
        "edge": edge,
        "kelly": kelly,
        "units": units,
        "play": should_bet(edge, conf),
    }
