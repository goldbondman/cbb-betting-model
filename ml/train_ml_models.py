#!/usr/bin/env python3
"""
Train ridge linear models (margin + total) from model_features.csv.

Outputs:
  - ml/models/margin_model.json
  - ml/models/total_model.json

Notes:
  - Ridge-first for stability (intercept not regularized).
  - Time-series split after stable sort by game_datetime_utc.
  - Stores feature medians for predict-time imputation.
  - Stores dropped constant features + train window metadata.
  - ✅ ADDED: Comprehensive validation to prevent constant predictions
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
    ridge_lambda: float = float(os.getenv("ML_RIDGE_LAMBDA", "1e-2"))
    min_train_rows: int = int(os.getenv("ML_MIN_TRAIN_ROWS", "25"))
    max_nan_pct: float = float(os.getenv("ML_MAX_NAN_PCT", "0.80"))  # ✅ NEW
    min_feature_variance: float = float(os.getenv("ML_MIN_FEATURE_VARIANCE", "0.01"))  # ✅ NEW
    debug: bool = os.getenv("ML_DEBUG", "0").strip().lower() in ("1", "true", "yes")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_features(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing features file: {path}")
    return pd.read_csv(path)


def _coerce_and_sort_by_datetime(df: pd.DataFrame) -> pd.DataFrame:
    if "game_datetime_utc" not in df.columns:
        raise ValueError("Missing required column: game_datetime_utc")

    df = df.copy()

    if "event_id" not in df.columns:
        df["event_id"] = df.index.astype(str)

    dt = pd.to_datetime(df["game_datetime_utc"], errors="coerce", utc=True)
    df["_dt_sort"] = dt
    df["_event_sort"] = df["event_id"].astype(str)

    df = df.sort_values(
        ["_dt_sort", "_event_sort"],
        ascending=[True, True],
        na_position="last",
        kind="mergesort",
    ).drop(columns=["_dt_sort", "_event_sort"])

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
        "row_hash",
        "model_version",
        "home_score",  # ✅ NEW
        "away_score",  # ✅ NEW
    }
    cols = [c for c in df.columns if c not in ignore]

    # Leakage guard
    suspicious = [c for c in cols if c.lower().startswith("actual_")]
    if suspicious:
        cols = [c for c in cols if c not in suspicious]
        print(f"[WARN] Dropping suspicious feature columns: {suspicious}", file=sys.stderr)

    return cols


def _compute_feature_medians(X: np.ndarray) -> np.ndarray:
    med = np.zeros(X.shape[1], dtype=np.float64)
    if X.shape[1] == 0:
        return med
    col_has_finite = np.isfinite(X).any(axis=0)
    if col_has_finite.any():
        med[col_has_finite] = np.nanmedian(X[:, col_has_finite], axis=0)
    med[~np.isfinite(med)] = 0.0
    return med


def _sanitize_xy(
    X: np.ndarray,
    y: np.ndarray,
    *,
    drop_const_cols: bool = True,
    max_row_nan_pct: float = 0.50,  # ✅ NEW: drop rows with >50% NaN
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, List[int], int]:
    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64).reshape(-1)

    X[~np.isfinite(X)] = np.nan
    y[~np.isfinite(y)] = np.nan

    # ✅ IMPROVED: Require at least 50% of features to be non-NaN
    row_ok = np.isfinite(y)
    features_ok_per_row = np.isfinite(X).sum(axis=1)
    row_ok = row_ok & (features_ok_per_row >= X.shape[1] * (1 - max_row_nan_pct))
    
    rows_before = len(X)
    X = X[row_ok]
    y = y[row_ok]
    n_rows_clean = int(X.shape[0])
    
    if n_rows_clean < rows_before:
        print(f"[INFO] Sanitize: {rows_before} → {n_rows_clean} rows (dropped {rows_before - n_rows_clean} with >{max_row_nan_pct:.0%} NaN)", file=sys.stderr)

    if X.size == 0 or y.size == 0:
        keep_mask = np.zeros((X.shape[1],), dtype=bool) if X.ndim == 2 else np.zeros((0,), dtype=bool)
        return X, y, keep_mask, np.zeros((X.shape[1],), dtype=np.float64), [], n_rows_clean

    medians_full = _compute_feature_medians(X)

    # ✅ IMPROVED: Report imputation stats
    if np.isnan(X).any():
        nan_count = np.isnan(X).sum()
        total_count = X.size
        nan_idx = np.where(np.isnan(X))
        X[nan_idx] = medians_full[nan_idx[1]]
        print(f"[INFO] Imputed {nan_count}/{total_count} ({nan_count/total_count:.1%}) NaN values with feature medians", file=sys.stderr)

    keep_mask = np.ones(X.shape[1], dtype=bool)
    dropped_const_idx: List[int] = []

    if drop_const_cols and X.shape[1] > 0 and X.shape[0] > 1:
        std = X.std(axis=0)
        keep_mask = std > 0
        dropped_const_idx = [int(i) for i in np.where(~keep_mask)[0].tolist()]
        if dropped_const_idx:
            print(f"[INFO] Dropped {len(dropped_const_idx)} constant features (std=0)", file=sys.stderr)
        X = X[:, keep_mask]

    return X, y, keep_mask, medians_full, dropped_const_idx, n_rows_clean


def _ridge_fit_intercept_unpenalized(X: np.ndarray, y: np.ndarray, lam: float) -> np.ndarray:
    """
    Fit ridge with intercept, intercept not regularized.

    Returns coef vector of length (p+1): [intercept, w...]
    """
    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64).reshape(-1)

    n, p = X.shape
    X_aug = np.column_stack([np.ones(n, dtype=np.float64), X])

    reg = lam * np.eye(p + 1, dtype=np.float64)
    reg[0, 0] = 0.0  # do not penalize intercept

    A = X_aug.T @ X_aug + reg
    b = X_aug.T @ y
    return np.linalg.solve(A, b)


def _fit_model(
    X: np.ndarray,
    y: np.ndarray,
    *,
    ridge_lambda: float,
    min_train_rows: int,
) -> Tuple[np.ndarray, float, np.ndarray, np.ndarray, List[int], int]:
    X_clean, y_clean, keep_mask, medians_full, dropped_const_idx, rows_train_clean = _sanitize_xy(
        X, y, drop_const_cols=True
    )

    if X_clean.shape[0] < min_train_rows:
        raise ValueError(
            f"Not enough clean training data: rows={X_clean.shape[0]} cols={X_clean.shape[1]} "
            f"(min_train_rows={min_train_rows})"
        )

    # Intercept-only fallback
    if X_clean.shape[1] == 0:
        mean_y = float(np.mean(y_clean))
        full_coef = np.zeros(X.shape[1] + 1, dtype=np.float64)
        full_coef[0] = mean_y
        rmse = float(np.sqrt(np.mean((y_clean - mean_y) ** 2)))
        print("[WARN] All features dropped; using intercept-only model.", file=sys.stderr)
        return full_coef, rmse, keep_mask, medians_full, dropped_const_idx, rows_train_clean

    lam = float(max(0.0, ridge_lambda))
    coef_small = _ridge_fit_intercept_unpenalized(X_clean, y_clean, lam=lam)

    preds = np.column_stack([np.ones(X_clean.shape[0]), X_clean]) @ coef_small
    rmse = float(np.sqrt(np.mean((preds - y_clean) ** 2)))

    # Expand back to original feature space
    full_coef = np.zeros(X.shape[1] + 1, dtype=np.float64)
    full_coef[0] = coef_small[0]
    if keep_mask.size > 0 and coef_small.size > 1:
        full_coef[1:][keep_mask] = coef_small[1:]

    return full_coef, rmse, keep_mask, medians_full, dropped_const_idx, rows_train_clean


def _rmse_from_coef(
    X: np.ndarray,
    y: np.ndarray,
    coef: np.ndarray,
    *,
    medians_full: Optional[np.ndarray] = None,
) -> float:
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


def _read_feature_schema_hash() -> Optional[str]:
    p = Path("ml/feature_schema_hash.txt")
    if not p.exists():
        return None
    val = (p.read_text() or "").strip()
    return val or None


def _validate_training_data(df: pd.DataFrame, feature_cols: List[str], cfg: TrainConfig) -> None:
    """
    ✅ NEW: Validate training data before attempting to train models.
    """
    print("\n[INFO] ============ TRAINING DATA VALIDATION ============")
    
    # Check targets exist
    for target in ["actual_margin_home", "actual_total"]:
        if target not in df.columns:
            raise ValueError(f"Missing required target column: {target}")
    
    # Check target variance
    for target in ["actual_margin_home", "actual_total"]:
        target_vals = pd.to_numeric(df[target], errors="coerce")
        mean = float(target_vals.mean())
        std = float(target_vals.std())
        
        print(f"[INFO] {target}:")
        print(f"       Mean: {mean:.2f}")
        print(f"       Std:  {std:.2f}")
        print(f"       Min:  {target_vals.min():.2f}")
        print(f"       Max:  {target_vals.max():.2f}")
        
        if std < cfg.min_feature_variance:
            raise ValueError(f"Target {target} has insufficient variance (std={std:.4f} < {cfg.min_feature_variance})")
        
        # ✅ CRITICAL: Check for suspicious constant mean
        if abs(mean - 24.04) < 0.5 and std < 5.0:
            print(f"[ERROR] Target {target} mean={mean:.2f} is suspiciously close to 24.04!", file=sys.stderr)
            print(f"[ERROR] This suggests the model might be predicting constants.", file=sys.stderr)
            print(f"[ERROR] Check feature_matrix.py for bugs in target calculation.", file=sys.stderr)
    
    # Check feature NaN percentage
    if feature_cols:
        nan_pct = df[feature_cols].isnull().mean().mean()
        print(f"\n[INFO] Feature columns: {len(feature_cols)}")
        print(f"[INFO] Feature NaN%: {nan_pct:.1%}")
        
        if nan_pct > cfg.max_nan_pct:
            print(f"[ERROR] >80% of features are NaN - check feature_matrix.py", file=sys.stderr)
            print(f"[ERROR] Top 10 worst features:", file=sys.stderr)
            worst = df[feature_cols].isnull().mean().sort_values(ascending=False).head(10)
            for col, pct in worst.items():
                print(f"        {col}: {pct:.1%}", file=sys.stderr)
            raise ValueError(f"Too many NaN features ({nan_pct:.1%} > {cfg.max_nan_pct:.1%})")
    
    print("[INFO] ✅ Training data validation passed")
    print("[INFO] ================================================\n")


def train_models(cfg: TrainConfig) -> Dict[str, Dict[str, object]]:
    df = _load_features(cfg.features_path)
    df = _coerce_and_sort_by_datetime(df)

    feature_cols = _select_feature_cols(df)
    if not feature_cols:
        raise ValueError("No feature columns available for training.")

    # ✅ NEW: Validate data before training
    _validate_training_data(df, feature_cols, cfg)

    val_ratio = max(0.0, min(0.5, float(cfg.val_split)))
    split_cfg = SplitConfig(val_ratio=val_ratio, test_ratio=0.0)
    train_df, val_df, _ = time_series_split(df, split_cfg)

    # Train window metadata
    train_dt = pd.to_datetime(train_df["game_datetime_utc"], utc=True, errors="coerce")
    train_start_utc = train_dt.min()
    train_end_utc = train_dt.max()

    # Coerce feature matrix without crashing on strings
    X_train = train_df[feature_cols].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=np.float64)
    X_val = (
        val_df[feature_cols].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=np.float64)
        if len(val_df) > 0
        else None
    )

    rows_total = int(len(df))
    rows_train = int(len(train_df))
    rows_val = int(len(val_df))

    if cfg.debug:
        non_finite = int((~np.isfinite(X_train)).sum())
        print(f"[DEBUG] X_train shape={X_train.shape} non_finite={non_finite}")

    results: Dict[str, Dict[str, object]] = {}
    schema_hash = _read_feature_schema_hash()

    for target, fname in [
        ("actual_margin_home", "margin_model.json"),
        ("actual_total", "total_model.json"),
    ]:
        print(f"\n[INFO] ============ TRAINING {target.upper()} ============")
        
        if target not in train_df.columns:
            raise ValueError(f"Missing required target column: {target}")

        y_train = pd.to_numeric(train_df[target], errors="coerce").to_numpy(dtype=np.float64)
        
        # ✅ NEW: Per-target validation
        y_mean = float(np.nanmean(y_train))
        y_std = float(np.nanstd(y_train))
        
        print(f"[INFO] Training set:")
        print(f"       Rows: {rows_train}")
        print(f"       Features: {len(feature_cols)}")
        print(f"       Target mean: {y_mean:.2f}")
        print(f"       Target std: {y_std:.2f}")
        
        if y_std < cfg.min_feature_variance:
            raise ValueError(f"Target {target} has no variance in training set (std={y_std:.4f})")

        coef, train_rmse, keep_mask, medians_full, dropped_const_idx, rows_train_clean = _fit_model(
            X_train,
            y_train,
            ridge_lambda=cfg.ridge_lambda,
            min_train_rows=cfg.min_train_rows,
        )
        
        print(f"[INFO] Model trained:")
        print(f"       RMSE: {train_rmse:.2f}")
        print(f"       Features used: {int(np.sum(keep_mask))}/{len(feature_cols)}")
        print(f"       Rows cleaned: {rows_train_clean}/{rows_train}")

        val_rmse = None
        if rows_val > 0 and X_val is not None:
            y_val = pd.to_numeric(val_df[target], errors="coerce").to_numpy(dtype=np.float64)
            val_rmse = _rmse_from_coef(X_val, y_val, coef, medians_full=medians_full)
            print(f"       Val RMSE: {val_rmse:.2f}")

        dropped_const_features = [feature_cols[i] for i in dropped_const_idx] if dropped_const_idx else []

        feature_medians: Dict[str, float] = {}
        for i, col in enumerate(feature_cols):
            v = float(medians_full[i]) if i < medians_full.shape[0] else 0.0
            if not np.isfinite(v):
                v = 0.0
            feature_medians[col] = v

        model = {
            "target": target,
            "model_version": cfg.model_version,
            "trained_at_utc": _utc_now_iso(),
            "feature_schema_hash": schema_hash,
            "train_start_utc": (train_start_utc.isoformat() if pd.notna(train_start_utc) else None),
            "train_end_utc": (train_end_utc.isoformat() if pd.notna(train_end_utc) else None),

            "intercept": float(coef[0]),
            "coefficients": [float(c) for c in coef[1:]],
            "feature_order": feature_cols,

            "rmse": float(train_rmse),
            "val_rmse": (float(val_rmse) if val_rmse is not None else None),

            "rows_total": rows_total,
            "rows_train": rows_train,
            "rows_train_clean": int(rows_train_clean),
            "rows_val": rows_val,

            "ridge_lambda": float(cfg.ridge_lambda),
            "val_split": float(val_ratio),
            "min_train_rows": int(cfg.min_train_rows),

            "n_features_total": int(len(feature_cols)),
            "n_features_used": int(np.sum(keep_mask)) if keep_mask.size else int(len(feature_cols)),
            "dropped_const_features": dropped_const_features,

            "feature_medians": feature_medians,
            "split_type": "time_series",
            "sort_key": "game_datetime_utc",
        }

        results[target] = model

        cfg.out_dir.mkdir(parents=True, exist_ok=True)
        (cfg.out_dir / fname).write_text(json.dumps(model, indent=2) + "\n")
        
        print(f"[INFO] ✅ Model saved to {cfg.out_dir / fname}")
        print("[INFO] ================================================\n")

    return results


def main() -> None:
    cfg = TrainConfig()
    train_models(cfg)


if __name__ == "__main__":
    main()
