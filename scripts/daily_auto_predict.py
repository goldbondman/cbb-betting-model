#!/usr/bin/env python3
"""
Daily auto-prediction pipeline:
1) Pull ESPN scoreboard games.
2) Upsert ingestion outputs to Supabase (teams, games, market_lines, dq_audit, raw_games).
3) Build model feature matrix.
4) Run ML predictions.
5) (TODO) Map predictions into public.predictions schema and upsert.

Notes (current state, Option 1):
- ML pipeline still reads local CSV feature store(s), e.g. espn_team_game_features.csv.
  The workflow should run espn_boxscore_builder.py before this script so the CSV exists.
- DB schemas per your dump:
  - public.teams: team_id (text) PK
  - public.games: game_id (text) PK
  - public.market_lines: id (uuid) PK, game_id/book/pulled_at required
  - public.dq_audit: id (uuid) + created_at (timestamptz) required; reason_codes is text[]; details is jsonb
  - raw.raw_games exists (also mirrored in public.raw_games)
  - public.predictions does NOT match the old "pred_spread/edge_spread" payload (TODO mapping)
"""

from __future__ import annotations

import json
import math
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
from supabase import create_client

# --- Ensure repo root is on sys.path so imports work when running: python scripts/daily_auto_predict.py
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import espn_boxscore_builder as espn  # noqa: E402
from ml.feature_matrix import BuildConfig, build_feature_matrix  # noqa: E402
from ml.predict_ml import PredictConfig, predict  # noqa: E402


SOURCE = "ESPN"
EDGE_MIN = float(os.getenv("EDGE_MIN", "3.0"))
EDGE_TIER2 = float(os.getenv("EDGE_TIER2", "6.0"))
EDGE_TIER3 = float(os.getenv("EDGE_TIER3", "9.0"))
MODEL_VERSION = os.getenv("MODEL_VERSION", "").strip()
DAYS_AHEAD = int(os.getenv("DAYS_AHEAD", "1"))
SEASON = int(os.getenv("SEASON", datetime.now().year + (1 if datetime.now().month >= 7 else 0)))

# You exposed "raw", so default to raw; can override to "public" if desired.
RAW_SCHEMA = (os.getenv("RAW_SCHEMA") or "raw").strip()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _safe_num(value: object) -> Optional[float]:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _has_value(value: object) -> bool:
    return _safe_num(value) is not None


def _has_text(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, float) and math.isnan(value):
        return False
    return bool(str(value).strip())


def _sanitize_payload(payload: Dict[str, object]) -> Dict[str, object]:
    cleaned: Dict[str, object] = {}
    for key, value in payload.items():
        if isinstance(value, float) and math.isnan(value):
            cleaned[key] = None
        else:
            cleaned[key] = value
    return cleaned


def _parse_game_datetime(row: Dict[str, object]) -> Optional[str]:
    if _has_text(row.get("game_datetime_utc")):
        return str(row.get("game_datetime_utc"))
    if _has_text(row.get("date")):
        try:
            dt = datetime.strptime(str(row.get("date")), "%Y%m%d").replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            return None
    return None


def _confidence_from_edge(edge: Optional[float]) -> Optional[float]:
    if edge is None:
        return None
    return float(1.0 - math.exp(-abs(edge) / 6.0))


def _bet_units(edge: Optional[float]) -> Optional[float]:
    if edge is None:
        return None
    abs_edge = abs(edge)
    if abs_edge >= EDGE_TIER3:
        return 3.0
    if abs_edge >= EDGE_TIER2:
        return 2.0
    if abs_edge >= EDGE_MIN:
        return 1.0
    return 0.0


def _bet_side(edge: Optional[float]) -> Optional[str]:
    if edge is None or abs(edge) < EDGE_MIN:
        return None
    return "home" if edge > 0 else "away"


def _validate_game(row: Dict[str, object]) -> Tuple[str, List[str]]:
    reasons: List[str] = []
    if not _has_text(row.get("game_datetime_utc")) and not _has_text(row.get("date")):
        reasons.append("missing_game_datetime")
    if not row.get("home_team") or not row.get("away_team"):
        reasons.append("missing_team_name")
    if row.get("completed") and (not _has_value(row.get("home_score")) or not _has_value(row.get("away_score"))):
        reasons.append("missing_final_score")
    if not _has_value(row.get("market_spread")) and not _has_value(row.get("market_total")):
        reasons.append("missing_market_lines")

    if "missing_game_datetime" in reasons or "missing_team_name" in reasons:
        return "rejected", reasons
    if reasons:
        return "partial", reasons
    return "verified", reasons


def _load_supabase():
    url = (os.getenv("SUPABASE_URL") or "").strip()
    key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        raise RuntimeError("SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY missing.")
    return create_client(url, key)


@dataclass(frozen=True)
class Counts:
    pulled: int = 0
    teams_upserted: int = 0
    games_upserted: int = 0
    markets_upserted: int = 0
    predictions_upserted: int = 0
    rejected: int = 0


def fetch_scoreboard() -> pd.DataFrame:
    # NOTE: this is "today + next DAYS_AHEAD days". DAYS_AHEAD=1 pulls today and tomorrow.
    now = datetime.now()
    today = now.strftime("%Y%m%d")
    dates = [today]
    for i in range(1, DAYS_AHEAD + 1):
        dates.append((now + timedelta(days=i)).strftime("%Y%m%d"))

    rows: List[Dict[str, object]] = []
    for d in dates:
        rows.extend(espn.fetch_scoreboard_games(d))
    return pd.DataFrame(rows)


def _require_feature_store_files() -> None:
    """
    Option 1 guardrail:
    ML expects local CSV feature store(s). Ensure they exist before building matrix.
    The workflow should run: python espn_boxscore_builder.py
    """
    required = [
        REPO_ROOT / "espn_team_game_features.csv",
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise FileNotFoundError(
            "Missing required feature store file(s): "
            + ", ".join(missing)
            + ". Run `python espn_boxscore_builder.py` earlier in the workflow (Option 1)."
        )


def build_ml_outputs() -> pd.DataFrame:
    _require_feature_store_files()
    build_feature_matrix(BuildConfig())
    predict(PredictConfig())
    return pd.read_csv(REPO_ROOT / "ml" / "predictions_latest.csv")


def upsert_rows(client, schema: str, table: str, rows: List[Dict[str, object]], on_conflict: Optional[str] = None) -> int:
    if not rows:
        return 0
    payload = rows if len(rows) > 1 else rows[0]
    req = client.schema(schema).table(table)
    if on_conflict:
        resp = req.upsert(payload, on_conflict=on_conflict).execute()
    else:
        resp = req.upsert(payload).execute()
    data = resp.data or []
    return len(data) if isinstance(data, list) else 1


def main() -> None:
    model_version = MODEL_VERSION or f"auto-{datetime.now().strftime('%Y%m%d')}"
    pulled_at = _utc_now()

    sb = _load_supabase()
    scoreboard = fetch_scoreboard()
    if scoreboard.empty:
        raise RuntimeError("No scoreboard data returned.")

    raw_rows: List[Dict[str, object]] = []
    team_rows: Dict[str, Dict[str, object]] = {}
    game_rows: List[Dict[str, object]] = []
    market_rows: List[Dict[str, object]] = []
    dq_rows: List[Dict[str, object]] = []

    game_id_set = set()

    for _, row in scoreboard.iterrows():
        external_game_id = str(row.get("game_id") or "").strip()
        if not external_game_id:
            continue

        status, reasons = _validate_game(row.to_dict())

        if status != "verified":
            dq_rows.append(
                {
                    "id": str(uuid.uuid4()),
                    "entity_type": "games",
                    "entity_id": None,
                    "severity": "warning" if status == "partial" else "error",
                    "reason_codes": reasons,  # text[]
                    "details": {"external_game_id": external_game_id},  # jsonb NOT NULL
                    "created_at": _iso(pulled_at),  # timestamptz NOT NULL
                }
            )

        raw_rows.append(
            {
                "id": str(uuid.uuid4()),  # uuid NOT NULL
                "season": SEASON,
                "source": SOURCE,
                "external_game_id": external_game_id,
                "payload": _sanitize_payload(row.to_dict()),
                "pulled_at": _iso(pulled_at),
                "verification_status": status,
                "verification_notes": "|".join(reasons) if reasons else None,
            }
        )

        home_team = row.get("home_team")
        away_team = row.get("away_team")
        if not home_team or not away_team:
            continue

        # Align to public.teams.team_id (text)
        home_team_id = f"{SOURCE}:{str(home_team).strip()}"
        away_team_id = f"{SOURCE}:{str(away_team).strip()}"

        team_rows[home_team_id] = {
            "team_id": home_team_id,
            "team_name": str(home_team).strip(),
            "conference": None,
        }
        team_rows[away_team_id] = {
            "team_id": away_team_id,
            "team_name": str(away_team).strip(),
            "conference": None,
        }

        # Align to public.games.game_id (text). Use ESPN game_id directly.
        game_datetime = _parse_game_datetime(row.to_dict())
        game_rows.append(
            {
                "game_id": external_game_id,
                "game_date": str(row.get("date") or "")[:8] if _has_text(row.get("date")) else None,  # text NOT NULL per schema
                "home_team": str(home_team).strip(),  # text NOT NULL
                "away_team": str(away_team).strip(),  # text NOT NULL
                "home_team_id": home_team_id,  # text NOT NULL
                "away_team_id": away_team_id,  # text NOT NULL
                "home_score": int(_safe_num(row.get("home_score"))) if _has_value(row.get("home_score")) else None,
                "away_score": int(_safe_num(row.get("away_score"))) if _has_value(row.get("away_score")) else None,
                "venue": row.get("venue"),
                "status": "final" if row.get("completed") else "scheduled",
                "source": SOURCE,
                "season": SEASON,
                "external_game_id": external_game_id,
                "game_datetime_utc": game_datetime,
                "verification_status": status,
            }
        )
        game_id_set.add(external_game_id)

        market_rows.append(
            {
                "game_id": external_game_id,  # text NOT NULL
                "book": row.get("market_provider") or "espn",  # text NOT NULL
                "pulled_at": _iso(pulled_at),  # timestamptz NOT NULL
                "spread_home": _safe_num(row.get("market_spread")),
                "total": _safe_num(row.get("market_total")),
                "ml_home": int(_safe_num(row.get("market_home_ml"))) if _has_value(row.get("market_home_ml")) else None,
                "ml_away": int(_safe_num(row.get("market_away_ml"))) if _has_value(row.get("market_away_ml")) else None,
            }
        )

    # Upserts
    upsert_rows(sb, RAW_SCHEMA, "raw_games", raw_rows, on_conflict="season,source,external_game_id")
    teams_upserted = upsert_rows(sb, "public", "teams", list(team_rows.values()), on_conflict="team_id")
    games_upserted = upsert_rows(sb, "public", "games", game_rows, on_conflict="game_id")
    markets_upserted = upsert_rows(sb, "public", "market_lines", market_rows, on_conflict="game_id,book,pulled_at")

    if dq_rows:
        upsert_rows(sb, "public", "dq_audit", dq_rows, on_conflict="id")

    # ML predictions (still CSV-based for now)
    preds = build_ml_outputs()
    if preds.empty:
        raise RuntimeError("No predictions generated.")

    preds["event_id"] = preds["event_id"].astype(str)
    playable = preds[preds["event_id"].isin(game_id_set)].copy()

    # TODO: map outputs -> public.predictions schema.
    # Do NOT guess mapping here; we will implement once we have predictions_latest.csv headers.

    counts = Counts(
        pulled=len(scoreboard),
        teams_upserted=teams_upserted,
        games_upserted=games_upserted,
        markets_upserted=markets_upserted,
        predictions_upserted=0,
        rejected=sum(1 for r in raw_rows if r["verification_status"] == "rejected"),
    )

    print(
        json.dumps(
            {
                "pulled": counts.pulled,
                "teams_upserted": counts.teams_upserted,
                "games_upserted": counts.games_upserted,
                "markets_upserted": counts.markets_upserted,
                "rejected": counts.rejected,
                "pred_rows_available_for_games": int(len(playable)),
                "model_version": model_version,
                "note": "predictions upsert TODO: public.predictions schema mismatch until mapping is implemented",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
