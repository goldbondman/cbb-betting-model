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
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from pathlib import Path as _Path
_ML_DIR = _Path(__file__).resolve().parent
if str(_ML_DIR) not in sys.path:
    sys.path.insert(0, str(_ML_DIR))

from splits import SplitConfig, time_series_split


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
    return [c for c in df.columns if c not in ignore]


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

    # Use your existing splitter for time-series consistency
    val_ratio = max(0.0, min(0.5, float(cfg.val_split)))
    split_cfg = SplitConfig(val_ratio=val_ratio, test_ratio=0.0)
    train_df, val_df, _ = time_series_split(df, split_cfg)

    # Build matrices
    X_train = train_df[feature_cols].astype(float).to_numpy()
    y_rows = len(df)
    n_val = int(len(val_df))

    results: Dict[str, Dict[str, object]] = {}

    for target, fname in [
        ("actual_margin_home", "margin_model.json"),
        ("actual_total", "total_model.json"),
    ]:
        y_train = train_df[target].astype(float).to_numpy()

        coef, train_rmse = _fit_linear(X_train, y_train)

        val_rmse = None
        if n_val > 0:
            X_val = val_df[feature_cols].astype(float).to_numpy()
            y_val = val_df[target].astype(float).to_numpy()
            val_rmse = _rmse_from_coef(X_val, y_val, coef)

        model = {
            "target": target,
            "model_version": cfg.model_version,
            "intercept": float(coef[0]),
            "coefficients": [float(c) for c in coef[1:]],
            "feature_order": feature_cols,
            "rmse": float(train_rmse),
            "val_rmse": (float(val_rmse) if val_rmse is not None else None),
            "val_rows": int(n_val),
            "n_rows": int(y_rows),
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
