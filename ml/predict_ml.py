#!/usr/bin/env python3
"""
Score upcoming games using trained linear models.

Inputs:
  - ml/models/margin_model.json
  - ml/models/total_model.json
  - ml/model_features.csv

Outputs:
  - ml/predictions_latest.csv
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PredictConfig:
    features_path: Path = Path("ml/model_features.csv")
    margin_model_path: Path = Path("ml/models/margin_model.json")
    total_model_path: Path = Path("ml/models/total_model.json")
    out_path: Path = Path("ml/predictions_latest.csv")


def _load_model(path: Path) -> Dict[str, object]:
    if not path.exists():
        raise FileNotFoundError(f"Missing model file: {path}")
    return json.loads(path.read_text())


def _load_features(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing feature store: {path}")
    return pd.read_csv(path)


def _score_row(row: pd.Series, model: Dict[str, object]) -> float:
    features = model["feature_order"]
    x = []
    for f in features:
        x.append(float(row.get(f, np.nan)))
    vec = np.array(x, dtype=float)
    vec = np.nan_to_num(vec, nan=0.0)
    return float(model["intercept"] + np.dot(vec, np.array(model["coefficients"], dtype=float)))


def predict(cfg: PredictConfig) -> pd.DataFrame:
    margin_model = _load_model(cfg.margin_model_path)
    total_model = _load_model(cfg.total_model_path)
    df = _load_features(cfg.features_path)

    rows: List[Dict[str, object]] = []
    for _, r in df.iterrows():
        margin_pred = _score_row(r, margin_model)
        total_pred = _score_row(r, total_model)
        model_version = margin_model.get("model_version", "ml-linear-v1")
        rows.append(
            {
                "event_id": r.get("event_id"),
                "team_id_home": r.get("team_id_home"),
                "team_id_away": r.get("team_id_away"),
                "team_home": r.get("team_home"),
                "team_away": r.get("team_away"),
                "game_datetime_utc": r.get("game_datetime_utc"),
                "actual_margin_home": r.get("actual_margin_home"),
                "actual_total": r.get("actual_total"),
                "pred_margin_home": margin_pred,
                "pred_total": total_pred,
                "model_version": model_version,
                "model_version": "ml-linear-v1",
            }
        )

    out = pd.DataFrame(rows)
    cfg.out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(cfg.out_path, index=False)
    return out


def main() -> None:
    cfg = PredictConfig()
    predict(cfg)


if __name__ == "__main__":
    main()
