#!/usr/bin/env python3
import os
import re
import sys
import time
from pathlib import Path

import psycopg

# Required
SUPABASE_DB_URL = (os.getenv("SUPABASE_DB_URL") or "").strip()

# Behavior (mirrors your uploader)
UPLOAD_GROUP = (os.getenv("UPLOAD_GROUP") or "all").strip().lower()
SKIP_MISSING = (os.getenv("SKIP_MISSING") or "0").strip().lower() in ("1", "true", "yes")

# DB settings
DB_SCHEMA = (os.getenv("DB_SCHEMA") or "raw").strip()
LOAD_MODE = (os.getenv("LOAD_MODE") or "replace").strip().lower()  # replace | append

# Basic retry (helps with transient network issues)
MAX_RETRIES = int(os.getenv("DB_LOAD_MAX_RETRIES", "3"))
RETRY_INITIAL_DELAY = float(os.getenv("DB_LOAD_RETRY_INITIAL_DELAY", "1.0"))
RETRY_BACKOFF = float(os.getenv("DB_LOAD_RETRY_BACKOFF", "2.0"))

# File groups (local file -> destination table)
# NOTE: Destination tables must already exist in Supabase (SQL Editor).
FILES_ESPN = [
    ("espn_games.csv", "espn_games"),
    ("espn_team_game_logs.csv", "espn_team_game_logs"),
    ("espn_team_game_features.csv", "espn_team_game_features"),
    ("espn_matchups_model_ready.csv", "espn_matchups_model_ready"),
    ("espn_feature_diagnostics.csv", "espn_feature_diagnostics"),
    ("espn_dq_audit.csv", "espn_dq_audit"),
    # JSON is not loaded by this script
    # ("espn_pipeline_errors.json", "espn_pipeline_errors"),
]

FILES_TORVIK = [
    ("barttorvik.csv", "barttorvik"),
    ("barttorvik_team_results.csv", "barttorvik_team_results"),
]

# UPDATED: include predictions_latest.csv
FILES_ML = [
    ("ml/model_features.csv", "model_features"),
    ("ml/dq_audit_ml.csv", "dq_audit_ml"),
    ("ml/predictions_latest.csv", "predictions_latest"),
    # Non-CSV artifacts are skipped by this DB loader
    # ("ml/feature_schema_hash.txt", "feature_schema_hash"),
    # ("ml/run_log.json", "run_log"),
    # ("ml/models/margin_model.json", "margin_model"),
    # ("ml/models/total_model.json", "total_model"),
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
    if LOAD_MODE not in ("replace", "append"):
        _die("LOAD_MODE must be one of: replace, append")
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", DB_SCHEMA):
        _die(f"DB_SCHEMA looks invalid: '{DB_SCHEMA}'")


def _files_for_group():
    if UPLOAD_GROUP == "espn":
        return FILES_ESPN
    if UPLOAD_GROUP == "torvik":
        return FILES_TORVIK
    if UPLOAD_GROUP == "ml":
        return FILES_ML
    return FILES_ESPN + FILES_TORVIK + FILES_ML


def _quote_ident(ident: str) -> str:
    # Safe quoting for schema/table names
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


def _copy_csv(conn: psycopg.Connection, local_path: Path, schema: str, table: str):
    """
    Uses Postgres COPY FROM STDIN with HEADER.
    IMPORTANT: This assumes the table columns are in the same order as the CSV header.
    """
    qt = _qualified_table(schema, table)

    with conn.cursor() as cur:
        if LOAD_MODE == "replace":
            cur.execute(f"truncate table {qt};")

        copy_sql = f"copy {qt} from stdin with (format csv, header true, delimiter ',', quote '\"');"

        with local_path.open("rb") as f:
            with cur.copy(copy_sql) as copy:
                while True:
                    chunk = f.read(1024 * 1024)  # 1MB chunks
                    if not chunk:
                        break
                    copy.write(chunk)


def load_one(local_path: str, table_name: str):
    lp = Path(local_path)

    if not lp.exists():
        if SKIP_MISSING:
            _warn(f"Skipping missing local file: {local_path}")
            return
        _die(f"Local file missing: {local_path}")

    last_err = None

    for attempt in range(MAX_RETRIES):
        if attempt > 0:
            delay = RETRY_INITIAL_DELAY * (RETRY_BACKOFF ** (attempt - 1))
            time.sleep(delay)

        try:
            with psycopg.connect(SUPABASE_DB_URL, autocommit=False) as conn:
                if not _check_table_exists(conn, DB_SCHEMA, table_name):
                    _die(
                        f"Destination table missing: {DB_SCHEMA}.{table_name}\n"
                        f"Create it in Supabase first (SQL Editor)."
                    )

                _copy_csv(conn, lp, DB_SCHEMA, table_name)
                conn.commit()

            print(f"[OK] Loaded {local_path} -> {DB_SCHEMA}.{table_name} (mode={LOAD_MODE})")
            return

        except Exception as e:
            last_err = str(e)

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
