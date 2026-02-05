#!/usr/bin/env python3
"""
Stability/variance diagnostics.
"""

from __future__ import annotations

import pandas as pd


def rolling_volatility(series: pd.Series, window: int = 5) -> pd.Series:
    return series.rolling(window, min_periods=2).std()


def dispersion_flag(series: pd.Series, threshold: float = 1.5) -> bool:
    return series.std(skipna=True) > threshold
