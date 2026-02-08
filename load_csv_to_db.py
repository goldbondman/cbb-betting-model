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
from typing import List, Optional, Tuple

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

# NEW: Auto-add columns policy
# Only allow automatic schema mutation for very wide, evolving raw feature tables.
# For all other tables, missing columns should be handled via migrations or upstream CSV fixes.
AUTO_ADD_COLUMN_ALLOWLIST = {
    ("raw", "espn_team_game_features"),
    ("raw", "espn_matchups_model_ready"),
}

# Optional: pack wide feature CSVs into a single JSON column to avoid Postgres row-size limits.
PACK_FEATURES_JSON = (os.getenv("PACK_FEATURES_JSON") or "1").strip().lower() in ("1", "true", "yes")
PACK_FEATURES_JSON_ALLOWLIST = {
    ("raw", "espn_team_game_features"),
}
PACK_FEATURES_BASE_COLS = {
    "espn_team_game_features": ["row_hash", "event_id", "team_id", "team", "home_away", "game_datetime_utc"],
}

# Basic retry (helps with transient network issues)
MAX_RETRIES = int(os.getenv("DB_LOAD_MAX_RETRIES", "3"))
RETRY_INITIAL_DELAY = float(os.getenv("DB_LOAD_RETRY_INITIAL_DELAY", "1.0"))
RETRY_BACKOFF = float(os.getenv("DB_LOAD_RETRY_BACKOFF", "2.0"))

# File groups (local file -> destination table)
FILES_ESPN = [
    ("espn_games.csv", "espn_games"),
    ("espn_team_game_logs.csv", "espn_team_game_logs"),
    ("espn_team_game_features.csv", "espn_team_game_features"),
    ("espn_matchups_model_ready.csv", "espn_matchups_model_ready"),
    # Diagnostic files (espn_feature_diagnostics.csv, espn_dq_audit.csv)
    # are only for troubleshooting storage uploads, not needed in database
]

FILES_TORVIK = [
    ("barttorvik.csv", "barttorvik"),
    ("barttorvik_team_results.csv", "barttorvik_team_results"),
]

FILES_ML = [
    ("ml/model_features.csv", "model_features"),
    ("ml/dq_audit_ml.csv", "dq_audit_ml"),
    ("ml/predictions_latest.csv", "predictions_latest"),
]


TABLE_SPECS = {
    "espn_games": {
        "required_cols": ["date", "game_id", "game_datetime_utc", "home_team", "away_team", "completed"],
        "row_hash_keys": [],
        "not_null": [],
        "dtypes": {"game_datetime_utc": "datetime"},
    },
    "espn_team_game_logs": {
        "required_cols": ["event_id", "team_id", "team", "home_away", "game_datetime_utc"],
        "row_hash_keys": ["event_id", "team_id", "home_away", "game_datetime_utc"],
        "not_null": ["row_hash"],
        "dtypes": {"game_datetime_utc": "datetime"},
    },
    "espn_team_game_features": {
        "required_cols": ["event_id", "team_id", "game_datetime_utc"],
        "row_hash_keys": ["event_id", "team_id", "game_datetime_utc"],
        "not_null": ["row_hash"],
        "dtypes": {"game_datetime_utc": "datetime"},
    },
    "espn_matchups_model_ready": {
        "required_cols": ["event_id"],
        "row_hash_keys": ["event_id", "game_datetime_utc", "h_team_id", "a_team_id"],
        "not_null": ["row_hash"],
        "dtypes": {"game_datetime_utc": "datetime"},
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
        ],
        "row_hash_keys": ["event_id", "team_id_home", "team_id_away", "game_datetime_utc"],
        "not_null": ["row_hash"],
        "dtypes": {"game_datetime_utc": "datetime", "actual_margin_home": "float", "actual_total": "float"},
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
        ],
        "row_hash_keys": [
            "event_id",
            "team_id_home",
            "team_id_away",
            "game_datetime_utc",
            "model_version",
        ],
        "not_null": ["row_hash"],
        "dtypes": {"game_datetime_utc": "datetime", "pred_margin_home": "float", "pred_total": "float"},
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
    """
    Ensure all required columns exist on schema.table.

    Policy change:
    - Only auto-add missing columns for allowlisted tables (wide, evolving feature stores).
    - For all other tables, fail fast to prevent silent schema drift and TEXT-typed numerics.
    """
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


def _prepare_csv_for_load(local_path: Path, table_name: str) -> Path:
    """
    Fix known bad inputs before COPY.

    We deterministically fill missing row_hash using stable columns from the row.
    """
    spec = _get_table_spec(table_name)
    cols = _read_csv_header_columns(local_path)
    _validate_csv_header(cols, local_path)

    has_row_hash = "row_hash" in cols
    wants_row_hash = "row_hash" in spec.get("not_null", []) or bool(spec.get("row_hash_keys"))

    # If we don't need row_hash handling, return original file
    if not wants_row_hash:
        return local_path

    # If we have row_hash and just need to fill blanks, or need to add it
    keys = [k for k in spec.get("row_hash_keys", []) if k in cols]
    if not keys:
        keys = cols

    idx = {c: i for i, c in enumerate(cols)}
    dtype_map = spec.get("dtypes", {})

    # Stream-rewrite to a temp file
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
            # row_hash column exists, just fill blanks
            writer.writerow(header)
            rh_i = idx["row_hash"]
            for row in reader:
                if len(row) < len(header):
                    row = row + [""] * (len(header) - len(row))
                if row[rh_i].strip() == "":
                    row[rh_i] = _stable_row_hash(row, keys, idx, dtype_map)
                writer.writerow(row)
        else:
            # Add row_hash column
            writer.writerow(["row_hash"] + header)
            for row in reader:
                if len(row) < len(header):
                    row = row + [""] * (len(header) - len(row))
                row_hash = _stable_row_hash(row, keys, idx, dtype_map)
                writer.writerow([row_hash] + row)

    return tmp


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

            base_values = [row[idx[c]] if idx.get(c) is not None else "" for c in base_cols]
            writer.writerow(base_values + [json.dumps(payload, separators=(",", ":"), ensure_ascii=False)])

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
                if _normalize_str(val) == "":
                    bad_not_null[col] += 1

            for col, dtype in dtype_map.items():
                i = idx.get(col)
                if i is None:
                    continue
                val = row[i]
                if _normalize_str(val) == "":
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


def _upsert_from_staging(conn: psycopg.Connection, schema: str, table: str, local_path: Path) -> None:
    qt = _qualified_table(schema, table)
    staging = f"tmp_{table}_{int(time.time() * 1000)}"
    qs = _quote_ident(staging)

    table_cols = _get_table_columns(conn, schema, table)
    if not table_cols:
        _die(f"Could not read columns for {schema}.{table} (table exists but no columns?)")

    pk_cols = _get_primary_key_columns(conn, schema, table)
    if not pk_cols:
        _die(f"LOAD_MODE=upsert requires a PRIMARY KEY on {schema}.{table}.\nAdd a PK and rerun.")

    csv_cols = _read_csv_header_columns(local_path)
    _validate_csv_header(csv_cols, local_path)

    # Auto-add missing columns ONLY if allowlisted (new policy)
    _ensure_columns_exist(conn, schema, table, csv_cols)

    # Refresh after ALTERs
    table_cols = _get_table_columns(conn, schema, table)
    missing_in_table = [c for c in csv_cols if c not in table_cols]
    if missing_in_table:
        _die(
            f"Table {schema}.{table} is still missing columns required by {local_path.name}:\n"
            f"{missing_in_table}\n"
            f"Fix: investigate permissions or invalid identifiers."
        )

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

    sql = f"""
      insert into {qt} ({qcols})
      select {qcols} from {qs}
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
        try:
            with psycopg.connect(SUPABASE_DB_URL, autocommit=False) as conn:
                if not _check_table_exists(conn, DB_SCHEMA, table_name):
                    _die(
                        f"Destination table missing: {DB_SCHEMA}.{table_name}\n"
                        f"Create it in Supabase first (SQL Editor)."
                    )

                table_cols = _get_table_columns(conn, DB_SCHEMA, table_name)

                prepared = _prepare_csv_for_load(lp, table_name)
                tmp_path = prepared if prepared != lp else None

                if (
                    PACK_FEATURES_JSON
                    and (DB_SCHEMA, table_name) in PACK_FEATURES_JSON_ALLOWLIST
                ):
                    base_cols = PACK_FEATURES_BASE_COLS.get(table_name, [])
                    if "features" not in table_cols:
                        raise ValueError(
                            f"{DB_SCHEMA}.{table_name} is missing a 'features' jsonb column required for "
                            f"PACK_FEATURES_JSON. Apply the migration and rerun."
                        )
                    packed = _pack_features_json(prepared, table_name, base_cols)
                    packed_path = packed if packed != prepared else None
                    prepared = packed

                validation_result = _preflight_validate_csv(prepared, table_name)

                # Skip empty files
                if validation_result.get("empty", False):
                    print(f"[SKIP] {local_path} is empty (zero data rows)")
                    return

                if LOAD_MODE == "replace":
                    csv_cols = _read_csv_header_columns(prepared)
                    _validate_csv_header(csv_cols, prepared)
                    _ensure_columns_exist(conn, DB_SCHEMA, table_name, csv_cols)

                    qt = _qualified_table(DB_SCHEMA, table_name)
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
                    with conn.cursor() as cur:
                        cur.execute(f"insert into {qt} ({qcols}) select {qcols} from {qtemp};")

                elif LOAD_MODE == "append":
                    csv_cols = _read_csv_header_columns(prepared)
                    _validate_csv_header(csv_cols, prepared)
                    _ensure_columns_exist(conn, DB_SCHEMA, table_name, csv_cols)
                    _copy_csv_into_table(conn, _qualified_table(DB_SCHEMA, table_name), prepared)

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
            if tmp_path and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            if packed_path and packed_path.exists():
                try:
                    packed_path.unlink()
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
