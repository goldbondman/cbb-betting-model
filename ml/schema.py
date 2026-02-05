#!/usr/bin/env python3
"""
Canonical feature schema and hash for ML pipeline.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable, List, Optional


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    dtype: str
    required: bool = True
    min_value: Optional[float] = None
    max_value: Optional[float] = None


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


def feature_schema_hash(schema: Iterable[FeatureSpec] = FEATURE_SCHEMA) -> str:
    payload = [
        {
            "name": spec.name,
            "dtype": spec.dtype,
            "required": spec.required,
            "min_value": spec.min_value,
            "max_value": spec.max_value,
        }
        for spec in schema
    ]
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
