#!/usr/bin/env python3
"""
Audit Supabase ingestion tables for expected data presence.

Purpose:
- Detect when raw ingestion tables are populated but normalized/public tables are empty.
- Surface missing tables so you can create migrations intentionally.

Requires:
- SUPABASE_DB_URL
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

import psycopg

SUPABASE_DB_URL = (os.getenv("SUPABASE_DB_URL") or "").strip()

RAW_TABLES = [
    ("raw", "espn_games"),
    ("raw", "espn_team_game_logs"),
    ("raw", "espn_team_game_features"),
    ("raw", "espn_team_game_extras"),
    ("raw", "espn_matchups_model_ready"),
    ("raw", "ncaa_games"),
    ("raw", "ncaa_team_game_logs"),
    ("raw", "ncaa_player_boxscores"),
    ("raw", "predictions_latest"),
]

PUBLIC_TABLES = [
    ("public", "team_game_features"),
    ("public", "team_metrics"),
    ("public", "team_boxscores"),
]

PAIR_CHECKS = [
    (("raw", "espn_team_game_features"), ("public", "team_game_features")),
    (("raw", "espn_team_game_logs"), ("public", "team_boxscores")),
    (("raw", "ncaa_team_game_logs"), ("public", "team_boxscores")),
]


@dataclass(frozen=True)
class TableStatus:
    schema: str
    table: str
    exists: bool
    rows: Optional[int] = None


def _die(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


def _table_exists(conn: psycopg.Connection, schema: str, table: str) -> bool:
    q = """
    select 1
    from information_schema.tables
    where table_schema = %s and table_name = %s
    limit 1
    """
    with conn.cursor() as cur:
        cur.execute(q, (schema, table))
        return cur.fetchone() is not None


def _row_count(conn: psycopg.Connection, schema: str, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"select count(*) from {schema}.{table};")
        return int(cur.fetchone()[0])


def _scan_tables(conn: psycopg.Connection, tables: Iterable[Tuple[str, str]]) -> List[TableStatus]:
    statuses: List[TableStatus] = []
    for schema, table in tables:
        exists = _table_exists(conn, schema, table)
        rows = _row_count(conn, schema, table) if exists else None
        statuses.append(TableStatus(schema=schema, table=table, exists=exists, rows=rows))
    return statuses


def _find_status(statuses: List[TableStatus], schema: str, table: str) -> Optional[TableStatus]:
    for status in statuses:
        if status.schema == schema and status.table == table:
            return status
    return None


def _print_statuses(title: str, statuses: List[TableStatus]) -> None:
    print(f"\n== {title} ==")
    for status in statuses:
        if not status.exists:
            print(f"[MISSING] {status.schema}.{status.table}")
            continue
        rows = status.rows if status.rows is not None else 0
        label = "empty" if rows == 0 else "rows"
        print(f"[OK] {status.schema}.{status.table}: {rows} {label}")


def _print_pair_warnings(statuses: List[TableStatus]) -> None:
    print("\n== Cross-table checks ==")
    any_warning = False
    for (raw_schema, raw_table), (pub_schema, pub_table) in PAIR_CHECKS:
        raw_status = _find_status(statuses, raw_schema, raw_table)
        pub_status = _find_status(statuses, pub_schema, pub_table)
        if not raw_status or not pub_status:
            continue
        if raw_status.exists and pub_status.exists:
            raw_rows = raw_status.rows or 0
            pub_rows = pub_status.rows or 0
            if raw_rows > 0 and pub_rows == 0:
                any_warning = True
                print(
                    f"[WARN] {raw_schema}.{raw_table} has data ({raw_rows} rows) but "
                    f"{pub_schema}.{pub_table} is empty."
                )
    if not any_warning:
        print("[OK] No raw/public mismatches detected.")


def main() -> None:
    if not SUPABASE_DB_URL:
        _die("SUPABASE_DB_URL is missing/empty.")

    with psycopg.connect(SUPABASE_DB_URL) as conn:
        raw_statuses = _scan_tables(conn, RAW_TABLES)
        public_statuses = _scan_tables(conn, PUBLIC_TABLES)

    all_statuses = raw_statuses + public_statuses

    _print_statuses("Raw ingestion tables", raw_statuses)
    _print_statuses("Public/model tables", public_statuses)
    _print_pair_warnings(all_statuses)

    print("\nNext steps:")
    print("- If public tables are missing, add a migration to create them.")
    print("- If public tables exist but are empty, add a normalization step to map raw -> public.")


if __name__ == "__main__":
    main()
