#!/usr/bin/env python3
"""
Time-series split helpers for sports data.
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


def time_series_split(df: pd.DataFrame, cfg: SplitConfig) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if df.empty:
        return df.copy(), df.copy(), df.copy()
    out = df.copy()
    out[cfg.date_col] = pd.to_datetime(out[cfg.date_col], utc=True, errors="coerce")
    out = out.sort_values(cfg.date_col)

    n = len(out)
    val_n = max(1, int(n * cfg.val_ratio)) if cfg.val_ratio > 0 else 0
    test_n = max(1, int(n * cfg.test_ratio)) if cfg.test_ratio > 0 else 0
    train_n = max(1, n - val_n - test_n)

    train = out.iloc[:train_n].copy()
    val = out.iloc[train_n:train_n + val_n].copy()
    test = out.iloc[train_n + val_n:].copy()
    return train, val, test
