#!/usr/bin/env python3
"""
Play/no-play rules.

Design goals:
- Small, dependency-free, and stable interface (should_bet signature stays compatible).
- Defensive handling of NaN/None inputs.
- Optional, env-configurable defaults without forcing callers to change.
"""

from __future__ import annotations

import os
import math
from typing import Optional


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except ValueError:
        return float(default)


def _is_finite(x: object) -> bool:
    try:
        return isinstance(x, (int, float)) and math.isfinite(float(x))
    except Exception:
        return False


def should_bet(
    edge: float,
    conf: float,
    min_edge: float = 0.02,
    min_conf: float = 0.7,
    *,
    allow_push: bool = True,
) -> bool:
    """
    Decide whether to place a bet.

    Inputs:
      edge: model_prob - market_prob (or equivalent edge metric)
      conf: confidence score in [0,1] (caller-defined)

    Defaults:
      min_edge/min_conf are arguments first; if caller passes defaults, env vars may override:
        - BET_MIN_EDGE
        - BET_MIN_CONF

    Rules:
      - Reject non-finite or missing inputs.
      - Use >= thresholds by default (allow_push=True).
    """
    # Defensive input handling
    if not _is_finite(edge) or not _is_finite(conf):
        return False

    # Allow env overrides without breaking call sites
    edge_thr = _env_float("BET_MIN_EDGE", float(min_edge))
    conf_thr = _env_float("BET_MIN_CONF", float(min_conf))

    # Basic sanity: conf should be in [0,1], but don't hard-fail if slightly off.
    c = float(conf)
    e = float(edge)

    if allow_push:
        return e >= edge_thr and c >= conf_thr
    return e > edge_thr and c > conf_thr
