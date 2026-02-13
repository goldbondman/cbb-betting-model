#!/usr/bin/env python3
"""
Full-season backtest + feature discovery (walk-forward).

Goal:
- Backtest margin + total models across the full season using walk-forward training.
- Discover what's most predictive via coefficient stability, correlation, and ablation.

Inputs:
- ml/model_features.csv  (from feature_matrix.py)
  Required columns:
    - game_datetime_utc
    - actual_margin_home
    - actual_total
    - feature columns (everything else except metadata)

Outputs (in ml/backtests_full_season/):
- walkforward_predictions.csv            (row-level preds + errors)
- walkforward_summary.csv                (overall + per-fold summary)
- feature_importance_margin.csv          (ranked feature importance for margin)
- feature_importance_total.csv           (ranked feature importance for total)
- ablation_margin_topN.csv               (drop-1 ablation for top N margin features)
- ablation_total_topN.csv                (drop-1 ablation for top N total features)
- data_coverage.json                     (sanity counts + exclusions)

Design:
- Walk-forward with expanding window.
- Ridge regression with intercept (intercept not regularized).
- Impute missing features with train medians.
- Drop constant features per fold (train-only decision).
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


# ----------------------------
# Config
# ----------------------------

@dataclass(frozen=True)
class BacktestSeasonConfig:
    features_path: Path = Path("ml/model_features.csv")
    out_dir: Path = Path("ml/backtests_full_season")

    # Walk-forward controls
    val_ratio: float = 0.10           # each fold tests on next val_ratio of remaining data
    min_train_rows: int = 500         # ensure enough training data early season
    n_folds: int = 6                  # number of walk-forward folds
    ridge_lambda: float = 1e-2        # ridge strength

    # Ablation controls
    ablation_top_n: int = 25          # test removing top N features per target

    # Optional date window
    start: Optional[str] = None       # YYYY-MM-DD
    end: Optional[str] = None         # YYYY-MM-DD

    debug: bool = False


# ----------------------------
# Helpers
# ----------------------------

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _write_json(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n")


def _coerce_datetime_utc(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, errors="coerce")


def _date_filter(df: pd.DataFrame, start: Optional[str], end: Optional[str]) -> pd.DataFrame:
    if "game_datetime_utc" not in df.columns:
        return df
    if start is None and end is None:
        return df

    out = df.copy()
    out["game_datetime_utc"] = _coerce_datetime_utc(out["game_datetime_utc"])

    if start:
        s = datetime.fromisoformat(start).date()
        out = out[out["game_datetime_utc"].dt.date >= s]
    if end:
        e = datetime.fromisoformat(end).date()
        out = out[out["game_datetime_utc"].dt.date <= e]
    return out


def _load_features(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing features file: {path}")
    df = pd.read_csv(path)

    if "game_datetime_utc" not in df.columns:
        raise ValueError("model_features.csv missing required column: game_datetime_utc")
    df["game_datetime_utc"] = _coerce_datetime_utc(df["game_datetime_utc"])

    # Deterministic sort
    if "event_id" not in df.columns:
        df["event_id"] = df.index.astype(str)

    df = df.sort_values(
        ["game_datetime_utc", "event_id"],
        ascending=[True, True],
        na_position="last",
        kind="mergesort",
    ).reset_index(drop=True)

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
        "verification_status",
        "home_score",
        "away_score",
    }
    cols = [c for c in df.columns if c not in ignore]

    # leakage guard
    bad_tokens = ["actual_", "points_for", "points_against", "score", "result", "winner"]
    offenders = [c for c in cols if any(tok in c.lower() for tok in bad_tokens)]
    if offenders:
        cols = [c for c in cols if c not in offenders]

    return cols


def _compute_medians(X: np.ndarray) -> np.ndarray:
    med = np.zeros(X.shape[1], dtype=np.float64)
    if X.shape[1] == 0:
        return med
    X2 = X.copy()
    X2[~np.isfinite(X2)] = np.nan
    col_has = np.isfinite(X2).any(axis=0)
    if col_has.any():
        med[col_has] = np.nanmedian(X2[:, col_has], axis=0)
    med[~np.isfinite(med)] = 0.0
    return med


def _impute_with_medians(X: np.ndarray, med: np.ndarray) -> np.ndarray:
    X = np.asarray(X, dtype=np.float64)
    X[~np.isfinite(X)] = np.nan
    if np.isnan(X).any():
        nan_idx = np.where(np.isnan(X))
        X[nan_idx] = med[nan_idx[1]]
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    return X


def _drop_constant_cols(X: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """
    Returns X_reduced, keep_mask
    """
    if X.shape[1] == 0 or X.shape[0] < 2:
        keep = np.ones(X.shape[1], dtype=bool)
        return X, keep
    std = X.std(axis=0)
    keep = std > 0
    return X[:, keep], keep


def _ridge_fit_intercept_unpenalized(X: np.ndarray, y: np.ndarray, lam: float) -> np.ndarray:
    """
    Fit ridge with intercept, intercept not regularized.
    Returns coef vector [intercept, w...]
    """
    X = np.asarray(X, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64).reshape(-1)

    n, p = X.shape
    X_aug = np.column_stack([np.ones(n, dtype=np.float64), X])

    reg = lam * np.eye(p + 1, dtype=np.float64)
    reg[0, 0] = 0.0

    A = X_aug.T @ X_aug + reg
    b = X_aug.T @ y
    return np.linalg.solve(A, b)


def _predict_from_coef(X: np.ndarray, coef: np.ndarray) -> np.ndarray:
    X = np.asarray(X, dtype=np.float64)
    X_aug = np.column_stack([np.ones(X.shape[0], dtype=np.float64), X])
    return (X_aug @ coef).astype(np.float64)


def _rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    y_true = np.asarray(y_true, dtype=np.float64)
    y_pred = np.asarray(y_pred, dtype=np.float64)
    mask = np.isfinite(y_true) & np.isfinite(y_pred)
    if not mask.any():
        return float("nan")
    err = y_pred[mask] - y_true[mask]
    return float(np.sqrt(np.mean(err * err)))


def _mae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    y_true = np.asarray(y_true, dtype=np.float64)
    y_pred = np.asarray(y_pred, dtype=np.float64)
    mask = np.isfinite(y_true) & np.isfinite(y_pred)
    if not mask.any():
        return float("nan")
    err = np.abs(y_pred[mask] - y_true[mask])
    return float(np.mean(err))


def _winner_acc(actual_margin: np.ndarray, pred_margin: np.ndarray) -> Tuple[Optional[float], int]:
    a = np.asarray(actual_margin, dtype=np.float64)
    p = np.asarray(pred_margin, dtype=np.float64)
    mask = np.isfinite(a) & np.isfinite(p) & (a != 0)
    if not mask.any():
        return None, 0
    acc = float((np.sign(a[mask]) == np.sign(p[mask])).mean())
    return acc, int(mask.sum())


# ----------------------------
# Walk-forward backtest
# ----------------------------

def _build_folds(n: int, n_folds: int, min_train_rows: int, val_ratio: float) -> List[Tuple[int, int, int, int]]:
    """
    Returns list of folds as (train_start, train_end, test_start, test_end) index ranges.
    Expanding window: train_start=0 always.

    Logic:
    - Start train_end at min_train_rows
    - Each fold tests on a chunk size roughly val_ratio * remaining, with a floor.
    """
    folds: List[Tuple[int, int, int, int]] = []
    if n <= min_train_rows + 50:
        return folds

    train_end = min_train_rows
    remaining = n - train_end
    if remaining <= 0:
        return folds

    # chunk size
    base_chunk = max(100, int(math.floor(n * val_ratio)))
    base_chunk = min(base_chunk, max(100, remaining // max(1, n_folds)))

    for _ in range(int(n_folds)):
        test_start = train_end
        test_end = min(n, test_start + base_chunk)
        if test_end - test_start < 25:
            break
        folds.append((0, train_end, test_start, test_end))
        train_end = test_end
        if train_end >= n - 25:
            break

    return folds


def _fit_and_score_target(
    df: pd.DataFrame,
    feature_cols: List[str],
    target_col: str,
    folds: List[Tuple[int, int, int, int]],
    ridge_lambda: float,
    debug: bool,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Returns:
      preds_all: row-level predictions across all folds
      coefs_all: per-fold coefficients on original feature space
    """
    preds_rows: List[pd.DataFrame] = []
    coef_rows: List[Dict[str, object]] = []

    for fold_idx, (tr0, tr1, te0, te1) in enumerate(folds, start=1):
        train = df.iloc[tr0:tr1].copy()
        test = df.iloc[te0:te1].copy()

        y_train = pd.to_numeric(train[target_col], errors="coerce").to_numpy(dtype=np.float64)
        y_test = pd.to_numeric(test[target_col], errors="coerce").to_numpy(dtype=np.float64)

        X_train_raw = train[feature_cols].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=np.float64)
        X_test_raw = test[feature_cols].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=np.float64)

        # Impute based on train medians
        med = _compute_medians(X_train_raw)
        X_train = _impute_with_medians(X_train_raw, med)
        X_test = _impute_with_medians(X_test_raw, med)

        # Drop constants based on train only
        X_train_red, keep_mask = _drop_constant_cols(X_train)
        X_test_red = X_test[:, keep_mask] if X_test.shape[1] == keep_mask.shape[0] else X_test

        # Drop rows with bad y
        tr_ok = np.isfinite(y_train)
        te_ok = np.isfinite(y_test)

        X_train_red = X_train_red[tr_ok]
        y_train2 = y_train[tr_ok]
        X_test_red = X_test_red[te_ok]
        y_test2 = y_test[te_ok]

        if X_train_red.shape[0] < 25 or X_test_red.shape[0] < 10:
            continue

        coef_small = _ridge_fit_intercept_unpenalized(X_train_red, y_train2, lam=float(max(0.0, ridge_lambda)))
        pred = _predict_from_coef(X_test_red, coef_small)

        # Expand coef back to full feature list
        full_w = np.zeros(len(feature_cols), dtype=np.float64)
        if keep_mask.any():
            full_w[keep_mask] = coef_small[1:]
        intercept = float(coef_small[0])

        # fold metrics
        rmse = _rmse(y_test2, pred)
        mae = _mae(y_test2, pred)

        if debug:
            print(
                f"[{target_col}] fold {fold_idx}: train={len(y_train2)} test={len(y_test2)} "
                f"rmse={rmse:.3f} mae={mae:.3f} kept_feats={int(keep_mask.sum())}/{len(feature_cols)}"
            )

        # row-level output
        out = test.loc[te_ok, [
            c for c in ["event_id", "game_datetime_utc", "team_home", "team_away"] if c in test.columns
        ]].copy()
        out["fold"] = fold_idx
        out["target"] = target_col
        out["y_true"] = y_test2
        out["y_pred"] = pred
        out["err"] = (pred - y_test2)
        out["abs_err"] = np.abs(out["err"].to_numpy(dtype=np.float64))

        preds_rows.append(out)

        # coef record (store top magnitude features per fold to keep JSON-ish row size sane)
        top_k = min(50, len(feature_cols))
        idx = np.argsort(np.abs(full_w))[::-1][:top_k]
        top_feats = [{"feature": feature_cols[i], "coef": float(full_w[i])} for i in idx if np.isfinite(full_w[i])]

        coef_rows.append({
            "target": target_col,
            "fold": fold_idx,
            "train_start_utc": str(train["game_datetime_utc"].min()) if "game_datetime_utc" in train.columns else None,
            "train_end_utc": str(train["game_datetime_utc"].max()) if "game_datetime_utc" in train.columns else None,
            "test_start_utc": str(test["game_datetime_utc"].min()) if "game_datetime_utc" in test.columns else None,
            "test_end_utc": str(test["game_datetime_utc"].max()) if "game_datetime_utc" in test.columns else None,
            "ridge_lambda": float(ridge_lambda),
            "n_features_total": int(len(feature_cols)),
            "n_features_used": int(keep_mask.sum()),
            "intercept": intercept,
            "rmse": float(rmse),
            "mae": float(mae),
            "top_features": top_feats,
        })

    preds_all = pd.concat(preds_rows, ignore_index=True) if preds_rows else pd.DataFrame()
    coefs_all = pd.DataFrame(coef_rows) if coef_rows else pd.DataFrame()
    return preds_all, coefs_all


def _aggregate_feature_importance(
    df: pd.DataFrame,
    feature_cols: List[str],
    target_col: str,
    ridge_lambda: float,
) -> pd.DataFrame:
    """
    Fit a single model on all rows (after sanitize) to get a global coefficient ranking.
    Importance score: abs(coef) * std(feature).
    """
    work = df.copy()
    y = pd.to_numeric(work[target_col], errors="coerce").to_numpy(dtype=np.float64)

    X_raw = work[feature_cols].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=np.float64)
    med = _compute_medians(X_raw)
    X = _impute_with_medians(X_raw, med)

    ok = np.isfinite(y)
    X = X[ok]
    y = y[ok]
    if X.shape[0] < 100:
        return pd.DataFrame()

    X_red, keep_mask = _drop_constant_cols(X)

    coef_small = _ridge_fit_intercept_unpenalized(X_red, y, lam=float(max(0.0, ridge_lambda)))
    w_red = coef_small[1:]

    w_full = np.zeros(len(feature_cols), dtype=np.float64)
    w_full[keep_mask] = w_red

    feat_std = X.std(axis=0)
    imp = np.abs(w_full) * np.nan_to_num(feat_std, nan=0.0, posinf=0.0, neginf=0.0)

    out = pd.DataFrame({
        "feature": feature_cols,
        "coef": w_full.astype(float),
        "abs_coef": np.abs(w_full).astype(float),
        "feature_std": feat_std.astype(float),
        "importance": imp.astype(float),
    }).sort_values("importance", ascending=False, kind="mergesort").reset_index(drop=True)

    return out


def _ablation_drop1(
    df: pd.DataFrame,
    feature_cols: List[str],
    target_col: str,
    ridge_lambda: float,
    top_n: int,
) -> pd.DataFrame:
    """
    Drop-1 ablation: fit global model RMSE with all features, then remove each top feature and refit.
    Uses a single train/val split (last 15% as validation) for speed and consistency.
    """
    if len(df) < 500:
        return pd.DataFrame()

    # deterministic time split (last 15% validation)
    n = len(df)
    cut = int(math.floor(n * 0.85))
    train = df.iloc[:cut].copy()
    val = df.iloc[cut:].copy()

    def fit_rmse(cols: List[str]) -> float:
        y_tr = pd.to_numeric(train[target_col], errors="coerce").to_numpy(dtype=np.float64)
        y_va = pd.to_numeric(val[target_col], errors="coerce").to_numpy(dtype=np.float64)

        X_tr_raw = train[cols].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=np.float64)
        X_va_raw = val[cols].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=np.float64)

        med = _compute_medians(X_tr_raw)
        X_tr = _impute_with_medians(X_tr_raw, med)
        X_va = _impute_with_medians(X_va_raw, med)

        ok_tr = np.isfinite(y_tr)
        ok_va = np.isfinite(y_va)
        X_tr = X_tr[ok_tr]
        y_tr = y_tr[ok_tr]
        X_va = X_va[ok_va]
        y_va = y_va[ok_va]

        if X_tr.shape[0] < 100 or X_va.shape[0] < 50:
            return float("nan")

        X_tr_red, keep_mask = _drop_constant_cols(X_tr)
        X_va_red = X_va[:, keep_mask] if X_va.shape[1] == keep_mask.shape[0] else X_va

        coef = _ridge_fit_intercept_unpenalized(X_tr_red, y_tr, lam=float(max(0.0, ridge_lambda)))
        pred = _predict_from_coef(X_va_red, coef)
        return _rmse(y_va, pred)

    base_rmse = fit_rmse(feature_cols)

    # get top features from coefficient importance
    imp = _aggregate_feature_importance(df, feature_cols, target_col, ridge_lambda)
    if imp.empty:
        return pd.DataFrame()

    candidates = imp["feature"].head(int(top_n)).tolist()

    rows = []
    for f in candidates:
        cols2 = [c for c in feature_cols if c != f]
        rmse2 = fit_rmse(cols2)
        rows.append({
            "target": target_col,
            "dropped_feature": f,
            "base_rmse": float(base_rmse) if np.isfinite(base_rmse) else None,
            "rmse_without": float(rmse2) if np.isfinite(rmse2) else None,
            "rmse_delta": (float(rmse2 - base_rmse) if (np.isfinite(rmse2) and np.isfinite(base_rmse)) else None),
        })

    return pd.DataFrame(rows).sort_values("rmse_delta", ascending=False, kind="mergesort").reset_index(drop=True)


def run_full_season_backtest(cfg: BacktestSeasonConfig) -> None:
    out_dir = cfg.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    df = _load_features(cfg.features_path)
    df = _date_filter(df, cfg.start, cfg.end)

    # Basic coverage checks
    required_targets = ["actual_margin_home", "actual_total"]
    for t in required_targets:
        if t not in df.columns:
            raise ValueError(f"model_features.csv missing required target: {t}")

    feature_cols = _select_feature_cols(df)
    if not feature_cols:
        raise ValueError("No usable feature columns found after filtering ignore/leakage.")

    # Drop rows missing datetime or event_id
    df = df[df["game_datetime_utc"].notna()].copy()
    df["event_id"] = df["event_id"].astype(str)

    # Build folds
    folds = _build_folds(
        n=len(df),
        n_folds=cfg.n_folds,
        min_train_rows=cfg.min_train_rows,
        val_ratio=cfg.val_ratio,
    )
    if not folds:
        raise ValueError("Not enough rows for walk-forward folds. Lower --min_train_rows or --n_folds.")

    # Walk-forward predictions for both targets
    preds_margin, coefs_margin = _fit_and_score_target(
        df=df,
        feature_cols=feature_cols,
        target_col="actual_margin_home",
        folds=folds,
        ridge_lambda=cfg.ridge_lambda,
        debug=cfg.debug,
    )
    preds_total, coefs_total = _fit_and_score_target(
        df=df,
        feature_cols=feature_cols,
        target_col="actual_total",
        folds=folds,
        ridge_lambda=cfg.ridge_lambda,
        debug=cfg.debug,
    )

    preds_all = pd.concat([preds_margin, preds_total], ignore_index=True) if (not preds_margin.empty or not preds_total.empty) else pd.DataFrame()

    # Summary metrics
    summary_rows = []

    def summarize_target(preds: pd.DataFrame, target_name: str) -> Dict[str, object]:
        y_true = preds["y_true"].to_numpy(dtype=np.float64)
        y_pred = preds["y_pred"].to_numpy(dtype=np.float64)
        return {
            "target": target_name,
            "rows": int(len(preds)),
            "rmse": _rmse(y_true, y_pred),
            "mae": _mae(y_true, y_pred),
        }

    if not preds_margin.empty:
        s = summarize_target(preds_margin, "actual_margin_home")
        # winner acc (only for margin)
        # Need to use full series, but we stored y_true and y_pred already
        acc, nacc = _winner_acc(preds_margin["y_true"].to_numpy(), preds_margin["y_pred"].to_numpy())
        s["winner_acc"] = acc
        s["winner_n"] = nacc
        summary_rows.append(s)

        for fold, g in preds_margin.groupby("fold"):
            ss = summarize_target(g, "actual_margin_home")
            acc2, nacc2 = _winner_acc(g["y_true"].to_numpy(), g["y_pred"].to_numpy())
            ss["winner_acc"] = acc2
            ss["winner_n"] = nacc2
            ss["fold"] = int(fold)
            summary_rows.append(ss)

    if not preds_total.empty:
        summary_rows.append(summarize_target(preds_total, "actual_total"))
        for fold, g in preds_total.groupby("fold"):
            ss = summarize_target(g, "actual_total")
            ss["fold"] = int(fold)
            summary_rows.append(ss)

    summary_df = pd.DataFrame(summary_rows)
    if "fold" not in summary_df.columns:
        summary_df["fold"] = np.nan
    summary_df = summary_df.sort_values(["target", "fold"], na_position="first", kind="mergesort").reset_index(drop=True)

    # Feature importance (global fit)
    imp_margin = _aggregate_feature_importance(df, feature_cols, "actual_margin_home", cfg.ridge_lambda)
    imp_total = _aggregate_feature_importance(df, feature_cols, "actual_total", cfg.ridge_lambda)

    # Ablation
    abl_margin = _ablation_drop1(df, feature_cols, "actual_margin_home", cfg.ridge_lambda, cfg.ablation_top_n)
    abl_total = _ablation_drop1(df, feature_cols, "actual_total", cfg.ridge_lambda, cfg.ablation_top_n)

    # Write outputs
    if not preds_all.empty:
        preds_all.to_csv(out_dir / "walkforward_predictions.csv", index=False)
    else:
        (out_dir / "walkforward_predictions.csv").write_text("")

    summary_df.to_csv(out_dir / "walkforward_summary.csv", index=False)

    if not coefs_margin.empty:
        coefs_margin.to_json(out_dir / "coefs_by_fold_margin.json", orient="records", indent=2)
    if not coefs_total.empty:
        coefs_total.to_json(out_dir / "coefs_by_fold_total.json", orient="records", indent=2)

    if not imp_margin.empty:
        imp_margin.to_csv(out_dir / "feature_importance_margin.csv", index=False)
    else:
        (out_dir / "feature_importance_margin.csv").write_text("feature,coef,abs_coef,feature_std,importance\n")

    if not imp_total.empty:
        imp_total.to_csv(out_dir / "feature_importance_total.csv", index=False)
    else:
        (out_dir / "feature_importance_total.csv").write_text("feature,coef,abs_coef,feature_std,importance\n")

    if not abl_margin.empty:
        abl_margin.to_csv(out_dir / "ablation_margin_topN.csv", index=False)
    else:
        (out_dir / "ablation_margin_topN.csv").write_text("target,dropped_feature,base_rmse,rmse_without,rmse_delta\n")

    if not abl_total.empty:
        abl_total.to_csv(out_dir / "ablation_total_topN.csv", index=False)
    else:
        (out_dir / "ablation_total_topN.csv").write_text("target,dropped_feature,base_rmse,rmse_without,rmse_delta\n")

    coverage = {
        "generated_at_utc": _utc_now_iso(),
        "features_path": str(cfg.features_path),
        "rows_total_loaded": int(len(df)),
        "n_features": int(len(feature_cols)),
        "folds": [{"train_end": tr1, "test_start": te0, "test_end": te1} for (tr0, tr1, te0, te1) in folds],
        "date_start": cfg.start,
        "date_end": cfg.end,
        "ridge_lambda": float(cfg.ridge_lambda),
        "min_train_rows": int(cfg.min_train_rows),
        "n_folds": int(cfg.n_folds),
        "val_ratio": float(cfg.val_ratio),
        "ablation_top_n": int(cfg.ablation_top_n),
    }
    _write_json(out_dir / "data_coverage.json", coverage)

    print(f"[OK] Wrote full-season backtest outputs to: {out_dir}")


# ----------------------------
# CLI
# ----------------------------

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--features", default="ml/model_features.csv")
    p.add_argument("--out", default="ml/backtests_full_season")
    p.add_argument("--start", default=None, help="YYYY-MM-DD (optional)")
    p.add_argument("--end", default=None, help="YYYY-MM-DD (optional)")

    p.add_argument("--n_folds", default="6")
    p.add_argument("--min_train_rows", default="500")
    p.add_argument("--val_ratio", default="0.10")
    p.add_argument("--ridge_lambda", default="1e-2")

    p.add_argument("--ablation_top_n", default="25")
    p.add_argument("--debug", action="store_true")

    args = p.parse_args()

    cfg = BacktestSeasonConfig(
        features_path=Path(args.features),
        out_dir=Path(args.out),
        start=args.start,
        end=args.end,
        n_folds=int(args.n_folds),
        min_train_rows=int(args.min_train_rows),
        val_ratio=float(args.val_ratio),
        ridge_lambda=float(args.ridge_lambda),
        ablation_top_n=int(args.ablation_top_n),
        debug=bool(args.debug),
    )

    run_full_season_backtest(cfg)


if __name__ == "__main__":
    main()
