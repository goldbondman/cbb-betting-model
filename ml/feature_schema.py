#!/usr/bin/env python3
"""
Canonical feature schema for the ML pipeline.

Defines expected column names, dtypes, and optional ranges.
Use feature_schema_hash() to detect drift and store with each run.

Future-proofing goals in this version:
- Backwards-compatible exports: FeatureSpec, FEATURE_SCHEMA, feature_schema_hash
- Stable hashing: canonical JSON, consistent float handling, optional exclusions
- Gentle evolution: allow extending schema without breaking current callers
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from typing import Dict, Iterable, List, Optional, Sequence, Set


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    dtype: str
    required: bool = True
    min_value: Optional[float] = None
    max_value: Optional[float] = None

    def to_dict(self) -> Dict[str, object]:
        d = asdict(self)
        # Normalize dtype for stability (defensive; keeps current strings unchanged)
        d["dtype"] = str(d["dtype"]).strip().lower()
        return d


# NOTE: Keep this list minimal and "upstream-facing".
# This schema validates raw feature ingestion rows (team-level pregame features),
# not the engineered model matrix (diffs, v2 features, etc).
FEATURE_SCHEMA: List[FeatureSpec] = [
    FeatureSpec("event_id", "string"),
    FeatureSpec("team_id", "string"),
    FeatureSpec("team", "string"),
    FeatureSpec("home_away", "string"),
    FeatureSpec("game_datetime_utc", "datetime"),
    FeatureSpec("points_for", "float"),
    FeatureSpec("points_against", "float"),
    FeatureSpec("ortg_l7_pre", "float", required=False),
    FeatureSpec("drtg_l7_pre", "float", required=False),
    FeatureSpec("netrtg_l7_pre", "float", required=False),
    FeatureSpec("pace_l7_pre", "float", required=False),
    FeatureSpec("efg_l7_pre", "float", required=False),
    FeatureSpec("tov_pct_l7_pre", "float", required=False),
    FeatureSpec("orb_pct_l7_pre", "float", required=False),
    FeatureSpec("drb_pct_l7_pre", "float", required=False),
    FeatureSpec("ftr_l7_pre", "float", required=False),
    FeatureSpec("3par_l7_pre", "float", required=False),
    FeatureSpec("exp_margin", "float", required=False),
    FeatureSpec("style_distance_l7", "float", required=False),
    FeatureSpec("games_last_3_days", "float", required=False, min_value=0.0),
    FeatureSpec("games_last_5_days", "float", required=False, min_value=0.0),
    FeatureSpec("games_last_7_days", "float", required=False, min_value=0.0),
    FeatureSpec("games_last_10_days", "float", required=False, min_value=0.0),
]


def _normalize_float(x: Optional[float]) -> Optional[float]:
    """
    Hash-stability helper:
    - Keep None as None
    - Convert ints to float
    - Normalize -0.0 to 0.0
    """
    if x is None:
        return None
    try:
        v = float(x)
    except Exception:
        return None
    if v == 0.0:
        return 0.0
    return v


def feature_schema_payload(
    schema: Iterable[FeatureSpec] = FEATURE_SCHEMA,
    *,
    exclude: Optional[Set[str]] = None,
) -> List[Dict[str, object]]:
    """
    Returns a JSON-serializable payload used for hashing and logging.

    exclude: optional set of FeatureSpec.name values to omit from payload/hash.
    """
    ex = exclude or set()
    payload: List[Dict[str, object]] = []
    for spec in schema:
        if spec.name in ex:
            continue
        d = spec.to_dict()
        d["min_value"] = _normalize_float(spec.min_value)
        d["max_value"] = _normalize_float(spec.max_value)
        payload.append(d)
    return payload


def feature_schema_hash(
    schema: Iterable[FeatureSpec] = FEATURE_SCHEMA,
    *,
    exclude: Optional[Set[str]] = None,
) -> str:
    """
    Stable hash for schema drift detection.

    - Canonical JSON (sort_keys=True, separators)
    - Normalized float fields
    - Optional exclude set for selective hashing
    """
    payload = feature_schema_payload(schema, exclude=exclude)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
