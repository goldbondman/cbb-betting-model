#!/usr/bin/env python3
"""
Score games using trained linear models.

Inputs:
  - ml/models/margin_model.json
  - ml/models/total_model.json
  - ml/model_features.csv

Outputs:
  - ml/predictions_latest.csv
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from pathlib import Path as _Path

_ML_DIR = _Path(__file__).resolve().parent
if str(_ML_DIR) not in sys.path:
    sys.path.insert(0, str(_ML_DIR))


@dataclass(frozen=True)
class PredictConfig:
    features_path: Path = Path("ml/model_features.csv")
    margin_model_path: Path = Path("ml/models/margin_model.json")
    total_model_path: Path = Path("ml/models/total_model.json")
    out_path: Path = Path("ml/predictions_latest.csv")


def _load_model(path: Path) -> Dict[str, object]:
    if not path.exists():
        raise FileNotFoundError(f"Missing model file: {path}")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in model file {path}: {e}") from e


def _load_features(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing feature store: {path}")
    return pd.read_csv(path)


def _require_model_fields(model: Dict[str, object], model_path: Path) -> None:
    required = ["feature_order", "coefficients", "intercept"]
    missing = [k for k in required if k not in model]
    if missing:
        raise ValueError(f"Model {model_path} missing required fields: {missing}")

    if not isinstance(model["feature_order"], list) or not model["feature_order"]:
        raise ValueError(f"Model {model_path} has empty/invalid feature_order")

    if not isinstance(model["coefficients"], list):
        raise ValueError(f"Model {model_path} has invalid coefficients (expected list)")

    # intercept must be numeric
    try:
        float(model["intercept"])
    except Exception as e:
        raise ValueError(f"Model {model_path} has non-numeric intercept") from e

    # coef length should match feature_order
    if len(model["coefficients"]) != len(model["feature_order"]):
        raise ValueError(
            f"Model {model_path} coefficient length mismatch: "
            f"coefficients={len(model['coefficients'])} feature_order={len(model['feature_order'])}"
        )


def _get_feature_medians(model: Dict[str, object]) -> Dict[str, float]:
    """
    Preferred: use feature_medians saved by training.
    Fallback: empty dict, caller will fill with 0.0.
    """
    med = model.get("feature_medians")
    if not isinstance(med, dict):
        return {}
    out: Dict[str, float] = {}
    for k, v in med.items():
        try:
            out[str(k)] = float(v)
        except Exception:
            # ignore malformed values
            continue
    return out


def _prepare_X(
    df: pd.DataFrame,
    feature_order: List[str],
    feature_medians: Optional[Dict[str, float]] = None,
) -> np.ndarray:
    """
    Build numeric feature matrix in the model's feature_order.
    - missing cols created as NaN
    - values coerced to numeric
    - NaNs filled with model medians, then 0.0
    """
    feature_medians = feature_medians or {}

    missing_cols = [c for c in feature_order if c not in df.columns]
    if missing_cols:
        print(f"[WARN] Missing {len(missing_cols)} feature columns in model_features.csv; filling with defaults.", file=sys.stderr)
        # create cols so reindex works
        for c in missing_cols:
            df[c] = np.nan

    Xdf = df.reindex(columns=feature_order).copy()

    # coerce to numeric
    for c in feature_order:
        Xdf[c] = pd.to_numeric(Xdf[c], errors="coerce")

    X = Xdf.to_numpy(dtype=np.float64)

    # fill NaNs with medians (per column)
    if np.isnan(X).any():
        # build fill vector aligned to feature_order
        fills = np.array([float(feature_medians.get(c, np.nan)) for c in feature_order], dtype=np.float64)
        nan_mask = np.isnan(X)
        # where fill is nan, we leave nan for now (will go to 0.0)
        for j in range(X.shape[1]):
            if np.isnan(fills[j]):
                continue
            col_nan = nan_mask[:, j]
            if col_nan.any():
                X[col_nan, j] = fills[j]

    # final fallback: remaining NaN -> 0.0
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    return X


def _score_df(df: pd.DataFrame, model: Dict[str, object], model_path: Path) -> np.ndarray:
    _require_model_fields(model, model_path)

    feature_order = [str(x) for x in model["feature_order"]]
    coefs = np.array([float(c) for c in model["coefficients"]], dtype=np.float64)
    intercept = float(model["intercept"])

    medians = _get_feature_medians(model)
    X = _prepare_X(df, feature_order, medians)

    # preds = intercept + X @ coefs
    return intercept + (X @ coefs)


def _row_hash_for_row(row: Dict[str, object]) -> str:
    keys = [
        "event_id",
        "team_id_home",
        "team_id_away",
        "game_datetime_utc",
        "model_version",
    ]
    payload = "|".join(str(row.get(k, "") or "") for k in keys)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def predict(cfg: PredictConfig) -> pd.DataFrame:
    margin_model = _load_model(cfg.margin_model_path)
    total_model = _load_model(cfg.total_model_path)
    df = _load_features(cfg.features_path)

    # Score vectorized
    margin_preds = _score_df(df, margin_model, cfg.margin_model_path)
    total_preds = _score_df(df, total_model, cfg.total_model_path)

    # choose model_version (prefer margin, fallback total, fallback default)
    model_version = (
        str(margin_model.get("model_version") or "")
        or str(total_model.get("model_version") or "")
        or "ml-linear-v1"
    )

    # Build output
    out = pd.DataFrame(
        {
            "event_id": df.get("event_id"),
            "team_id_home": df.get("team_id_home"),
            "team_id_away": df.get("team_id_away"),
            "team_home": df.get("team_home"),
            "team_away": df.get("team_away"),
            "game_datetime_utc": df.get("game_datetime_utc"),
            "actual_margin_home": df.get("actual_margin_home"),
            "actual_total": df.get("actual_total"),
            "pred_margin_home": margin_preds.astype(float),
            "pred_total": total_preds.astype(float),
            "model_version": model_version,
        }
    )

    # deterministic row_hash
    out["row_hash"] = [
        _row_hash_for_row(
            {
                "event_id": out.at[i, "event_id"],
                "team_id_home": out.at[i, "team_id_home"],
                "team_id_away": out.at[i, "team_id_away"],
                "game_datetime_utc": out.at[i, "game_datetime_utc"],
                "model_version": model_version,
            }
        )
        for i in range(len(out))
    ]

    cfg.out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(cfg.out_path, index=False)
    return out


def main() -> None:
    cfg = PredictConfig()
    predict(cfg)


if __name__ == "__main__":
    main()
