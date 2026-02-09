#!/usr/bin/env python3
"""
CLI wrapper for ML predictions by date.

Design goals:
- Keep PredictConfig / predict_ml.predict() usage unchanged
- Support single day runs and simple ranges without breaking existing flags
- Make date parsing and filtering deterministic and robust
"""

from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from predict_ml import PredictConfig, predict


def _parse_date(d: str) -> date:
    """
    Accepts YYYY-MM-DD. Raises ValueError with a clear message.
    """
    try:
        return datetime.fromisoformat(d).date()
    except Exception as e:
        raise ValueError(f"Invalid date '{d}'. Expected YYYY-MM-DD.") from e


def _load_features(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing feature store: {path}")
    df = pd.read_csv(path)

    if "game_datetime_utc" not in df.columns:
        raise ValueError("Feature store missing required column: game_datetime_utc")

    df["game_datetime_utc"] = pd.to_datetime(df["game_datetime_utc"], utc=True, errors="coerce")
    # Drop rows with bad datetimes to avoid silent mis-filters
    df = df[df["game_datetime_utc"].notna()].copy()

    return df


def _filter_features(
    df: pd.DataFrame,
    *,
    on: Optional[date] = None,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> pd.DataFrame:
    """
    Filter by:
      - on: exact date (UTC date)
      - start/end: inclusive date range (UTC date)
    """
    if on is not None and (start is not None or end is not None):
        raise ValueError("Use either --date OR --start/--end, not both.")

    if on is not None:
        return df[df["game_datetime_utc"].dt.date == on].copy()

    if start is None and end is None:
        return df

    if start is None:
        start = end
    if end is None:
        end = start
    if start > end:
        raise ValueError(f"Invalid range: start {start.isoformat()} is after end {end.isoformat()}")

    d = df["game_datetime_utc"].dt.date
    return df[(d >= start) & (d <= end)].copy()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="YYYY-MM-DD (single day)")
    parser.add_argument("--start", help="YYYY-MM-DD (range start, inclusive)")
    parser.add_argument("--end", help="YYYY-MM-DD (range end, inclusive)")
    parser.add_argument(
        "--out",
        default="ml/predictions_filtered.csv",
        help="Output path for filtered predictions CSV (default: ml/predictions_filtered.csv)",
    )
    parser.add_argument(
        "--tmp",
        default="ml/model_features_filtered.csv",
        help="Temp filtered feature store path (default: ml/model_features_filtered.csv)",
    )
    args = parser.parse_args()

    if not args.date and not args.start and not args.end:
        raise ValueError("Provide --date or --start/--end.")

    on = _parse_date(args.date) if args.date else None
    start = _parse_date(args.start) if args.start else None
    end = _parse_date(args.end) if args.end else None

    cfg = PredictConfig()
    df = _load_features(cfg.features_path)

    filtered = _filter_features(df, on=on, start=start, end=end)
    if filtered.empty:
        if on is not None:
            raise ValueError(f"No games found for {on.isoformat()}")
        raise ValueError(
            f"No games found for range {start.isoformat() if start else ''}..{end.isoformat() if end else ''}"
        )

    tmp_path = Path(args.tmp)
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    filtered.to_csv(tmp_path, index=False)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    run_cfg = PredictConfig(features_path=tmp_path, out_path=out_path)
    predict(run_cfg)


if __name__ == "__main__":
    main()
