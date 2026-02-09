#!/usr/bin/env python3
"""
Train simple linear models (margin + total) from model_features.csv.

Outputs:
  - ml/models/margin_model.json
  - ml/models/total_model.json

Notes:
  - Uses numpy least squares (no external ML deps).
  - Stores coefficients, feature order, and training metadata.

Hardening:
  - Sanitizes NaN/inf
  - Drops constant columns to reduce ill-conditioning
  - Ridge fallback if SVD fails to converge
  - Keeps coefficient vector aligned to feature_order

Updates in this version:
  - Explicit sort by game_datetime_utc before time-series split
  - Correct row counts (total/train/val/clean)
  - Store feature medians used for imputation (predict should reuse)
  - Record dropped constant features
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple, Optional

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
    ridge_lambda: float = float(os.getenv("ML_RIDGE_LAMBDA", "1e-3"))
    min_train_rows: int = int(os.getenv("ML_MIN_TRAIN_ROWS", "10"))
    debug: bool = os.getenv("ML_DEBUG", "0").strip().lower() in ("1", "true", "yes")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_features(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing features file: {path}")
    return pd.read_csv(path)


def _coerce_and_sort_by_datetime(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ensures game_datetime_utc exists and sorts ascending for time-series split.
    If parse fails, falls back to stable sort by string.
    """
    if "game_datetime_utc" not in df.columns:
        raise ValueError("Missing required column: game_datetime_utc")

    dt = pd.to_datetime(df["game_datetime_utc"], errors="coerce", utc=True)
    # Put NaT last, keep deterministic order
    df = df.copy()
    df["_dt_sort"] = dt
    df = df.sort_values(["_dt_sort", "event_id"], ascending=[True, True], na_position="last")
    df = df.drop(columns=["_dt_sort"])
    return df


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
    cols = [c for c in df.columns if c not in ignore]
    # Optional cheap leakage guard: drop any accidental target-ish columns
    suspicious = [c for c in cols if c.lower().startswith("actual_")]
    if suspicious:
        cols = [c for c in cols if c not in suspicious]
        print(f"[WARN] Dropping suspicious feature columns: {suspicious}", file=sys.stderr)
    return cols


def _compute_feature_medians(X: np.ndarray) -> np.ndarray:
    """
    Column medians over finite values. All-non-finite column -> 0.0.
    Assumes X already has inf->nan applied.
    """
    med = np.zeros(X.shape[1], dtype=np.float64)
    if X.shape[1] == 0:
        return med
    col_has_finite = np.isfinite(X).any(axis=0)
    if col_has_finite.any():
        med[col_has_finite] = np.nanmedian(X[:, col_has_finite], axis=0)
    return med


def _sanitize_xy(
    X: np.ndarray,
    y: np.ndarray,
    *,
    drop_const_cols: bool = True,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, List[int], int]:
    """
    Returns:
      X_clean, y_clean, keep_cols_mask, medians_full, dropped_const_idx, n_rows_clean

    Behavior:
      - forces float64
      - converts inf -> nan
      - drops rows where y is non-finite or all X features are non-finite
      - imputes remaining NaNs in X with column medians (all-NaN col -> 0.0)
      - optionally drops constant columns (std=0) to improve conditioning
    """
    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64).reshape(-1)

    # normalize non-finite
    X[~np.isfinite(X)] = np.nan
    y[~np.isfinite(y)] = np.nan

    # keep rows where y is finite and at least one feature is finite
    row_ok = np.isfinite(y) & np.isfinite(X).any(axis=1)
    X = X[row_ok]
    y = y[row_ok]

    n_rows_clean = int(X.shape[0])

    if X.size == 0 or y.size == 0:
        keep_mask = np.zeros((X.shape[1],), dtype=bool) if X.ndim == 2 else np.zeros((0,), dtype=bool)
        return X, y, keep_mask, np.zeros((X.shape[1],), dtype=np.float64), [], n_rows_clean

    medians_full = _compute_feature_medians(X)

    # impute NaNs in X with precomputed medians
    if np.isnan(X).any():
        nan_idx = np.where(np.isnan(X))
        X[nan_idx] = medians_full[nan_idx[1]]

    keep_mask = np.ones(X.shape[1], dtype=bool)
    dropped_const_idx: List[int] = []

    if drop_const_cols and X.shape[1] > 0 and X.shape[0] > 1:
        std = X.std(axis=0)
        keep_mask = std > 0
        dropped_const_idx = [int(i) for i in np.where(~keep_mask)[0].tolist()]
        X = X[:, keep_mask]

    return X, y, keep_mask, medians_full, dropped_const_idx, n_rows_clean


def _fit_linear(
    X: np.ndarray,
    y: np.ndarray,
    *,
    ridge_lambda: float = 1e-3,
    min_train_rows: int = 10,
) -> Tuple[np.ndarray, float, np.ndarray, np.ndarray, List[int], int]:
    """
    Fits linear regression with intercept.
    - lstsq primary
    - ridge fallback if SVD fails
    - returns:
        full_coef (intercept + coef per ORIGINAL X columns),
        train_rmse,
        keep_mask (const cols kept),
        medians_full (per ORIGINAL col),
        dropped_const_idx,
        n_rows_clean (rows after dropping bad y/all-bad X)
    """
    X_clean, y_clean, keep_mask, medians_full, dropped_const_idx, n_rows_clean = _sanitize_xy(
        X, y, drop_const_cols=True
    )

    if X_clean.shape[0] < min_train_rows:
        raise ValueError(
            f"Not enough clean training data after sanitize: rows={X_clean.shape[0]} cols={X_clean.shape[1]}"
        )

    if X_clean.shape[1] == 0:
        mean_y = float(np.mean(y_clean)) if y_clean.size > 0 else 0.0
        full_coef = np.zeros(X.shape[1] + 1, dtype=np.float64)
        full_coef[0] = mean_y
        rmse = float(np.sqrt(np.mean((y_clean - mean_y) ** 2)))
        print("[WARN] All features dropped after sanitize; using intercept-only model.", file=sys.stderr)
        return full_coef, rmse, keep_mask, medians_full, dropped_const_idx, n_rows_clean

    X_aug = np.column_stack([np.ones(X_clean.shape[0], dtype=np.float64), X_clean])

    if not np.isfinite(X_aug).all() or not np.isfinite(y_clean).all():
        raise ValueError("Non-finite values remain after sanitize (unexpected).")

    try:
        coef_small, *_ = np.linalg.lstsq(X_aug, y_clean, rcond=None)
    except np.linalg.LinAlgError:
        lam = float(ridge_lambda)
        A = X_aug.T @ X_aug + lam * np.eye(X_aug.shape[1], dtype=np.float64)
        b = X_aug.T @ y_clean
        coef_small = np.linalg.solve(A, b)

    preds = X_aug @ coef_small
    rmse = float(np.sqrt(np.mean((preds - y_clean) ** 2)))

    # Expand back to original feature space so coef aligns with feature_order
    full_coef = np.zeros(X.shape[1] + 1, dtype=np.float64)  # intercept + features
    full_coef[0] = coef_small[0]
    if keep_mask.size > 0:
        full_coef[1:][keep_mask] = coef_small[1:]

    return full_coef, rmse, keep_mask, medians_full, dropped_const_idx, n_rows_clean


def _rmse_from_coef(
    X: np.ndarray,
    y: np.ndarray,
    coef: np.ndarray,
    *,
    medians_full: Optional[np.ndarray] = None,
) -> float:
    """
    RMSE with the SAME sanitation assumptions as training:
      - drops rows where y non-finite or all X non-finite
      - imputes remaining X NaNs with column medians (provided or computed)
    """
    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64).reshape(-1)

    X[~np.isfinite(X)] = np.nan
    y[~np.isfinite(y)] = np.nan

    row_ok = np.isfinite(y) & np.isfinite(X).any(axis=1)
    X = X[row_ok]
    y = y[row_ok]

    if X.size == 0:
        return float("nan")

    if medians_full is None:
        medians_full = _compute_feature_medians(X)

    if np.isnan(X).any():
        nan_idx = np.where(np.isnan(X))
        X[nan_idx] = medians_full[nan_idx[1]]

    X_aug = np.column_stack([np.ones(X.shape[0], dtype=np.float64), X])
    preds = X_aug @ coef
    return float(np.sqrt(np.mean((preds - y) ** 2)))


def train_models(cfg: TrainConfig) -> Dict[str, Dict[str, object]]:
    df = _load_features(cfg.features_path)
    df = _coerce_and_sort_by_datetime(df)

    feature_cols = _select_feature_cols(df)
    if not feature_cols:
        raise ValueError("No feature columns available for training.")

    # time-series split (assumes df already sorted)
    val_ratio = max(0.0, min(0.5, float(cfg.val_split)))
    split_cfg = SplitConfig(val_ratio=val_ratio, test_ratio=0.0)
    train_df, val_df, _ = time_series_split(df, split_cfg)

    # matrices
    X_train = train_df[feature_cols].astype(float).to_numpy()
    X_val = val_df[feature_cols].astype(float).to_numpy() if len(val_df) > 0 else None

    rows_total = int(len(df))
    rows_train = int(len(train_df))
    rows_val = int(len(val_df))

    if cfg.debug:
        non_finite = int((~np.isfinite(X_train)).sum())
        print(f"[DEBUG] X_train shape={X_train.shape} non_finite={non_finite}")

    results: Dict[str, Dict[str, object]] = {}

    for target, fname in [
        ("actual_margin_home", "margin_model.json"),
        ("actual_total", "total_model.json"),
    ]:
        y_train = train_df[target].astype(float).to_numpy()

        coef, train_rmse, keep_mask, medians_full, dropped_const_idx, rows_train_clean = _fit_linear(
            X_train,
            y_train,
            ridge_lambda=cfg.ridge_lambda,
            min_train_rows=cfg.min_train_rows,
        )

        val_rmse = None
        if rows_val > 0 and X_val is not None:
            y_val = val_df[target].astype(float).to_numpy()
            val_rmse = _rmse_from_coef(X_val, y_val, coef, medians_full=medians_full)

        dropped_const_features = [feature_cols[i] for i in dropped_const_idx] if dropped_const_idx else []

        model = {
            "target": target,
            "model_version": cfg.model_version,
            "trained_at_utc": _utc_now_iso(),
            "intercept": float(coef[0]),
            "coefficients": [float(c) for c in coef[1:]],
            "feature_order": feature_cols,
            # metrics
            "rmse": float(train_rmse),
            "val_rmse": (float(val_rmse) if val_rmse is not None else None),
            # counts
            "rows_total": rows_total,
            "rows_train": rows_train,
            "rows_train_clean": int(rows_train_clean),
            "rows_val": rows_val,
            # training params
            "ridge_lambda": float(cfg.ridge_lambda),
            "val_split": float(val_ratio),
            "min_train_rows": int(cfg.min_train_rows),
            # sanitation metadata for predict to reuse
            "feature_medians": {feature_cols[i]: float(medians_full[i]) for i in range(len(feature_cols))},
            "dropped_const_features": dropped_const_features,
            # split assumptions
            "split_type": "time_series",
            "sort_key": "game_datetime_utc",
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
