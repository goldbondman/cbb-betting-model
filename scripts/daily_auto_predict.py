#!/usr/bin/env python3
"""
Daily auto-prediction pipeline:
1) Pull ESPN scoreboard games.
2) Build model feature matrix.
3) Run ML predictions.
4) Calculate edges vs Vegas.
5) Upsert to Supabase (teams, games, market_lines, predictions, dq_audit, raw_games).
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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _uuid_for_team(season: int, source: str, name: str) -> str:
    key = f"{season}:{source}:{name.lower().strip()}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def _uuid_for_game(season: int, source: str, external_game_id: str) -> str:
    key = f"{season}:{source}:{external_game_id}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


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
    games_upserted: int = 0
    markets_upserted: int = 0
    predictions_upserted: int = 0
    rejected: int = 0


def fetch_scoreboard() -> pd.DataFrame:
    today = datetime.now().strftime("%Y%m%d")
    dates = [today]
    for i in range(1, DAYS_AHEAD + 1):
        dates.append((datetime.now() + timedelta(days=i)).strftime("%Y%m%d"))

    rows: List[Dict[str, object]] = []
    for d in dates:
        rows.extend(espn.fetch_scoreboard_games(d))
    return pd.DataFrame(rows)


def build_ml_outputs() -> pd.DataFrame:
    build_feature_matrix(BuildConfig())
    predict(PredictConfig())
    return pd.read_csv(REPO_ROOT / "ml" / "predictions_latest.csv")


def upsert_rows(client, schema: str, table: str, rows: List[Dict[str, object]], on_conflict: Optional[str] = None):
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
                    "entity_type": "games",
                    "entity_id": None,
                    "severity": "warning" if status == "partial" else "error",
                    "reason_codes": reasons,
                    "details": {"external_game_id": external_game_id},
                }
            )

        raw_rows.append(
            {
                "season": SEASON,
                "source": SOURCE,
                "external_game_id": external_game_id,
                "payload": _sanitize_payload(row.to_dict()),
                "pulled_at": _iso(pulled_at),
                "verification_status": status,
                "verification_notes": "|".join(reasons),
            }
        )

        home_team = row.get("home_team")
        away_team = row.get("away_team")
        if not home_team or not away_team:
            continue

        home_id = _uuid_for_team(SEASON, SOURCE, home_team)
        away_id = _uuid_for_team(SEASON, SOURCE, away_team)
        team_rows[home_id] = {
            "id": home_id,
            "season": SEASON,
            "source_team_id": None,
            "team_name": home_team,
            "conference": None,
        }
        team_rows[away_id] = {
            "id": away_id,
            "season": SEASON,
            "source_team_id": None,
            "team_name": away_team,
            "conference": None,
        }

        game_id = _uuid_for_game(SEASON, SOURCE, external_game_id)
        game_datetime = _parse_game_datetime(row.to_dict())
        if game_datetime is None:
            continue
        game_rows.append(
            {
                "id": game_id,
                "season": SEASON,
                "game_datetime_utc": game_datetime,
                "home_team_id": home_id,
                "away_team_id": away_id,
                "home_score": _safe_num(row.get("home_score")),
                "away_score": _safe_num(row.get("away_score")),
                "status": "final" if row.get("completed") else "scheduled",
                "venue": row.get("venue"),
                "source": SOURCE,
                "external_game_id": external_game_id,
                "verification_status": status,
            }
        )
        game_id_set.add(game_id)

        market_rows.append(
            {
                "game_id": game_id,
                "book": row.get("market_provider") or "espn",
                "pulled_at": _iso(pulled_at),
                "spread_home": _safe_num(row.get("market_spread")),
                "total": _safe_num(row.get("market_total")),
                "ml_home": _safe_num(row.get("market_home_ml")),
                "ml_away": _safe_num(row.get("market_away_ml")),
            }
        )

    counts = Counts(pulled=len(scoreboard), rejected=sum(1 for r in raw_rows if r["verification_status"] == "rejected"))

    upsert_rows(sb, "raw", "raw_games", raw_rows, on_conflict="season,source,external_game_id")
    upsert_rows(sb, "public", "teams", list(team_rows.values()), on_conflict="season,team_name")
    counts = counts.__class__(
        pulled=counts.pulled,
        rejected=counts.rejected,
        games_upserted=upsert_rows(sb, "public", "games", game_rows, on_conflict="season,source,external_game_id"),
        markets_upserted=upsert_rows(sb, "public", "market_lines", market_rows, on_conflict="game_id,book,pulled_at"),
        predictions_upserted=counts.predictions_upserted,
    )

    if dq_rows:
        upsert_rows(sb, "public", "dq_audit", dq_rows)

    preds = build_ml_outputs()
    if preds.empty:
        raise RuntimeError("No predictions generated.")

    preds["event_id"] = preds["event_id"].astype(str)
    scoreboard["game_id"] = scoreboard["game_id"].astype(str)
    merged = preds.merge(scoreboard, left_on="event_id", right_on="game_id", how="left", suffixes=("", "_game"))

    prediction_rows: List[Dict[str, object]] = []
    for _, row in merged.iterrows():
        external_game_id = str(row.get("event_id") or "").strip()
        if not external_game_id:
            continue
        game_id = _uuid_for_game(SEASON, SOURCE, external_game_id)
        if game_id not in game_id_set:
            continue
        pred_spread = _safe_num(row.get("pred_margin_home"))
        pred_total = _safe_num(row.get("pred_total"))
        market_spread = _safe_num(row.get("market_spread"))
        market_total = _safe_num(row.get("market_total"))
        edge_spread = pred_spread - market_spread if pred_spread is not None and market_spread is not None else None
        edge_total = pred_total - market_total if pred_total is not None and market_total is not None else None
        confidence = _confidence_from_edge(edge_spread)
        bet_side = _bet_side(edge_spread)
        bet_units = _bet_units(edge_spread)
        prediction_rows.append(
            {
                "model_version": model_version,
                "game_id": game_id,
                "source": SOURCE,
                "external_game_id": external_game_id,
                "game_datetime_utc": row.get("game_datetime_utc") or row.get("game_datetime_utc_game"),
                "home_team": row.get("team_home") or row.get("home_team"),
                "away_team": row.get("team_away") or row.get("away_team"),
                "pred_spread": pred_spread,
                "pred_total": pred_total,
                "win_prob_home": None,
                "market_spread": market_spread,
                "market_total": market_total,
                "edge_spread": edge_spread,
                "edge_total": edge_total,
                "bet_side": bet_side,
                "bet_units": bet_units if bet_units else None,
                "bet_signal": bool(bet_side),
                "confidence": confidence,
                "notes": None,
                "model_inputs": json.dumps({"row_hash": row.get("row_hash")}),
            }
        )

    counts = counts.__class__(
        pulled=counts.pulled,
        rejected=counts.rejected,
        games_upserted=counts.games_upserted,
        markets_upserted=counts.markets_upserted,
        predictions_upserted=upsert_rows(
            sb, "public", "predictions", prediction_rows, on_conflict="model_version,external_game_id"
        ),
    )

    print(
        json.dumps(
            {
                "pulled": counts.pulled,
                "games_upserted": counts.games_upserted,
                "markets_upserted": counts.markets_upserted,
                "predictions_upserted": counts.predictions_upserted,
                "rejected": counts.rejected,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
