#!/usr/bin/env python3
"""
Calibration helpers (Platt scaling + metrics).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

import numpy as np


@dataclass(frozen=True)
class PlattModel:
    a: float
    b: float


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def platt_fit(probs: np.ndarray, y: np.ndarray, lr: float = 0.1, steps: int = 500) -> PlattModel:
    x = np.clip(probs, 1e-6, 1 - 1e-6)
    x = np.log(x / (1 - x))
    a, b = 1.0, 0.0
    for _ in range(steps):
        z = a * x + b
        p = _sigmoid(z)
        grad_a = np.mean((p - y) * x)
        grad_b = np.mean(p - y)
        a -= lr * grad_a
        b -= lr * grad_b
    return PlattModel(a=a, b=b)


def platt_apply(probs: np.ndarray, model: PlattModel) -> np.ndarray:
    x = np.clip(probs, 1e-6, 1 - 1e-6)
    x = np.log(x / (1 - x))
    return _sigmoid(model.a * x + model.b)


def log_loss(probs: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(probs, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def brier_score(probs: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((probs - y) ** 2))


def ece(probs: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0, 1, bins + 1)
    ece_val = 0.0
    for i in range(bins):
        mask = (probs >= edges[i]) & (probs < edges[i + 1])
        if not np.any(mask):
            continue
        avg_prob = np.mean(probs[mask])
        avg_outcome = np.mean(y[mask])
        ece_val += np.abs(avg_prob - avg_outcome) * np.mean(mask)
    return float(ece_val)


def calibration_metrics(probs: np.ndarray, y: np.ndarray) -> Dict[str, float]:
    return {
        "log_loss": log_loss(probs, y),
        "brier": brier_score(probs, y),
        "ece": ece(probs, y),
    }
