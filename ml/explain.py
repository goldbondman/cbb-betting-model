#!/usr/bin/env python3
"""
Explainability helpers for linear models.
"""

from __future__ import annotations

from typing import Dict, List, Tuple


def top_contributions(features: Dict[str, float], coefficients: Dict[str, float], top_n: int = 5) -> List[Tuple[str, float]]:
    contributions = []
    for name, value in features.items():
        coef = coefficients.get(name, 0.0)
        contributions.append((name, value * coef))
    contributions.sort(key=lambda x: abs(x[1]), reverse=True)
    return contributions[:top_n]
