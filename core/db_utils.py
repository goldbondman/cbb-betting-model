"""Shared data-sanitization helpers for Supabase interactions.

Consolidates NaN handling, type coercion, and payload cleaning
previously duplicated across daily_auto_predict.py, normalize_raw_to_public.py,
and other scripts.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional


def safe_float(value: object) -> Optional[float]:
    """Convert a value to float, returning None for NaN/empty/invalid."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def has_text(value: object) -> bool:
    """Return True when *value* is a non-empty, non-NaN string."""
    if value is None:
        return False
    if isinstance(value, float) and math.isnan(value):
        return False
    return bool(str(value).strip())


def sanitize_nan_dict(payload: Dict[str, object]) -> Dict[str, object]:
    """Return a copy of *payload* with NaN float values replaced by None.

    Supabase/Postgres cannot store Python ``float('nan')``; this helper
    ensures every value is JSON-serialisable before upsert.
    """
    cleaned: Dict[str, object] = {}
    for key, value in payload.items():
        if isinstance(value, float) and math.isnan(value):
            cleaned[key] = None
        else:
            cleaned[key] = value
    return cleaned


def resolve_first(row: Dict[str, Any], aliases: List[str]) -> Optional[float]:
    """Return the first non-None ``safe_float`` value from *aliases* in *row*."""
    for col in aliases:
        val = safe_float(row.get(col))
        if val is not None:
            return val
    return None
