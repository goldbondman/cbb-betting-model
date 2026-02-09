#!/usr/bin/env python3
"""
Seed public.teams from teams_2026.json.

Mapping:
- sourceId -> source_team_id
- displayName -> team_name
- shortDisplayName -> short_name
- mascot -> mascot
- conference -> conference

Requires:
- SUPABASE_DB_URL
- SEASON (env)
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

import psycopg

SUPABASE_DB_URL = (os.getenv("SUPABASE_DB_URL") or "").strip()
SEASON = int(os.getenv("SEASON", "2026"))
TEAMS_JSON_PATH = Path(os.getenv("TEAMS_JSON_PATH", "data/cbbapi/teams/teams_2026.json"))


@dataclass(frozen=True)
class Counts:
    total: int = 0
    inserted: int = 0
    rejected: int = 0


def _die(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


def _has_column(conn: psycopg.Connection, schema: str, table: str, column: str) -> bool:
    q = """
    select 1
    from information_schema.columns
    where table_schema = %s and table_name = %s and column_name = %s
    limit 1
    """
    with conn.cursor() as cur:
        cur.execute(q, (schema, table, column))
        return cur.fetchone() is not None


def _teams_pk_column(conn: psycopg.Connection) -> Optional[str]:
    if _has_column(conn, "public", "teams", "team_id"):
        return "team_id"
    if _has_column(conn, "public", "teams", "id"):
        return "id"
    return None


def _exec_rowcount(conn: psycopg.Connection, sql: str, params: Iterable[object]) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def _insert_dq(conn: psycopg.Connection, missing: List[dict]) -> int:
    if not missing:
        return 0
    sql = """
    insert into public.dq_audit (id, entity_type, entity_id, severity, reason_codes, details, created_at)
    values (gen_random_uuid(), 'teams', null, 'error', array['missing_required_fields'], %s::jsonb, now())
    """
    payload = json.dumps({"rows": missing}, ensure_ascii=False)
    return _exec_rowcount(conn, sql, (payload,))


def main() -> None:
    if not SUPABASE_DB_URL:
        _die("SUPABASE_DB_URL is missing/empty.")
    if not TEAMS_JSON_PATH.exists():
        _die(f"teams json not found: {TEAMS_JSON_PATH}")

    data = json.loads(TEAMS_JSON_PATH.read_text())
    if not isinstance(data, list):
        _die("teams json must be a list")

    with psycopg.connect(SUPABASE_DB_URL) as conn:
        pk_col = _teams_pk_column(conn)
        if not pk_col:
            _die("public.teams must have either team_id or id column")

        has_short_name = _has_column(conn, "public", "teams", "short_name")
        has_mascot = _has_column(conn, "public", "teams", "mascot")
        has_conference = _has_column(conn, "public", "teams", "conference")

        rows = []
        missing = []
        for row in data:
            source_id = str(row.get("sourceId") or "").strip()
            display_name = str(row.get("displayName") or "").strip()
            if not source_id or not display_name:
                missing.append({"sourceId": row.get("sourceId"), "displayName": row.get("displayName")})
                continue
            rows.append(
                {
                    "source_team_id": source_id,
                    "team_name": display_name,
                    "short_name": str(row.get("shortDisplayName") or "").strip(),
                    "mascot": str(row.get("mascot") or "").strip(),
                    "conference": str(row.get("conference") or "").strip(),
                }
            )

        rejected = _insert_dq(conn, missing)

        if not rows:
            _die("No valid team rows found in JSON.")

        columns = [pk_col, "season", "source_team_id", "team_name", "created_at", "updated_at"]
        select_expr = ["gen_random_uuid()", "%s", "%s", "%s", "now()", "now()"]
        if has_short_name:
            columns.append("short_name")
            select_expr.append("%s")
        if has_mascot:
            columns.append("mascot")
            select_expr.append("%s")
        if has_conference:
            columns.append("conference")
            select_expr.append("%s")

        qcols = ", ".join(columns)
        qvals = ", ".join(select_expr)

        sql = f"""
        insert into public.teams ({qcols})
        values ({qvals})
        on conflict (season, source_team_id)
        do update set
          team_name = excluded.team_name,
          updated_at = now()
        """
        if has_short_name:
            sql += ", short_name = excluded.short_name"
        if has_mascot:
            sql += ", mascot = excluded.mascot"
        if has_conference:
            sql += ", conference = excluded.conference"
        sql += ";"

        inserted = 0
        for row in rows:
            params: List[object] = [SEASON, row["source_team_id"], row["team_name"]]
            if has_short_name:
                params.append(row["short_name"])
            if has_mascot:
                params.append(row["mascot"])
            if has_conference:
                params.append(row["conference"])
            inserted += _exec_rowcount(conn, sql, params)

        conn.commit()

    print(f"[OK] teams seeded: total={len(rows)} inserted/updated={inserted} rejected={rejected}")


if __name__ == "__main__":
    main()
