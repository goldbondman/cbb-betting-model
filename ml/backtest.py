#!/usr/bin/env python3
"""
Simple backtesting harness for predictions vs actuals.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, Tuple

import pandas as pd


def _load_predictions(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["game_datetime_utc"] = pd.to_datetime(df["game_datetime_utc"], utc=True, errors="coerce")
    return df


def _load_lines(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    if "event_id" in df.columns and "event_id" not in df.columns:
        df["event_id"] = df["event_id"].astype(str)
    return df


def backtest(
    preds: pd.DataFrame,
    lines: pd.DataFrame,
    market: str,
) -> Tuple[pd.DataFrame, Dict[str, float]]:
    df = preds.copy()
    if not lines.empty and "event_id" in lines.columns:
        df = df.merge(lines, on="event_id", how="left")

    if market == "spread" and "vegas_spread" in df.columns:
        df["edge"] = df["pred_margin_home"] - df["vegas_spread"]
        df["won"] = (df["actual_margin_home"] > df["vegas_spread"]).astype(int)
    elif market == "total" and "vegas_total" in df.columns:
        df["edge"] = df["pred_total"] - df["vegas_total"]
        df["won"] = (df["actual_total"] > df["vegas_total"]).astype(int)
    else:
        df["edge"] = pd.NA
        df["won"] = pd.NA

    summary = {
        "bets": int(df["edge"].notna().sum()),
        "hit_rate": float(df["won"].mean()) if df["won"].notna().any() else 0.0,
        "avg_edge": float(df["edge"].mean()) if df["edge"].notna().any() else 0.0,
    }
    return df, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--market", default="spread", choices=["spread", "total"])
    args = parser.parse_args()

    preds_path = Path("ml/predictions_latest.csv")
    lines_path = Path("espn_games.csv")

    preds = _load_predictions(preds_path)
    start = datetime.fromisoformat(args.start).date()
    end = datetime.fromisoformat(args.end).date()
    preds = preds[(preds["game_datetime_utc"].dt.date >= start) & (preds["game_datetime_utc"].dt.date <= end)]

    lines = _load_lines(lines_path)
    results, summary = backtest(preds, lines, args.market)
    Path("backtests").mkdir(parents=True, exist_ok=True)
    results.to_csv("backtests/bet_log.csv", index=False)
    results.to_csv("backtests/backtest_results.csv", index=False)
    Path("backtests/diagnostics.json").write_text(
        pd.Series(summary).to_json(indent=2)  # type: ignore[arg-type]
    )


if __name__ == "__main__":
    main()
