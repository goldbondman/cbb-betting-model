#!/usr/bin/env python3
"""
Validation helpers for feature dataframes.

Updates:
- Stronger dtype checking (including int/string/bool/json where applicable)
- Safer range checks (skip when all-null after coercion)
- Optional unique key enforcement with configurable keys
- Optional "required_values" check for categorical columns (eg home_away in {home, away})
- Leakage token scan (wider than suffix-only)
- Returns ok=False only for severity=error issues
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import json
import pandas as pd

from schema import FeatureSpec


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    severity: str = "warning"


def _coerce_numeric(series: pd.Series) -> pd.Series:
    # Works for ints/floats/strings; coerces bad values to NaN
    return pd.to_numeric(series, errors="coerce")


def _null_rate(series: pd.Series) -> float:
    return float(series.isna().mean()) if len(series) else 1.0


def _empty_string_rate(series: pd.Series) -> float:
    s = series.astype("string")
    # Count empties among non-null rows
    non_null = s.notna()
    if not bool(non_null.any()):
        return 0.0
    return float(s[non_null].str.strip().eq("").mean())


def _coerce_datetime(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", utc=True)


def _is_boolish(series: pd.Series) -> bool:
    # Accept actual bool dtype, or strings like "true/false/1/0/yes/no"
    if str(series.dtype).lower() == "bool":
        return True
    s = series.astype("string").str.strip().str.lower()
    s = s[s.notna()]
    if len(s) == 0:
        return True
    allowed = {"true", "false", "1", "0", "yes", "no", "y", "n", "t", "f"}
    sample = s.head(200)
    return bool(sample.isin(allowed).mean() >= 0.95)


def _is_jsonish(series: pd.Series) -> bool:
    # Conservative: only checks a sample of non-null strings
    s = series.dropna()
    if s.empty:
        return True
    sample = s.astype("string").head(50)
    ok = 0
    for v in sample:
        txt = str(v).strip()
        if not txt:
            ok += 1
            continue
        if txt[0] not in ("{", "["):
            continue
        try:
            json.loads(txt)
            ok += 1
        except Exception:
            pass
    return ok >= max(1, int(0.7 * len(sample)))


def validate_dataframe(
    df: pd.DataFrame,
    schema: Iterable[FeatureSpec],
    *,
    null_threshold: float = 0.5,
    min_rows: int = 10,
    unique_keys: Optional[Sequence[str]] = ("event_id", "team_id"),
    required_values: Optional[Dict[str, Sequence[object]]] = None,
    leakage_tokens: Optional[Sequence[str]] = None,
) -> Tuple[bool, List[ValidationIssue]]:
    """
    Validate a dataframe against a FeatureSpec schema.

    Args:
      df: input dataframe
      schema: iterable of FeatureSpec
      null_threshold: warn if a column's null rate exceeds this threshold
      min_rows: warn if fewer rows than this
      unique_keys: if provided and all columns exist, enforce uniqueness (error on dupes)
      required_values: optional dict of {col: allowed values} (error on out-of-set, nulls ignored)
      leakage_tokens: optional token list to scan for suspicious columns

    Returns:
      (ok, issues) where ok=False only if any severity="error" issues exist.
    """
    issues: List[ValidationIssue] = []

    if df is None or df.empty:
        return False, [ValidationIssue("empty_df", "Dataframe is empty", "error")]

    if len(df) < int(min_rows):
        issues.append(
            ValidationIssue(
                "min_rows",
                f"Only {len(df)} rows available (min_rows={min_rows})",
                "warning",
            )
        )

    # Required columns + per-column checks
    for spec in schema:
        if spec.required and spec.name not in df.columns:
            issues.append(ValidationIssue("missing_column", f"Missing required column: {spec.name}", "error"))
            continue

        if spec.name not in df.columns:
            continue

        series = df[spec.name]

        nr = _null_rate(series)
        if nr > float(null_threshold):
            issues.append(
                ValidationIssue(
                    "high_null_rate",
                    f"{spec.name} null rate {nr:.1%} exceeds {float(null_threshold):.1%}",
                    "warning",
                )
            )

        dtype = (spec.dtype or "").lower().strip()

        # dtype-aware checks (best-effort, non-fatal by default)
        if dtype in ("float", "int", "number"):
            numeric = _coerce_numeric(series)
            if numeric.notna().sum() == 0:
                if spec.required:
                    issues.append(
                        ValidationIssue(
                            "dtype_coerce_failed",
                            f"{spec.name} could not be coerced to numeric (all values non-numeric/null).",
                            "warning",
                        )
                    )
            else:
                if spec.min_value is not None:
                    mn = float(numeric.min(skipna=True))
                    if mn < float(spec.min_value):
                        issues.append(
                            ValidationIssue("range_violation", f"{spec.name} min {mn} < {spec.min_value}", "warning")
                        )
                if spec.max_value is not None:
                    mx = float(numeric.max(skipna=True))
                    if mx > float(spec.max_value):
                        issues.append(
                            ValidationIssue("range_violation", f"{spec.name} max {mx} > {spec.max_value}", "warning")
                        )

        elif dtype in ("str", "string"):
            esr = _empty_string_rate(series)
            if esr > float(null_threshold):
                issues.append(
                    ValidationIssue(
                        "high_empty_string_rate",
                        f"{spec.name} empty-string rate {esr:.1%} exceeds {float(null_threshold):.1%}",
                        "warning",
                    )
                )

        elif dtype in ("datetime", "date", "timestamp"):
            dt = _coerce_datetime(series)
            if spec.required and dt.notna().sum() == 0:
                issues.append(
                    ValidationIssue(
                        "dtype_coerce_failed",
                        f"{spec.name} could not be coerced to datetime (all values invalid/null).",
                        "warning",
                    )
                )

        elif dtype in ("bool", "boolean"):
            if not _is_boolish(series):
                issues.append(
                    ValidationIssue(
                        "dtype_coerce_failed",
                        f"{spec.name} does not look boolean-like (expected bool/true/false/1/0).",
                        "warning",
                    )
                )

        elif dtype in ("json", "dict", "object"):
            if not _is_jsonish(series):
                issues.append(
                    ValidationIssue(
                        "dtype_coerce_failed",
                        f"{spec.name} does not look JSON-like in sampled rows.",
                        "warning",
                    )
                )

    # Unique key enforcement
    if unique_keys:
        keys = list(unique_keys)
        if set(keys) <= set(df.columns):
            dupes = int(df.duplicated(subset=keys).sum())
            if dupes > 0:
                issues.append(ValidationIssue("duplicate_keys", f"Duplicate key rows on {keys}: {dupes}", "error"))

    # Allowed values checks (optional)
    if required_values:
        for col, allowed in required_values.items():
            if col not in df.columns:
                continue
            allowed_set = {str(x).strip() for x in allowed}
            s = df[col].astype("string").str.strip()
            # ignore nulls; this check is about invalid concrete values
            mask = s.notna() & ~s.isin(allowed_set)
            n_bad = int(mask.sum())
            if n_bad > 0:
                issues.append(
                    ValidationIssue(
                        "invalid_values",
                        f"{col} has {n_bad} values outside allowed set {sorted(list(allowed_set))[:10]}",
                        "error",
                    )
                )

    # Leakage scans (column-name heuristics)
    leakage_cols = [c for c in df.columns if c.endswith("_post") or c.endswith("_final")]
    if leakage_cols:
        issues.append(
            ValidationIssue(
                "leakage_columns",
                f"Potential leakage columns present: {sorted(leakage_cols)[:10]}",
                "warning",
            )
        )

    tokens = list(leakage_tokens) if leakage_tokens else [
        "actual_",
        "final_",
        "result",
        "winner",
        "score_",
        "_score",
        "closing",
        "closing_line",
        "vegas_",
        "spread",
        "total",
    ]
    token_hits = [c for c in df.columns if any(tok in c.lower() for tok in tokens)]
    if token_hits:
        issues.append(
            ValidationIssue(
                "leakage_token_hits",
                f"Potential leakage token hits in columns: {sorted(token_hits)[:10]}",
                "warning",
            )
        )

    fatal = any(issue.severity == "error" for issue in issues)
    return (not fatal), issues
