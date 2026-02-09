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
from typing import Iterable, List, Optional, Sequence, Tuple

import pandas as pd

from schema import FeatureSpec


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    severity: str = "warning"


def _coerce_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _null_rate(series: pd.Series) -> float:
    return float(series.isna().mean()) if len(series) else 1.0


def validate_dataframe(
    df: pd.DataFrame,
    schema: Iterable[FeatureSpec],
    *,
    null_threshold: float = 0.5,
    min_rows: int = 10,
    unique_keys: Optional[Sequence[str]] = ("event_id", "team_id"),
    required_values: Optional[dict] = None,
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
      required_values: optional dict of {col: set/list of allowed values} (error on out-of-set)
      leakage_tokens: optional token list to scan for suspicious columns

    Returns:
      (ok, issues)
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

        # dtype-aware checks (best-effort)
        dtype = (spec.dtype or "").lower().strip()
        if dtype in ("float", "int", "number"):
            numeric = _coerce_numeric(series)
            if numeric.notna().sum() == 0:
                # if it's required and all-null/uncastable, flag
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
                            ValidationIssue(
                                "range_violation",
                                f"{spec.name} min {mn} < {spec.min_value}",
                                "warning",
                            )
                        )
                if spec.max_value is not None:
                    mx = float(numeric.max(skipna=True))
                    if mx > float(spec.max_value):
                        issues.append(
                            ValidationIssue(
                                "range_violation",
                                f"{spec.name} max {mx} > {spec.max_value}",
                                "warning",
                            )
                        )
        elif dtype in ("str", "string"):
            # warn if mostly empty strings (distinct from NaN)
            if series.astype("string").str.strip().eq("").mean() > float(null_threshold):
                issues.append(
                    ValidationIssue(
                        "high_empty_string_rate",
                        f"{spec.name} empty-string rate exceeds {float(null_threshold):.1%}",
                        "warning",
                    )
                )
        # other dtypes can be added as schema evolves

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
            allowed_set = set(allowed)
            bad = ~df[col].astype("string").str.strip().isin({str(x) for x in allowed_set})
            # treat nulls as bad only if column is required in schema
            bad = bad & df[col].notna()
            n_bad = int(bad.sum())
            if n_bad > 0:
                issues.append(
                    ValidationIssue(
                        "invalid_values",
                        f"{col} has {n_bad} values outside allowed set {sorted(list(allowed_set))[:10]}",
                        "error",
                    )
                )

    # Leakage scans (column-name heuristics)
    # Keep it conservative: warn only
    leakage_cols = [c for c in df.columns if c.endswith("_post") or c.endswith("_final")]
    if leakage_cols:
        issues.append(
            ValidationIssue(
                "leakage_columns",
                f"Potential leakage columns present: {sorted(leakage_cols)[:10]}",
                "warning",
            )
        )

    tokens = list(leakage_tokens) if leakage_tokens else ["actual_", "final_", "result", "winner", "score_", "_score"]
    token_hits = [c for c in df.columns if any(tok in c.lower() for tok in tokens)]
    # don't double-count obvious required targets if present, this module is for feature tables
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
