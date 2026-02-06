#!/usr/bin/env python3
"""
Grid-search ensemble optimizer for weighted averages.

Example:
  python ml/optimize_ensemble.py \
    --input ml/predictions_latest.csv \
    --model-cols model_a_pred,model_b_pred \
    --target-col actual_margin_home \
    --metric mae \
    --step 0.05
"""

from __future__ import annotations

import argparse
import json
from itertools import product
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd


def _parse_cols(text: str) -> List[str]:
    return [c.strip() for c in text.split(",") if c.strip()]


def _generate_weights(n: int, step: float) -> List[Tuple[float, ...]]:
    grid = np.arange(0.0, 1.0 + 1e-9, step)
    combos = []
    for weights in product(grid, repeat=n):
        if abs(sum(weights) - 1.0) <= 1e-6:
            combos.append(tuple(float(w) for w in weights))
    return combos


def _metric_mae(pred: np.ndarray, target: np.ndarray) -> float:
    return float(np.nanmean(np.abs(pred - target)))


def _metric_mse(pred: np.ndarray, target: np.ndarray) -> float:
    return float(np.nanmean((pred - target) ** 2))


def _metric_hit_rate(pred: np.ndarray, target: np.ndarray) -> float:
    return float(np.nanmean((pred > 0) == (target > 0)))


def _select_metric(name: str):
    if name == "mae":
        return _metric_mae, "min"
    if name == "mse":
        return _metric_mse, "min"
    if name == "hit_rate":
        return _metric_hit_rate, "max"
    raise ValueError("metric must be one of: mae, mse, hit_rate")


def optimize(
    df: pd.DataFrame,
    model_cols: List[str],
    target_col: str,
    metric: str,
    step: float,
) -> Dict[str, object]:
    for col in model_cols + [target_col]:
        if col not in df.columns:
            raise ValueError(f"Missing column: {col}")

    matrix = df[model_cols].to_numpy(dtype=float)
    target = df[target_col].to_numpy(dtype=float)

    metric_fn, mode = _select_metric(metric)
    best_score = None
    best_weights = None

    weights_grid = _generate_weights(len(model_cols), step)
    if not weights_grid:
        raise ValueError("No weight combinations generated; try a smaller --step.")

    for weights in weights_grid:
        pred = np.average(matrix, axis=1, weights=np.array(weights))
        score = metric_fn(pred, target)
        if best_score is None:
            best_score = score
            best_weights = weights
            continue
        if mode == "min" and score < best_score:
            best_score = score
            best_weights = weights
        if mode == "max" and score > best_score:
            best_score = score
            best_weights = weights

    return {
        "metric": metric,
        "score": best_score,
        "weights": {col: float(w) for col, w in zip(model_cols, best_weights)},
        "rows": int(len(df)),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to CSV with model columns + target.")
    parser.add_argument("--model-cols", required=True, help="Comma-separated list of model prediction columns.")
    parser.add_argument("--target-col", required=True, help="Target column name.")
    parser.add_argument("--metric", default="mae", choices=["mae", "mse", "hit_rate"])
    parser.add_argument("--step", type=float, default=0.05, help="Grid step (e.g., 0.05).")
    parser.add_argument("--out", default="ml/ensemble_weights.json", help="Output JSON path.")
    args = parser.parse_args()

    df = pd.read_csv(Path(args.input))
    model_cols = _parse_cols(args.model_cols)

    result = optimize(df, model_cols, args.target_col, args.metric, args.step)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
