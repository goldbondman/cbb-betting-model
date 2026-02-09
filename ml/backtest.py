#!/usr/bin/env python3
"""
Backtesting harness for predictions vs actuals.

Supports:
  - Line-free backtest (accuracy, ranking, bucketed diagnostics)
  - Optional lines backtest (spread/total edges + hit rate) when vegas lines exist

Inputs:
  - ml/predictions_latest.csv
  - Optional lines file (default: espn_games.csv) containing columns like:
      - event_id (or game_id)
      - vegas_spread (spread for home team, home - away)
      - vegas_total

Outputs (in backtests/):
  - backtest_results.csv              (row-level merged dataset + derived metrics)
  - bet_log.csv                       (alias of backtest_results.csv, for continuity)
  - metrics_overall.json              (summary metrics)
  - metrics_by_bucket.csv             (bucketed performance by confidence)
  - worst_errors.csv                  (top N worst absolute errors)
  - coverage_report.json              (data availability + merge coverage)
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


# ----------------------------
# Config / helpers
# ----------------------------

@dataclass(frozen=True)
class BacktestConfig:
    preds_path: Path = Path("ml/predictions_latest.csv")
    lines_path: Path = Path("espn_games.csv")  # optional
    out_dir: Path = Path("backtests")
    mode: str = "line_free"  # line_free | with_lines
    market: str = "spread"   # spread | total
    start: Optional[str] = None  # YYYY-MM-DD
    end: Optional[str] = None    # YYYY-MM-DD
    id_col: str = "event_id"     # merge key preference
    top_n_worst: int = 25
    min_rows_warn: int = 10


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


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


def _ensure_str_id(df: pd.DataFrame, col: str) -> pd.DataFrame:
    if col in df.columns:
        df = df.copy()
        df[col] = df[col].astype(str)
    return df


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [c.strip() for c in out.columns]
    return out


def _infer_merge_key(preds: pd.DataFrame, lines: pd.DataFrame) -> str:
    """
    Prefer event_id if present in both, else game_id if present in both.
    """
    for k in ["event_id", "game_id", "external_game_id"]:
        if k in preds.columns and k in lines.columns:
            return k
    # fallback to preds-only, but merge won't happen
    if "event_id" in preds.columns:
        return "event_id"
    return "game_id"


def _confidence_from_preds(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds confidence proxies if missing.
      - conf_margin: abs(pred_margin_home)
      - conf_total: abs(pred_total - median(pred_total)) as a crude proxy
    """
    out = df.copy()

    if "conf_margin" not in out.columns and "pred_margin_home" in out.columns:
        out["conf_margin"] = (pd.to_numeric(out["pred_margin_home"], errors="coerce")).abs()

    if "conf_total" not in out.columns and "pred_total" in out.columns:
        pt = pd.to_numeric(out["pred_total"], errors="coerce")
        baseline = float(np.nanmedian(pt.to_numpy())) if np.isfinite(pt.to_numpy()).any() else 0.0
        out["conf_total"] = (pt - baseline).abs()

    return out


def _bucketize(series: pd.Series, edges: List[float]) -> pd.Series:
    """
    Bucket numeric series using edges like [0,2,5,10,20].
    """
    s = pd.to_numeric(series, errors="coerce")
    labels = []
    for i in range(len(edges) - 1):
        labels.append(f"{edges[i]}-{edges[i+1]}")
    labels.append(f"{edges[-1]}+")
    bins = edges + [np.inf]
    return pd.cut(s, bins=bins, labels=labels, include_lowest=True, right=False)


def _write_json(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n")


# ----------------------------
# Loaders
# ----------------------------

def _load_predictions(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing predictions file: {path}")
    df = pd.read_csv(path)
    df = _normalize_columns(df)
    if "game_datetime_utc" in df.columns:
        df["game_datetime_utc"] = _coerce_datetime_utc(df["game_datetime_utc"])
    return df


def _load_lines(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    df = _normalize_columns(df)
    # Lowercase common lines headers for robustness
    df.columns = [c.strip().lower() for c in df.columns]
    # try to preserve expected key casing by re-alias later
    return df


def _alias_lines_cols(lines: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize likely column names to:
      - event_id
      - vegas_spread
      - vegas_total
    """
    if lines.empty:
        return lines

    df = lines.copy()

    # Merge key aliases
    if "event_id" not in df.columns:
        for c in ["game_id", "external_game_id", "id"]:
            if c in df.columns:
                df["event_id"] = df[c].astype(str)
                break

    # Spread aliases
    if "vegas_spread" not in df.columns:
        for c in ["closing_spread", "spread", "line_spread", "home_spread", "vegas_line_spread"]:
            if c in df.columns:
                df["vegas_spread"] = pd.to_numeric(df[c], errors="coerce")
                break

    # Total aliases
    if "vegas_total" not in df.columns:
        for c in ["closing_total", "total", "line_total", "vegas_line_total", "ou"]:
            if c in df.columns:
                df["vegas_total"] = pd.to_numeric(df[c], errors="coerce")
                break

    # Ensure types
    if "event_id" in df.columns:
        df["event_id"] = df["event_id"].astype(str)

    return df


# ----------------------------
# Core backtest
# ----------------------------

def _line_free_metrics(df: pd.DataFrame) -> Dict[str, object]:
    out: Dict[str, object] = {}

    # Ensure numerics
    for c in ["pred_margin_home", "actual_margin_home", "pred_total", "actual_total"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # Spread accuracy
    if {"pred_margin_home", "actual_margin_home"} <= set(df.columns):
        err = df["pred_margin_home"] - df["actual_margin_home"]
        out["margin_mae"] = float(np.nanmean(np.abs(err.to_numpy()))) if err.notna().any() else None
        out["margin_rmse"] = float(np.sqrt(np.nanmean((err.to_numpy()) ** 2))) if err.notna().any() else None

        # directional accuracy (exclude actual=0 pushes)
        actual = df["actual_margin_home"]
        pred = df["pred_margin_home"]
        mask = actual.notna() & pred.notna() & (actual != 0)
        if mask.any():
            out["winner_acc"] = float((np.sign(pred[mask]) == np.sign(actual[mask])).mean())
            out["winner_n"] = int(mask.sum())
        else:
            out["winner_acc"] = None
            out["winner_n"] = 0

        # bias
        if err.notna().any():
            out["margin_bias_mean"] = float(np.nanmean(err.to_numpy()))
        else:
            out["margin_bias_mean"] = None

    # Total accuracy
    if {"pred_total", "actual_total"} <= set(df.columns):
        errt = df["pred_total"] - df["actual_total"]
        out["total_mae"] = float(np.nanmean(np.abs(errt.to_numpy()))) if errt.notna().any() else None
        out["total_rmse"] = float(np.sqrt(np.nanmean((errt.to_numpy()) ** 2))) if errt.notna().any() else None
        if errt.notna().any():
            out["total_bias_mean"] = float(np.nanmean(errt.to_numpy()))
        else:
            out["total_bias_mean"] = None

    return out


def _with_lines_metrics(df: pd.DataFrame, market: str) -> Dict[str, object]:
    """
    Optional: edges + simple hit rate if vegas lines exist.

    Assumptions:
      - vegas_spread is home spread (home - away)
      - For spread: bet home if edge>0, away if edge<0; win based on actual_margin_home vs spread.
      - For total: bet over if edge>0, under if edge<0; win based on actual_total vs total.
    """
    out: Dict[str, object] = {}

    for c in ["pred_margin_home", "actual_margin_home", "pred_total", "actual_total", "vegas_spread", "vegas_total"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    if market == "spread" and {"vegas_spread", "pred_margin_home", "actual_margin_home"} <= set(df.columns):
        df["edge"] = df["pred_margin_home"] - df["vegas_spread"]
        # home covers if actual_margin_home > vegas_spread
        df["won_home"] = (df["actual_margin_home"] > df["vegas_spread"]).astype(float)
        # interpret bet side based on sign(edge)
        df["bet_side"] = np.where(df["edge"] > 0, "home", np.where(df["edge"] < 0, "away", "pass"))
        # win depends on bet side
        df["won"] = np.where(
            df["bet_side"] == "home",
            df["won_home"],
            np.where(df["bet_side"] == "away", 1.0 - df["won_home"], np.nan),
        )

    elif market == "total" and {"vegas_total", "pred_total", "actual_total"} <= set(df.columns):
        df["edge"] = df["pred_total"] - df["vegas_total"]
        df["won_over"] = (df["actual_total"] > df["vegas_total"]).astype(float)
        df["bet_side"] = np.where(df["edge"] > 0, "over", np.where(df["edge"] < 0, "under", "pass"))
        df["won"] = np.where(
            df["bet_side"] == "over",
            df["won_over"],
            np.where(df["bet_side"] == "under", 1.0 - df["won_over"], np.nan),
        )
    else:
        df["edge"] = np.nan
        df["won"] = np.nan
        df["bet_side"] = "pass"

    # summary
    bets_mask = df["edge"].notna() & df["won"].notna()
    out["bets"] = int(bets_mask.sum())
    out["hit_rate"] = float(df.loc[bets_mask, "won"].mean()) if bets_mask.any() else None
    out["avg_edge"] = float(df.loc[bets_mask, "edge"].mean()) if bets_mask.any() else None

    return out


def _bucket_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """
    Bucket by confidence and compute accuracy metrics per bucket.
    Uses conf_margin if present, else abs(pred_margin_home).
    """
    out = df.copy()

    # derive absolute error columns
    if {"pred_margin_home", "actual_margin_home"} <= set(out.columns):
        out["abs_err_margin"] = (pd.to_numeric(out["pred_margin_home"], errors="coerce") -
                                pd.to_numeric(out["actual_margin_home"], errors="coerce")).abs()

    if {"pred_total", "actual_total"} <= set(out.columns):
        out["abs_err_total"] = (pd.to_numeric(out["pred_total"], errors="coerce") -
                               pd.to_numeric(out["actual_total"], errors="coerce")).abs()

    # choose bucket driver
    conf_col = None
    if "conf_margin" in out.columns:
        conf_col = "conf_margin"
    elif "pred_margin_home" in out.columns:
        out["conf_margin"] = pd.to_numeric(out["pred_margin_home"], errors="coerce").abs()
        conf_col = "conf_margin"

    if conf_col is None:
        return pd.DataFrame()

    out["conf_bucket"] = _bucketize(out[conf_col], edges=[0, 2, 5, 10, 20])

    agg = []
    for bucket, g in out.groupby("conf_bucket", dropna=False):
        rec: Dict[str, object] = {
            "conf_bucket": str(bucket),
            "n": int(len(g)),
        }
        if "abs_err_margin" in g.columns:
            rec["margin_mae"] = float(np.nanmean(g["abs_err_margin"].to_numpy())) if g["abs_err_margin"].notna().any() else None
        if "abs_err_total" in g.columns:
            rec["total_mae"] = float(np.nanmean(g["abs_err_total"].to_numpy())) if g["abs_err_total"].notna().any() else None

        # winner accuracy within bucket
        if {"pred_margin_home", "actual_margin_home"} <= set(g.columns):
            actual = pd.to_numeric(g["actual_margin_home"], errors="coerce")
            pred = pd.to_numeric(g["pred_margin_home"], errors="coerce")
            mask = actual.notna() & pred.notna() & (actual != 0)
            rec["winner_acc"] = float((np.sign(pred[mask]) == np.sign(actual[mask])).mean()) if mask.any() else None
            rec["winner_n"] = int(mask.sum())
        agg.append(rec)

    return pd.DataFrame(agg)


def _worst_errors(df: pd.DataFrame, top_n: int) -> pd.DataFrame:
    out = df.copy()
    # margin worst
    if {"pred_margin_home", "actual_margin_home"} <= set(out.columns):
        out["abs_err_margin"] = (pd.to_numeric(out["pred_margin_home"], errors="coerce") -
                                pd.to_numeric(out["actual_margin_home"], errors="coerce")).abs()
    else:
        out["abs_err_margin"] = np.nan

    # total worst
    if {"pred_total", "actual_total"} <= set(out.columns):
        out["abs_err_total"] = (pd.to_numeric(out["pred_total"], errors="coerce") -
                               pd.to_numeric(out["actual_total"], errors="coerce")).abs()
    else:
        out["abs_err_total"] = np.nan

    out["abs_err_any"] = out[["abs_err_margin", "abs_err_total"]].max(axis=1, skipna=True)
    out = out.sort_values("abs_err_any", ascending=False).head(int(top_n)).copy()

    keep = [
        "event_id",
        "game_datetime_utc",
        "team_home",
        "team_away",
        "pred_margin_home",
        "actual_margin_home",
        "abs_err_margin",
        "pred_total",
        "actual_total",
        "abs_err_total",
        "model_version",
        "row_hash",
    ]
    keep = [c for c in keep if c in out.columns]
    return out[keep]


def backtest(cfg: BacktestConfig) -> Tuple[pd.DataFrame, Dict[str, object], pd.DataFrame, pd.DataFrame, Dict[str, object]]:
    preds = _load_predictions(cfg.preds_path)
    preds = _confidence_from_preds(preds)

    # Filter date range first
    preds = _date_filter(preds, cfg.start, cfg.end)

    lines_raw = _load_lines(cfg.lines_path)
    lines = _alias_lines_cols(lines_raw)

    merge_key = _infer_merge_key(preds, lines) if not lines.empty else (cfg.id_col if cfg.id_col in preds.columns else "event_id")

    preds = _ensure_str_id(preds, merge_key) if merge_key in preds.columns else preds
    if not lines.empty and merge_key in lines.columns:
        lines = _ensure_str_id(lines, merge_key)

    merged = preds.copy()
    merged["merge_key"] = merged[merge_key].astype(str) if merge_key in merged.columns else ""

    merged_with_lines = False
    if not lines.empty and merge_key in preds.columns and merge_key in lines.columns:
        # Avoid column collisions from lines (lowercase)
        merged = merged.merge(lines, on=merge_key, how="left", suffixes=("", "_lines"))
        merged_with_lines = True

    # Coverage + integrity
    coverage = {
        "generated_at_utc": _utc_now_iso(),
        "mode": cfg.mode,
        "market": cfg.market,
        "preds_path": str(cfg.preds_path),
        "lines_path": str(cfg.lines_path),
        "merge_key": merge_key,
        "pred_rows": int(len(preds)),
        "lines_rows": int(len(lines)) if not lines.empty else 0,
        "merged_rows": int(len(merged)),
        "lines_merged": bool(merged_with_lines),
        "preds_missing_game_datetime_utc": int(preds["game_datetime_utc"].isna().sum()) if "game_datetime_utc" in preds.columns else None,
        "preds_missing_actual_margin_home": int(pd.to_numeric(preds.get("actual_margin_home", pd.Series([])), errors="coerce").isna().sum())
            if "actual_margin_home" in preds.columns else None,
        "preds_missing_actual_total": int(pd.to_numeric(preds.get("actual_total", pd.Series([])), errors="coerce").isna().sum())
            if "actual_total" in preds.columns else None,
        "preds_duplicates_event_id": int(preds.duplicated(subset=[merge_key]).sum()) if merge_key in preds.columns else None,
    }

    # Overall metrics (always line-free)
    overall = _line_free_metrics(merged)

    # With-lines metrics if requested and available
    lines_metrics: Dict[str, object] = {}
    if cfg.mode == "with_lines":
        lines_metrics = _with_lines_metrics(merged, cfg.market)

    # Bucketed metrics
    buckets = _bucket_metrics(merged)

    # Worst errors
    worst = _worst_errors(merged, cfg.top_n_worst)

    metrics = {
        "generated_at_utc": _utc_now_iso(),
        "mode": cfg.mode,
        "market": cfg.market,
        "merge_key": merge_key,
        "overall": overall,
        "with_lines": lines_metrics if cfg.mode == "with_lines" else None,
        "coverage": coverage,
    }

    return merged, metrics, buckets, worst, coverage


# ----------------------------
# CLI
# ----------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=None, help="YYYY-MM-DD (optional)")
    parser.add_argument("--end", default=None, help="YYYY-MM-DD (optional)")
    parser.add_argument("--mode", default="line_free", choices=["line_free", "with_lines"])
    parser.add_argument("--market", default="spread", choices=["spread", "total"])
    parser.add_argument("--preds", default="ml/predictions_latest.csv")
    parser.add_argument("--lines", default="espn_games.csv")
    parser.add_argument("--out_dir", default="backtests")
    parser.add_argument("--top_n_worst", default="25")
    args = parser.parse_args()

    cfg = BacktestConfig(
        preds_path=Path(args.preds),
        lines_path=Path(args.lines),
        out_dir=Path(args.out_dir),
        mode=args.mode,
        market=args.market,
        start=args.start,
        end=args.end,
        top_n_worst=int(args.top_n_worst),
    )

    merged, metrics, buckets, worst, coverage = backtest(cfg)

    cfg.out_dir.mkdir(parents=True, exist_ok=True)

    # Row-level outputs
    merged.to_csv(cfg.out_dir / "backtest_results.csv", index=False)
    merged.to_csv(cfg.out_dir / "bet_log.csv", index=False)

    # Metrics + diagnostics
    _write_json(cfg.out_dir / "metrics_overall.json", metrics)
    _write_json(cfg.out_dir / "coverage_report.json", coverage)

    if not buckets.empty:
        buckets.to_csv(cfg.out_dir / "metrics_by_bucket.csv", index=False)
    else:
        # still write an empty file for pipeline stability
        (cfg.out_dir / "metrics_by_bucket.csv").write_text("conf_bucket,n,margin_mae,total_mae,winner_acc,winner_n\n")

    if not worst.empty:
        worst.to_csv(cfg.out_dir / "worst_errors.csv", index=False)
    else:
        (cfg.out_dir / "worst_errors.csv").write_text("event_id,game_datetime_utc,team_home,team_away,abs_err_any\n")

    print(f"[OK] Wrote outputs to: {cfg.out_dir}")


if __name__ == "__main__":
    main()
