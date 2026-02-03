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
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class TrainConfig:
    features_path: Path = Path("ml/model_features.csv")
    out_dir: Path = Path("ml/models")


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


def train_models(cfg: TrainConfig) -> Dict[str, Dict[str, object]]:
    df = _load_features(cfg.features_path)
    feature_cols = _select_feature_cols(df)

    if not feature_cols:
        raise ValueError("No feature columns available for training.")

    X = df[feature_cols].astype(float).to_numpy()

    results: Dict[str, Dict[str, object]] = {}
    for target, fname in [
        ("actual_margin_home", "margin_model.json"),
        ("actual_total", "total_model.json"),
    ]:
        y = df[target].astype(float).to_numpy()
        coef, rmse = _fit_linear(X, y)
        model = {
            "target": target,
            "intercept": float(coef[0]),
            "coefficients": [float(c) for c in coef[1:]],
            "feature_order": feature_cols,
            "rmse": rmse,
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
