#!/usr/bin/env python3
"""
Score games using trained linear models.

Inputs:
  - ml/models/margin_model.json
  - ml/models/total_model.json
  - ml/model_features.csv

Outputs:
  - ml/predictions_latest.csv

Future-proofing notes:
- Vectorized scoring (no per-row loops for dot products)
- Reuses training-time feature medians when present (train_ml_models.py writes feature_medians)
- Deterministic row_hash and output ordering (when game_datetime_utc exists)
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

    if not isinstance(model["feature_order"], list):
        raise ValueError(f"Model {model_path} has invalid feature_order")
    if not model["feature_order"] and model.get("fallback") != "intercept_only":
        raise ValueError(f"Model {model_path} has empty feature_order without intercept-only fallback flag")

    if not isinstance(model["coefficients"], list):
        raise ValueError(f"Model {model_path} has invalid coefficients (expected list)")

    try:
        float(model["intercept"])
    except Exception as e:
        raise ValueError(f"Model {model_path} has non-numeric intercept") from e

    if len(model["coefficients"]) != len(model["feature_order"]):
        raise ValueError(
            f"Model {model_path} coefficient length mismatch: "
            f"coefficients={len(model['coefficients'])} feature_order={len(model['feature_order'])}"
        )


def _get_feature_medians(model: Dict[str, object]) -> Dict[str, float]:
    """
    Preferred: use feature_medians saved by training.
    Fallback: empty dict (caller fills remaining NaNs with 0.0).
    """
    med = model.get("feature_medians")
    if not isinstance(med, dict):
        return {}
    out: Dict[str, float] = {}
    for k, v in med.items():
        try:
            fv = float(v)
            if not np.isfinite(fv):
                fv = 0.0
            out[str(k)] = fv
        except Exception:
            continue
    return out


def _prepare_X(
    df: pd.DataFrame,
    feature_order: List[str],
    feature_medians: Optional[Dict[str, float]] = None,
) -> np.ndarray:
    """
    Build numeric feature matrix in the model's feature_order.
    - Missing cols become NaN
    - Values coerced to numeric
    - NaNs filled with model medians, then 0.0
    """
    feature_medians = feature_medians or {}

    # Create any missing columns so reindex is stable
    missing_cols = [c for c in feature_order if c not in df.columns]
    if missing_cols:
        print(
            f"[WARN] Missing {len(missing_cols)} feature columns in {len(df.columns)}-col features; filling defaults.",
            file=sys.stderr,
        )
        for c in missing_cols:
            df[c] = np.nan

    Xdf = df.reindex(columns=feature_order).copy()

    # Coerce all to numeric (best-effort)
    for c in feature_order:
        Xdf[c] = pd.to_numeric(Xdf[c], errors="coerce")

    X = Xdf.to_numpy(dtype=np.float64)

    # Fill NaNs with per-feature medians (aligned to feature_order)
    if np.isnan(X).any():
        fills = np.array([float(feature_medians.get(c, np.nan)) for c in feature_order], dtype=np.float64)
        fills[~np.isfinite(fills)] = np.nan  # keep NaN to allow fallback to 0.0

        nan_mask = np.isnan(X)
        # Column-wise fill (fast enough, keeps logic simple)
        for j in range(X.shape[1]):
            if np.isnan(fills[j]):
                continue
            col_nan = nan_mask[:, j]
            if col_nan.any():
                X[col_nan, j] = fills[j]

    # Final fallback: remaining NaN/inf -> 0.0
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    return X


def _score_df(df: pd.DataFrame, model: Dict[str, object], model_path: Path) -> np.ndarray:
    _require_model_fields(model, model_path)

    feature_order = [str(x) for x in model["feature_order"]]
    coefs = np.array([float(c) for c in model["coefficients"]], dtype=np.float64)
    intercept = float(model["intercept"])

    medians = _get_feature_medians(model)
    X = _prepare_X(df, feature_order, medians)
    return intercept + (X @ coefs)


def _row_hash_for_row(row: Dict[str, object]) -> str:
    keys = ["event_id", "team_id_home", "team_id_away", "game_datetime_utc", "model_version"]
    payload = "|".join(str(row.get(k, "") or "") for k in keys)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sort_for_determinism(out: pd.DataFrame) -> pd.DataFrame:
    """
    Keep outputs stable across runs when possible.
    Sort by game_datetime_utc, then event_id, then team ids.
    If datetime parse fails, fall back to event_id sort.
    """
    df = out.copy()
    if "game_datetime_utc" in df.columns:
        dt = pd.to_datetime(df["game_datetime_utc"], utc=True, errors="coerce")
        df["_dt_sort"] = dt
        # stable sort
        df = df.sort_values(
            ["_dt_sort", "event_id", "team_id_home", "team_id_away"],
            ascending=[True, True, True, True],
            na_position="last",
            kind="mergesort",
        ).drop(columns=["_dt_sort"])
        return df.reset_index(drop=True)

    return df.sort_values(["event_id"], kind="mergesort").reset_index(drop=True)


def _emit_prediction_diagnostics(out: pd.DataFrame) -> None:
    n_rows = int(len(out))
    margin = pd.to_numeric(out["pred_margin_home"], errors="coerce")
    total = pd.to_numeric(out["pred_total"], errors="coerce")

    margin_std = float(margin.std(ddof=0)) if n_rows else 0.0
    total_std = float(total.std(ddof=0)) if n_rows else 0.0
    margin_unique = int(margin.nunique(dropna=True))
    total_unique = int(total.nunique(dropna=True))

    print("[DIAG] Prediction diagnostics")
    print(f"[DIAG] rows={n_rows}")
    print(f"[DIAG] pred_margin_home std={margin_std:.6f} unique={margin_unique}")
    print(f"[DIAG] pred_total std={total_std:.6f} unique={total_unique}")
    print(f"[DIAG] pred_margin_home top5={margin.value_counts(dropna=False).head(5).to_dict()}")
    print(f"[DIAG] pred_total top5={total.value_counts(dropna=False).head(5).to_dict()}")

    if n_rows == 0 or margin_std == 0.0 or total_std == 0.0 or margin_unique <= 1 or total_unique <= 1:
        raise RuntimeError(
            "Constant/invalid predictions detected. Inspect model artifacts (missing or fallback intercept-only), "
            "feature matrix variability, and all-zero/NaN features before prediction."
        )


def predict(cfg: PredictConfig) -> pd.DataFrame:
    margin_model = _load_model(cfg.margin_model_path)
    total_model = _load_model(cfg.total_model_path)
    df = _load_features(cfg.features_path)

    # Vectorized scoring
    margin_preds = _score_df(df, margin_model, cfg.margin_model_path)
    total_preds = _score_df(df, total_model, cfg.total_model_path)

    # Model version preference order: margin -> total -> default
    model_version = (
        str(margin_model.get("model_version") or "").strip()
        or str(total_model.get("model_version") or "").strip()
        or "ml-linear-v1"
    )

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

    out = _sort_for_determinism(out)

    # Deterministic row_hash (based on stable columns + model_version)
    # Use apply for clarity; performance is fine at typical daily volumes.
    out["row_hash"] = out.apply(
        lambda r: _row_hash_for_row(
            {
                "event_id": r.get("event_id"),
                "team_id_home": r.get("team_id_home"),
                "team_id_away": r.get("team_id_away"),
                "game_datetime_utc": r.get("game_datetime_utc"),
                "model_version": model_version,
            }
        ),
        axis=1,
    )

    cfg.out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(cfg.out_path, index=False)
    _emit_prediction_diagnostics(out)
    return out


def main() -> None:
    cfg = PredictConfig()
    predict(cfg)


if __name__ == "__main__":
    main()
