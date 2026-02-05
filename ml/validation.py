#!/usr/bin/env python3
"""
Validation helpers for feature dataframes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

from schema import FeatureSpec


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    severity: str = "warning"


def validate_dataframe(
    df: pd.DataFrame,
    schema: Iterable[FeatureSpec],
    null_threshold: float = 0.5,
    min_rows: int = 10,
) -> Tuple[bool, List[ValidationIssue]]:
    issues: List[ValidationIssue] = []

    if df is None or df.empty:
        return False, [ValidationIssue("empty_df", "Dataframe is empty", "error")]

    if len(df) < min_rows:
        issues.append(
            ValidationIssue(
                "min_rows",
                f"Only {len(df)} rows available (min_rows={min_rows})",
                "warning",
            )
        )

    for spec in schema:
        if spec.required and spec.name not in df.columns:
            issues.append(
                ValidationIssue("missing_column", f"Missing required column: {spec.name}", "error")
            )
            continue

        if spec.name not in df.columns:
            continue

        series = df[spec.name]
        null_rate = float(series.isna().mean())
        if null_rate > null_threshold:
            issues.append(
                ValidationIssue(
                    "high_null_rate",
                    f"{spec.name} null rate {null_rate:.1%} exceeds {null_threshold:.1%}",
                    "warning",
                )
            )

        if spec.dtype == "float":
            numeric = pd.to_numeric(series, errors="coerce")
            if spec.min_value is not None and numeric.min(skipna=True) < spec.min_value:
                issues.append(
                    ValidationIssue(
                        "range_violation",
                        f"{spec.name} min {numeric.min(skipna=True)} < {spec.min_value}",
                        "warning",
                    )
                )
            if spec.max_value is not None and numeric.max(skipna=True) > spec.max_value:
                issues.append(
                    ValidationIssue(
                        "range_violation",
                        f"{spec.name} max {numeric.max(skipna=True)} > {spec.max_value}",
                        "warning",
                    )
                )

    if {"event_id", "team_id"} <= set(df.columns):
        dupes = df.duplicated(subset=["event_id", "team_id"]).sum()
        if dupes > 0:
            issues.append(
                ValidationIssue("duplicate_keys", f"Duplicate event_id/team_id rows: {dupes}", "error")
            )

    leakage_cols = [c for c in df.columns if c.endswith("_post") or c.endswith("_final")]
    if leakage_cols:
        issues.append(
            ValidationIssue(
                "leakage_columns",
                f"Potential leakage columns present: {sorted(leakage_cols)[:10]}",
                "warning",
            )
        )

    fatal = any(issue.severity == "error" for issue in issues)
    return (not fatal), issues
