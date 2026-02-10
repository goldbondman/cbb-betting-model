#!/usr/bin/env python3
"""
Canonical feature schema and hash for ML pipeline.

This schema is for the *raw per-team, per-game feature store* (home/away rows)
before we build diffs and targets.

Key clarifications:
- event_id/team_id/team/home_away/game_datetime_utc are required inputs.
- points_for/points_against are OPTIONAL in the feature store.
  Targets are ideally joined from public.games (home_score/away_score) downstream.
- All "pre" features are optional and can be sparsely populated early in pipeline.

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

_ALLOWED_DTYPES = {"string", "float", "int", "bool", "datetime", "json"}


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    dtype: str  # "string" | "float" | "int" | "bool" | "datetime" | "json"
    required: bool = True
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    description: Optional[str] = None  # excluded from hash by default

    def __post_init__(self) -> None:
        # dataclass is frozen, so we can only validate (not mutate)
        if self.dtype not in _ALLOWED_DTYPES:
            raise ValueError(f"Unsupported dtype '{self.dtype}' for feature '{self.name}'. Allowed: {sorted(_ALLOWED_DTYPES)}")
        if self.min_value is not None and self.max_value is not None and self.min_value > self.max_value:
            raise ValueError(f"Invalid bounds for '{self.name}': min_value > max_value")


# Optional: bump if you intentionally change schema semantics and want hash drift to reflect it.
SCHEMA_VERSION: str = "v2"


# NOTE: Keep this list in a stable order.
# Add new fields at the end (or be intentional, because ordering affects hash).
FEATURE_SCHEMA: List[FeatureSpec] = [
    # ---- Required identifiers / join keys ----
    FeatureSpec("event_id", "string", description="ESPN event id (join key to games.external_game_id)"),
    FeatureSpec("team_id", "string", description="ESPN team id for this row"),
    FeatureSpec("team", "string", description="Team display name"),
    FeatureSpec("home_away", "string", description="home|away"),
    FeatureSpec("game_datetime_utc", "datetime", description="Game start datetime in UTC"),

    # ---- OPTIONAL: outcomes/scores (prefer sourced from public.games) ----
    FeatureSpec("points_for", "float", required=False, min_value=0, max_value=250, description="Optional, team points scored"),
    FeatureSpec("points_against", "float", required=False, min_value=0, max_value=250, description="Optional, opponent points allowed"),

    # ---- Optional pre-game features (may be sparse early on) ----
    FeatureSpec("ortg_l7_pre", "float", required=False, description="Offensive rating last 7 (pre-game)"),
    FeatureSpec("drtg_l7_pre", "float", required=False, description="Defensive rating last 7 (pre-game)"),
    FeatureSpec("netrtg_l7_pre", "float", required=False, description="Net rating last 7 (pre-game)"),
    FeatureSpec("pace_l7_pre", "float", required=False, description="Pace last 7 (pre-game)"),
    FeatureSpec("efg_l7_pre", "float", required=False, description="eFG% last 7 (pre-game)"),
    FeatureSpec("tov_pct_l7_pre", "float", required=False, description="TOV% last 7 (pre-game)"),
    FeatureSpec("orb_pct_l7_pre", "float", required=False, description="ORB% last 7 (pre-game)"),
    FeatureSpec("drb_pct_l7_pre", "float", required=False, description="DRB% last 7 (pre-game)"),
    FeatureSpec("ftr_l7_pre", "float", required=False, description="FT rate last 7 (pre-game)"),
    FeatureSpec("3par_l7_pre", "float", required=False, description="3PA rate last 7 (pre-game)"),
    FeatureSpec("exp_margin", "float", required=False, description="Expected margin (pre-game, model-derived)"),
    FeatureSpec("style_distance_l7", "float", required=False, description="Style distance metric (last 7, pre-game)"),
    FeatureSpec("games_last_3_days", "float", required=False, min_value=0, description="Count of games in last 3 days"),
    FeatureSpec("games_last_5_days", "float", required=False, min_value=0, description="Count of games in last 5 days"),
    FeatureSpec("games_last_7_days", "float", required=False, min_value=0, description="Count of games in last 7 days"),
    FeatureSpec("games_last_10_days", "float", required=False, min_value=0, description="Count of games in last 10 days"),
]


# ---- Hashing + helpers ----

def _spec_to_dict(spec: FeatureSpec) -> Dict[str, object]:
    # Only include fields that define the schema contract.
    # description is excluded so you can improve docs without drift.
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


def schema_optional_names(schema: Sequence[FeatureSpec] = FEATURE_SCHEMA) -> List[str]:
    return [s.name for s in schema if not s.required]


def schema_lookup(schema: Sequence[FeatureSpec] = FEATURE_SCHEMA) -> Dict[str, FeatureSpec]:
    """
    Convenience: name -> FeatureSpec (detects duplicates early if introduced).
    """
    out: Dict[str, FeatureSpec] = {}
    for s in schema:
        if s.name in out:
            raise ValueError(f"Duplicate feature name in schema: {s.name}")
        out[s.name] = s
    return out
