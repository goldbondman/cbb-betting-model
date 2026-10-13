"""Fail-closed Stage A feature extraction for College Basketball forecasts.

This module enforces CBB-STAGEA-FTR-v1.0 at the handoff between mixed
analytics tables and blind forecasting.  It is intentionally additive: it
neither changes the existing ESPN matchup builder nor infers a model feature
set from dataframe dtypes or column names.

The consuming model must provide an explicit predictor allowlist.  Outcome,
current-game blowout, market, eligibility, and lineage fields never enter X.
Market-like columns are treated more strictly than ordinary mixed-table labels:
their presence means the Stage A source boundary was contaminated.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence

import pandas as pd


CONTRACT_VERSION = "CBB-STAGEA-FTR-v1.0"
EXTRACTOR_VERSION = "CBB-STAGEA-EXTRACTOR-v1.0"

REQUIRED_LINEAGE_FIELDS = (
    "source_id",
    "source_version",
    "source_snapshot_id",
    "feature_contract_version",
    "model_version",
    "forecast_at",
    "data_cutoff",
)

# These fields may coexist in mixed training/research tables, but they cannot be
# selected as predictors for the same target game.
_FORBIDDEN_PREDICTOR_BASES = {
    "points_for",
    "points_against",
    "margin",
    "home_points",
    "away_points",
    "home_win",
    "away_win",
    "home_score",
    "away_score",
    "winner",
    "blowout",
    "completed",
    "state",
    "status",
    "status_desc",
    "status_detail",
    "data_ok",
    "opp_join_ok",
    "source",
    "parse_version",
    "pulled_at_utc",
    "row_hash",
}

_MARKET_BASES = {
    "market",
    "spread",
    "total",
    "moneyline",
    "odds",
    "price",
    "edge",
    "implied_probability",
    "book",
    "bookmaker",
    "sportsbook",
    "wager",
    "bet",
    "bet_decision",
    "market_home_ml",
    "market_away_ml",
    "market_spread",
    "market_total",
}


class StageAExtractionError(RuntimeError):
    """Base fail-closed Stage A extraction error."""

    decision_state = "MODEL_BLOCKED"
    reason_code = "STAGE_A_EXTRACTION_ERROR"


class StageADataBlocked(StageAExtractionError):
    """Required point-in-time data or lineage is missing/invalid."""

    decision_state = "DATA_BLOCKED"
    reason_code = "DATA_BLOCKED"


class StageAModelBlocked(StageAExtractionError):
    """The requested predictor contract itself is unsafe or invalid."""

    decision_state = "MODEL_BLOCKED"
    reason_code = "MODEL_BLOCKED"


class StageAMarketContamination(StageAExtractionError):
    """Market information crossed the Stage A source boundary."""

    decision_state = "MODEL_BLOCKED"
    reason_code = "MARKET_CONTAMINATION"


@dataclass(frozen=True)
class StageASnapshot:
    """Deterministic blind-forecast input snapshot."""

    predictors: pd.DataFrame
    predictor_columns: tuple[str, ...]
    eligibility_columns: tuple[str, ...]
    lineage: Mapping[str, str]
    snapshot_hash: str
    contract_version: str = CONTRACT_VERSION
    extractor_version: str = EXTRACTOR_VERSION


def _base_name(column: str) -> str:
    """Normalize home/away prefixes without guessing arbitrary schema roles."""

    name = str(column).strip().lower()
    # Matchup rows prefix side-specific fields with h_ / a_.  Strip only these
    # known side prefixes; do not recursively infer roles from arbitrary names.
    if name.startswith("h_") or name.startswith("a_"):
        name = name[2:]
    return name


def _contains_token(name: str, token: str) -> bool:
    parts = [p for p in re.split(r"[^a-z0-9]+", name.lower()) if p]
    token_parts = [p for p in token.lower().split("_") if p]
    if not token_parts:
        return False
    if len(token_parts) == 1:
        return token_parts[0] in parts
    for i in range(0, len(parts) - len(token_parts) + 1):
        if parts[i : i + len(token_parts)] == token_parts:
            return True
    return False


def _is_market_column(column: str) -> bool:
    base = _base_name(column)
    if base in _MARKET_BASES:
        return True
    return any(_contains_token(base, token) for token in _MARKET_BASES)


def _is_forbidden_predictor(column: str) -> bool:
    base = _base_name(column)
    return base in _FORBIDDEN_PREDICTOR_BASES


def _truthy(value: Any) -> bool:
    if value is True:
        return True
    if value is False or value is None:
        return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and math.isnan(value):
            return False
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "1.0", "yes", "y"}
    return False


def _canonical_value(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (ValueError, AttributeError):
            pass
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _normalize_lineage(lineage: Mapping[str, Any]) -> dict[str, str]:
    missing = [
        key
        for key in REQUIRED_LINEAGE_FIELDS
        if key not in lineage or lineage.get(key) is None or str(lineage.get(key)).strip() == ""
    ]
    if missing:
        raise StageADataBlocked(f"Missing required Stage A lineage fields: {missing}")

    normalized = {key: str(lineage[key]).strip() for key in REQUIRED_LINEAGE_FIELDS}
    if normalized["feature_contract_version"] != CONTRACT_VERSION:
        raise StageAModelBlocked(
            "Feature-contract version mismatch: "
            f"expected {CONTRACT_VERSION}, got {normalized['feature_contract_version']}"
        )

    forecast_at = pd.to_datetime(normalized["forecast_at"], utc=True, errors="coerce")
    data_cutoff = pd.to_datetime(normalized["data_cutoff"], utc=True, errors="coerce")
    if pd.isna(forecast_at) or pd.isna(data_cutoff):
        raise StageADataBlocked("forecast_at and data_cutoff must be valid timestamps")
    if data_cutoff > forecast_at:
        raise StageADataBlocked(
            f"Point-in-time violation: data_cutoff {data_cutoff.isoformat()} is after "
            f"forecast_at {forecast_at.isoformat()}"
        )

    normalized["forecast_at"] = forecast_at.isoformat()
    normalized["data_cutoff"] = data_cutoff.isoformat()
    return normalized


def _snapshot_hash(
    frame: pd.DataFrame,
    predictor_columns: Sequence[str],
    lineage: Mapping[str, str],
) -> str:
    records = []
    for row in frame.to_dict(orient="records"):
        records.append({k: _canonical_value(row.get(k)) for k in frame.columns})
    payload = {
        "contract_version": CONTRACT_VERSION,
        "extractor_version": EXTRACTOR_VERSION,
        "predictor_columns": list(predictor_columns),
        "lineage": dict(sorted(lineage.items())),
        "records": records,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def extract_stage_a_snapshot(
    source: pd.DataFrame,
    predictor_columns: Sequence[str],
    *,
    eligibility_columns: Sequence[str],
    lineage: Mapping[str, Any],
    event_id_column: str = "event_id",
) -> StageASnapshot:
    """Return a deterministic, fail-closed Stage A predictor snapshot.

    Parameters
    ----------
    source:
        Mixed analytics table.  Outcome labels may coexist here and are ignored
        unless a caller tries to request them as predictors.  Market-like source
        columns are never allowed because their presence indicates that Stage B
        information crossed the Stage A boundary.
    predictor_columns:
        Explicit, consuming-model-approved predictor columns.  No dtype- or
        name-based automatic feature discovery occurs.
    eligibility_columns:
        Explicit hard gates such as h_data_ok/a_data_ok/h_opp_join_ok/
        a_opp_join_ok.  Every value must evaluate true for every target row.
    lineage:
        Required immutable source/version/snapshot/model/timestamp metadata.
    event_id_column:
        Stable target-event identifier used for uniqueness and deterministic
        ordering.  It is returned for joining/audit but never counted as a
        predictor.
    """

    if not isinstance(source, pd.DataFrame):
        raise StageADataBlocked("Stage A source must be a pandas DataFrame")
    if source.empty:
        raise StageADataBlocked("Stage A source contains no target rows")

    predictors = tuple(str(c) for c in predictor_columns)
    eligibility = tuple(str(c) for c in eligibility_columns)
    if not predictors:
        raise StageAModelBlocked("Predictor allowlist must be explicit and non-empty")
    if len(set(predictors)) != len(predictors):
        raise StageAModelBlocked("Predictor allowlist contains duplicate columns")

    # Market isolation is stricter than ordinary label separation.  Market-like
    # fields anywhere in the source mean Stage A was contaminated before feature
    # extraction, even if the caller did not request those columns.
    market_columns = [c for c in source.columns if _is_market_column(str(c))]
    if market_columns:
        raise StageAMarketContamination(
            f"Market-like columns are present in the Stage A source: {market_columns}"
        )
    requested_market = [c for c in predictors if _is_market_column(c)]
    if requested_market:
        raise StageAMarketContamination(
            f"Market-like predictors are forbidden in Stage A: {requested_market}"
        )

    forbidden = [c for c in predictors if _is_forbidden_predictor(c)]
    if forbidden:
        raise StageAModelBlocked(
            f"Outcome/eligibility/lineage fields are forbidden as Stage A predictors: {forbidden}"
        )

    required_columns = [event_id_column, *predictors, *eligibility]
    missing_columns = [c for c in required_columns if c not in source.columns]
    if missing_columns:
        raise StageADataBlocked(f"Missing required Stage A columns: {missing_columns}")

    if source[event_id_column].isna().any():
        raise StageADataBlocked("Stage A event identifier contains null values")
    if source[event_id_column].astype(str).duplicated().any():
        dupes = source.loc[
            source[event_id_column].astype(str).duplicated(keep=False), event_id_column
        ].astype(str).unique().tolist()
        raise StageADataBlocked(f"Duplicate Stage A event identifiers: {dupes[:10]}")

    for column in eligibility:
        failed = ~source[column].map(_truthy)
        if failed.any():
            failed_ids = source.loc[failed, event_id_column].astype(str).tolist()
            raise StageADataBlocked(
                f"Eligibility gate {column} failed for event(s): {failed_ids[:10]}"
            )

    for column in predictors:
        if source[column].isna().any():
            failed_ids = source.loc[source[column].isna(), event_id_column].astype(str).tolist()
            raise StageADataBlocked(
                f"Required predictor {column} is missing for event(s): {failed_ids[:10]}"
            )

    normalized_lineage = _normalize_lineage(lineage)

    # Deterministic output: sort target rows by stable event id and emit only id
    # plus the caller's explicit predictor order.  Eligibility/lineage/outcomes
    # never enter the model matrix.
    out = source[[event_id_column, *predictors]].copy()
    out[event_id_column] = out[event_id_column].astype(str)
    out = out.sort_values(event_id_column, kind="mergesort").reset_index(drop=True)

    digest = _snapshot_hash(out, predictors, normalized_lineage)
    return StageASnapshot(
        predictors=out,
        predictor_columns=predictors,
        eligibility_columns=eligibility,
        lineage=normalized_lineage,
        snapshot_hash=digest,
    )
