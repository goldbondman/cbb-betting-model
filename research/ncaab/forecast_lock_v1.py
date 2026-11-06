"""Research-only, append-only NCAAB Stage A forecast snapshot lock.

No book lines or postgame outcomes are accepted. This module does not generate
forecasts or deploy/modify the production R1/R2 stack.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FORBIDDEN = (
    "odds", "sportsbook", "bookmaker", "betting", "spread", "closing",
    "market", "ticket_share", "money_share", "actual", "final_score",
    "realized", "result_margin", "result_total", "outcome", "wager",
)
REQUIRED = ("event_id", "tipoff_at", "forecast_at", "data_cutoff",
            "model_version", "population_version", "source_dataset_sha256",
            "expected_home_score", "expected_away_score",
            "expected_margin_home", "expected_total", "input_records")


class SnapshotRejected(ValueError):
    """Forecast cannot be frozen safely."""


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      allow_nan=False, ensure_ascii=False).encode("utf-8")


def digest(obj: Any) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()


def timestamp(value: str, field: str) -> datetime:
    if not isinstance(value, str):
        raise SnapshotRejected(f"{field}: timestamp string required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as err:
        raise SnapshotRejected(f"{field}: invalid timestamp") from err
    if parsed.tzinfo is None:
        raise SnapshotRejected(f"{field}: timezone offset required")
    return parsed.astimezone(timezone.utc)


def check_no_forbidden(value: Any, prefix: str = "input") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key).lower()
            if any(word in name for word in FORBIDDEN):
                raise SnapshotRejected(f"{prefix}.{key}: forbidden Stage A field")
            check_no_forbidden(item, f"{prefix}.{key}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            check_no_forbidden(item, f"{prefix}[{i}]")


def validate(payload: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise SnapshotRejected("JSON object required")
    missing = [key for key in REQUIRED if key not in payload]
    if missing:
        raise SnapshotRejected("missing: " + ", ".join(missing))

    at = timestamp(payload["forecast_at"], "forecast_at")
    cut = timestamp(payload["data_cutoff"], "data_cutoff")
    tip = timestamp(payload["tipoff_at"], "tipoff_at")
    clock = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not cut <= at < tip:
        raise SnapshotRejected("required: data_cutoff <= forecast_at < tipoff_at")
    if at > clock:
        raise SnapshotRejected("cannot claim a lock timestamp in the future")
    if not re.fullmatch("[A-Za-z0-9_:.+-]{3,128}", str(payload["event_id"])):
        raise SnapshotRejected("unsafe event_id")
    if not re.fullmatch("[A-Za-z0-9_.:-]{3,128}", str(payload["model_version"])):
        raise SnapshotRejected("unsafe model_version")

    inputs = payload["input_records"]
    if not isinstance(inputs, list) or not inputs:
        raise SnapshotRejected("nonempty point-in-time input_records required")
    seen = set()
    for i, record in enumerate(inputs):
        if not isinstance(record, dict):
            raise SnapshotRejected(f"input_records[{i}]: object required")
        for name in ("source_id", "published_at", "content_sha256", "features"):
            if name not in record:
                raise SnapshotRejected(f"input_records[{i}]: missing {name}")
        if not re.fullmatch("[0-9a-f]{64}", str(record["content_sha256"])):
            raise SnapshotRejected(f"input_records[{i}]: invalid content SHA256")
        first = timestamp(record["published_at"], f"input_records[{i}].published_at")
        if first > cut:
            raise SnapshotRejected("declared source publication after feature cutoff")
        if record["source_id"] in seen:
            raise SnapshotRejected("duplicate source_id")
        seen.add(record["source_id"])
        check_no_forbidden(record["features"], str(record["source_id"]))

    if not re.fullmatch("[0-9a-f]{64}", str(payload["source_dataset_sha256"])):
        raise SnapshotRejected("invalid source_dataset_sha256")
    for name in ("expected_home_score", "expected_away_score",
                 "expected_margin_home", "expected_total"):
        value = payload[name]
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise SnapshotRejected(f"{name}: finite numeric required")
    h = float(payload["expected_home_score"])
    a = float(payload["expected_away_score"])
    if min(h, a) < 0:
        raise SnapshotRejected("negative predicted score")
    if abs(h - a - float(payload["expected_margin_home"])) > 1e-4:
        raise SnapshotRejected("predicted margin does not equal home minus away")
    if abs(h + a - float(payload["expected_total"])) > 1e-4:
        raise SnapshotRejected("predicted total does not equal sum of scores")

    safe = {key: payload[key] for key in REQUIRED}
    safe["stage"] = "A_MARKET_BLIND"
    safe["source_vintage_status"] = "DECLARED_SOURCE_TIMESTAMP_NOT_EXTERNALLY_AUTHENTICATED"
    safe["input_snapshot_sha256"] = digest(inputs)
    safe["frozen_record_sha256"] = digest(safe)
    return safe


def freeze(payload: dict[str, Any], archive_dir: Path,
           now: datetime | None = None) -> Path:
    record = validate(payload, now=now)
    # Each freeze produces a distinct, never-overwritten file.
    event = record["event_id"].replace(":", "_")
    stamp = timestamp(record["forecast_at"], "forecast_at").strftime("%Y%m%dT%H%M%SZ")
    filename = f"{event}_{stamp}_{record['frozen_record_sha256'][:16]}.json"
    archive_dir.mkdir(parents=True, exist_ok=True)
    target = archive_dir / filename
    with target.open("x", encoding="utf-8") as output:
        json.dump(record, output, sort_keys=True, indent=2, ensure_ascii=False)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--archive-dir", type=Path, required=True)
    args = parser.parse_args()
    with args.input.open(encoding="utf-8") as stream:
        data = json.load(stream)
    path = freeze(data, args.archive_dir)
    print(json.dumps({"frozen_path": str(path), "status": "STAGE_A_LOCK_FILE_CREATED"}))


if __name__ == "__main__":
    main()
