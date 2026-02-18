#!/usr/bin/env python3
import csv
import json
import hashlib
import os
import re
import sys
import time
import tempfile
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import psycopg

# Required
SUPABASE_DB_URL = (os.getenv("SUPABASE_DB_URL") or "").strip()

# Behavior (mirrors your uploader)
UPLOAD_GROUP = (os.getenv("UPLOAD_GROUP") or "all").strip().lower()
SKIP_MISSING = (os.getenv("SKIP_MISSING") or "0").strip().lower() in ("1", "true", "yes")

# DB settings
DB_SCHEMA = (os.getenv("DB_SCHEMA") or "raw").strip()

# replace | append | upsert
LOAD_MODE = (os.getenv("LOAD_MODE") or "replace").strip().lower()

# parse version default
PARSE_VERSION = (os.getenv("PARSE_VERSION") or "v1").strip() or "v1"

# defaults for required cols sometimes omitted upstream (common in ML)
DEFAULT_SOURCE = (os.getenv("DEFAULT_SOURCE") or (UPLOAD_GROUP if UPLOAD_GROUP != "all" else "ml")).strip() or "ml"
DEFAULT_PULLED_AT_UTC = (os.getenv("DEFAULT_PULLED_AT_UTC") or "").strip()

# Only allow automatic schema mutation for very wide, evolving raw feature tables.
AUTO_ADD_COLUMN_ALLOWLIST = {
    ("raw", "espn_games"),
    ("raw", "espn_team_game_logs"),
    ("raw", "espn_team_game_features"),
    ("raw", "espn_matchups_model_ready"),
    ("raw", "espn_player_boxscores"),
    ("raw", "model_features"),
    ("raw", "haslametrics"),
}

ALLOW_SCHEMA_MIGRATION = (os.getenv("ALLOW_SCHEMA_MIGRATION") or "0").strip().lower() in ("1", "true", "yes")

# Optional: pack wide feature CSVs into a single JSON column to avoid Postgres row-size limits.
PACK_FEATURES_JSON = (os.getenv("PACK_FEATURES_JSON") or "1").strip().lower() in ("1", "true", "yes")

PACK_FEATURES_JSON_ALLOWLIST = {
    ("raw", table_name) for table_name in ["espn_team_game_features", "espn_matchups_model_ready"]
}

PACK_FEATURES_BASE_COLS = {
    "espn_team_game_features": [
        "row_hash",
        "event_id",
        "team_id",
        "team",
        "home_away",
        "game_datetime_utc",
        "pulled_at_utc",
        "source",
        "parse_version",
    ],
    "espn_matchups_model_ready": [
        "row_hash",
        "event_id",
        "game_datetime_utc",
        "status",
        "home_points",
        "away_points",
        "home_win",
        "pulled_at_utc",
        "source",
        "parse_version",
    ],
}

# Basic retry
MAX_RETRIES = int(os.getenv("DB_LOAD_MAX_RETRIES", "3"))
RETRY_INITIAL_DELAY = float(os.getenv("DB_LOAD_RETRY_INITIAL_DELAY", "1.0"))
RETRY_BACKOFF = float(os.getenv("DB_LOAD_RETRY_BACKOFF", "2.0"))

# File groups
FILES_ESPN = [
    ("CSV/espn_games.csv", "espn_games"),
    ("CSV/espn_team_game_logs.csv", "espn_team_game_logs"),
    ("CSV/espn_team_game_features.csv", "espn_team_game_features"),
    ("CSV/espn_matchups_model_ready.csv", "espn_matchups_model_ready"),
    ("CSV/espn_player_boxscores.csv", "espn_player_boxscores"),
    ("CSV/espn_teams.csv", "espn_teams"),
    ("CSV/espn_injuries.csv", "espn_injuries"),
    ("CSV/espn_dq_audit.csv", "espn_dq_audit"),
    ("CSV/espn_feature_diagnostics.csv", "espn_feature_diagnostics"),
    ("CSV/ncaa_team_game_logs.csv", "ncaa_team_game_logs"),
    ("CSV/ncaa_games.csv", "ncaa_games"),
    ("CSV/ncaa_player_boxscores.csv", "ncaa_player_boxscores"),
]

FILES_TORVIK = [
    ("barttorvik.csv", "barttorvik"),
    ("barttorvik_team_results.csv", "barttorvik_team_results"),
    ("haslametrics.csv", "haslametrics"),
]

FILES_ML = [
    ("ml/model_features.csv", "model_features"),
    ("ml/dq_audit_ml.csv", "dq_audit_ml"),
    ("ml/predictions_latest.csv", "predictions_latest"),
]

TABLE_SPECS = {
    "espn_games": {
        "required_cols": [
            "date",
            "game_id",
            "game_datetime_utc",
            "home_team",
            "away_team",
            "completed",
            "pulled_at_utc",
            "source",
            "parse_version",
        ],
        "row_hash_keys": [],
        "not_null": ["game_id", "game_datetime_utc", "pulled_at_utc", "source", "parse_version"],
        "dtypes": {"game_datetime_utc": "datetime", "pulled_at_utc": "datetime"},
    },
    "espn_team_game_logs": {
        "required_cols": [
            "event_id",
            "team_id",
            "team",
            "home_away",
            "game_datetime_utc",
            "pulled_at_utc",
            "source",
            "parse_version",
        ],
        "row_hash_keys": ["event_id", "team_id", "home_away", "game_datetime_utc"],
        "not_null": [
            "row_hash",
            "event_id",
            "team_id",
            "home_away",
            "game_datetime_utc",
            "pulled_at_utc",
            "source",
            "parse_version",
        ],
        "dtypes": {"game_datetime_utc": "datetime", "pulled_at_utc": "datetime"},
    },
    "espn_team_game_features": {
        "required_cols": [
            "event_id",
            "team_id",
            "game_datetime_utc",
            "pulled_at_utc",
            "source",
            "parse_version",
        ],
        "row_hash_keys": ["event_id", "team_id", "game_datetime_utc"],
        "not_null": ["row_hash", "event_id", "team_id", "game_datetime_utc", "pulled_at_utc", "source", "parse_version"],
        "dtypes": {"game_datetime_utc": "datetime", "pulled_at_utc": "datetime"},
    },
    "espn_matchups_model_ready": {
        "required_cols": [
            "event_id",
            "game_datetime_utc",
            "pulled_at_utc",
            "source",
            "parse_version",
        ],
        "row_hash_keys": ["event_id", "game_datetime_utc"],
        "not_null": ["row_hash", "event_id", "game_datetime_utc", "pulled_at_utc", "source", "parse_version"],
        "dtypes": {"game_datetime_utc": "datetime", "pulled_at_utc": "datetime"},
    },
    "model_features": {
        "required_cols": [
            "event_id",
            "team_id_home",
            "team_id_away",
            "team_home",
            "team_away",
            "game_datetime_utc",
            "actual_margin_home",
            "actual_total",
            "pulled_at_utc",
            "source",
            "parse_version",
        ],
        "row_hash_keys": ["event_id", "team_id_home", "team_id_away", "game_datetime_utc"],
        "not_null": [
            "row_hash",
            "event_id",
            "team_id_home",
            "team_id_away",
            "game_datetime_utc",
            "pulled_at_utc",
            "source",
            "parse_version",
        ],
        "dtypes": {
            "game_datetime_utc": "datetime",
            "pulled_at_utc": "datetime",
            "actual_margin_home": "float",
            "actual_total": "float",
        },
    },
    "predictions_latest": {
        "required_cols": [
            "event_id",
            "team_id_home",
            "team_id_away",
            "team_home",
            "team_away",
            "game_datetime_utc",
            "pred_margin_home",
            "pred_total",
            "model_version",
            "pulled_at_utc",
            "source",
            "parse_version",
        ],
        "row_hash_keys": [
            "event_id",
            "team_id_home",
            "team_id_away",
            "game_datetime_utc",
            "model_version",
        ],
        "not_null": [
            "row_hash",
            "event_id",
            "team_id_home",
            "team_id_away",
            "game_datetime_utc",
            "model_version",
            "pulled_at_utc",
            "source",
            "parse_version",
        ],
        "dtypes": {
            "game_datetime_utc": "datetime",
            "pulled_at_utc": "datetime",
            "pred_margin_home": "float",
            "pred_total": "float",
        },
    },
    "espn_player_boxscores": {
        "required_cols": [
            "event_id",
            "game_datetime_utc",
            "team_id",
            "team",
            "home_away",
            "athlete_id",
            "player",
            "pulled_at_utc",
            "source",
            "parse_version",
        ],
        "row_hash_keys": ["event_id", "team_id", "athlete_id"],
        "not_null": [
            "row_hash",
            "event_id",
            "team_id",
            "athlete_id",
            "home_away",
            "game_datetime_utc",
            "pulled_at_utc",
            "source",
            "parse_version",
        ],
        "dtypes": {"game_datetime_utc": "datetime", "pulled_at_utc": "datetime"},
    },
    "espn_teams": {
        "required_cols": ["espn_id", "name", "pulled_at_utc", "source", "parse_version"],
        "row_hash_keys": ["espn_id"],
        "not_null": ["row_hash", "espn_id", "name", "pulled_at_utc", "source", "parse_version"],
        "dtypes": {"pulled_at_utc": "datetime"},
    },
    "espn_injuries": {
        "required_cols": [
            "team_id",
            "athlete_id",
            "status",
            "pulled_at_utc",
            "source",
            "parse_version",
        ],
        "row_hash_keys": ["team_id", "athlete_id", "status", "return_date", "pulled_at_utc"],
        "not_null": ["row_hash", "team_id", "athlete_id", "pulled_at_utc", "source", "parse_version"],
        "dtypes": {"pulled_at_utc": "datetime"},
    },
    "espn_dq_audit": {
        "required_cols": ["event_id", "team_id", "pulled_at_utc", "source", "parse_version"],
        "row_hash_keys": ["event_id", "team_id", "home_away", "pulled_at_utc"],
        "not_null": ["row_hash", "event_id", "team_id", "pulled_at_utc", "source", "parse_version"],
        "dtypes": {"pulled_at_utc": "datetime"},
    },
    "espn_feature_diagnostics": {
        "required_cols": ["event_id", "team_id", "diagnostic_reason", "pulled_at_utc", "source", "parse_version"],
        "row_hash_keys": ["event_id", "team_id", "diagnostic_reason"],
        "not_null": ["row_hash", "event_id", "team_id", "diagnostic_reason", "pulled_at_utc", "source", "parse_version"],
        "dtypes": {"pulled_at_utc": "datetime"},
    },
    "ncaa_team_game_logs": {
        "required_cols": [
            "game_id",
            "team",
            "opponent",
            "home_away",
            "game_datetime",
            "pulled_at_utc",
            "source",
            "parse_version",
        ],
        "row_hash_keys": ["game_id", "team", "home_away"],
        "not_null": ["row_hash", "game_id", "team", "home_away", "pulled_at_utc", "source", "parse_version"],
        "dtypes": {"game_datetime": "datetime", "pulled_at_utc": "datetime"},
    },
    "ncaa_games": {
        "required_cols": [
            "game_id",
            "date",
            "game_datetime",
            "home_team",
            "away_team",
            "pulled_at_utc",
            "source",
            "parse_version",
        ],
        "row_hash_keys": ["game_id", "game_datetime"],
        "not_null": [
            "row_hash",
            "game_id",
            "game_datetime",
            "home_team",
            "away_team",
            "pulled_at_utc",
            "source",
            "parse_version",
        ],
        "dtypes": {"game_datetime": "datetime", "pulled_at_utc": "datetime"},
    },
    "ncaa_player_boxscores": {
        "required_cols": [
            "game_id",
            "team",
            "player_name",
            "pulled_at_utc",
            "source",
            "parse_version",
        ],
        "row_hash_keys": ["game_id", "team", "player_name", "player_id"],
        "not_null": [
            "row_hash",
            "game_id",
            "team",
            "player_name",
            "pulled_at_utc",
            "source",
            "parse_version",
        ],
        "dtypes": {"pulled_at_utc": "datetime"},
    },
    "haslametrics": {
        "required_cols": ["pulled_at_utc", "source", "parse_version"],
        "row_hash_keys": [],
        "not_null": ["row_hash", "pulled_at_utc", "source", "parse_version"],
        "dtypes": {"pulled_at_utc": "datetime"},
    },
    # IMPORTANT: this matches your actual DB schema for raw.dq_audit_ml
    "dq_audit_ml": {
        # Keep this loose; we rewrite into the DB schema and guarantee row_hash
        "required_cols": [],
        # We will ensure row_hash exists (DB has NOT NULL on row_hash)
        "row_hash_keys": ["entity_type", "entity_id", "severity", "reason_codes", "details"],
        "not_null": ["row_hash"],
        "dtypes": {"pulled_at_utc": "datetime"},
    },
}


def _die(msg: str, code: int = 1) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(code)


def _warn(msg: str) -> None:
    print(f"[WARN] {msg}")


def _validate_env() -> None:
    if not SUPABASE_DB_URL:
        _die("SUPABASE_DB_URL is missing/empty. Add it as a GitHub Secret / env var.")
    if UPLOAD_GROUP not in ("espn", "torvik", "ml", "all"):
        _die("UPLOAD_GROUP must be one of: espn, torvik, ml, all")
    if LOAD_MODE not in ("replace", "append", "upsert"):
        _die("LOAD_MODE must be one of: replace, append, upsert")
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", DB_SCHEMA):
        _die(f"DB_SCHEMA looks invalid: '{DB_SCHEMA}'")


def _files_for_group() -> List[Tuple[str, str]]:
    if UPLOAD_GROUP == "espn":
        return FILES_ESPN
    if UPLOAD_GROUP == "torvik":
        return FILES_TORVIK
    if UPLOAD_GROUP == "ml":
        return FILES_ML
    return FILES_ESPN + FILES_TORVIK + FILES_ML


def _quote_ident(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def _qualified_table(schema: str, table: str) -> str:
    return f"{_quote_ident(schema)}.{_quote_ident(table)}"


def _check_table_exists(conn: psycopg.Connection, schema: str, table: str) -> bool:
    q = """
    select 1
    from information_schema.tables
    where table_schema = %s and table_name = %s
    limit 1
    """
    with conn.cursor() as cur:
        cur.execute(q, (schema, table))
        return cur.fetchone() is not None


def _get_table_columns(conn: psycopg.Connection, schema: str, table: str) -> List[str]:
    q = """
    select column_name
    from information_schema.columns
    where table_schema = %s and table_name = %s
    order by ordinal_position
    """
    with conn.cursor() as cur:
        cur.execute(q, (schema, table))
        rows = cur.fetchall()
    return [r[0] for r in rows]


def _get_primary_key_columns(conn: psycopg.Connection, schema: str, table: str) -> List[str]:
    q = """
    select kcu.column_name
    from information_schema.table_constraints tc
    join information_schema.key_column_usage kcu
      on tc.constraint_name = kcu.constraint_name
     and tc.table_schema = kcu.table_schema
    where tc.table_schema = %s
      and tc.table_name = %s
      and tc.constraint_type = 'PRIMARY KEY'
    order by kcu.ordinal_position
    """
    with conn.cursor() as cur:
        cur.execute(q, (schema, table))
        rows = cur.fetchall()
    return [r[0] for r in rows]


def _read_csv_header_columns(local_path: Path) -> List[str]:
    with local_path.open("r", encoding="utf-8", errors="replace", newline="") as fin:
        reader = csv.reader(fin)
        try:
            header = next(reader)
        except StopIteration:
            return []
    cols = [c.strip() for c in header if c.strip()]
    return cols


def _validate_csv_header(cols: List[str], local_path: Path) -> None:
    if not cols:
        raise ValueError(f"CSV appears to have an empty header: {local_path}")
    dupes = sorted({c for c in cols if cols.count(c) > 1})
    if dupes:
        raise ValueError(f"CSV header has duplicate columns {dupes} in {local_path}")


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _normalize_str(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in ("none", "nan"):
        return ""
    return text


def _normalize_datetime(value: object) -> str:
    text = _normalize_str(value)
    if not text:
        return ""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return text


def _normalize_float(value: object) -> str:
    text = _normalize_str(value)
    if not text:
        return ""
    try:
        num = Decimal(text)
    except InvalidOperation:
        return text
    return f"{num.quantize(Decimal('0.000001'))}"


def _stable_row_hash(row: List[str], keys: List[str], idx: dict, dtype_map: dict) -> str:
    parts: List[str] = []
    for key in sorted(keys):
        i = idx.get(key)
        raw = row[i] if i is not None and i < len(row) else ""
        dtype = dtype_map.get(key)
        if dtype == "datetime":
            parts.append(_normalize_datetime(raw))
        elif dtype in ("float", "numeric"):
            parts.append(_normalize_float(raw))
        else:
            parts.append(_normalize_str(raw))
    payload = "|".join(parts)
    return _sha256(payload)


def _ensure_columns_exist(conn: psycopg.Connection, schema: str, table: str, required_cols: List[str]) -> None:
    existing = set(_get_table_columns(conn, schema, table))
    missing = [c for c in required_cols if c not in existing]
    if not missing:
        return

    if (schema, table) not in AUTO_ADD_COLUMN_ALLOWLIST:
        preview = ", ".join(missing[:25]) + (" ..." if len(missing) > 25 else "")
        raise ValueError(
            f"{schema}.{table}: CSV contains {len(missing)} column(s) not present in destination table. "
            f"Auto-add is disabled for this table. Missing columns: {preview}"
        )

    qt = _qualified_table(schema, table)
    with conn.cursor() as cur:
        for c in missing:
            cur.execute(f"alter table {qt} add column if not exists {_quote_ident(c)} text;")

    print(f"[INFO] Added {len(missing)} missing columns to {schema}.{table} (auto-add allowlisted)")
    conn.commit()


def _get_table_spec(table_name: str) -> dict:
    return TABLE_SPECS.get(table_name, {"required_cols": [], "row_hash_keys": [], "not_null": [], "dtypes": {}})


def _is_blank(value: object) -> bool:
    return _normalize_str(value) == ""


def _infer_types_from_csv(local_path: Path, columns: List[str], sample_limit: int = 5000) -> Dict[str, Dict[str, str]]:
    stats = {c: {"seen": 0, "non_numeric": 0, "has_fractional": 0, "example": ""} for c in columns}

    with local_path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if i >= sample_limit:
                break
            for c in columns:
                raw = row.get(c, "")
                if _is_blank(raw):
                    continue

                stats[c]["seen"] += 1
                text = _normalize_str(raw)
                try:
                    num = Decimal(text)
                except InvalidOperation:
                    stats[c]["non_numeric"] += 1
                    if not stats[c]["example"]:
                        stats[c]["example"] = text
                    continue

                if num != num.to_integral_value():
                    stats[c]["has_fractional"] += 1

    inferred: Dict[str, Dict[str, str]] = {}
    for c, s in stats.items():
        if s["seen"] == 0:
            inferred[c] = {
                "type": "type ambiguous",
                "reason": "no non-empty sample values in CSV",
            }
            continue
        if s["non_numeric"] > 0:
            sample = f" sample_non_numeric='{s['example']}'" if s["example"] else ""
            inferred[c] = {
                "type": "type ambiguous",
                "reason": f"{s['non_numeric']} non-numeric value(s) in sample;{sample}".strip(),
            }
            continue

        inferred[c] = {
            "type": "double precision",
            "reason": f"{s['seen']} numeric sample value(s), fractional_values={s['has_fractional']}",
        }

    return inferred


def _build_schema_drift_sql(schema: str, table: str, inferred: Dict[str, Dict[str, str]]) -> Tuple[str, List[str]]:
    sql_lines: List[str] = []
    ambiguous: List[str] = []
    for col, meta in inferred.items():
        inferred_type = meta["type"]
        if inferred_type == "double precision":
            sql_lines.append(
                f"alter table {_qualified_table(schema, table)} add column if not exists {_quote_ident(col)} {inferred_type};"
            )
        else:
            ambiguous.append(col)
            sql_lines.append(f"-- type ambiguous for {col}: {meta['reason']}")

    banner = (
        "-- Schema drift fix for CSV -> Postgres load\n"
        f"-- destination: {schema}.{table}\n"
        "-- Add only missing columns. Nullable by default.\n"
    )
    return banner + "\n".join(sql_lines), ambiguous


def _ensure_columns_with_schema_guidance(
    conn: psycopg.Connection,
    schema: str,
    table: str,
    csv_cols: List[str],
    local_path: Path,
) -> None:
    try:
        _ensure_columns_exist(conn, schema, table, csv_cols)
        return
    except ValueError as exc:
        msg = str(exc)
        if "Auto-add is disabled for this table" not in msg:
            raise

    table_cols = _get_table_columns(conn, schema, table)
    missing_cols = [c for c in csv_cols if c not in table_cols]
    inferred = _infer_types_from_csv(local_path, missing_cols)
    sql_block, ambiguous_cols = _build_schema_drift_sql(schema, table, inferred)

    print(f"[SCHEMA_DRIFT] destination={schema}.{table}")
    print(f"[SCHEMA_DRIFT] missing_columns={missing_cols}")
    print("[SCHEMA_DRIFT_SQL_BEGIN]")
    print(sql_block)
    print("[SCHEMA_DRIFT_SQL_END]")

    if not ALLOW_SCHEMA_MIGRATION:
        raise RuntimeError(
            f"{schema}.{table}: CSV has missing columns and auto-add is disabled. "
            "Set ALLOW_SCHEMA_MIGRATION=1 to auto-apply inferred ALTER TABLE statements "
            "(only when all missing columns infer cleanly as numeric)."
        )

    if ambiguous_cols:
        raise RuntimeError(
            f"{schema}.{table}: ALLOW_SCHEMA_MIGRATION=1 but type inference is ambiguous for columns: {ambiguous_cols}. "
            "Not auto-running ALTER TABLE; apply SQL manually."
        )

    with conn.cursor() as cur:
        for col, meta in inferred.items():
            cur.execute(
                f"alter table {_qualified_table(schema, table)} "
                f"add column if not exists {_quote_ident(col)} {meta['type']};"
            )

    conn.commit()
    print(f"[INFO] Auto-applied schema migration for {schema}.{table} because ALLOW_SCHEMA_MIGRATION=1")


def _required_defaults_for_table(table_name: str) -> dict:
    spec = _get_table_spec(table_name)
    required = set(spec.get("required_cols", []))
    if not required:
        return {}

    now_utc = datetime.now(timezone.utc).isoformat()
    pulled_default = DEFAULT_PULLED_AT_UTC or now_utc
    source_default = DEFAULT_SOURCE or "ml"

    defaults = {}
    if "pulled_at_utc" in required:
        defaults["pulled_at_utc"] = pulled_default
    if "source" in required:
        defaults["source"] = source_default
    return defaults


def _ensure_cols_with_defaults(local_path: Path, table_name: str, defaults: dict) -> Path:
    cols = _read_csv_header_columns(local_path)
    _validate_csv_header(cols, local_path)

    missing = [c for c in defaults.keys() if c not in cols]
    if not missing:
        return local_path

    tmp = Path(tempfile.mkstemp(prefix=f"loadcols_{table_name}_", suffix=".csv")[1])

    with local_path.open("r", encoding="utf-8", errors="replace", newline="") as fin, tmp.open(
        "w", encoding="utf-8", newline=""
    ) as fout:
        reader = csv.reader(fin)
        writer = csv.writer(fout)

        try:
            header = next(reader)
        except StopIteration:
            return local_path

        writer.writerow(header + missing)

        for row in reader:
            if len(row) < len(header):
                row = row + [""] * (len(header) - len(row))
            row_out = row[:]
            for c in missing:
                row_out.append(str(defaults[c]))
            writer.writerow(row_out)

    return tmp


def _ensure_parse_version(local_path: Path, table_name: str) -> Path:
    spec = _get_table_spec(table_name)
    if "parse_version" not in spec.get("required_cols", []):
        return local_path

    cols = _read_csv_header_columns(local_path)
    _validate_csv_header(cols, local_path)

    tmp = Path(tempfile.mkstemp(prefix=f"loadpv_{table_name}_", suffix=".csv")[1])

    has_pv = "parse_version" in cols
    idx = {c: i for i, c in enumerate(cols)}
    pv_i = idx.get("parse_version")

    with local_path.open("r", encoding="utf-8", errors="replace", newline="") as fin, tmp.open(
        "w", encoding="utf-8", newline=""
    ) as fout:
        reader = csv.reader(fin)
        writer = csv.writer(fout)

        try:
            header = next(reader)
        except StopIteration:
            try:
                tmp.unlink()
            except Exception:
                pass
            return local_path

        if not has_pv:
            writer.writerow(header + ["parse_version"])
            for row in reader:
                if len(row) < len(header):
                    row = row + [""] * (len(header) - len(row))
                writer.writerow(row + [PARSE_VERSION])
            return tmp

        writer.writerow(header)
        for row in reader:
            if len(row) < len(header):
                row = row + [""] * (len(header) - len(row))
            raw = (row[pv_i] if pv_i is not None else "").strip()
            if raw == "" or raw == r"\N":
                row[pv_i] = PARSE_VERSION
            writer.writerow(row)

    return tmp


def _prepare_csv_for_load(local_path: Path, table_name: str) -> Path:
    spec = _get_table_spec(table_name)
    cols = _read_csv_header_columns(local_path)
    _validate_csv_header(cols, local_path)

    has_row_hash = "row_hash" in cols
    wants_row_hash = "row_hash" in spec.get("not_null", []) or bool(spec.get("row_hash_keys"))

    if not wants_row_hash:
        return local_path

    keys = [k for k in spec.get("row_hash_keys", []) if k in cols]
    if not keys:
        keys = cols

    idx = {c: i for i, c in enumerate(cols)}
    dtype_map = spec.get("dtypes", {})

    tmp = Path(tempfile.mkstemp(prefix=f"loadfix_{table_name}_", suffix=".csv")[1])

    with local_path.open("r", encoding="utf-8", errors="replace", newline="") as fin, tmp.open(
        "w", encoding="utf-8", newline=""
    ) as fout:
        reader = csv.reader(fin)
        writer = csv.writer(fout)

        try:
            header = next(reader)
        except StopIteration:
            return local_path

        if has_row_hash:
            writer.writerow(header)
            rh_i = idx["row_hash"]
            for row in reader:
                if len(row) < len(header):
                    row = row + [""] * (len(header) - len(row))
                if row[rh_i].strip() == "":
                    row[rh_i] = _stable_row_hash(row, keys, idx, dtype_map)
                writer.writerow(row)
        else:
            writer.writerow(["row_hash"] + header)
            for row in reader:
                if len(row) < len(header):
                    row = row + [""] * (len(header) - len(row))
                row_hash = _stable_row_hash(row, keys, idx, dtype_map)
                writer.writerow([row_hash] + row)

    return tmp


def _drop_rows_missing_required_values(local_path: Path, table_name: str) -> Path:
    spec = _get_table_spec(table_name)
    cols = _read_csv_header_columns(local_path)
    _validate_csv_header(cols, local_path)

    required_vals = [c for c in spec.get("not_null", []) if c in cols]
    if not required_vals:
        return local_path

    idx = {c: i for i, c in enumerate(cols)}
    tmp = Path(tempfile.mkstemp(prefix=f"loadclean_{table_name}_", suffix=".csv")[1])

    dropped = 0

    def is_missing(v: str) -> bool:
        raw = (v or "").strip()
        if raw == r"\N":
            return True
        return _normalize_str(raw) == ""

    with local_path.open("r", encoding="utf-8", errors="replace", newline="") as fin, tmp.open(
        "w", encoding="utf-8", newline=""
    ) as fout:
        reader = csv.reader(fin)
        writer = csv.writer(fout)

        try:
            header = next(reader)
        except StopIteration:
            return local_path

        writer.writerow(header)

        for row in reader:
            if len(row) < len(cols):
                row = row + [""] * (len(cols) - len(row))

            bad = False
            for c in required_vals:
                i = idx.get(c)
                if i is None:
                    continue
                if is_missing(row[i]):
                    bad = True
                    break

            if bad:
                dropped += 1
                continue

            writer.writerow(row)

    if dropped > 0:
        print(f"[WARN] Dropped {dropped} row(s) from {local_path.name} missing required values for {table_name}")
        return tmp

    try:
        tmp.unlink()
    except Exception:
        pass
    return local_path


def _clean_numeric_value(value: str, col_name: str) -> str:
    normalized = _normalize_str(value)
    if not normalized:
        return ""

    if col_name in ("home_points", "away_points", "home_win"):
        try:
            num = float(normalized)
            if num.is_integer():
                return str(int(num))
        except (ValueError, AttributeError):
            pass

    return normalized


def _pack_features_json(local_path: Path, table_name: str, base_cols: List[str]) -> Path:
    cols = _read_csv_header_columns(local_path)
    _validate_csv_header(cols, local_path)

    missing_base = [c for c in base_cols if c not in cols]
    if missing_base:
        raise ValueError(f"{local_path.name}: missing base columns required for packing: {missing_base}")

    extra_cols = [c for c in cols if c not in base_cols]
    if not extra_cols:
        return local_path

    idx = {c: i for i, c in enumerate(cols)}
    tmp = Path(tempfile.mkstemp(prefix=f"loadpack_{table_name}_", suffix=".csv")[1])

    with local_path.open("r", encoding="utf-8", errors="replace", newline="") as fin, tmp.open(
        "w", encoding="utf-8", newline=""
    ) as fout:
        reader = csv.reader(fin)
        writer = csv.writer(fout)

        try:
            header = next(reader)
        except StopIteration:
            return local_path

        writer.writerow(base_cols + ["features"])
        for row in reader:
            if len(row) < len(header):
                row = row + [""] * (len(header) - len(row))

            payload = {}
            for col in extra_cols:
                raw = row[idx[col]]
                if _normalize_str(raw) == "":
                    continue
                payload[col] = raw

            base_values = []
            for c in base_cols:
                raw = row[idx[c]] if idx.get(c) is not None else ""
                cleaned = _clean_numeric_value(raw, c)
                base_values.append(cleaned)

            writer.writerow(base_values + [json.dumps(payload, separators=(",", ":"), ensure_ascii=False)])

    return tmp


def _rewrite_dq_audit_ml_csv(local_path: Path) -> Path:
    """
    Accepts various dq_audit_ml CSV shapes and rewrites into the DB schema columns:
      row_hash, pulled_at_utc, source, parse_version, payload, entity_type, entity_id,
      severity, reason_codes, details

    Common upstream shapes we normalize:
      table_name,event_id,severity,code,details
      table_name,event_id,level,code,details
      entity_type,entity_id,severity,reason_codes,details (already close)
    """
    cols = _read_csv_header_columns(local_path)
    _validate_csv_header(cols, local_path)
    idx = {c: i for i, c in enumerate(cols)}

    # header aliases (normalize to internal names)
    aliases = {
        "level": "severity",
        "reason_code": "reason_codes",
        "codes": "reason_codes",
        "code": "reason_codes",
        "table": "table_name",
        "event": "event_id",
    }

    normalized_cols: List[str] = []
    for c in cols:
        normalized_cols.append(aliases.get(c, c))

    # If we changed anything, we need to read using normalized positions
    # easiest: map original -> normalized, then use original index but reference normalized name
    orig_to_norm = {cols[i]: normalized_cols[i] for i in range(len(cols))}

    # default meta
    now_utc = datetime.now(timezone.utc).isoformat()
    pulled_default = DEFAULT_PULLED_AT_UTC or now_utc
    source_default = DEFAULT_SOURCE or "ml"

    out_cols = [
        "row_hash",
        "pulled_at_utc",
        "source",
        "parse_version",
        "payload",
        "entity_type",
        "entity_id",
        "severity",
        "reason_codes",
        "details",
    ]

    tmp = Path(tempfile.mkstemp(prefix="load_dqml_", suffix=".csv")[1])

    def get_val(row: List[str], name: str) -> str:
        # find first original col that normalized to name
        for oc, nc in orig_to_norm.items():
            if nc == name:
                i = idx.get(oc)
                if i is None or i >= len(row):
                    return ""
                return row[i]
        return ""

    with local_path.open("r", encoding="utf-8", errors="replace", newline="") as fin, tmp.open(
        "w", encoding="utf-8", newline=""
    ) as fout:
        reader = csv.reader(fin)
        writer = csv.writer(fout)

        try:
            header = next(reader)
        except StopIteration:
            # empty file, keep as-is
            try:
                tmp.unlink()
            except Exception:
                pass
            return local_path

        writer.writerow(out_cols)

        for row in reader:
            if len(row) < len(header):
                row = row + [""] * (len(header) - len(row))

            # entity mapping: prefer entity_type/entity_id if present, else map from table_name/event_id
            entity_type = _normalize_str(get_val(row, "entity_type")) or _normalize_str(get_val(row, "table_name"))
            entity_id = _normalize_str(get_val(row, "entity_id")) or _normalize_str(get_val(row, "event_id"))

            severity = _normalize_str(get_val(row, "severity"))
            reason_codes = _normalize_str(get_val(row, "reason_codes"))
            details = _normalize_str(get_val(row, "details"))

            pulled_at_utc = _normalize_str(get_val(row, "pulled_at_utc")) or pulled_default
            source = _normalize_str(get_val(row, "source")) or source_default
            parse_version = _normalize_str(get_val(row, "parse_version")) or PARSE_VERSION

            # payload: capture any extra fields not used above
            used_norm = {
                "row_hash",
                "pulled_at_utc",
                "source",
                "parse_version",
                "payload",
                "entity_type",
                "entity_id",
                "severity",
                "reason_codes",
                "details",
                "table_name",
                "event_id",
            }

            payload_obj: Dict[str, str] = {}
            for i, oc in enumerate(cols):
                nc = orig_to_norm.get(oc, oc)
                if nc in used_norm:
                    continue
                if i < len(row) and _normalize_str(row[i]) != "":
                    payload_obj[nc] = row[i]

            # If upstream provided "payload" column, include it, and merge extras under "_extra" to avoid overwrite
            raw_payload = _normalize_str(get_val(row, "payload"))
            payload_text = ""
            if raw_payload:
                # keep upstream payload as string; if it's JSON, store as-is
                payload_text = raw_payload
                if payload_obj:
                    try:
                        base = json.loads(raw_payload)
                        if isinstance(base, dict):
                            base["_extra"] = payload_obj
                            payload_text = json.dumps(base, separators=(",", ":"), ensure_ascii=False)
                        else:
                            payload_text = json.dumps({"payload": base, "_extra": payload_obj}, separators=(",", ":"), ensure_ascii=False)
                    except Exception:
                        payload_text = json.dumps({"payload": raw_payload, "_extra": payload_obj}, separators=(",", ":"), ensure_ascii=False)
            else:
                payload_text = json.dumps(payload_obj, separators=(",", ":"), ensure_ascii=False) if payload_obj else ""

            # row_hash: use upstream if present, else compute stable hash on core identity fields
            upstream_rh = _normalize_str(get_val(row, "row_hash"))
            if upstream_rh:
                row_hash = upstream_rh
            else:
                key = "|".join(
                    [
                        entity_type,
                        entity_id,
                        severity,
                        reason_codes,
                        details,
                    ]
                )
                row_hash = _sha256(key)

            writer.writerow(
                [
                    row_hash,
                    pulled_at_utc,
                    source,
                    parse_version,
                    payload_text,
                    entity_type,
                    entity_id,
                    severity,
                    reason_codes,
                    details,
                ]
            )

    print("[INFO] Rewrote dq_audit_ml into DB schema columns")
    return tmp


def _copy_csv_into_table(conn: psycopg.Connection, qualified_table: str, local_path: Path) -> None:
    cols = _read_csv_header_columns(local_path)
    _validate_csv_header(cols, local_path)

    col_list = ", ".join(_quote_ident(c) for c in cols)
    copy_sql = (
        f"copy {qualified_table} ({col_list}) "
        f"from stdin with (format csv, header true, delimiter ',', quote '\"');"
    )

    with conn.cursor() as cur:
        with local_path.open("rb") as f:
            with cur.copy(copy_sql) as copy:
                while True:
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    copy.write(chunk)


def _preflight_validate_csv(local_path: Path, table_name: str) -> dict:
    spec = _get_table_spec(table_name)
    cols = _read_csv_header_columns(local_path)
    _validate_csv_header(cols, local_path)

    required = spec.get("required_cols", [])
    missing = [c for c in required if c not in cols]
    if missing:
        raise ValueError(f"{local_path.name}: missing required columns: {missing}")

    idx = {c: i for i, c in enumerate(cols)}
    not_null_cols = spec.get("not_null", [])
    dtype_map = spec.get("dtypes", {})

    rows = 0
    bad_not_null = {c: 0 for c in not_null_cols}
    bad_dtype = {c: 0 for c in dtype_map.keys()}

    def is_missing(v: str) -> bool:
        raw = (v or "").strip()
        if raw == r"\N":
            return True
        return _normalize_str(raw) == ""

    with local_path.open("r", encoding="utf-8", errors="replace", newline="") as fin:
        reader = csv.reader(fin)
        try:
            next(reader)
        except StopIteration:
            raise ValueError(f"{local_path.name}: CSV has no rows")

        for row in reader:
            rows += 1
            if len(row) < len(cols):
                row = row + [""] * (len(cols) - len(row))

            for col in not_null_cols:
                i = idx.get(col)
                val = row[i] if i is not None else ""
                if is_missing(val):
                    bad_not_null[col] += 1

            for col, dtype in dtype_map.items():
                i = idx.get(col)
                if i is None:
                    continue
                val = row[i]
                if is_missing(val):
                    continue
                if dtype == "datetime":
                    try:
                        datetime.fromisoformat(_normalize_str(val).replace("Z", "+00:00"))
                    except Exception:
                        bad_dtype[col] += 1
                elif dtype in ("float", "numeric"):
                    try:
                        Decimal(_normalize_str(val))
                    except InvalidOperation:
                        bad_dtype[col] += 1

    bad_not_null = {k: v for k, v in bad_not_null.items() if v > 0}
    bad_dtype = {k: v for k, v in bad_dtype.items() if v > 0}

    if bad_not_null:
        raise ValueError(f"{local_path.name}: NOT NULL violations: {bad_not_null}")
    if bad_dtype:
        raise ValueError(f"{local_path.name}: dtype violations: {bad_dtype}")

    report = {"rows": rows, "columns": len(cols), "table": table_name, "empty": rows == 0}
    if rows == 0:
        print(f"[INFO] Preflight warning: {table_name} has zero data rows (will skip)")
    else:
        print(f"[INFO] Preflight ok: {table_name} rows={rows} cols={len(cols)}")
    return report


def _truncate(conn: psycopg.Connection, schema: str, table: str) -> None:
    qt = _qualified_table(schema, table)
    with conn.cursor() as cur:
        cur.execute(f"truncate table {qt};")


def _dedup_select_sql(qtemp: str, qcols: str, pk_cols: List[str], all_cols: List[str]) -> str:
    if not pk_cols:
        return f"select {qcols} from {qtemp}"

    pk_expr = ", ".join(_quote_ident(c) for c in pk_cols)
    order_parts = [pk_expr]
    if "pulled_at_utc" in all_cols:
        order_parts.append(f"{_quote_ident('pulled_at_utc')} desc nulls last")
    order_by = ", ".join(order_parts)

    return f"select distinct on ({pk_expr}) {qcols} from {qtemp} order by {order_by}"


def _resolve_upsert_conflict_columns(
    table_name: str,
    pk_cols: List[str],
    csv_cols: List[str],
    table_cols: List[str],
) -> List[str]:
    spec = TABLE_SPECS.get(table_name, {})
    rh_keys = spec.get("row_hash_keys", []) if isinstance(spec, dict) else []

    # When PK is only "row_hash" (synthetic), prefer the natural key from
    # row_hash_keys so ON CONFLICT targets the real unique constraint and
    # avoids violating separate UNIQUE indexes on the natural-key columns.
    if pk_cols == ["row_hash"] and isinstance(rh_keys, list) and rh_keys:
        if all(c in csv_cols and c in table_cols for c in rh_keys):
            return list(rh_keys)

    if pk_cols:
        return pk_cols

    if isinstance(rh_keys, list) and rh_keys:
        if all(c in csv_cols and c in table_cols for c in rh_keys):
            _warn(
                f"Using row_hash_keys fallback for upsert conflict target on {table_name}: {rh_keys}"
            )
            return list(rh_keys)

    _die(
        f"LOAD_MODE=upsert requires a PRIMARY KEY or usable unique conflict columns on {DB_SCHEMA}.{table_name}.\n"
        "Add a PK/UNIQUE constraint and rerun."
    )


def _upsert_from_staging(conn: psycopg.Connection, schema: str, table: str, local_path: Path) -> None:
    qt = _qualified_table(schema, table)
    staging = f"tmp_{table}_{int(time.time() * 1000)}"
    qs = _quote_ident(staging)

    table_cols = _get_table_columns(conn, schema, table)
    if not table_cols:
        _die(f"Could not read columns for {schema}.{table} (table exists but no columns?)")

    csv_cols = _read_csv_header_columns(local_path)
    _validate_csv_header(csv_cols, local_path)

    _ensure_columns_with_schema_guidance(conn, schema, table, csv_cols, local_path)

    table_cols = _get_table_columns(conn, schema, table)
    missing_in_table = [c for c in csv_cols if c not in table_cols]
    if missing_in_table:
        _die(
            f"Table {schema}.{table} is still missing columns required by {local_path.name}:\n"
            f"{missing_in_table}\n"
            f"Fix: investigate permissions or invalid identifiers."
        )

    pk_cols = _get_primary_key_columns(conn, schema, table)
    pk_cols = _resolve_upsert_conflict_columns(table, pk_cols, csv_cols, table_cols)

    qcols = ", ".join(_quote_ident(c) for c in csv_cols)
    conflict = ", ".join(_quote_ident(c) for c in pk_cols)

    non_pk_cols = [c for c in csv_cols if c not in pk_cols]
    if non_pk_cols:
        set_clause = ", ".join(f"{_quote_ident(c)} = excluded.{_quote_ident(c)}" for c in non_pk_cols)
        on_conflict = f"on conflict ({conflict}) do update set {set_clause}"
    else:
        on_conflict = f"on conflict ({conflict}) do nothing"

    with conn.cursor() as cur:
        cur.execute(f"create temp table {qs} (like {qt} including defaults) on commit drop;")

    _copy_csv_into_table(conn, qs, local_path)

    dedup_select = _dedup_select_sql(qs, qcols, pk_cols, csv_cols)

    sql = f"""
      insert into {qt} ({qcols})
      {dedup_select}
      {on_conflict};
    """
    with conn.cursor() as cur:
        cur.execute(sql)


def load_one(local_path: str, table_name: str) -> None:
    lp = Path(local_path)

    if not lp.exists():
        if SKIP_MISSING:
            _warn(f"Skipping missing local file: {local_path}")
            return
        _die(f"Local file missing: {local_path}")

    last_err: Optional[str] = None

    for attempt in range(MAX_RETRIES):
        if attempt > 0:
            delay = RETRY_INITIAL_DELAY * (RETRY_BACKOFF ** (attempt - 1))
            time.sleep(delay)

        tmp_path: Optional[Path] = None
        packed_path: Optional[Path] = None
        cleaned_path: Optional[Path] = None
        parsever_path: Optional[Path] = None
        requiredcols_path: Optional[Path] = None
        dqrewrite_path: Optional[Path] = None

        try:
            with psycopg.connect(SUPABASE_DB_URL, autocommit=False) as conn:
                if not _check_table_exists(conn, DB_SCHEMA, table_name):
                    _die(
                        f"Destination table missing: {DB_SCHEMA}.{table_name}\n"
                        f"Create it in Supabase first (SQL Editor)."
                    )

                table_cols = _get_table_columns(conn, DB_SCHEMA, table_name)

                prepared = lp

                # 1) DQ audit special handling: rewrite into the DB schema first
                if table_name == "dq_audit_ml":
                    rewritten = _rewrite_dq_audit_ml_csv(prepared)
                    dqrewrite_path = rewritten if rewritten != prepared else None
                    prepared = rewritten

                # 2) Ensure required cols that upstream may omit (pulled_at_utc/source) for tables that require them
                required_defaults = _required_defaults_for_table(table_name)
                if required_defaults:
                    prepared2 = _ensure_cols_with_defaults(prepared, table_name, required_defaults)
                    requiredcols_path = prepared2 if prepared2 != prepared else None
                    prepared = prepared2

                # 3) Ensure parse_version exists + filled (when required by spec)
                pv = _ensure_parse_version(prepared, table_name)
                parsever_path = pv if pv != prepared else None
                prepared = pv

                # 4) Pack ESPN feature tables (does not apply to ML)
                if PACK_FEATURES_JSON and (DB_SCHEMA, table_name) in PACK_FEATURES_JSON_ALLOWLIST:
                    base_cols = PACK_FEATURES_BASE_COLS.get(table_name, [])
                    if not base_cols:
                        raise ValueError(
                            f"{table_name}: PACK_FEATURES_JSON enabled but no base columns configured in PACK_FEATURES_BASE_COLS"
                        )
                    if "features" not in table_cols:
                        raise ValueError(
                            f"{DB_SCHEMA}.{table_name} is missing a 'features' jsonb column required for PACK_FEATURES_JSON."
                        )
                    packed = _pack_features_json(prepared, table_name, base_cols)
                    packed_path = packed if packed != prepared else None
                    prepared = packed

                # 5) Ensure row_hash exists (DB requires it for dq_audit_ml, and many others)
                prepared3 = _prepare_csv_for_load(prepared, table_name)
                tmp_path = prepared3 if prepared3 != prepared else None
                prepared = prepared3

                # 6) Drop rows missing required values (only enforced where spec.not_null lists columns)
                cleaned = _drop_rows_missing_required_values(prepared, table_name)
                cleaned_path = cleaned if cleaned != prepared else None
                prepared = cleaned

                # 7) Validate
                validation_result = _preflight_validate_csv(prepared, table_name)
                if validation_result.get("empty", False):
                    print(f"[SKIP] {local_path} is empty (zero data rows)")
                    return

                qt = _qualified_table(DB_SCHEMA, table_name)

                if LOAD_MODE == "replace":
                    csv_cols = _read_csv_header_columns(prepared)
                    _validate_csv_header(csv_cols, prepared)
                    _ensure_columns_with_schema_guidance(conn, DB_SCHEMA, table_name, csv_cols, prepared)

                    pk_cols = _get_primary_key_columns(conn, DB_SCHEMA, table_name)

                    temp_name = f"tmp_{table_name}_{int(time.time() * 1000)}"
                    qtemp = _quote_ident(temp_name)
                    qcols = ", ".join(_quote_ident(c) for c in csv_cols)

                    with conn.cursor() as cur:
                        cur.execute(f"create temp table {qtemp} (like {qt} including defaults) on commit drop;")

                    _copy_csv_into_table(conn, qtemp, prepared)

                    with conn.cursor() as cur:
                        cur.execute(f"select count(*) from {qtemp};")
                        count = cur.fetchone()[0]
                    if count == 0:
                        raise ValueError(f"{prepared.name}: temp load produced zero rows")

                    _truncate(conn, DB_SCHEMA, table_name)

                    dedup_select = _dedup_select_sql(qtemp, qcols, pk_cols, csv_cols)
                    with conn.cursor() as cur:
                        cur.execute(f"insert into {qt} ({qcols}) {dedup_select};")

                elif LOAD_MODE == "append":
                    csv_cols = _read_csv_header_columns(prepared)
                    _validate_csv_header(csv_cols, prepared)
                    _ensure_columns_with_schema_guidance(conn, DB_SCHEMA, table_name, csv_cols, prepared)
                    _copy_csv_into_table(conn, qt, prepared)

                elif LOAD_MODE == "upsert":
                    _upsert_from_staging(conn, DB_SCHEMA, table_name, prepared)

                conn.commit()

            print(f"[OK] Loaded {local_path} -> {DB_SCHEMA}.{table_name} (mode={LOAD_MODE})")
            return

        except SystemExit:
            raise
        except Exception as exc:
            last_err = str(exc)
        finally:
            for p in (tmp_path, packed_path, parsever_path, requiredcols_path, cleaned_path, dqrewrite_path):
                if p and p.exists():
                    try:
                        p.unlink()
                    except Exception:
                        pass

    raise RuntimeError(
        f"DB load failed after {MAX_RETRIES} attempts: {local_path} -> {DB_SCHEMA}.{table_name}\n"
        f"Last error: {last_err}"
    )


def main() -> None:
    _validate_env()
    files = _files_for_group()
    print(f"[INFO] Loading group='{UPLOAD_GROUP}' files={len(files)} skip_missing={SKIP_MISSING} mode={LOAD_MODE}")
    for local, table in files:
        load_one(local, table)


if __name__ == "__main__":
    main()
