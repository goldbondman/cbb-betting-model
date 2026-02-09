#!/usr/bin/env python3
"""
Explainability helpers for linear models.

Edits / hardening:
- Handles missing / non-numeric feature values safely.
- Ignores features not present in coefficients (optional include_zeros flag).
- Supports returning signed contributions and absolute ranking.
- Adds optional intercept contribution.
- Deterministic ordering for ties.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple


def _to_float(x: object) -> Optional[float]:
    try:
        v = float(x)  # type: ignore[arg-type]
        if v != v:  # NaN
            return None
        if v == float("inf") or v == float("-inf"):
            return None
        return v
    except Exception:
        return None


def top_contributions(
    features: Dict[str, object],
    coefficients: Dict[str, object],
    top_n: int = 5,
    *,
    rank_by: str = "abs",  # "abs" or "signed"
    include_zero_coef: bool = False,
    include_intercept: bool = False,
    intercept_key: str = "__intercept__",
) -> List[Tuple[str, float]]:
    """
    Compute per-feature contributions for a linear model.

    features: {feature_name: value}
    coefficients: {feature_name: coef} (may also include intercept via intercept_key)
    top_n: number of rows returned
    rank_by:
      - "abs": rank by absolute contribution magnitude
      - "signed": rank by signed contribution (largest positive first)
    include_zero_coef: if False, features with missing/zero coef are skipped
    include_intercept: if True, include intercept contribution (as its own row)
    intercept_key: key in coefficients dict for intercept, if present

    Returns: List[(feature_name, contribution)]
    """
    if top_n <= 0:
        return []

    # Build contributions
    rows: List[Tuple[str, float]] = []

    for name, raw_val in features.items():
        val = _to_float(raw_val)
        if val is None:
            continue

        raw_coef = coefficients.get(name, 0.0)
        coef = _to_float(raw_coef)
        if coef is None:
            coef = 0.0

        if not include_zero_coef and (name not in coefficients or coef == 0.0):
            continue

        rows.append((str(name), float(val * coef)))

    if include_intercept:
        raw_b = coefficients.get(intercept_key, 0.0)
        b = _to_float(raw_b)
        if b is None:
            b = 0.0
        rows.append((intercept_key, float(b)))

    # Rank
    if rank_by not in ("abs", "signed"):
        raise ValueError("rank_by must be 'abs' or 'signed'")

    if rank_by == "abs":
        rows.sort(key=lambda x: (abs(x[1]), x[0]), reverse=True)
    else:
        rows.sort(key=lambda x: (x[1], x[0]), reverse=True)

    return rows[:top_n]
