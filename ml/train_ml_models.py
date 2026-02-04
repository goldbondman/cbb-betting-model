#!/usr/bin/env python3
"""
Train simple linear models (margin + total) from model_features.csv.

Outputs:
  - ml/models/margin_model.json
  - ml/models/total_model.json

Notes:
  - Uses numpy least squares (no external ML deps).
  - Stores coefficients, feature order, and training metadata.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class TrainConfig:
    features_path: Path = Path("ml/model_features.csv")
    out_dir: Path = Path("ml/models")
    val_split: float = float(os.getenv("ML_VAL_SPLIT", "0.1"))
    model_version: str = os.getenv("ML_MODEL_VERSION", "ml-linear-v1")


def _load_features(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing features file: {path}")
    return pd.read_csv(path)


def _select_feature_cols(df: pd.DataFrame) -> List[str]:
    ignore = {
        "event_id",
        "team_id_home",
        "team_id_away",
        "team_home",
        "team_away",
        "game_datetime_utc",
        "actual_margin_home",
        "actual_total",
    }
    feature_cols = [c for c in df.columns if c not in ignore]
    return feature_cols


def _fit_linear(X: np.ndarray, y: np.ndarray) -> Tuple[np.ndarray, float]:
    X_aug = np.column_stack([np.ones(X.shape[0]), X])
    coef, *_ = np.linalg.lstsq(X_aug, y, rcond=None)
    preds = X_aug @ coef
    rmse = float(np.sqrt(np.mean((preds - y) ** 2)))
    return coef, rmse


def _rmse_from_coef(X: np.ndarray, y: np.ndarray, coef: np.ndarray) -> float:
    X_aug = np.column_stack([np.ones(X.shape[0]), X])
    preds = X_aug @ coef
    return float(np.sqrt(np.mean((preds - y) ** 2)))


def train_models(cfg: TrainConfig) -> Dict[str, Dict[str, object]]:
    df = _load_features(cfg.features_path)
    feature_cols = _select_feature_cols(df)

    if not feature_cols:
        raise ValueError("No feature columns available for training.")

    X = df[feature_cols].astype(float).to_numpy()
    val_split = max(0.0, min(0.5, float(cfg.val_split)))
    if val_split > 0 and len(df) > 10:
        n_val = max(1, int(len(df) * val_split))
        X_train, X_val = X[:-n_val], X[-n_val:]
    else:
        n_val = 0
        X_train, X_val = X, None

    results: Dict[str, Dict[str, object]] = {}
    for target, fname in [
        ("actual_margin_home", "margin_model.json"),
        ("actual_total", "total_model.json"),
    ]:
        y = df[target].astype(float).to_numpy()
        y_train = y[:-n_val] if n_val else y
        y_val = y[-n_val:] if n_val else None
        coef, rmse = _fit_linear(X_train, y_train)
        val_rmse = _rmse_from_coef(X_val, y_val, coef) if n_val else None
        model = {
            "target": target,
            "model_version": cfg.model_version,
            "intercept": float(coef[0]),
            "coefficients": [float(c) for c in coef[1:]],
            "feature_order": feature_cols,
            "rmse": rmse,
            "val_rmse": val_rmse,
            "val_rows": int(n_val),
            "n_rows": int(len(df)),
        }
        results[target] = model
        cfg.out_dir.mkdir(parents=True, exist_ok=True)
        (cfg.out_dir / fname).write_text(json.dumps(model, indent=2))

    return results


def main() -> None:
    cfg = TrainConfig()
    train_models(cfg)


if __name__ == "__main__":
    main()
