#!/usr/bin/env python3
"""
CLI wrapper for ML predictions by date.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd

from predict_ml import predict, PredictConfig


def _filter_by_date(path: Path, date_str: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["game_datetime_utc"] = pd.to_datetime(df["game_datetime_utc"], utc=True, errors="coerce")
    target = datetime.fromisoformat(date_str).date()
    return df[df["game_datetime_utc"].dt.date == target]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    args = parser.parse_args()

    cfg = PredictConfig()
    if not cfg.features_path.exists():
        raise FileNotFoundError(f"Missing feature store: {cfg.features_path}")

    filtered = _filter_by_date(cfg.features_path, args.date)
    if filtered.empty:
        raise ValueError(f"No games found for {args.date}")

    tmp_path = Path("ml/model_features_filtered.csv")
    filtered.to_csv(tmp_path, index=False)
    cfg = PredictConfig(features_path=tmp_path, out_path=Path("ml/predictions_filtered.csv"))
    predict(cfg)


if __name__ == "__main__":
    main()
