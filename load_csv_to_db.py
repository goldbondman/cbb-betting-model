#!/usr/bin/env python3
import os
import re
import sys
import time
import hashlib
import tempfile
from pathlib import Path
from typing import List, Tuple, Optional

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
    ("espn_feature_diagnostics.csv", "espn_feature_diagnostics"),
    ("espn_dq_audit.csv", "espn_dq_audit"),
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


def _die(msg: str, code: int = 1):
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(code)


def _warn(msg: str):
    print(f"[WARN] {msg}")


def _validate_env():
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
    header_line = local_path.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    cols = [c.strip() for c in header_line.split(",") if c.strip()]
    return cols


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _ensure_columns_exist(conn: psycopg.Connection, schema: str, table: str, required_cols: List[str]) -> None:
    """
    Ensure all required columns exist on schema.table.
    Adds missing columns as TEXT (fast + safe for CSV COPY).
    """
    existing = set(_get_table_columns(conn, schema, table))
    missing = [c for c in required_cols if c not in existing]
    if not missing:
        return

    qt = _qualified_table(schema, table)
    with conn.cursor() as cur:
        for c in missing:
            cur.execute(f"alter table {qt} add column if not exists {_quote_ident(c)} text;")

    print(f"[INFO] Added {len(missing)} missing columns to {schema}.{table}")


def _prepare_csv_for_load(local_path: Path, table_name: str) -> Path:
    """
    Fix known bad inputs before COPY.

    Current known issue:
      - espn_team_game_logs / espn_team_game_features occasionally have blank row_hash values.
        But row_hash is PK + NOT NULL, so COPY fails.

    We deterministically fill missing row_hash using stable columns from the row.
    """
    if table_name not in ("espn_team_game_logs", "espn_team_game_features"):
        return local_path

    cols = _read_csv_header_columns(local_path)
    if "row_hash" not in cols:
        # If row_hash is missing entirely, let the normal "missing columns" gate catch it.
        return local_path

    idx = {c: i for i, c in enumerate(cols)}
    rh_i = idx["row_hash"]

    # Build candidate key columns (use what exists in the CSV)
    key_candidates = [
        "event_id",
        "team_id",
        "team",
        "opponent",
        "home_away",
        "game_datetime_utc",
        "game_date_utc",
        "game_date",
        "parse_version",
    ]
    key_cols = [c for c in key_candidates if c in idx]
    if not key_cols:
        # Worst case: fall back to full row content (still deterministic, but more sensitive to small changes)
        key_cols = cols

    # Stream-rewrite to a temp file
    tmp = Path(tempfile.mkstemp(prefix=f"loadfix_{table_name}_", suffix=".csv")[1])
    with local_path.open("r", encoding="utf-8", errors="replace", newline="") as fin, tmp.open(
        "w", encoding="utf-8", newline=""
    ) as fout:
        header = fin.readline()
        if not header:
            return local_path
        fout.write(header)

        for line in fin:
            # NOTE: This assumes no embedded commas in fields (your pipeline outputs appear simple).
            # If this ever breaks, we'll swap to Python's csv module for robust parsing.
            parts = line.rstrip("\n").split(",")
            if len(parts) < len(cols):
                fout.write(line)
                continue

            if parts[rh_i].strip() == "":
                key = "|".join((parts[idx[c]].strip() if idx[c] < len(parts) else "") for c in key_cols)
                parts[rh_i] = _sha256(key)
                fout.write(",".join(parts) + "\n")
            else:
                fout.write(line)

    return tmp


def _copy_csv_into_table(conn: psycopg.Connection, qualified_table: str, local_path: Path):
    cols = _read_csv_header_columns(local_path)
    if not cols:
        raise ValueError(f"CSV appears to have an empty header: {local_path}")

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


def _truncate(conn: psycopg.Connection, schema: str, table: str):
    qt = _qualified_table(schema, table)
    with conn.cursor() as cur:
        cur.execute(f"truncate table {qt};")


def _upsert_from_staging(conn: psycopg.Connection, schema: str, table: str, local_path: Path):
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

    # Auto-add missing columns as TEXT so evolving feature CSVs don't break the pipeline
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


def load_one(local_path: str, table_name: str):
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
        try:
            prepared = _prepare_csv_for_load(lp, table_name)
            tmp_path = prepared if prepared != lp else None

            with psycopg.connect(SUPABASE_DB_URL, autocommit=False) as conn:
                if not _check_table_exists(conn, DB_SCHEMA, table_name):
                    _die(
                        f"Destination table missing: {DB_SCHEMA}.{table_name}\n"
                        f"Create it in Supabase first (SQL Editor)."
                    )

                if LOAD_MODE == "replace":
                    _truncate(conn, DB_SCHEMA, table_name)
                    _copy_csv_into_table(conn, _qualified_table(DB_SCHEMA, table_name), prepared)

                elif LOAD_MODE == "append":
                    _copy_csv_into_table(conn, _qualified_table(DB_SCHEMA, table_name), prepared)

                elif LOAD_MODE == "upsert":
                    _upsert_from_staging(conn, DB_SCHEMA, table_name, prepared)

                conn.commit()

            print(f"[OK] Loaded {local_path} -> {DB_SCHEMA}.{table_name} (mode={LOAD_MODE})")
            return

        except SystemExit:
            raise
        except Exception as e:
            last_err = str(e)
        finally:
            if tmp_path and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

    raise RuntimeError(
        f"DB load failed after {MAX_RETRIES} attempts: {local_path} -> {DB_SCHEMA}.{table_name}\n"
        f"Last error: {last_err}"
    )


def main():
    _validate_env()
    files = _files_for_group()
    print(f"[INFO] Loading group='{UPLOAD_GROUP}' files={len(files)} skip_missing={SKIP_MISSING} mode={LOAD_MODE}")
    for local, table in files:
        load_one(local, table)


if __name__ == "__main__":
    main()
