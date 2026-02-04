#!/usr/bin/env python3
"""
Feature matrix builder for CBB models.

Inputs:
  - espn_team_game_features.csv
Outputs:
  - model_features.csv (home/away merged, leak-free inputs)
  - dq_audit_ml.csv (row-level integrity notes)

Design:
  - No external ML deps; pure pandas/numpy.
  - Deterministic, idempotent output.
  - Validates required fields before training.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple
import sys

from pathlib import Path as _Path
_ML_DIR = _Path(__file__).resolve().parent
if str(_ML_DIR) not in sys.path:
    sys.path.insert(0, str(_ML_DIR))

import numpy as np
import pandas as pd

from schema import FEATURE_SCHEMA, feature_schema_hash
from validation import validate_dataframe
from features_v2 import add_features_v2, FEATURES_V2


REQUIRED_FEATURE_COLS = [
    "event_id",
    "team_id",
    "team",
    "home_away",
    "game_datetime_utc",
    "points_for",
    "points_against",
]

BASE_FEATURES = [
    "ortg_l7_pre",
    "drtg_l7_pre",
    "netrtg_l7_pre",
    "pace_l7_pre",
    "efg_l7_pre",
    "tov_pct_l7_pre",
    "orb_pct_l7_pre",
    "drb_pct_l7_pre",
    "ftr_l7_pre",
    "3par_l7_pre",
    "exp_margin",
    "style_distance_l7",
    "games_last_3_days",
    "games_last_5_days",
    "games_last_7_days",
    "games_last_10_days",
]


@dataclass(frozen=True)
class BuildConfig:
    features_path: Path = Path("espn_team_game_features.csv")
    out_features_path: Path = Path("ml/model_features.csv")
    out_audit_path: Path = Path("ml/dq_audit_ml.csv")
    out_schema_path: Path = Path("ml/feature_schema_hash.txt")


def _load_features(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing feature store: {path}")
    df = pd.read_csv(path)
    return df


def _ensure_required_cols(df: pd.DataFrame) -> List[str]:
    missing = [c for c in REQUIRED_FEATURE_COLS if c not in df.columns]
    return missing


def _normalize_home_away(s: pd.Series) -> pd.Series:
    return s.astype("string").str.strip().str.lower()


def _safe_numeric(df: pd.DataFrame, cols: List[str]) -> pd.DataFrame:
    out = df.copy()
    for c in cols:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def _build_audit_rows(
    df: pd.DataFrame,
    issues: List[Tuple[str, str]],
    entity_type: str = "team_game_features",
) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for event_id, reason in issues:
        rows.append(
            {
                "entity_type": entity_type,
                "entity_id": str(event_id),
                "severity": "warning",
                "reason_codes": reason,
                "details": json.dumps({}),
            }
        )
    return rows


def _write_audit(path: Path, rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["entity_type", "entity_id", "severity", "reason_codes", "details"]
        )
        writer.writeheader()
        writer.writerows(rows)


def build_feature_matrix(cfg: BuildConfig) -> pd.DataFrame:
    df = _load_features(cfg.features_path)

    missing = _ensure_required_cols(df)
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    ok, issues_schema = validate_dataframe(df, FEATURE_SCHEMA)
    if not ok:
        raise ValueError("Schema validation failed: " + "; ".join([i.message for i in issues_schema]))

    df["home_away"] = _normalize_home_away(df["home_away"])
    df["game_dt"] = pd.to_datetime(df["game_datetime_utc"], utc=True, errors="coerce")

    df = _safe_numeric(df, BASE_FEATURES + ["points_for", "points_against"])

    issues: List[Tuple[str, str]] = []
    bad_dt = df["game_dt"].isna()
    if bad_dt.any():
        for event_id in df.loc[bad_dt, "event_id"].astype(str).tolist():
            issues.append((event_id, "missing_game_datetime"))

    df = df[~bad_dt].copy()

    home = df[df["home_away"] == "home"].copy()
    away = df[df["home_away"] == "away"].copy()

    counts = df.groupby(["event_id", "home_away"]).size().unstack(fill_value=0)
    bad_events = counts[(counts.get("home", 0) != 1) | (counts.get("away", 0) != 1)].index.tolist()
    if bad_events:
        for event_id in bad_events:
            issues.append((str(event_id), "home_away_mismatch"))
        home = home[~home["event_id"].isin(bad_events)]
        away = away[~away["event_id"].isin(bad_events)]

    merged = home.merge(
        away,
        on=["event_id"],
        suffixes=("_home", "_away"),
        how="inner",
    )

    merged["actual_margin_home"] = merged["points_for_home"] - merged["points_for_away"]
    merged["actual_total"] = merged["points_for_home"] + merged["points_for_away"]

    keep_features = []
    for feat in BASE_FEATURES:
        if f"{feat}_home" in merged.columns and f"{feat}_away" in merged.columns:
            merged[f"{feat}_diff"] = merged[f"{feat}_home"] - merged[f"{feat}_away"]
            keep_features.append(f"{feat}_diff")
        elif f"{feat}_home" in merged.columns:
            keep_features.append(f"{feat}_home")

    merged = add_features_v2(merged)
    keep_features.extend([f for f in FEATURES_V2 if f in merged.columns])

    output_cols = [
        "event_id",
        "team_id_home",
        "team_id_away",
        "team_home",
        "team_away",
        "game_datetime_utc_home",
        "actual_margin_home",
        "actual_total",
    ] + keep_features

    out = merged[output_cols].copy()
    out = out.rename(columns={"game_datetime_utc_home": "game_datetime_utc"})

    audit_rows = _build_audit_rows(df, issues)
    _write_audit(cfg.out_audit_path, audit_rows)
    cfg.out_schema_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.out_schema_path.write_text(feature_schema_hash() + "\n")

    cfg.out_features_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(cfg.out_features_path, index=False)
    return out


def main() -> None:
    cfg = BuildConfig()
    build_feature_matrix(cfg)


if __name__ == "__main__":
    main()
