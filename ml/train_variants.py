#!/usr/bin/env python3
"""
Train + evaluate variants of simple lookback linear models and
50/50 ensemble combinations.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

# Import your existing train and predict helpers
from train_ml_models import (
    _load_features,
    _coerce_and_sort_by_datetime,
    _select_feature_cols,
    _fit_linear,
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
    train_rmse: float
    val_rmse: float
    rows_train: int
    rows_val: int
    ensemble: bool = False
    ensemble_mae: float = None
    ensemble_rmse: float = None


# ------------------------
# Helpers
# ------------------------

def _pick_features_for_window(df: pd.DataFrame, window: int) -> pd.DataFrame:
    """
    Keep only:
      - recency features matching *_l{window}_pre
      - *_season_pre
      - actual target columns
      - event_id, datetime, team ids
    """
    out = df.copy()
    cols = [
        c for c in out.columns
        if (f"_l{window}_pre" in c)
        or ("_season_pre" in c)
        or (c.startswith("actual_"))
        or (c in ["event_id", "game_datetime_utc", "team_id_home", "team_id_away"])
    ]
    return out[cols]


def _save_model_json(model: Dict[str, object], fname: str) -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    (MODELS_DIR / fname).write_text(json.dumps(model, indent=2))


def _rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.sqrt(np.mean((y_pred - y_true) ** 2)))


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

        # Train margin and total
        for target in ["actual_margin_home", "actual_total"]:
            X = df_feat.drop(columns=[target], errors="ignore")
            y = df_feat[target].to_numpy(dtype=float)

            # Fit
            coef, train_rmse, keep_mask, medians_full, dropped_const_idx, rows_train_clean = _fit_linear(
                X.to_numpy(dtype=np.float64),
                y,
                ridge_lambda=1e-3,
                min_train_rows=10,
            )

            # Save JSON
            model_data = {
                "target": target,
                "model_version": var_name,
                "trained_at_utc": "",
                "intercept": float(coef[0]),
                "coefficients": [float(c) for c in coef[1:]],
                "feature_order": list(X.columns),
                "feature_medians": {c: float(m) for c, m in zip(X.columns, medians_full)},
                "dropped_const_features": [],
                "rmse": float(train_rmse),
                "val_rmse": None,
            }
            fname = f"{target.replace('actual_','')}_{var_name}.json"
            _save_model_json(model_data, fname)

            results.append(
                VariantResult(
                    variant=var_name,
                    window=str(w),
                    target=target,
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
    score_cache: Dict[str, pd.DataFrame] = {}
    for w in WINDOWS:
        var_name = f"l{w}_simple"
        for target in ["margin", "total"]:
            model_fname = f"{target}_l{w}_simple.json"
            model_path = MODELS_DIR / model_fname
            model_json = json.loads(model_path.read_text())
            _require_model_fields(model_json, model_path)

            preds = _score_df(pred_df, model_json, model_path)
            score_cache[f"{var_name}_{target}"] = preds

    # Build ensembles for pairs at least 2 apart
    pairs = [p for p in combinations(WINDOWS, 2) if abs(p[1] - p[0]) >= 2]
    for w1, w2 in pairs:
        ensemble_name = f"ensemble_l{w1}_l{w2}"

        for target_tag in [("margin", "actual_margin_home"), ("total", "actual_total")]:
            short_tgt, actual_col = target_tag
            key1 = f"l{w1}_simple_{short_tgt}"
            key2 = f"l{w2}_simple_{short_tgt}"
            if key1 not in score_cache or key2 not in score_cache:
                continue

            p1 = score_cache[key1]
            p2 = score_cache[key2]

            avg_pred = 0.5 * p1 + 0.5 * p2
            actual = pred_df[actual_col].to_numpy(dtype=float)

            valid = ~np.isnan(actual)
            mae = float(np.mean(np.abs(avg_pred[valid] - actual[valid])))
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
                    ensemble_mae=mae,
                    ensemble_rmse=rmse_val,
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
