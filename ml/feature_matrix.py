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
from typing import Dict, List, Tuple, Optional

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
    db_limit: str = DB_LIMIT

    out_features_path: Path = Path("ml/model_features.csv")
    out_audit_path: Path = Path("ml/dq_audit_ml.csv")
    out_schema_path: Path = Path("ml/feature_schema_hash.txt")


def _quote_ident(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def _db_enabled() -> bool:
    return bool(SUPABASE_DB_URL)


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


def _load_features_from_db(schema: str, table: str, limit_str: str) -> pd.DataFrame:
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

    if limit_str:
        try:
            lim = int(limit_str)
            if lim > 0:
                sql += f"\nlimit {lim}"
        except ValueError:
            pass

    with psycopg.connect(SUPABASE_DB_URL) as conn:
        df = pd.read_sql(sql, conn)

    df = _expand_features_json(df)
    print(f"[INFO] Loaded features from DB: {schema}.{table} rows={len(df)} cols={len(df.columns)}")
    return df


def _load_features_from_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing feature store: {path}")
    df = pd.read_csv(path)
    df = _expand_features_json(df)
    print(f"[INFO] Loaded features from CSV: rows={len(df)} cols={len(df.columns)}")
    return df


def _load_features(cfg: BuildConfig) -> pd.DataFrame:
    if _db_enabled():
        return _load_features_from_db(cfg.db_schema, cfg.db_table, cfg.db_limit)
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
    issues: List[Tuple[str, str, Dict[str, object]]],
    entity_type: str = "team_game_features",
) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for event_id, reason, details in issues:
        rows.append(
            {
                "entity_type": entity_type,
                "entity_id": str(event_id),
                "severity": "warning",
                "reason_codes": reason,
                "details": json.dumps(details or {}, ensure_ascii=False),
            }
        )
    return rows


def _write_audit(path: Path, rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["entity_type", "entity_id", "severity", "reason_codes", "details"],
        )
        writer.writeheader()
        writer.writerows(rows)


def _alias_team_name_cols(df: pd.DataFrame) -> pd.DataFrame:
    """
    Some sources might use team_name instead of team.
    """
    out = df.copy()
    if "team" not in out.columns and "team_name" in out.columns:
        out["team"] = out["team_name"]
    return out


def _dedupe_side(df: pd.DataFrame, side: str, issues: List[Tuple[str, str, Dict[str, object]]]) -> pd.DataFrame:
    """
    Ensure one row per event_id for each side (home/away).
    Deterministic pick: latest pulled_at_utc/pulled_at if available, else keep first after sort.
    """
    cols = set(df.columns)
    pulled_col = None
    for c in ["pulled_at_utc", "pulled_at"]:
        if c in cols:
            pulled_col = c
            break

    out = df.copy()
    if pulled_col:
        out[pulled_col] = pd.to_datetime(out[pulled_col], utc=True, errors="coerce")
        out = out.sort_values([pulled_col], ascending=[True], na_position="last")
    # count duplicates
    dup_counts = out.groupby("event_id").size()
    dups = dup_counts[dup_counts > 1]
    if len(dups) > 0:
        for eid, n in dups.items():
            issues.append(
                (
                    str(eid),
                    f"duplicate_{side}_rows",
                    {"side": side, "rows": int(n)},
                )
            )
        out = out.drop_duplicates(subset=["event_id"], keep="last")

    return out


def _leakage_guard(feature_cols: List[str]) -> None:
    bad_tokens = ["actual_", "points_for", "points_against", "score", "result", "winner"]
    offenders = [c for c in feature_cols if any(tok in c.lower() for tok in bad_tokens)]
    if offenders:
        raise ValueError(f"Feature leakage risk, refusing to train with: {offenders}")


def build_feature_matrix(cfg: BuildConfig) -> pd.DataFrame:
    df = _load_features(cfg)
    df = _alias_team_name_cols(df)

    missing = _ensure_required_cols(df)
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    ok, issues_schema = validate_dataframe(df, FEATURE_SCHEMA)
    if not ok:
        raise ValueError("Schema validation failed: " + "; ".join([i.message for i in issues_schema]))

    issues: List[Tuple[str, str, Dict[str, object]]] = []

    df["home_away"] = _normalize_home_away(df["home_away"])
    bad_ha = df["home_away"].isna() | (df["home_away"].str.len() == 0) | (~df["home_away"].isin(["home", "away"]))
    if bad_ha.any():
        for eid in df.loc[bad_ha, "event_id"].astype(str).head(200).tolist():
            issues.append((eid, "invalid_home_away", {}))
        df = df.loc[~bad_ha].copy()

    df["game_dt"] = pd.to_datetime(df["game_datetime_utc"], utc=True, errors="coerce")
    missing_dt = df["game_dt"].isna()
    if missing_dt.any():
        for eid in df.loc[missing_dt, "event_id"].astype(str).head(200).tolist():
            issues.append((eid, "missing_game_datetime_utc", {}))
        df = df.loc[~missing_dt].copy()

    df = _safe_numeric(df, BASE_FEATURES + ["points_for", "points_against"])

    # Split sides
    home = df[df["home_away"] == "home"].copy()
    away = df[df["home_away"] == "away"].copy()

    home = _dedupe_side(home, "home", issues)
    away = _dedupe_side(away, "away", issues)

    # Track missing pairs
    home_ids = set(home["event_id"].astype(str).tolist())
    away_ids = set(away["event_id"].astype(str).tolist())
    missing_away = home_ids - away_ids
    missing_home = away_ids - home_ids
    for eid in list(sorted(missing_away))[:200]:
        issues.append((eid, "missing_away_row", {}))
    for eid in list(sorted(missing_home))[:200]:
        issues.append((eid, "missing_home_row", {}))

    merged = home.merge(
        away,
        on=["event_id"],
        suffixes=("_home", "_away"),
        how="inner",
    )

    # Targets: require both scores present
    merged["actual_margin_home"] = merged["points_for_home"] - merged["points_for_away"]
    merged["actual_total"] = merged["points_for_home"] + merged["points_for_away"]

    bad_targets = ~np.isfinite(merged["actual_margin_home"].to_numpy()) | ~np.isfinite(merged["actual_total"].to_numpy())
    if bad_targets.any():
        for eid in merged.loc[bad_targets, "event_id"].astype(str).head(200).tolist():
            issues.append((eid, "missing_scores_for_targets", {}))
        merged = merged.loc[~bad_targets].copy()

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
        for base in sorted(common):
            if any(tok in base.lower() for tok in ignore_tokens):
                continue

            hcol = f"{base}_home"
            acol = f"{base}_away"

            merged[hcol] = pd.to_numeric(merged[hcol], errors="coerce")
            merged[acol] = pd.to_numeric(merged[acol], errors="coerce")

            diff_col = f"{base}_diff_auto"
            merged[diff_col] = merged[hcol] - merged[acol]

            if merged[diff_col].notna().any():
                keep_features.append(diff_col)
                added += 1

        print(f"[INFO] Auto feature fallback added={added}")

    if not keep_features:
        raise ValueError("Feature matrix produced zero usable features.")

    # leakage guard
    _leakage_guard(keep_features)

    output_cols = [
        "event_id",
        "team_id_home",
        "team_id_away",
        "team_home",
        "team_away",
        "game_dt_home",
        "actual_margin_home",
        "actual_total",
    ] + keep_features

    # Ensure all keep features are numeric
    for c in keep_features:
        merged[c] = pd.to_numeric(merged[c], errors="coerce")

    out = merged[output_cols].copy()
    out = out.rename(columns={"game_dt_home": "game_datetime_utc"})

    # deterministic order
    out = out.sort_values(["game_datetime_utc", "event_id"], ascending=[True, True]).reset_index(drop=True)

    audit_rows = _build_audit_rows(issues)
    _write_audit(cfg.out_audit_path, audit_rows)

    cfg.out_schema_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.out_schema_path.write_text(feature_schema_hash() + "\n")

    cfg.out_features_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(cfg.out_features_path, index=False)

    print(f"[INFO] model_features.csv rows={len(out)} cols={len(out.columns)} dq_rows={len(audit_rows)}")

    return out


def main() -> None:
    cfg = BuildConfig()
    build_feature_matrix(cfg)


if __name__ == "__main__":
    main()
