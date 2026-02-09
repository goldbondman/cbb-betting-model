#!/usr/bin/env python3
"""
Stability/variance diagnostics.

Design goals:
- Small, dependency-light helpers
- Safe behavior with NaNs, non-numeric inputs, and tiny samples
- Backward compatible function names/signatures
"""

from __future__ import annotations

from typing import Optional, Union

import numpy as np
import pandas as pd


def _to_numeric_series(series: pd.Series) -> pd.Series:
    """
    Coerce to numeric; non-parsable values become NaN.
    Keeps index and dtype stable for downstream code.
    """
    if series is None:
        return pd.Series(dtype="float64")
    if not isinstance(series, pd.Series):
        series = pd.Series(series)
    return pd.to_numeric(series, errors="coerce")


def rolling_volatility(series: pd.Series, window: int = 5) -> pd.Series:
    """
    Rolling standard deviation (volatility).
    - Coerces to numeric
    - Uses ddof=1 (pandas default) for sample std
    - Ensures sensible min_periods for small windows
    """
    s = _to_numeric_series(series)

    try:
        w = int(window)
    except Exception:
        w = 5
    if w <= 0:
        # No meaningful rolling window; return all-NaN aligned output
        return pd.Series(index=s.index, data=np.nan, dtype="float64")

    min_p = 2 if w >= 2 else 1
    return s.rolling(window=w, min_periods=min_p).std()


def dispersion_flag(series: pd.Series, threshold: float = 1.5) -> bool:
    """
    Flags if overall dispersion (std) exceeds threshold.
    Notes:
    - Coerces to numeric
    - Requires at least 2 finite observations to make a meaningful claim
    """
    s = _to_numeric_series(series)
    finite = s[np.isfinite(s.to_numpy(dtype="float64", na_value=np.nan))]

    if len(finite) < 2:
        return False

    try:
        thr = float(threshold)
    except Exception:
        thr = 1.5

    return float(finite.std(skipna=True)) > thr


def zscore(series: pd.Series) -> pd.Series:
    """
    Standardize series to z-scores.
    Returns NaN if std is 0 or insufficient data.
    """
    s = _to_numeric_series(series)
    mu = float(s.mean(skipna=True)) if s.notna().any() else np.nan
    sd = float(s.std(skipna=True)) if s.notna().sum() >= 2 else np.nan
    if not np.isfinite(sd) or sd == 0.0:
        return pd.Series(index=s.index, data=np.nan, dtype="float64")
    return (s - mu) / sd


def stability_score(
    series: pd.Series,
    window: int = 5,
    *,
    method: str = "cv",
    eps: float = 1e-9,
) -> float:
    """
    Scalar stability score for ranking:
      - method="cv": coefficient of variation over rolling window means/stds (lower is more stable)
      - method="std": global std (lower is more stable)

    Returns NaN if not computable.
    """
    s = _to_numeric_series(series)
    if s.notna().sum() < 2:
        return float("nan")

    m = (method or "cv").strip().lower()

    if m == "std":
        return float(s.std(skipna=True))

    # default: CV style using rolling mean/std, then average over windows
    rv = rolling_volatility(s, window=window)
    rm = s.rolling(window=int(window) if str(window).isdigit() else 5, min_periods=2).mean()

    valid = rv.notna() & rm.notna() & (rm.abs() > 0)
    if not valid.any():
        return float("nan")

    cv = (rv[valid] / (rm[valid].abs() + float(eps))).astype(float)
    return float(cv.mean(skipna=True))
