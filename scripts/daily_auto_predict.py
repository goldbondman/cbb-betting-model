#!/usr/bin/env python3
"""
Daily auto-prediction pipeline (DB-backed predictions_latest):

1) Pull ESPN scoreboard games (today +/- DAYS_BACK + DAYS_AHEAD).
2) Upsert ingestion outputs to Supabase:
   - raw.raw_games (or public.raw_games if RAW_SCHEMA=public)
   - public.teams
   - public.games
   - public.market_lines
   - public.dq_audit (warnings/errors)
3) Pull latest ML predictions from Supabase DB (raw.predictions_latest).
4) Join predictions to scoreboard market lines and upsert into public.predictions.

Verified constraints:
- public.predictions: PRIMARY KEY (id), UNIQUE (prediction_key)
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

# Ensure repo root is on sys.path so imports work when running:
# python scripts/daily_auto_predict.py
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

ESPN_DIR = REPO_ROOT / "ESPN"
if str(ESPN_DIR) not in sys.path:
    sys.path.insert(0, str(ESPN_DIR))

try:
    import espn_boxscore_builder_modular as espn  # noqa: E402
except Exception:
    import espn_boxscore_builder as espn  # noqa: E402

SOURCE = "ESPN"
EDGE_MIN = float(os.getenv("EDGE_MIN", "3.0"))
EDGE_TIER2 = float(os.getenv("EDGE_TIER2", "6.0"))
EDGE_TIER3 = float(os.getenv("EDGE_TIER3", "9.0"))
MODEL_VERSION_OVERRIDE = (os.getenv("MODEL_VERSION") or "").strip()

# Pull past days too (so completed games get final scores)
DAYS_AHEAD = int(os.getenv("DAYS_AHEAD", "1"))
DAYS_BACK = int(os.getenv("DAYS_BACK", "0"))

SEASON = int(os.getenv("SEASON", datetime.now().year + (1 if datetime.now().month >= 7 else 0)))

RAW_SCHEMA = (os.getenv("RAW_SCHEMA") or "raw").strip()

RAW_PREDICTIONS_SCHEMA = (os.getenv("RAW_PREDICTIONS_SCHEMA") or "raw").strip()
RAW_PREDICTIONS_TABLE = (os.getenv("RAW_PREDICTIONS_TABLE") or "predictions_latest").strip()
RAW_PREDICTIONS_LIMIT = int(os.getenv("RAW_PREDICTIONS_LIMIT", "10000"))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _safe_float(value: object) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


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


def _validate_game(row: Dict[str, object]) -> Tuple[str, List[str]]:
    reasons: List[str] = []

    if not _has_text(row.get("game_datetime_utc")) and not _has_text(row.get("date")):
        reasons.append("missing_game_datetime")
    if not row.get("home_team") or not row.get("away_team"):
        reasons.append("missing_team_name")
    if row.get("completed") and (
        _safe_float(row.get("home_score")) is None or _safe_float(row.get("away_score")) is None
    ):
        reasons.append("missing_final_score")
    if _safe_float(row.get("market_spread")) is None and _safe_float(row.get("market_total")) is None:
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
    """
    Pull scoreboard for:
      - past DAYS_BACK days (oldest -> newest)
      - today
      - next DAYS_AHEAD days

    Ordering matters: newer pulls should overwrite older snapshots via upserts.
    """
    now = datetime.now()
    dates: List[str] = []

    # Past window (oldest -> newest)
    for i in range(DAYS_BACK, 0, -1):
        dates.append((now - timedelta(days=i)).strftime("%Y%m%d"))

    # Today + future window
    dates.append(now.strftime("%Y%m%d"))
    for i in range(1, DAYS_AHEAD + 1):
        dates.append((now + timedelta(days=i)).strftime("%Y%m%d"))

    rows: List[Dict[str, object]] = []
    for d in dates:
        rows.extend(espn.fetch_scoreboard_games(d))
    return pd.DataFrame(rows)


def upsert_rows(
    client,
    schema: str,
    table: str,
    rows: List[Dict[str, object]],
    on_conflict: Optional[str] = None,
) -> int:
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


def fetch_predictions_latest_from_db(sb) -> pd.DataFrame:
    all_rows: List[Dict[str, object]] = []
    offset = 0
    page = 1000

    while True:
        resp = (
            sb.schema(RAW_PREDICTIONS_SCHEMA)
            .table(RAW_PREDICTIONS_TABLE)
            .select("*")
            .range(offset, offset + page - 1)
            .execute()
        )
        rows = resp.data or []
        all_rows.extend(rows)

        if len(rows) < page:
            break

        offset += page
        if offset >= RAW_PREDICTIONS_LIMIT:
            break

    return pd.DataFrame(all_rows)


def main() -> None:
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
                    "reason_codes": reasons,
                    "details": {"external_game_id": external_game_id},
                    "created_at": _iso(pulled_at),
                }
            )

        raw_rows.append(
            {
                "id": str(uuid.uuid4()),
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

        game_datetime = _parse_game_datetime(row.to_dict())

        game_rows.append(
            {
                "game_id": external_game_id,
                "game_date": str(row.get("date") or "")[:8] if _has_text(row.get("date")) else None,
                "home_team": str(home_team).strip(),
                "away_team": str(away_team).strip(),
                "home_team_id": home_team_id,
                "away_team_id": away_team_id,
                "home_score": int(_safe_float(row.get("home_score")))
                if _safe_float(row.get("home_score")) is not None
                else None,
                "away_score": int(_safe_float(row.get("away_score")))
                if _safe_float(row.get("away_score")) is not None
                else None,
                "venue": row.get("venue"),
                "status": "final" if bool(row.get("completed")) else "scheduled",
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
                "game_id": external_game_id,
                "book": row.get("market_provider") or "espn",
                "pulled_at": _iso(pulled_at),
                "spread_home": _safe_float(row.get("market_spread")),
                "total": _safe_float(row.get("market_total")),
                "ml_home": int(_safe_float(row.get("market_home_ml")))
                if _safe_float(row.get("market_home_ml")) is not None
                else None,
                "ml_away": int(_safe_float(row.get("market_away_ml")))
                if _safe_float(row.get("market_away_ml")) is not None
                else None,
            }
        )

    # Ingestion upserts
    upsert_rows(sb, RAW_SCHEMA, "raw_games", raw_rows, on_conflict="season,source,external_game_id")
    teams_upserted = upsert_rows(sb, "public", "teams", list(team_rows.values()), on_conflict="team_id")
    games_upserted = upsert_rows(sb, "public", "games", game_rows, on_conflict="game_id")
    markets_upserted = upsert_rows(sb, "public", "market_lines", market_rows, on_conflict="game_id,book,pulled_at")

    if dq_rows:
        upsert_rows(sb, "public", "dq_audit", dq_rows, on_conflict="id")

    # Pull DB-backed predictions_latest
    preds = fetch_predictions_latest_from_db(sb)
    if preds.empty:
        raise RuntimeError(f"No rows found in {RAW_PREDICTIONS_SCHEMA}.{RAW_PREDICTIONS_TABLE}.")

    # Normalize join keys
    preds["event_id"] = preds["event_id"].astype(str)
    scoreboard["game_id"] = scoreboard["game_id"].astype(str)

    merged = preds.merge(scoreboard, left_on="event_id", right_on="game_id", how="left", suffixes=("", "_game"))

    prediction_rows: List[Dict[str, object]] = []
    for _, row in merged.iterrows():
        event_id = str(row.get("event_id") or "").strip()
        if not event_id or event_id not in game_id_set:
            continue

        model_version = MODEL_VERSION_OVERRIDE or str(row.get("model_version") or "").strip() or "ml-linear-v1"

        pred_margin_home = _safe_float(row.get("pred_margin_home"))
        pred_total = _safe_float(row.get("pred_total"))

        market_spread = _safe_float(row.get("market_spread"))
        market_total = _safe_float(row.get("market_total"))

        vegas_edge = None
        if pred_margin_home is not None and market_spread is not None:
            vegas_edge = pred_margin_home - market_spread

        total_edge = None
        if pred_total is not None and market_total is not None:
            total_edge = pred_total - market_total

        confidence = _confidence_from_edge(vegas_edge if vegas_edge is not None else pred_margin_home)

        prediction_key = f"{model_version}:{event_id}"

        team_a = str(row.get("team_home") or row.get("home_team") or "").strip()
        team_b = str(row.get("team_away") or row.get("away_team") or "").strip()
        if not team_a or not team_b:
            continue

        prediction_rows.append(
            {
                # required
                "id": prediction_key,
                "prediction_key": prediction_key,
                "model_version_id": model_version,
                "game_date": str(row.get("date") or "").strip() or str(row.get("game_date") or "").strip(),
                "team_a": team_a,
                "team_b": team_b,
                "ensemble_prediction": pred_margin_home if pred_margin_home is not None else 0.0,
                "confidence": confidence if confidence is not None else 0.0,
                "model_predictions": {
                    "source_table": f"{RAW_PREDICTIONS_SCHEMA}.{RAW_PREDICTIONS_TABLE}",
                    "row_hash": str(row.get("row_hash") or ""),
                    "model_version": model_version,
                    "pred_margin_home": pred_margin_home,
                    "pred_total": pred_total,
                    "vegas_edge": vegas_edge,
                    "total_edge": total_edge,
                },
                # optional / useful
                "game_id": event_id,
                "home_team": (str(row.get("team_home") or row.get("home_team") or "").strip() or None),
                "away_team": (str(row.get("team_away") or row.get("away_team") or "").strip() or None),
                "venue": row.get("venue") or None,
                "vegas_line": market_spread,
                "vegas_edge": vegas_edge,
                "vegas_total": market_total,
                "odds_provider": (row.get("market_provider") or "espn"),
                "inputs": {
                    "predictions_latest_row_hash": str(row.get("row_hash") or ""),
                    "pulled_at_utc": str(row.get("pulled_at_utc") or ""),
                },
                "model_version": model_version,
                "updated_at": _iso(pulled_at),
            }
        )

    predictions_upserted = 0
    if prediction_rows:
        prediction_rows = [r for r in prediction_rows if r.get("prediction_key") and r.get("id")]
        predictions_upserted = upsert_rows(
            sb,
            "public",
            "predictions",
            prediction_rows,
            on_conflict="prediction_key",
        )

    counts = Counts(
        pulled=len(scoreboard),
        teams_upserted=teams_upserted,
        games_upserted=games_upserted,
        markets_upserted=markets_upserted,
        predictions_upserted=predictions_upserted,
        rejected=sum(1 for r in raw_rows if r["verification_status"] == "rejected"),
    )

    print(
        json.dumps(
            {
                "pulled": counts.pulled,
                "teams_upserted": counts.teams_upserted,
                "games_upserted": counts.games_upserted,
                "markets_upserted": counts.markets_upserted,
                "predictions_upserted": counts.predictions_upserted,
                "rejected": counts.rejected,
                "raw_predictions_table": f"{RAW_PREDICTIONS_SCHEMA}.{RAW_PREDICTIONS_TABLE}",
                "days_back": DAYS_BACK,
                "days_ahead": DAYS_AHEAD,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
