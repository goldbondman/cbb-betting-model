#!/usr/bin/env python3
"""
Edge calculations for betting decisions.
"""

from __future__ import annotations


def edge_prob(model_prob: float, market_prob: float) -> float:
    return model_prob - market_prob


def fair_line(edge: float) -> float:
    return edge
