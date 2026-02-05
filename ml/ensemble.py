#!/usr/bin/env python3
"""
Simple ensemble utilities: weighted average and ridge stacker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import numpy as np


def weighted_average(preds: List[np.ndarray], weights: List[float]) -> np.ndarray:
    w = np.array(weights, dtype=float)
    w = w / np.sum(w)
    stacked = np.vstack(preds)
    return np.average(stacked, axis=0, weights=w)


@dataclass(frozen=True)
class RidgeStacker:
    coef: np.ndarray
    intercept: float


def ridge_fit(X: np.ndarray, y: np.ndarray, l2: float = 1e-3) -> RidgeStacker:
    n, p = X.shape
    X_aug = np.column_stack([np.ones(n), X])
    reg = l2 * np.eye(p + 1)
    coef = np.linalg.inv(X_aug.T @ X_aug + reg) @ X_aug.T @ y
    return RidgeStacker(coef=coef[1:], intercept=coef[0])


def ridge_predict(X: np.ndarray, model: RidgeStacker) -> np.ndarray:
    return model.intercept + X @ model.coef
