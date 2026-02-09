#!/usr/bin/env python3
"""
Canonical feature schema and hash for ML pipeline.

Design goals:
- Backward compatible with existing imports:
    from schema import FEATURE_SCHEMA, FeatureSpec, feature_schema_hash
- Future-proofed hashing: stable ordering + explicit versioning hooks
- Helpful utilities without requiring downstream changes
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence


# ---- Core schema types ----

@dataclass(frozen=True)
class FeatureSpec:
    name: str
    dtype: str  # "string" | "float" | "int" | "bool" | "datetime" | "json"
    required: bool = True
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    description: Optional[str] = None  # optional, does not impact validation unless you choose to


# NOTE: Keep this list in a stable order.
# Add new fields at the end (or be intentional, because ordering affects hash).
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
    FeatureSpec("games_last_3_days", "float", required=False, min_value=0),
    FeatureSpec("games_last_5_days", "float", required=False, min_value=0),
    FeatureSpec("games_last_7_days", "float", required=False, min_value=0),
    FeatureSpec("games_last_10_days", "float", required=False, min_value=0),
]


# Optional: a version string you can bump if you ever need to change hash behavior intentionally.
# Not required by any callers, but can be useful to store in run logs.
SCHEMA_VERSION: str = "v1"


# ---- Hashing + helpers ----

def _spec_to_dict(spec: FeatureSpec) -> Dict[str, object]:
    # Only include fields that should define the schema contract.
    # description is excluded by default so you can improve docs without drift.
    return {
        "name": spec.name,
        "dtype": spec.dtype,
        "required": bool(spec.required),
        "min_value": spec.min_value,
        "max_value": spec.max_value,
    }


def feature_schema_hash(schema: Iterable[FeatureSpec] = FEATURE_SCHEMA) -> str:
    """
    Stable hash of schema contract.

    Rules:
    - Sorts by feature name to avoid drift from incidental list ordering changes.
    - Excludes `description` so documentation edits don't break the hash.
    - Includes SCHEMA_VERSION so you can deliberately change semantics later.
    """
    specs = list(schema)

    payload: List[Dict[str, object]] = [_spec_to_dict(s) for s in specs]
    payload_sorted = sorted(payload, key=lambda d: str(d.get("name", "")))

    raw = json.dumps(
        {"schema_version": SCHEMA_VERSION, "features": payload_sorted},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    return hashlib.sha256(raw).hexdigest()


def schema_feature_names(schema: Sequence[FeatureSpec] = FEATURE_SCHEMA) -> List[str]:
    return [s.name for s in schema]


def schema_required_names(schema: Sequence[FeatureSpec] = FEATURE_SCHEMA) -> List[str]:
    return [s.name for s in schema if s.required]
