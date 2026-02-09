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

Notes:
- This is a brute-force grid search. Complexity grows fast with:
    (#grid points per weight)^(n_models)
  Use fewer models, larger --step, or add --max-combos as a safety valve.
"""

from __future__ import annotations

import argparse
import json
import math
from itertools import product
from pathlib import Path
from typing import Dict, List, Tuple, Callable, Optional

import numpy as np
import pandas as pd


def _parse_cols(text: str) -> List[str]:
    if not text:
        return []
    return [c.strip() for c in str(text).split(",") if c.strip()]


def _is_finite_array(a: np.ndarray) -> np.ndarray:
    return np.isfinite(a)


def _generate_weights(n: int, step: float, *, tol: float = 1e-6, max_combos: Optional[int] = None) -> List[Tuple[float, ...]]:
    """
    Generate weight tuples of length n in [0,1] summing to 1 within tolerance.

    Safety:
    - max_combos: stop early after generating this many combos
    """
    if n <= 0:
        return []
    if not isinstance(step, (int, float)) or not math.isfinite(step) or step <= 0 or step > 1:
        raise ValueError("--step must be a finite float in (0, 1].")

    grid = np.arange(0.0, 1.0 + 1e-12, step, dtype=float)

    combos: List[Tuple[float, ...]] = []
    # Simple pruning: build n-1 weights, derive last weight = 1 - sum(prefix)
    # This reduces the search space by one dimension.
    if n == 1:
        return [(1.0,)]

    for prefix in product(grid, repeat=n - 1):
        s = float(sum(prefix))
        last = 1.0 - s
        if last < -tol or last > 1.0 + tol:
            continue
        # snap last to grid if close enough (keeps combos consistent w/ step)
        # but don't require exact grid membership; allow tolerance.
        if abs(round(last / step) * step - last) <= max(tol, 1e-12):
            last = round(last / step) * step
        if abs((s + last) - 1.0) <= tol and 0.0 - tol <= last <= 1.0 + tol:
            w = tuple(float(x) for x in prefix) + (float(last),)
            combos.append(w)
            if max_combos is not None and len(combos) >= max_combos:
                break

    return combos


def _metric_mae(pred: np.ndarray, target: np.ndarray) -> float:
    mask = _is_finite_array(pred) & _is_finite_array(target)
    if not np.any(mask):
        return float("nan")
    return float(np.mean(np.abs(pred[mask] - target[mask])))


def _metric_mse(pred: np.ndarray, target: np.ndarray) -> float:
    mask = _is_finite_array(pred) & _is_finite_array(target)
    if not np.any(mask):
        return float("nan")
    return float(np.mean((pred[mask] - target[mask]) ** 2))


def _metric_hit_rate(pred: np.ndarray, target: np.ndarray) -> float:
    mask = _is_finite_array(pred) & _is_finite_array(target)
    if not np.any(mask):
        return float("nan")
    return float(np.mean((pred[mask] > 0) == (target[mask] > 0)))


def _select_metric(name: str) -> Tuple[Callable[[np.ndarray, np.ndarray], float], str]:
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
    *,
    max_combos: Optional[int] = None,
) -> Dict[str, object]:
    if not model_cols:
        raise ValueError("No --model-cols provided.")
    if target_col not in df.columns:
        raise ValueError(f"Missing target column: {target_col}")
    for col in model_cols:
        if col not in df.columns:
            raise ValueError(f"Missing model column: {col}")

    # Coerce to numeric to avoid silent object dtypes
    matrix = df[model_cols].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    target = pd.to_numeric(df[target_col], errors="coerce").to_numpy(dtype=float)

    metric_fn, mode = _select_metric(metric)

    weights_grid = _generate_weights(len(model_cols), step, max_combos=max_combos)
    if not weights_grid:
        raise ValueError("No weight combinations generated; try a different --step or fewer models.")

    best_score: Optional[float] = None
    best_weights: Optional[Tuple[float, ...]] = None

    # Evaluate
    for weights in weights_grid:
        w = np.array(weights, dtype=float)
        # np.average will error if weights sum to 0; guard anyway
        if not np.isfinite(w).all() or w.sum() <= 0:
            continue

        pred = np.average(matrix, axis=1, weights=w)
        score = metric_fn(pred, target)

        if not math.isfinite(score):
            continue

        if best_score is None:
            best_score = score
            best_weights = weights
            continue

        if mode == "min" and score < best_score:
            best_score = score
            best_weights = weights
        elif mode == "max" and score > best_score:
            best_score = score
            best_weights = weights

    if best_score is None or best_weights is None:
        raise ValueError("All candidate weight sets produced non-finite scores. Check your data for NaNs.")

    return {
        "metric": metric,
        "mode": mode,
        "score": float(best_score),
        "weights": {col: float(w) for col, w in zip(model_cols, best_weights)},
        "rows": int(len(df)),
        "models": list(model_cols),
        "step": float(step),
        "max_combos": (int(max_combos) if max_combos is not None else None),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to CSV with model columns + target.")
    parser.add_argument("--model-cols", required=True, help="Comma-separated list of model prediction columns.")
    parser.add_argument("--target-col", required=True, help="Target column name.")
    parser.add_argument("--metric", default="mae", choices=["mae", "mse", "hit_rate"])
    parser.add_argument("--step", type=float, default=0.05, help="Grid step (e.g., 0.05).")
    parser.add_argument("--out", default="ml/ensemble_weights.json", help="Output JSON path.")
    parser.add_argument(
        "--max-combos",
        type=int,
        default=0,
        help="Safety cap on number of weight combos to evaluate (0 = no cap).",
    )
    args = parser.parse_args()

    df = pd.read_csv(Path(args.input))
    model_cols = _parse_cols(args.model_cols)

    max_combos = None if int(args.max_combos) <= 0 else int(args.max_combos)
    result = optimize(df, model_cols, args.target_col, args.metric, float(args.step), max_combos=max_combos)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
