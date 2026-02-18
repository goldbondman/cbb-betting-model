#!/usr/bin/env python3
"""Run all configured game APIs and write output artifacts for comparison."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import yaml

# Add core directory to path
REPO_ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = REPO_ROOT / "core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from data_sources import GameData, SourceResult  # noqa: E402
from source_implementations import (  # noqa: E402
    ESPNDataSource,
    NCAADataSource,
    HenryAPIDataSource,
    CBBpyDataSource,
    CBBDDataSource,
)


def _source_from_name(name: str):
    source_map = {
        "espn": ESPNDataSource,
        "ncaa_casablanca": NCAADataSource,
        "henry_api": HenryAPIDataSource,
        "cbbpy": CBBpyDataSource,
        "cbbd": CBBDDataSource,
    }
    key = (name or "").strip().lower()
    if key not in source_map:
        raise ValueError(f"Unknown source in config: {name}")
    return source_map[key]()


def _validate_game(game: GameData) -> Tuple[str, List[str]]:
    reasons: List[str] = []

    if not game.game_id:
        reasons.append("missing_game_id")
    if not game.home_team or not game.away_team:
        reasons.append("missing_team_name")
    if not game.game_datetime:
        reasons.append("missing_game_datetime")

    status = (game.status or "").strip().lower()
    if status in {"final", "completed", "complete", "finished", "post"}:
        if game.home_score is None or game.away_score is None:
            reasons.append("missing_final_score")

    if game.home_team and game.away_team and game.home_team.strip().lower() == game.away_team.strip().lower():
        reasons.append("duplicate_team_matchup")

    if "missing_game_id" in reasons or "missing_team_name" in reasons:
        return "rejected", reasons
    if reasons:
        return "partial", reasons
    return "verified", reasons


def _game_to_dict(game: GameData, verification_status: str, verification_notes: List[str]) -> Dict[str, object]:
    row = asdict(game)
    quality = row.get("quality")
    if hasattr(quality, "value"):
        row["quality"] = quality.value
    row["verification_status"] = verification_status
    row["verification_notes"] = verification_notes
    return row


def run_probe(config: Dict[str, object]) -> Dict[str, object]:
    dates = config.get("dates") or []
    sources = config.get("sources") or []
    options = config.get("options") or {}

    continue_on_error = bool(options.get("continue_on_error", True))

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": config,
        "runs": [],
        "summary": {
            "total_sources": 0,
            "total_runs": 0,
            "total_games_pulled": 0,
            "total_verified": 0,
            "total_partial": 0,
            "total_rejected": 0,
            "total_conflicts": 0,
        },
    }

    for date in dates:
        for source_name in sources:
            run_info = {
                "date": date,
                "source": source_name,
                "success": False,
                "error": None,
                "games_pulled": 0,
                "verified": 0,
                "partial": 0,
                "rejected": 0,
                "duplicates_removed": 0,
                "dq_audit": [],
                "games": [],
            }

            report["summary"]["total_runs"] += 1
            report["summary"]["total_sources"] = len(sources)

            try:
                source = _source_from_name(source_name)
                result: SourceResult = source.fetch_games(date)

                if not result.success:
                    run_info["error"] = result.error
                    report["runs"].append(run_info)
                    if not continue_on_error:
                        raise RuntimeError(f"Source {source_name} failed on {date}: {result.error}")
                    continue

                dedupe_map: Dict[Tuple[str, str], GameData] = {}
                for game in result.games:
                    dedupe_key = (str(game.game_id), str(game.date))
                    if dedupe_key in dedupe_map:
                        run_info["duplicates_removed"] += 1
                    dedupe_map[dedupe_key] = game

                unique_games = list(dedupe_map.values())
                run_info["games_pulled"] = len(unique_games)

                for game in unique_games:
                    v_status, reasons = _validate_game(game)
                    run_info[v_status] += 1
                    run_info["games"].append(_game_to_dict(game, v_status, reasons))
                    if reasons:
                        run_info["dq_audit"].append(
                            {
                                "entity_type": "games",
                                "external_game_id": game.game_id,
                                "severity": "warning" if v_status == "partial" else "error",
                                "reason_codes": reasons,
                                "details": {
                                    "source": source_name,
                                    "date": date,
                                },
                            }
                        )

                run_info["success"] = True

                report["summary"]["total_games_pulled"] += run_info["games_pulled"]
                report["summary"]["total_verified"] += run_info["verified"]
                report["summary"]["total_partial"] += run_info["partial"]
                report["summary"]["total_rejected"] += run_info["rejected"]

            except Exception as exc:
                run_info["error"] = str(exc)
                if not continue_on_error:
                    report["runs"].append(run_info)
                    raise

            report["runs"].append(run_info)

    return report


def _write_artifacts(report: Dict[str, object], config: Dict[str, object]) -> Dict[str, str]:
    output = config.get("output") or {}
    artifact_dir = REPO_ROOT / str(output.get("artifact_dir", "artifacts"))
    basename = str(output.get("basename", "game_api_probe"))
    artifact_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = artifact_dir / f"{basename}_{stamp}.json"
    csv_path = artifact_dir / f"{basename}_{stamp}.csv"

    with json_path.open("w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    rows = []
    for run in report.get("runs", []):
        for game in run.get("games", []):
            rows.append(
                {
                    "date": run["date"],
                    "source": run["source"],
                    "success": run["success"],
                    "game_id": game.get("game_id"),
                    "home_team": game.get("home_team"),
                    "away_team": game.get("away_team"),
                    "home_score": game.get("home_score"),
                    "away_score": game.get("away_score"),
                    "status": game.get("status"),
                    "game_datetime": game.get("game_datetime"),
                    "verification_status": game.get("verification_status"),
                    "verification_notes": "|".join(game.get("verification_notes") or []),
                }
            )

    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        headers = [
            "date",
            "source",
            "success",
            "game_id",
            "home_team",
            "away_team",
            "home_score",
            "away_score",
            "status",
            "game_datetime",
            "verification_status",
            "verification_notes",
        ]
        writer = csv.DictWriter(fh, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)

    return {"json": str(json_path), "csv": str(csv_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run configured game API probe and write artifacts")
    parser.add_argument("--config", type=str, default="config/game_data_apis.yml", help="Path to YAML config")
    args = parser.parse_args()

    config_path = REPO_ROOT / args.config
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh) or {}

    report = run_probe(config)
    artifacts = _write_artifacts(report, config)

    print(json.dumps({"summary": report.get("summary", {}), "artifacts": artifacts}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
