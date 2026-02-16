#!/usr/bin/env python3
"""
Feature matrix builder for CBB models.

Inputs (preferred order):
  1) Supabase Postgres table: raw.espn_team_game_core (when SUPABASE_DB_URL is set)
  2) Local CSV: espn_team_game_features.csv

Targets (scores) source:
  - Prefer joining from public.games using raw.features.event_id = public.games.external_game_id
    This fixes cases where raw.espn_team_game_features is missing points_for/points_against.

Outputs:
  - ml/model_features.csv
  - ml/dq_audit_ml.csv
  - ml/feature_schema_hash.txt

Key behaviors:
  - Expands JSONB `features` (if present) into columns.
  - Validates schema (FEATURE_SCHEMA) for required fields only.
  - Normalizes home/away and parses game datetime.
  - Dedupe per side (home/away) per event_id deterministically.
  - Builds diff features (home - away) and optional v2 features.
  - Optional auto-diff fallback to reach minimum feature count.
  - Writes a lightweight DQ audit CSV for surfaced issues.
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
DB_TABLE = (os.getenv("ESPN_FEATURES_TABLE") or "espn_team_game_core").strip()
DB_LIMIT = (os.getenv("ESPN_FEATURES_LIMIT") or "").strip()

# Scores table (targets)
SCORES_SCHEMA = (os.getenv("SCORES_SCHEMA") or "public").strip()
SCORES_TABLE = (os.getenv("SCORES_TABLE") or "games").strip()

MIN_FEATURES = int(os.getenv("ML_MIN_FEATURES", "25"))

# IMPORTANT:
# - points_for / points_against are no longer REQUIRED from the raw features store.
#   They are derived from public.games (home_score/away_score) after merging home+away.
REQUIRED_FEATURE_COLS = [
    "event_id",
    "team_id",
    "team",
    "home_away",
    "game_datetime_utc",
]

# --- multi-window expanded lookback features ---
LOOKBACK_WINDOWS = ["l3", "l4", "l5", "l6", "l7", "l10", "l12"]

def _make_lookback_cols(metric: str) -> List[str]:
    """
    Generate all lookback feature names for a given base metric.
    """
    return [f"{metric}_{window}_pre" for window in LOOKBACK_WINDOWS]

BASE_FEATURES = (
    _make_lookback_cols("ortg")
  + _make_lookback_cols("drtg")
  + _make_lookback_cols("netrtg")
  + _make_lookback_cols("pace")
  + _make_lookback_cols("efg")
  + _make_lookback_cols("tov_pct")
  + _make_lookback_cols("orb_pct")
  + _make_lookback_cols("drb_pct")
  + _make_lookback_cols("ftr")
  + _make_lookback_cols("3par")
  + _make_lookback_cols("style_distance")
  + [
        "exp_margin",
        "games_last_3_days",
        "games_last_5_days",
        "games_last_7_days",
        "games_last_10_days",
    ]
)

@dataclass(frozen=True)
class BuildConfig:
    features_path: Path = Path("espn_team_game_features.csv")
    db_schema: str = DB_SCHEMA
    db_table: str = DB_TABLE
    db_limit: str = DB_LIMIT

    scores_schema: str = SCORES_SCHEMA
    scores_table: str = SCORES_TABLE

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
    """
    If a 'features' json/jsonb column exists, expand it into top-level columns.
    """
    if "features" not in df.columns:
        return df

    base_df = df.drop(columns=["features"])
    feature_series = df["features"].apply(_parse_features_cell)
    features_df = pd.json_normalize(feature_series)

    # if json keys collide with base cols, prefer base cols
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
    df = _derive_features_if_needed(df)
    print(f"[INFO] Loaded features from DB: {schema}.{table} rows={len(df)} cols={len(df.columns)}")
    return df


def _load_features_from_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing feature store: {path}")
    df = pd.read_csv(path)
    df = _expand_features_json(df)
    df = _derive_features_if_needed(df)
    print(f"[INFO] Loaded features from CSV: rows={len(df)} cols={len(df.columns)}")
    return df


def _safe_div(numer: pd.Series, denom: pd.Series) -> pd.Series:
    n = pd.to_numeric(numer, errors="coerce")
    d = pd.to_numeric(denom, errors="coerce")
    out = n / d.replace(0, np.nan)
    return out.replace([np.inf, -np.inf], np.nan)


def _team_group_key(df: pd.DataFrame) -> pd.Series:
    """
    Choose a stable grouping key for team-history rollups.
    Prefer team_id, but fall back to normalized team name when ids are unstable.
    """
    if "team_id" in df.columns:
        tid = df["team_id"].astype("string")
        valid = tid.notna() & (tid.str.strip() != "")
        n_valid = int(valid.sum())
        if n_valid > 0:
            uniq_ratio = float(tid[valid].nunique(dropna=True)) / float(n_valid)
            if uniq_ratio < 0.98:
                return tid
    if "team" in df.columns:
        print("[WARN] team_id appears unstable; deriving rollups by normalized team name")
        return df["team"].astype("string").str.strip().str.lower()
    return df.get("team_id", pd.Series(index=df.index, dtype="string")).astype("string")


def _rolling_pre(df: pd.DataFrame, metric: str, group_key: pd.Series) -> pd.DataFrame:
    out = df.copy()
    s = pd.to_numeric(out[metric], errors="coerce")
    for w in LOOKBACK_WINDOWS:
        col = f"{metric}_{w}_pre"
        out[col] = (
            s.groupby(group_key, dropna=False)
            .transform(lambda g: g.shift(1).rolling(window=int(w[1:]), min_periods=1).mean())
        )
    return out


def _games_last_days(df: pd.DataFrame, days: int, group_key: pd.Series) -> pd.Series:
    out = pd.Series(np.nan, index=df.index, dtype=float)
    for _, idx in df.groupby(group_key, dropna=False).groups.items():
        sub = df.loc[idx].sort_values("game_dt")
        ts = sub["game_dt"].astype("int64") // 10**9
        vals = []
        for i, t in enumerate(ts):
            low = t - days * 86400
            c = int(((ts[:i] >= low) & (ts[:i] < t)).sum())
            vals.append(float(c))
        out.loc[sub.index] = vals
    return out


def _derive_features_if_needed(df: pd.DataFrame) -> pd.DataFrame:
    pre_cols = [c for c in df.columns if c.endswith("_l7_pre")]
    if pre_cols:
        return df

    required = {"event_id", "team_id", "team", "home_away", "game_datetime_utc"}
    if not required.issubset(df.columns):
        return df

    # If no precomputed pre-game features exist, derive from boxscore primitives.
    out = df.copy()
    out["game_dt"] = pd.to_datetime(out["game_datetime_utc"], utc=True, errors="coerce")
    out["_team_group_key"] = _team_group_key(out)
    out = out.sort_values(["_team_group_key", "game_dt", "event_id"], na_position="last").reset_index(drop=True)

    # base rates from raw boxscore primitives
    out["poss"] = pd.to_numeric(out.get("fga"), errors="coerce") - pd.to_numeric(out.get("orb"), errors="coerce") + pd.to_numeric(out.get("tov"), errors="coerce") + 0.44 * pd.to_numeric(out.get("fta"), errors="coerce")
    out["efg"] = _safe_div(pd.to_numeric(out.get("fgm"), errors="coerce") + 0.5 * pd.to_numeric(out.get("tpm"), errors="coerce"), out.get("fga"))
    out["ftr"] = _safe_div(out.get("fta"), out.get("fga"))
    out["3par"] = _safe_div(out.get("tpa"), out.get("fga"))
    out["tov_pct"] = _safe_div(out.get("tov"), out.get("poss"))
    out["pace"] = pd.to_numeric(out.get("poss"), errors="coerce")
    out["ortg"] = 100.0 * _safe_div(out.get("points_for"), out.get("poss"))
    out["drtg"] = 100.0 * _safe_div(out.get("points_against"), out.get("poss"))
    out["netrtg"] = out["ortg"] - out["drtg"]

    # opponent rebound context per game for orb_pct/drb_pct
    opp = out[["event_id", "team_id", "orb", "drb"]].rename(columns={"team_id": "opp_team_id", "orb": "opp_orb", "drb": "opp_drb"})
    out = out.merge(opp, on="event_id", how="left")
    out = out[out["team_id"] != out["opp_team_id"]].copy()
    out["orb_pct"] = _safe_div(out.get("orb"), pd.to_numeric(out.get("orb"), errors="coerce") + pd.to_numeric(out.get("opp_drb"), errors="coerce"))
    out["drb_pct"] = _safe_div(out.get("drb"), pd.to_numeric(out.get("drb"), errors="coerce") + pd.to_numeric(out.get("opp_orb"), errors="coerce"))

    metric_candidates = ["ortg", "drtg", "netrtg", "pace", "efg", "tov_pct", "orb_pct", "drb_pct", "ftr", "3par"]
    for m in metric_candidates:
        if m in out.columns:
            out = _rolling_pre(out, m, out["_team_group_key"])

    out["games_last_3_days"] = _games_last_days(out, 3, out["_team_group_key"])
    out["games_last_5_days"] = _games_last_days(out, 5, out["_team_group_key"])
    out["games_last_7_days"] = _games_last_days(out, 7, out["_team_group_key"])
    out["games_last_10_days"] = _games_last_days(out, 10, out["_team_group_key"])
    out["exp_margin"] = out.get("netrtg_l7_pre")
    out = out.drop(columns=["_team_group_key"], errors="ignore")

    print("[INFO] Derived pre-game rolling features from raw boxscore primitives")
    return out


def _load_features(cfg: BuildConfig) -> pd.DataFrame:
    if _db_enabled():
        return _load_features_from_db(cfg.db_schema, cfg.db_table, cfg.db_limit)
    return _load_features_from_csv(cfg.features_path)


def _load_scores_from_db(cfg: BuildConfig) -> pd.DataFrame:
    """
    Load scoring targets from public.games.
    We expect:
      - external_game_id (join key to event_id)
      - home_score, away_score
    """
    if not SUPABASE_DB_URL:
        raise ValueError("SUPABASE_DB_URL is not set")
    if psycopg is None:
        raise ImportError("psycopg is not installed")

    qschema = _quote_ident(cfg.scores_schema)
    qtable = _quote_ident(cfg.scores_table)

    sql = f"""
      select
        cast(external_game_id as text) as event_id,
        home_score,
        away_score,
        verification_status
      from {qschema}.{qtable}
      where external_game_id is not null
      order by external_game_id asc
    """

    with psycopg.connect(SUPABASE_DB_URL) as conn:
        scores = pd.read_sql(sql, conn)

    scores["event_id"] = scores["event_id"].astype(str)
    scores["home_score"] = pd.to_numeric(scores.get("home_score"), errors="coerce")
    scores["away_score"] = pd.to_numeric(scores.get("away_score"), errors="coerce")

    print(f"[INFO] Loaded scores from DB: {cfg.scores_schema}.{cfg.scores_table} rows={len(scores)}")
    return scores

# ============================================================================

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
    """
    out = df.copy()

    out["event_id"] = out["event_id"].astype(str)
    if "team_id" in out.columns:
        out["team_id"] = out["team_id"].astype(str)

    sort_cols: List[str] = ["game_dt", "event_id"]
    if "team_id" in out.columns:
        sort_cols.append("team_id")

    pulled_col = None
    for c in ["pulled_at_utc", "pulled_at"]:
        if c in out.columns:
            pulled_col = c
            break
    if pulled_col:
        out[pulled_col] = pd.to_datetime(out[pulled_col], utc=True, errors="coerce")
        sort_cols.append(pulled_col)

    out = out.sort_values(sort_cols, ascending=[True] * len(sort_cols), na_position="last", kind="mergesort")

    dup_counts = out.groupby("event_id").size()
    dups = dup_counts[dup_counts > 1]
    if len(dups) > 0:
        for eid, n in dups.items():
            issues.append((str(eid), f"duplicate_{side}_rows", {"side": side, "rows": int(n)}))
        out = out.drop_duplicates(subset=["event_id"], keep="last")

    return out


def _leakage_guard(feature_cols: List[str]) -> None:
    """
    We only want pre-game features. Ban anything that smells like outcomes.
    """
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
        required_set = set(REQUIRED_FEATURE_COLS)
        filtered = []
        for i in issues_schema:
            col = getattr(i, "column", None) or getattr(i, "field", None) or ""
            if str(col) in required_set:
                filtered.append(i)
        if filtered:
            raise ValueError("Schema validation failed: " + "; ".join([i.message for i in filtered]))
    
    issues: List[Tuple[str, str, Dict[str, object]]] = []
    
    df["home_away"] = _normalize_home_away(df["home_away"])
    bad_ha = df["home_away"].isna() | (df["home_away"] == "") | (~df["home_away"].isin(["home", "away"]))
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
    
    df = _safe_numeric(df, BASE_FEATURES)
    
    home = df[df["home_away"] == "home"].copy()
    away = df[df["home_away"] == "away"].copy()
    
    home = _dedupe_side(home, "home", issues)
    away = _dedupe_side(away, "away", issues)
    
    home_ids = set(home["event_id"].astype(str).tolist())
    away_ids = set(away["event_id"].astype(str).tolist())
    for eid in sorted(home_ids - away_ids)[:200]:
        issues.append((eid, "missing_away_row", {}))
    for eid in sorted(away_ids - home_ids)[:200]:
        issues.append((eid, "missing_home_row", {}))
    
    merged = home.merge(
        away,
        on=["event_id"],
        suffixes=("_home", "_away"),
        how="inner",
    )
    
    scores = None
    if _db_enabled():
        try:
            scores = _load_scores_from_db(cfg)
        except Exception as e:
            issues.append(("*", "scores_load_failed", {"error": str(e)}))
            scores = None
    
    if scores is not None and len(scores) > 0:
        merged = merged.merge(scores, on="event_id", how="left")
        merged["actual_margin_home"] = merged["home_score"] - merged["away_score"]
        merged["actual_total"] = merged["home_score"] + merged["away_score"]
    else:
        if "points_for_home" in merged.columns and "points_for_away" in merged.columns:
            merged["actual_margin_home"] = pd.to_numeric(merged["points_for_home"], errors="coerce") - pd.to_numeric(
                merged["points_for_away"], errors="coerce"
            )
            merged["actual_total"] = pd.to_numeric(merged["points_for_home"], errors="coerce") + pd.to_numeric(
                merged["points_for_away"], errors="coerce"
            )
            issues.append(("*", "targets_from_raw_points_for", {"note": "Used points_for_* because DB scores unavailable"}))
        else:
            raise ValueError("No targets available. Enable SUPABASE_DB_URL or include points_for_* columns.")
    
    bad_targets = ~np.isfinite(merged["actual_margin_home"].to_numpy()) | ~np.isfinite(merged["actual_total"].to_numpy())
    if bad_targets.any():
        for eid in merged.loc[bad_targets, "event_id"].astype(str).head(200).tolist():
            issues.append((eid, "missing_scores_for_targets", {}))
        merged = merged.loc[~bad_targets].copy()
    
    keep_features: List[str] = []
    
    # BASE_FEATURES diffs (home - away)
    for feat in BASE_FEATURES:
        hcol = f"{feat}_home"
        acol = f"{feat}_away"
        if hcol in merged.columns and acol in merged.columns:
            merged[f"{feat}_diff"] = merged[hcol] - merged[acol]
            keep_features.append(f"{feat}_diff")
    
    merged = add_features_v2(merged)
    keep_features.extend([f for f in FEATURES_V2 if f in merged.columns])
    
    if len(keep_features) < MIN_FEATURES:
        ignore_tokens = ["points", "actual", "margin", "game_dt", "datetime", "team_id", "event_id", "home_score", "away_score"]
        
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
    
    _leakage_guard(keep_features)
    
    for c in keep_features:
        merged[c] = pd.to_numeric(merged[c], errors="coerce")
    
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
    
    out = merged[output_cols].copy()
    out = out.rename(columns={"game_dt_home": "game_datetime_utc"})
    
    out["game_datetime_utc"] = pd.to_datetime(out["game_datetime_utc"], utc=True, errors="coerce").dt.strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    out = out.sort_values(["game_datetime_utc", "event_id"], ascending=[True, True], na_position="last").reset_index(drop=True)
    
    audit_rows = _build_audit_rows(issues)
    _write_audit(cfg.out_audit_path, audit_rows)
    
    cfg.out_schema_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.out_schema_path.write_text(feature_schema_hash() + "\n")
    
    cfg.out_features_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(cfg.out_features_path, index=False)
    
    # ============================================================================
    # ✅ VALIDATION: Catch bugs before training
    # ============================================================================
    print(f"\n[INFO] ============ FEATURE MATRIX VALIDATION ============")
    
    # Validate targets
    for target in ["actual_margin_home", "actual_total"]:
        vals = out[target].to_numpy()
        mean = float(np.nanmean(vals))
        std = float(np.nanstd(vals))
        min_val = float(np.nanmin(vals))
        max_val = float(np.nanmax(vals))
        
        print(f"[INFO] {target}:")
        print(f"       Mean: {mean:.2f}")
        print(f"       Std:  {std:.2f}")
        print(f"       Min:  {min_val:.2f}")
        print(f"       Max:  {max_val:.2f}")
        
        if std < 1.0:
            raise ValueError(f"Target {target} has no variance (std={std:.4f})")
        
        if abs(mean - 24.04) < 0.5 and std < 5.0:
            print(f"[ERROR] Target {target} appears broken: mean={mean:.2f}, std={std:.2f}", file=sys.stderr)
            print(f"[ERROR] This will cause constant predictions. Check target calculation.", file=sys.stderr)
            raise ValueError(f"Target {target} validation failed")
    
    # Validate features
    nan_pct = out[keep_features].isnull().mean().mean()
    print(f"\n[INFO] Features: {len(keep_features)}")
    print(f"[INFO] Feature NaN%: {nan_pct:.1%}")
    
    if nan_pct > 0.80:
        print(f"[ERROR] >80% features are NaN - training will fail", file=sys.stderr)
        worst = out[keep_features].isnull().mean().sort_values(ascending=False).head(10)
        print(f"[ERROR] Worst features:", file=sys.stderr)
        for col, pct in worst.items():
            print(f"        {col}: {pct:.1%}", file=sys.stderr)
        raise ValueError(f"Too many NaN features: {nan_pct:.1%}")
    
    if len(keep_features) < 10:
        print(f"[WARN] Only {len(keep_features)} features - might not be enough for good predictions", file=sys.stderr)
    
    # Sample check: ensure features have variance
    feature_stds = out[keep_features].std()
    zero_var_features = feature_stds[feature_stds == 0].index.tolist()
    if zero_var_features:
        print(f"[WARN] {len(zero_var_features)} features have zero variance:", file=sys.stderr)
        for feat in zero_var_features[:5]:
            print(f"        {feat}", file=sys.stderr)
    
    print(f"[INFO] ✅ Feature matrix validation passed")
    print(f"[INFO] ==============================================\n")
    
    print(f"[INFO] model_features.csv rows={len(out)} cols={len(out.columns)} dq_rows={len(audit_rows)}")
    return out

def main() -> None:
    cfg = BuildConfig()
    build_feature_matrix(cfg)


if __name__ == "__main__":
    main()
