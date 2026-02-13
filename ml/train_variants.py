#!/usr/bin/env python3
"""
Train + evaluate variants of simple lookback ridge linear models and
50/50 ensemble combinations.

Key fixes vs prior version:
- train_ml_models.py no longer exports _fit_linear, uses _fit_model (ridge-first).
- Ensure X contains only numeric feature columns (exclude ids/datetime/targets).
- Standardize saved model filenames so ensemble scorer loads correctly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

from train_ml_models import (
    _coerce_and_sort_by_datetime,
    _fit_model,
)

from predict_ml import _load_features as _load_pred_features, _score_df, _require_model_fields


# ------------------------
# Config
# ------------------------

WINDOWS = [4, 5, 6, 7, 10, 12]
SUMMARY_PATH = Path("ml/variant_results.csv")
MODELS_DIR = Path("ml/models")


@dataclass
class VariantResult:
    variant: str
    window: str
    target: str
    train_rmse: float | None
    val_rmse: float | None
    rows_train: int
    rows_val: int
    ensemble: bool = False
    ensemble_mae: float | None = None
    ensemble_rmse: float | None = None


# ------------------------
# Helpers
# ------------------------

ID_COLS = {"event_id", "game_datetime_utc", "team_id_home", "team_id_away"}
TARGET_COLS = {"actual_margin_home", "actual_total"}


def _pick_features_for_window(df: pd.DataFrame, window: int) -> pd.DataFrame:
    """
    Keep only:
      - recency features matching *_l{window}_pre
      - *_season_pre
      - targets
      - id cols (used for joins/sorting only, excluded from X)
    """
    out = df.copy()
    cols = [
        c for c in out.columns
        if (f"_l{window}_pre" in c)
        or ("_season_pre" in c)
        or (c in TARGET_COLS)
        or (c in ID_COLS)
    ]
    return out[cols]


def _feature_cols_only(df: pd.DataFrame) -> List[str]:
    """
    Numeric feature columns only (exclude ids + targets).
    """
    return [c for c in df.columns if c not in ID_COLS and c not in TARGET_COLS]


def _save_model_json(model: Dict[str, object], fname: str) -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    (MODELS_DIR / fname).write_text(json.dumps(model, indent=2) + "\n")


def _rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.sqrt(np.mean((y_pred - y_true) ** 2)))


def _mae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.mean(np.abs(y_pred - y_true)))


def _model_filename(variant: str, target_short: str) -> str:
    """
    Standard filenames:
      - margin_{variant}.json
      - total_{variant}.json
    """
    if target_short not in {"margin", "total"}:
        raise ValueError(f"Unknown target_short: {target_short}")
    return f"{target_short}_{variant}.json"


# ------------------------
# Main logic
# ------------------------

def train_and_eval_variants() -> List[VariantResult]:
    df_raw = pd.read_csv("ml/model_features.csv")
    df_raw = _coerce_and_sort_by_datetime(df_raw)

    results: List[VariantResult] = []

    # Train single variants
    for w in WINDOWS:
        var_name = f"l{w}_simple"
        print(f"[INFO] Training variant {var_name}")

        df_feat = _pick_features_for_window(df_raw, w)
        df_feat = df_feat.sort_values("game_datetime_utc", kind="mergesort")

        feat_cols = _feature_cols_only(df_feat)
        if not feat_cols:
            print(f"[WARN] No feature columns found for window {w}. Skipping.")
            continue

        # Train margin + total
        for target_col, short in [("actual_margin_home", "margin"), ("actual_total", "total")]:
            if target_col not in df_feat.columns:
                print(f"[WARN] Missing target {target_col}. Skipping {var_name}/{short}.")
                continue

            # Coerce numeric like train_ml_models.py
            Xdf = df_feat[feat_cols].apply(pd.to_numeric, errors="coerce")
            y = pd.to_numeric(df_feat[target_col], errors="coerce").to_numpy(dtype=np.float64)

            X = Xdf.to_numpy(dtype=np.float64)

            coef, train_rmse, keep_mask, medians_full, dropped_const_idx, rows_train_clean = _fit_model(
                X,
                y,
                ridge_lambda=1e-3,
                min_train_rows=25,
            )

            # Save JSON model
            model_data = {
                "target": target_col,
                "model_version": var_name,
                "trained_at_utc": "",
                "intercept": float(coef[0]),
                "coefficients": [float(c) for c in coef[1:]],
                "feature_order": list(feat_cols),
                "feature_medians": {c: float(m) for c, m in zip(feat_cols, medians_full)},
                "dropped_const_features": [feat_cols[i] for i in dropped_const_idx] if dropped_const_idx else [],
                "rmse": float(train_rmse),
                "val_rmse": None,
            }

            fname = _model_filename(var_name, short)
            _save_model_json(model_data, fname)

            results.append(
                VariantResult(
                    variant=var_name,
                    window=str(w),
                    target=target_col,
                    train_rmse=float(train_rmse),
                    val_rmse=None,
                    rows_train=rows_train_clean,
                    rows_val=0,
                )
            )

    # Ensemble evaluation
    print("[INFO] Evaluating ensembles...")
    pred_df = _load_pred_features(Path("ml/model_features.csv"))

    # Score all single models
    score_cache: Dict[str, np.ndarray] = {}
    for w in WINDOWS:
        var_name = f"l{w}_simple"
        for short in ["margin", "total"]:
            model_fname = _model_filename(var_name, short)
            model_path = MODELS_DIR / model_fname
            if not model_path.exists():
                continue

            model_json = json.loads(model_path.read_text())
            _require_model_fields(model_json, model_path)

            preds = _score_df(pred_df, model_json, model_path)
            score_cache[f"{var_name}_{short}"] = np.asarray(preds, dtype=np.float64)

    # Build ensembles for pairs at least 2 apart
    pairs = [p for p in combinations(WINDOWS, 2) if abs(p[1] - p[0]) >= 2]
    for w1, w2 in pairs:
        ensemble_name = f"ensemble_l{w1}_l{w2}"

        for short, actual_col in [("margin", "actual_margin_home"), ("total", "actual_total")]:
            key1 = f"l{w1}_simple_{short}"
            key2 = f"l{w2}_simple_{short}"
            if key1 not in score_cache or key2 not in score_cache:
                continue
            if actual_col not in pred_df.columns:
                continue

            p1 = score_cache[key1]
            p2 = score_cache[key2]
            avg_pred = 0.5 * p1 + 0.5 * p2

            actual = pd.to_numeric(pred_df[actual_col], errors="coerce").to_numpy(dtype=np.float64)
            valid = np.isfinite(actual)

            if not valid.any():
                continue

            mae = _mae(actual[valid], avg_pred[valid])
            rmse_val = _rmse(actual[valid], avg_pred[valid])

            results.append(
                VariantResult(
                    variant=ensemble_name,
                    window=f"{w1},{w2}",
                    target=actual_col,
                    train_rmse=None,
                    val_rmse=None,
                    rows_train=0,
                    rows_val=0,
                    ensemble=True,
                    ensemble_mae=float(mae),
                    ensemble_rmse=float(rmse_val),
                )
            )

    return results


def write_summary_csv(results: List[VariantResult]) -> None:
    records = []
    for r in results:
        rec = {
            "variant": r.variant,
            "window": r.window,
            "target": r.target,
            "train_rmse": r.train_rmse,
            "val_rmse": r.val_rmse,
            "rows_train": r.rows_train,
            "rows_val": r.rows_val,
        }
        if r.ensemble:
            rec["ensemble_mae"] = r.ensemble_mae
            rec["ensemble_rmse"] = r.ensemble_rmse
        records.append(rec)

    df = pd.DataFrame(records)
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(SUMMARY_PATH, index=False)
    print(f"[INFO] Wrote summary to {SUMMARY_PATH}")


def main() -> None:
    results = train_and_eval_variants()
    write_summary_csv(results)


if __name__ == "__main__":
    main()
