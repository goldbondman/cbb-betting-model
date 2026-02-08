#!/usr/bin/env python3
"""
Feature matrix builder for CBB models.

Inputs (preferred order):
  1) Supabase Postgres table: raw.espn_team_game_features (when SUPABASE_DB_URL is set)
  2) Local CSV: espn_team_game_features.csv

Outputs:
  - ml/model_features.csv
  - ml/dq_audit_ml.csv
  - ml/feature_schema_hash.txt
"""

from __future__ import annotations

import csv
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

from pathlib import Path as _Path

_ML_DIR = _Path(__file__).resolve().parent
if str(_ML_DIR) not in sys.path:
    sys.path.insert(0, str(_ML_DIR))

import numpy as np
import pandas as pd

from schema import FEATURE_SCHEMA, feature_schema_hash
from validation import validate_dataframe
from features_v2 import add_features_v2, FEATURES_V2

try:
    import psycopg
except Exception:
    psycopg = None


SUPABASE_DB_URL = (os.getenv("SUPABASE_DB_URL") or "").strip()
DB_SCHEMA = (os.getenv("DB_SCHEMA") or "raw").strip()
DB_TABLE = (os.getenv("ESPN_FEATURES_TABLE") or "espn_team_game_features").strip()
DB_LIMIT = (os.getenv("ESPN_FEATURES_LIMIT") or "").strip()

MIN_FEATURES = int(os.getenv("ML_MIN_FEATURES", "25"))


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
    db_schema: str = DB_SCHEMA
    db_table: str = DB_TABLE
    out_features_path: Path = Path("ml/model_features.csv")
    out_audit_path: Path = Path("ml/dq_audit_ml.csv")
    out_schema_path: Path = Path("ml/feature_schema_hash.txt")


def _quote_ident(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def _db_enabled() -> bool:
    return bool(SUPABASE_DB_URL)


def _load_features_from_db(schema: str, table: str) -> pd.DataFrame:
    if not SUPABASE_DB_URL:
        raise ValueError("SUPABASE_DB_URL is not set")
    if psycopg is None:
        raise ImportError("psycopg is not installed")

    qschema = _quote_ident(schema)
    qtable = _quote_ident(table)

    sql = f"""
      select *
      from {qschema}.{qtable}
      order by game_datetime_utc asc nulls last, event_id asc, team_id asc
    """

    if DB_LIMIT:
        try:
            lim = int(DB_LIMIT)
            if lim > 0:
                sql += f"\nlimit {lim}"
        except ValueError:
            pass

    with psycopg.connect(SUPABASE_DB_URL) as conn:
        df = pd.read_sql(sql, conn)

    df = _expand_features_json(df)
    print(f"[INFO] Loaded features from DB: {schema}.{table} rows={len(df)} cols={len(df.columns)}")
    return df


def _parse_features_cell(value: object) -> Dict[str, object]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _expand_features_json(df: pd.DataFrame) -> pd.DataFrame:
    if "features" not in df.columns:
        return df

    base_df = df.drop(columns=["features"])
    feature_series = df["features"].apply(_parse_features_cell)
    features_df = pd.json_normalize(feature_series)

    overlap = set(base_df.columns).intersection(features_df.columns)
    if overlap:
        features_df = features_df.drop(columns=list(overlap))

    expanded = pd.concat([base_df.reset_index(drop=True), features_df.reset_index(drop=True)], axis=1)
    print(f"[INFO] Expanded features json: +{len(features_df.columns)} cols")
    return expanded


def _load_features_from_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing feature store: {path}")
    df = pd.read_csv(path)
    print(f"[INFO] Loaded features from CSV: rows={len(df)} cols={len(df.columns)}")
    return df


def _load_features(cfg: BuildConfig) -> pd.DataFrame:
    if _db_enabled():
        return _load_features_from_db(cfg.db_schema, cfg.db_table)
    return _load_features_from_csv(cfg.features_path)


def _ensure_required_cols(df: pd.DataFrame) -> List[str]:
    return [c for c in REQUIRED_FEATURE_COLS if c not in df.columns]


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
            f,
            fieldnames=[
                "entity_type",
                "entity_id",
                "severity",
                "reason_codes",
                "details",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def build_feature_matrix(cfg: BuildConfig) -> pd.DataFrame:
    df = _load_features(cfg)

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
    df = df[~df["game_dt"].isna()].copy()

    home = df[df["home_away"] == "home"].copy()
    away = df[df["home_away"] == "away"].copy()

    merged = home.merge(
        away,
        on=["event_id"],
        suffixes=("_home", "_away"),
        how="inner",
    )

    merged["actual_margin_home"] = merged["points_for_home"] - merged["points_for_away"]
    merged["actual_total"] = merged["points_for_home"] + merged["points_for_away"]

    keep_features: List[str] = []

    # BASE_FEATURES diffs
    for feat in BASE_FEATURES:
        hcol = f"{feat}_home"
        acol = f"{feat}_away"
        if hcol in merged.columns and acol in merged.columns:
            merged[f"{feat}_diff"] = merged[hcol] - merged[acol]
            keep_features.append(f"{feat}_diff")

    # v2 features
    merged = add_features_v2(merged)
    keep_features.extend([f for f in FEATURES_V2 if f in merged.columns])

    # fallback auto numeric diffs
    if len(keep_features) < MIN_FEATURES:
        ignore_tokens = [
            "points",
            "actual",
            "margin",
            "game_dt",
            "datetime",
            "team_id",
            "event_id",
        ]

        home_cols = [c for c in merged.columns if c.endswith("_home")]
        away_cols = [c for c in merged.columns if c.endswith("_away")]

        base_home = {c[:-5] for c in home_cols}
        base_away = {c[:-5] for c in away_cols}
        common = base_home.intersection(base_away)

        added = 0
        for base in common:
            if any(tok in base for tok in ignore_tokens):
                continue

            hcol = f"{base}_home"
            acol = f"{base}_away"

            try:
                merged[hcol] = pd.to_numeric(merged[hcol], errors="coerce")
                merged[acol] = pd.to_numeric(merged[acol], errors="coerce")
            except Exception:
                continue

            diff_col = f"{base}_diff_auto"
            merged[diff_col] = merged[hcol] - merged[acol]

            if merged[diff_col].notna().any():
                keep_features.append(diff_col)
                added += 1

        print(f"[INFO] Auto feature fallback added={added}")

    if not keep_features:
        raise ValueError("Feature matrix produced zero usable features.")

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

    print(f"[INFO] model_features.csv rows={len(out)} cols={len(out.columns)}")

    return out


def main() -> None:
    cfg = BuildConfig()
    build_feature_matrix(cfg)


if __name__ == "__main__":
    main()
