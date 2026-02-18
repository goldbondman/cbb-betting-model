"""Centralised Supabase schema, table, and column-alias definitions.

Keeps table names, schema references, and column-mapping logic in one
place so that ``daily_auto_predict.py``, ``data_loader.py``,
``normalize_raw_to_public.py``, and ``load_csv_to_db.py`` can all import
from here rather than maintaining their own copies.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Schema names (override via env for test / staging)
# ---------------------------------------------------------------------------
RAW_SCHEMA: str = (os.getenv("RAW_SCHEMA") or "raw").strip()
PUBLIC_SCHEMA: str = "public"

# ---------------------------------------------------------------------------
# Table names – raw layer
# ---------------------------------------------------------------------------
RAW_GAMES_TABLE: str = (os.getenv("RAW_GAMES_TABLE") or "espn_games").strip()
RAW_LOGS_TABLE: str = (os.getenv("RAW_LOGS_TABLE") or "espn_team_game_logs").strip()
RAW_FEATURES_TABLE: str = (os.getenv("RAW_FEATURES_TABLE") or "espn_team_game_features").strip()
RAW_PREDICTIONS_TABLE: str = (os.getenv("RAW_PREDICTIONS_TABLE") or "predictions_latest").strip()
RAW_PREDICTIONS_SCHEMA: str = (os.getenv("RAW_PREDICTIONS_SCHEMA") or "raw").strip()
RAW_PREDICTIONS_LIMIT: int = int(os.getenv("RAW_PREDICTIONS_LIMIT", "10000"))

# ---------------------------------------------------------------------------
# Table names – public layer
# ---------------------------------------------------------------------------
PUBLIC_TEAMS_TABLE: str = "teams"
PUBLIC_GAMES_TABLE: str = "games"
PUBLIC_PREDICTIONS_TABLE: str = "predictions"
PUBLIC_MARKET_LINES_TABLE: str = "market_lines"
PUBLIC_DQ_AUDIT_TABLE: str = "dq_audit"

# ---------------------------------------------------------------------------
# Column alias groups
#
# When the same semantic field has different names across pipeline stages
# (CSV columns, raw tables, public tables), these lists let callers
# resolve the first available name without duplicating the mapping.
# ---------------------------------------------------------------------------
PREDICTION_MARGIN_ALIASES: list[str] = [
    "pred_margin_home",
    "pred_spread",
    "ensemble_prediction",
]

PREDICTION_TOTAL_ALIASES: list[str] = [
    "pred_total",
    "predicted_total",
]

MARKET_SPREAD_ALIASES: list[str] = [
    "market_spread",
    "vegas_spread",
    "spread",
    "spread_home",
    "closing_spread_home",
]

HOME_TEAM_ALIASES: list[str] = [
    "team_home",
    "home_team",
]

AWAY_TEAM_ALIASES: list[str] = [
    "team_away",
    "away_team",
]
