#!/usr/bin/env python3
"""
Calibration helpers (Platt scaling + metrics).

Edits / hardening:
- Stable sigmoid to avoid overflow.
- Input validation (shape, finite values, y in {0,1}).
- Safer Platt fitting:
  - uses logit(x) of input probs (as before)
  - adds L2 regularization option to prevent runaway a/b
  - early stopping on log-loss improvement
  - handles degenerate y (all 0 or all 1) gracefully
- ECE binning now includes the 1.0 edge in the last bin.
- All functions accept array-like inputs; coerced to float64 1D.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np


@dataclass(frozen=True)
class PlattModel:
    a: float
    b: float


def _to_1d_float(x: np.ndarray, name: str) -> np.ndarray:
    arr = np.asarray(x, dtype=np.float64).reshape(-1)
    if arr.size == 0:
        raise ValueError(f"{name} is empty.")
    if not np.isfinite(arr).any():
        raise ValueError(f"{name} has no finite values.")
    return arr


def _validate_y(y: np.ndarray) -> np.ndarray:
    y = _to_1d_float(y, "y")
    # allow floats that are exactly 0/1
    uniq = np.unique(y[np.isfinite(y)])
    if not np.all(np.isin(uniq, [0.0, 1.0])):
        raise ValueError(f"y must be binary {0,1}. Found values: {uniq[:10]}")
    return y


def _stable_sigmoid(z: np.ndarray) -> np.ndarray:
    z = np.asarray(z, dtype=np.float64)
    out = np.empty_like(z, dtype=np.float64)
    pos = z >= 0
    neg = ~pos
    out[pos] = 1.0 / (1.0 + np.exp(-z[pos]))
    ez = np.exp(z[neg])
    out[neg] = ez / (1.0 + ez)
    return out


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1.0 - 1e-6)
    return np.log(p / (1.0 - p))


def platt_fit(
    probs: np.ndarray,
    y: np.ndarray,
    *,
    lr: float = 0.1,
    steps: int = 500,
    l2: float = 1e-3,
    early_stop: bool = True,
    tol: float = 1e-7,
    patience: int = 20,
) -> PlattModel:
    """
    Fit Platt scaling on probabilities.

    Uses gradient descent on log-loss with optional L2 regularization on (a,b).
    Handles degenerate y (all 0 or all 1) by returning a near-constant model.

    Returns PlattModel(a,b) such that calibrated_prob = sigmoid(a*logit(p)+b).
    """
    if steps <= 0:
        raise ValueError("steps must be > 0")
    if lr <= 0:
        raise ValueError("lr must be > 0")
    if l2 < 0:
        raise ValueError("l2 must be >= 0")

    p = _to_1d_float(probs, "probs")
    yv = _validate_y(y)

    if p.shape[0] != yv.shape[0]:
        raise ValueError(f"Shape mismatch: probs={p.shape[0]} y={yv.shape[0]}")

    # drop non-finite rows (keep behavior predictable)
    mask = np.isfinite(p) & np.isfinite(yv)
    p = p[mask]
    yv = yv[mask]

    # Degenerate labels: can't learn slope, just return a flat-ish calibrator.
    y_mean = float(np.mean(yv))
    if y_mean <= 0.0:
        return PlattModel(a=0.0, b=-20.0)  # ~0
    if y_mean >= 1.0:
        return PlattModel(a=0.0, b=20.0)  # ~1

    x = _logit(p)

    a, b = 1.0, 0.0
    best_loss = float("inf")
    best = (a, b)
    no_improve = 0

    for _ in range(int(steps)):
        z = a * x + b
        pred = _stable_sigmoid(z)

        # log-loss
        loss = float(-np.mean(yv * np.log(np.clip(pred, 1e-12, 1.0)) + (1.0 - yv) * np.log(np.clip(1.0 - pred, 1e-12, 1.0))))
        if l2 > 0:
            loss += float(l2 * (a * a + b * b))

        # gradients (mean)
        err = (pred - yv)
        grad_a = float(np.mean(err * x) + (2.0 * l2 * a if l2 > 0 else 0.0))
        grad_b = float(np.mean(err) + (2.0 * l2 * b if l2 > 0 else 0.0))

        a -= lr * grad_a
        b -= lr * grad_b

        if early_stop:
            if loss + tol < best_loss:
                best_loss = loss
                best = (a, b)
                no_improve = 0
            else:
                no_improve += 1
                if no_improve >= patience:
                    a, b = best
                    break

    return PlattModel(a=float(a), b=float(b))


def platt_apply(probs: np.ndarray, model: PlattModel) -> np.ndarray:
    p = _to_1d_float(probs, "probs")
    x = _logit(p)
    return _stable_sigmoid(model.a * x + model.b)


def log_loss(probs: np.ndarray, y: np.ndarray) -> float:
    p = _to_1d_float(probs, "probs")
    yv = _validate_y(y)
    if p.shape[0] != yv.shape[0]:
        raise ValueError(f"Shape mismatch: probs={p.shape[0]} y={yv.shape[0]}")
    p = np.clip(p, 1e-6, 1.0 - 1e-6)
    return float(-np.mean(yv * np.log(p) + (1.0 - yv) * np.log(1.0 - p)))


def brier_score(probs: np.ndarray, y: np.ndarray) -> float:
    p = _to_1d_float(probs, "probs")
    yv = _validate_y(y)
    if p.shape[0] != yv.shape[0]:
        raise ValueError(f"Shape mismatch: probs={p.shape[0]} y={yv.shape[0]}")
    p = np.clip(p, 0.0, 1.0)
    return float(np.mean((p - yv) ** 2))


def ece(probs: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    if bins <= 1:
        raise ValueError("bins must be > 1")
    p = _to_1d_float(probs, "probs")
    yv = _validate_y(y)
    if p.shape[0] != yv.shape[0]:
        raise ValueError(f"Shape mismatch: probs={p.shape[0]} y={yv.shape[0]}")

    p = np.clip(p, 0.0, 1.0)

    edges = np.linspace(0.0, 1.0, int(bins) + 1)
    ece_val = 0.0
    n = float(len(p))

    for i in range(int(bins)):
        lo, hi = edges[i], edges[i + 1]
        # include 1.0 in last bin
        if i == bins - 1:
            mask = (p >= lo) & (p <= hi)
        else:
            mask = (p >= lo) & (p < hi)

        if not np.any(mask):
            continue

        avg_prob = float(np.mean(p[mask]))
        avg_outcome = float(np.mean(yv[mask]))
        weight = float(np.sum(mask)) / n
        ece_val += abs(avg_prob - avg_outcome) * weight

    return float(ece_val)


def calibration_metrics(probs: np.ndarray, y: np.ndarray, bins: int = 10) -> Dict[str, float]:
    return {
        "log_loss": log_loss(probs, y),
        "brier": brier_score(probs, y),
        "ece": ece(probs, y, bins=bins),
    }
