#!/usr/bin/env python3
"""
Simple ensemble utilities: weighted average and ridge stacker.

Edits / hardening:
- Validate shapes and weights (no silent broadcasting, no divide-by-zero).
- Handle preds as 1D arrays consistently.
- Ridge stacker:
  - Uses solve() instead of inv() for numerical stability.
  - Does NOT regularize intercept term by default.
  - Sanitizes non-finite values (inf -> nan -> impute 0) to avoid crashes.
  - Optional fit_intercept toggle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np


def _as_1d(a: np.ndarray) -> np.ndarray:
    arr = np.asarray(a, dtype=float).reshape(-1)
    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    return arr


def weighted_average(preds: List[np.ndarray], weights: List[float]) -> np.ndarray:
    """
    Weighted average over multiple prediction vectors.

    preds: list of arrays shaped (n,) (or anything reshapeable to 1D)
    weights: list of floats, same length as preds

    Returns: array shaped (n,)
    """
    if not preds:
        raise ValueError("weighted_average: preds is empty.")
    if len(preds) != len(weights):
        raise ValueError(f"weighted_average: len(preds)={len(preds)} != len(weights)={len(weights)}")

    vecs = [_as_1d(p) for p in preds]
    n = vecs[0].shape[0]
    if any(v.shape[0] != n for v in vecs):
        shapes = [v.shape for v in vecs]
        raise ValueError(f"weighted_average: prediction length mismatch: {shapes}")

    w = np.asarray(weights, dtype=float).reshape(-1)
    if w.shape[0] != len(vecs):
        raise ValueError("weighted_average: bad weights shape.")
    if not np.isfinite(w).all():
        raise ValueError("weighted_average: weights contain non-finite values.")
    if np.allclose(w.sum(), 0.0):
        raise ValueError("weighted_average: weights sum to 0 (cannot normalize).")

    w = w / w.sum()
    stacked = np.vstack(vecs)  # (k, n)
    return np.average(stacked, axis=0, weights=w)


@dataclass(frozen=True)
class RidgeStacker:
    coef: np.ndarray
    intercept: float
    l2: float
    fit_intercept: bool = True


def _sanitize_xy(X: np.ndarray, y: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float).reshape(-1)

    if X.ndim != 2:
        raise ValueError(f"ridge_fit: X must be 2D, got shape={X.shape}")
    if X.shape[0] != y.shape[0]:
        raise ValueError(f"ridge_fit: X rows {X.shape[0]} != y rows {y.shape[0]}")
    if X.shape[0] == 0:
        raise ValueError("ridge_fit: empty X/y.")
    if X.shape[1] == 0:
        raise ValueError("ridge_fit: X has 0 columns.")

    # convert non-finite to 0 to avoid blowing up the solve
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    y = np.nan_to_num(y, nan=0.0, posinf=0.0, neginf=0.0)

    return X, y


def ridge_fit(X: np.ndarray, y: np.ndarray, l2: float = 1e-3, *, fit_intercept: bool = True) -> RidgeStacker:
    """
    Fit a ridge regression stacker:
      minimize ||y - (b + Xw)||^2 + l2 * ||w||^2

    Notes:
    - Intercept is NOT regularized (common default) when fit_intercept=True.
    - Uses np.linalg.solve for stability.
    """
    if l2 < 0:
        raise ValueError("ridge_fit: l2 must be >= 0.")
    X, y = _sanitize_xy(X, y)
    n, p = X.shape

    if fit_intercept:
        X_aug = np.column_stack([np.ones(n, dtype=float), X])  # (n, p+1)
        reg = l2 * np.eye(p + 1, dtype=float)
        reg[0, 0] = 0.0  # don't penalize intercept
        A = X_aug.T @ X_aug + reg
        b = X_aug.T @ y
        coef_all = np.linalg.solve(A, b)
        intercept = float(coef_all[0])
        coef = coef_all[1:].astype(float)
    else:
        reg = l2 * np.eye(p, dtype=float)
        A = X.T @ X + reg
        b = X.T @ y
        coef = np.linalg.solve(A, b).astype(float)
        intercept = 0.0

    return RidgeStacker(coef=coef, intercept=intercept, l2=float(l2), fit_intercept=fit_intercept)


def ridge_predict(X: np.ndarray, model: RidgeStacker) -> np.ndarray:
    X = np.asarray(X, dtype=float)
    if X.ndim == 1:
        X = X.reshape(1, -1)
    if X.ndim != 2:
        raise ValueError(f"ridge_predict: X must be 2D, got shape={X.shape}")
    if X.shape[1] != model.coef.shape[0]:
        raise ValueError(
            f"ridge_predict: X cols {X.shape[1]} != model coef {model.coef.shape[0]}"
        )

    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    return (model.intercept + X @ model.coef).astype(float)
