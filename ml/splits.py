#!/usr/bin/env python3
"""
Time-series split helpers for sports data.

Goals:
- Deterministic, leakage-safe chronological splitting
- Backward compatible API:
    - SplitConfig
    - time_series_split(df, cfg) -> (train, val, test)
- More resilient handling of missing/invalid datetimes and tiny datasets
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

import pandas as pd


@dataclass(frozen=True)
class SplitConfig:
    date_col: str = "game_datetime_utc"
    val_ratio: float = 0.1
    test_ratio: float = 0.1
    min_train_rows: int = 1
    drop_na_dates: bool = True  # If False, keep NaT rows at end (won't leak, but may reduce usefulness)


def _clamp_ratio(x: float) -> float:
    try:
        x = float(x)
    except Exception:
        return 0.0
    if x < 0.0:
        return 0.0
    if x > 0.95:
        return 0.95
    return x


def time_series_split(df: pd.DataFrame, cfg: SplitConfig) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Chronological split:
      train = earliest rows
      val   = next window
      test  = latest rows

    Notes:
    - Uses cfg.date_col for ordering; coerces to UTC datetime.
    - If dates are invalid and cfg.drop_na_dates=True, NaT rows are dropped.
      If False, NaT rows are pushed to the end (still deterministic).
    - Ensures at least cfg.min_train_rows in train when possible.
    """
    if df is None or df.empty:
        empty = df.copy() if df is not None else pd.DataFrame()
        return empty.copy(), empty.copy(), empty.copy()

    out = df.copy()

    if cfg.date_col not in out.columns:
        # Backward compatible: if column missing, just split by row order deterministically.
        # This is safer than crashing in production pipelines.
        out = out.reset_index(drop=True)
    else:
        out[cfg.date_col] = pd.to_datetime(out[cfg.date_col], utc=True, errors="coerce")
        if cfg.drop_na_dates:
            out = out.loc[~out[cfg.date_col].isna()].copy()
        # Stable sort: date first, then index to keep deterministic ordering for ties
        out = out.sort_values([cfg.date_col], ascending=[True], na_position="last").reset_index(drop=True)

    if out.empty:
        # All rows dropped due to NaT date handling
        empty = out.copy()
        return empty.copy(), empty.copy(), empty.copy()

    n = int(len(out))

    val_ratio = _clamp_ratio(cfg.val_ratio)
    test_ratio = _clamp_ratio(cfg.test_ratio)

    # Ensure ratios don't exceed 1.0 in combination.
    total_ratio = val_ratio + test_ratio
    if total_ratio > 0.95:
        # Scale down proportionally to leave room for train
        scale = 0.95 / total_ratio
        val_ratio *= scale
        test_ratio *= scale

    val_n = int(round(n * val_ratio)) if val_ratio > 0 else 0
    test_n = int(round(n * test_ratio)) if test_ratio > 0 else 0

    # Clamp to valid range
    val_n = max(0, min(val_n, n))
    test_n = max(0, min(test_n, n - val_n))

    # Guarantee some training rows when possible
    min_train = max(1, int(cfg.min_train_rows))
    train_n = n - val_n - test_n
    if train_n < min_train:
        deficit = min_train - train_n
        # Prefer reducing test, then val
        take_from_test = min(deficit, test_n)
        test_n -= take_from_test
        deficit -= take_from_test
        take_from_val = min(deficit, val_n)
        val_n -= take_from_val
        train_n = n - val_n - test_n

    # Final safeguard
    train_n = max(1, min(train_n, n))
    val_start = train_n
    val_end = min(n, val_start + val_n)

    train = out.iloc[:train_n].copy()
    val = out.iloc[val_start:val_end].copy()
    test = out.iloc[val_end:].copy()

    return train, val, test
