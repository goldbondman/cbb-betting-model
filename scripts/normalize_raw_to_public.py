#!/usr/bin/env python3
"""
Normalize raw ESPN ingestion tables into public model tables.

Targets:
- public.teams
- public.games
- public.team_boxscores
- public.team_game_features
- public.dq_audit

Contract notes (based on your indexes):
- public.games PK: game_id (TEXT), unique: (season, source, external_game_id)
- public.team_boxscores unique: (game_id, team_id) and (game_id, team)
- public.team_game_features unique: (game_id, team_id, feature_set)
- public.teams unique: (season, source_team_id)

This script:
- Dedupe raw rows before inserts to avoid ON CONFLICT cardinality issues.
- Normalizes types defensively (completed, scores, numeric stats).
- Populates deterministic TEXT game_id (no DB default required).
- Includes comprehensive error handling and validation.

NOTE:
- public.team_boxscores has GENERATED ALWAYS columns (efg, tov_pct). We never insert/update them.
- IMPORTANT: public.team_boxscores.team_id has an FK to public.teams(team_id).
  So we must insert the *public teams PK* (teams.team_id), not the raw/source team_id.

SEASON / DATE RANGE BEHAVIOR (important for 25-26 season):
- In your pipeline, SEASON=2025 represents the 2025-26 season.
- By default, we filter games to start at Oct 1 of SEASON (2025-10-01) through "now".
- Override with:
  - SEASON_START_DATE (YYYY-MM-DD), default: "{SEASON}-10-01"
  - SEASON_END_DATE (YYYY-MM-DD), optional

CRITICAL DATA INTEGRITY FIX (scores being wiped):
- Raw "completed=true" rows may arrive later without scores.
- If you dedupe by latest pulled_at, you can select a null-score row.
- If you ON CONFLICT update scores directly, you can overwrite a prior non-null score with null.
Fixes in this file:
1) Dedupe for games prefers rows WITH scores, then latest pulled_at.
2) Upsert for games never overwrites existing non-null scores with nulls.

2026-02-10 FIX:
- Game-level scores/status MUST come from raw.espn_games (not team logs).
- Team IDs (home/away) still come from raw.espn_team_game_logs.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import psycopg

SUPABASE_DB_URL = (os.getenv("SUPABASE_DB_URL") or "").strip()
SEASON = int(os.getenv("SEASON", "2025"))
SOURCE = (os.getenv("SOURCE", "espn").strip().lower() or "espn")
FEATURE_SET = (os.getenv("FEATURE_SET", "espn_v1").strip() or "espn_v1")

RAW_SCHEMA = (os.getenv("RAW_SCHEMA", "raw").strip() or "raw")
RAW_GAMES_TABLE = (os.getenv("RAW_GAMES_TABLE", "espn_games").strip() or "espn_games")
RAW_LOGS_TABLE = (os.getenv("RAW_LOGS_TABLE", "espn_team_game_logs").strip() or "espn_team_game_logs")
RAW_FEATURES_TABLE = (os.getenv("RAW_FEATURES_TABLE", "espn_team_game_features").strip() or "espn_team_game_features")

TEAMS_SEED_JSON = (os.getenv("TEAMS_SEED_JSON") or "").strip()

# Default: academic season window for SEASON=2025 -> start 2025-10-01
SEASON_START_DATE = (os.getenv("SEASON_START_DATE") or f"{SEASON}-10-01").strip()
SEASON_END_DATE = (os.getenv("SEASON_END_DATE") or "").strip()  # optional

COMPLETED_TRUE_TOKENS = (
    "true",
    "t",
    "1",
    "yes",
    "y",
    "final",
    "completed",
    "complete",
    "finished",
    "post",
)

# Validation constants
MAX_REASONABLE_SCORE = 200  # Sanity check for basketball scores
MIN_REASONABLE_YEAR = 2000
MAX_REASONABLE_YEAR = 2100


@dataclass(frozen=True)
class Counts:
    pulled: int = 0
    upserted: int = 0
    rejected: int = 0


def _die(msg: str, exit_code: int = 1) -> None:
    """Print error message and exit with code."""
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(exit_code)


def _warn(msg: str) -> None:
    """Print warning message without exiting."""
    print(f"[WARN] {msg}", file=sys.stderr)


def _info(msg: str) -> None:
    """Print info message."""
    print(f"[INFO] {msg}")


def _validate_env_vars() -> None:
    """Validate required environment variables."""
    if not SUPABASE_DB_URL:
        _die("SUPABASE_DB_URL is required but not set")

    if not MIN_REASONABLE_YEAR <= SEASON <= MAX_REASONABLE_YEAR:
        _die(f"SEASON {SEASON} is outside reasonable range [{MIN_REASONABLE_YEAR}, {MAX_REASONABLE_YEAR}]")

    if not SOURCE:
        _die("SOURCE cannot be empty")

    if TEAMS_SEED_JSON and not os.path.exists(TEAMS_SEED_JSON):
        _warn(f"TEAMS_SEED_JSON points to non-existent file: {TEAMS_SEED_JSON}")

    # Basic date sanity checks (lightweight, avoid extra deps)
    if len(SEASON_START_DATE) != 10 or SEASON_START_DATE[4] != "-" or SEASON_START_DATE[7] != "-":
        _die(f"SEASON_START_DATE must be YYYY-MM-DD, got: {SEASON_START_DATE}")

    if SEASON_END_DATE:
        if len(SEASON_END_DATE) != 10 or SEASON_END_DATE[4] != "-" or SEASON_END_DATE[7] != "-":
            _die(f"SEASON_END_DATE must be YYYY-MM-DD, got: {SEASON_END_DATE}")


def _table_exists(conn: psycopg.Connection, schema: str, table: str) -> bool:
    """Check if a table exists in the database."""
    try:
        q = """
        select 1
        from information_schema.tables
        where table_schema = %s and table_name = %s
        limit 1
        """
        with conn.cursor() as cur:
            cur.execute(q, (schema, table))
            return cur.fetchone() is not None
    except Exception as e:
        _warn(f"Error checking if table {schema}.{table} exists: {e}")
        return False


def _has_column(conn: psycopg.Connection, schema: str, table: str, column: str) -> bool:
    """Check if a column exists in a table."""
    try:
        q = """
        select 1
        from information_schema.columns
        where table_schema = %s and table_name = %s and column_name = %s
        limit 1
        """
        with conn.cursor() as cur:
            cur.execute(q, (schema, table, column))
            return cur.fetchone() is not None
    except Exception as e:
        _warn(f"Error checking column {schema}.{table}.{column}: {e}")
        return False


def _pick_existing_column(
    conn: psycopg.Connection, schema: str, table: str, candidates: Sequence[str]
) -> Optional[str]:
    """Return the first existing column from candidates, or None."""
    for col in candidates:
        if _has_column(conn, schema, table, col):
            return col
    return None


def _exec_rowcount(
    conn: psycopg.Connection,
    sql: str,
    params: Optional[Iterable[object]] = None,
    description: str = "",
) -> int:
    """Execute SQL and return rowcount with error handling."""
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.rowcount
    except Exception as e:
        error_msg = "Error executing SQL"
        if description:
            error_msg += f" ({description})"
        error_msg += f": {e}"
        _warn(error_msg)
        _warn(f"SQL (first 500 chars): {sql[:500]}")
        raise


def _count_rows(
    conn: psycopg.Connection,
    sql: str,
    params: Optional[Iterable[object]] = None,
) -> int:
    """Execute count query and return integer result."""
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            result = cur.fetchone()
            return int(result[0]) if result else 0
    except Exception as e:
        _warn(f"Error counting rows: {e}")
        return 0


def json_dump(obj: Any) -> str:
    """Safely dump object to JSON string."""
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception as e:
        _warn(f"Error serializing to JSON: {e}")
        return "{}"


def _dq_id(entity_type: str, reason_codes: List[str], details: dict) -> str:
    """Generate deterministic UUID for DQ audit entry."""
    payload = f"{entity_type}|{','.join(sorted(reason_codes))}|{json_dump(details)}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, payload))


def _insert_dq(
    conn: psycopg.Connection,
    entity_type: str,
    reason_codes: List[str],
    details: dict,
) -> int:
    """Insert data quality audit record (idempotent via deterministic ID)."""
    try:
        dqid = _dq_id(entity_type, reason_codes, details)
        sql = """
        insert into public.dq_audit (id, entity_type, entity_id, severity, reason_codes, details, created_at)
        values (%s, %s, null, %s, %s, %s::jsonb, now())
        on conflict (id) do nothing
        """
        return _exec_rowcount(
            conn,
            sql,
            (dqid, entity_type, "error", reason_codes, json_dump(details)),
            f"insert DQ audit for {entity_type}",
        )
    except Exception as e:
        _warn(f"Error inserting DQ audit: {e}")
        return 0


def _teams_pk_column(conn: psycopg.Connection) -> str:
    """Determine the primary key column name for teams table."""
    if _has_column(conn, "public", "teams", "team_id"):
        return "team_id"
    if _has_column(conn, "public", "teams", "id"):
        return "id"
    _warn("teams table has neither team_id nor id column, defaulting to 'id'")
    return "id"


def _teams_uuid_col(conn: psycopg.Connection) -> Optional[str]:
    """Get the UUID column name for teams table (the PK we populate)."""
    if _has_column(conn, "public", "teams", "team_id"):
        return "team_id"
    if _has_column(conn, "public", "teams", "id"):
        return "id"
    return None


def _validate_raw_table(conn: psycopg.Connection, schema: str, table: str, required_cols: List[str]) -> bool:
    """Validate that a raw table exists and has required columns."""
    if not _table_exists(conn, schema, table):
        _warn(f"Raw table {schema}.{table} does not exist")
        return False

    missing_cols = [col for col in required_cols if not _has_column(conn, schema, table, col)]
    if missing_cols:
        _warn(f"Raw table {schema}.{table} is missing required columns: {missing_cols}")
        return False

    return True


def seed_teams_from_json(conn: psycopg.Connection, path: str) -> Counts:
    """Seed teams from JSON reference file."""
    if not path:
        return Counts()

    if not os.path.exists(path):
        _insert_dq(conn, "teams", ["seed_file_missing"], {"seed_path": path})
        return Counts(rejected=1)

    teams_uuid_col = _teams_uuid_col(conn)
    if not teams_uuid_col:
        _insert_dq(
            conn,
            "teams",
            ["public_teams_missing_pk"],
            {"note": "Expected public.teams to have team_id or id."},
        )
        return Counts(rejected=1)

    has_conference = _has_column(conn, "public", "teams", "conference")
    has_short_name = _has_column(conn, "public", "teams", "short_name")

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        _insert_dq(conn, "teams", ["seed_file_invalid_json"], {"seed_path": path, "error": str(e)})
        return Counts(rejected=1)
    except Exception as e:
        _insert_dq(conn, "teams", ["seed_file_read_error"], {"seed_path": path, "error": str(e)})
        return Counts(rejected=1)

    if not isinstance(data, list):
        _insert_dq(conn, "teams", ["seed_file_invalid_json"], {"seed_path": path, "note": "Expected JSON array."})
        return Counts(rejected=1)

    seen: Dict[str, Tuple[str, str, Optional[str], Optional[str]]] = {}
    skipped = 0
    for _, row in enumerate(data):
        if not isinstance(row, dict):
            skipped += 1
            continue

        source_id = row.get("sourceId")
        if source_id is None or str(source_id).strip() == "":
            skipped += 1
            continue

        team_name = row.get("shortDisplayName") or row.get("school") or row.get("displayName")
        if not team_name:
            skipped += 1
            continue

        conf = row.get("conference")
        short_name = row.get("shortDisplayName") or row.get("school") or None

        seen[str(source_id)] = (
            str(source_id),
            str(team_name),
            (str(conf) if conf is not None else None),
            (str(short_name) if short_name else None),
        )

    pulled = len(seen)
    if pulled == 0:
        _warn(f"No valid teams found in {path} (skipped {skipped} rows)")
        return Counts()

    if skipped > 0:
        _info(f"Skipped {skipped} invalid team entries from {path}")

    try:
        if has_conference and has_short_name:
            sql = f"""
            with src as (
              select * from unnest(%s::text[], %s::text[], %s::text[], %s::text[])
                as s(source_team_id, team_name, conference, short_name)
            )
            insert into public.teams ({teams_uuid_col}, season, source_team_id, team_name, conference, short_name, created_at, updated_at)
            select
              gen_random_uuid(),
              %s,
              s.source_team_id,
              s.team_name,
              nullif(s.conference,''),
              nullif(s.short_name,''),
              now(),
              now()
            from src s
            on conflict (season, source_team_id)
            do update set
              team_name = excluded.team_name,
              conference = excluded.conference,
              short_name = excluded.short_name,
              updated_at = now();
            """
            source_ids = [t[0] for t in seen.values()]
            names = [t[1] for t in seen.values()]
            confs = [t[2] or "" for t in seen.values()]
            shorts = [t[3] or "" for t in seen.values()]
            upserted = _exec_rowcount(
                conn,
                sql,
                (source_ids, names, confs, shorts, SEASON),
                "seed teams with conference and short_name",
            )
        elif has_conference:
            sql = f"""
            with src as (
              select * from unnest(%s::text[], %s::text[], %s::text[])
                as s(source_team_id, team_name, conference)
            )
            insert into public.teams ({teams_uuid_col}, season, source_team_id, team_name, conference, created_at, updated_at)
            select
              gen_random_uuid(),
              %s,
              s.source_team_id,
              s.team_name,
              nullif(s.conference,''),
              now(),
              now()
            from src s
            on conflict (season, source_team_id)
            do update set
              team_name = excluded.team_name,
              conference = excluded.conference,
              updated_at = now();
            """
            source_ids = [t[0] for t in seen.values()]
            names = [t[1] for t in seen.values()]
            confs = [t[2] or "" for t in seen.values()]
            upserted = _exec_rowcount(conn, sql, (source_ids, names, confs, SEASON), "seed teams with conference")
        else:
            sql = f"""
            with src as (
              select * from unnest(%s::text[], %s::text[])
                as s(source_team_id, team_name)
            )
            insert into public.teams ({teams_uuid_col}, season, source_team_id, team_name, created_at, updated_at)
            select
              gen_random_uuid(),
              %s,
              s.source_team_id,
              s.team_name,
              now(),
              now()
            from src s
            on conflict (season, source_team_id)
            do update set
              team_name = excluded.team_name,
              updated_at = now();
            """
            source_ids = [t[0] for t in seen.values()]
            names = [t[1] for t in seen.values()]
            upserted = _exec_rowcount(conn, sql, (source_ids, names, SEASON), "seed teams basic")

        return Counts(pulled=pulled, upserted=upserted, rejected=0)
    except Exception as e:
        _warn(f"Error seeding teams from JSON: {e}")
        traceback.print_exc()
        return Counts(pulled=pulled, rejected=pulled)


def upsert_teams(conn: psycopg.Connection) -> Counts:
    """Upsert teams from raw logs table."""
    if not _validate_raw_table(conn, RAW_SCHEMA, RAW_LOGS_TABLE, ["team_id", "team"]):
        return Counts(rejected=1)

    try:
        pulled = _count_rows(
            conn,
            f"""
            select count(distinct team_id)
            from {RAW_SCHEMA}.{RAW_LOGS_TABLE}
            where team_id is not null and team is not null
            """,
        )
    except Exception as e:
        _warn(f"Error counting teams: {e}")
        return Counts(rejected=1)

    teams_uuid_col = _teams_uuid_col(conn)
    if not teams_uuid_col:
        _insert_dq(conn, "teams", ["public_teams_missing_pk"], {"note": "Expected public.teams to have team_id or id."})
        return Counts(pulled=pulled, rejected=1)

    has_conference = _has_column(conn, "public", "teams", "conference")
    has_short_name = _has_column(conn, "public", "teams", "short_name")

    try:
        if has_conference and has_short_name:
            sql = f"""
            with src as (
              select
                cast(team_id as text) as source_team_id,
                max(team) as team_name,
                null::text as conference,
                max(team) as short_name
              from {RAW_SCHEMA}.{RAW_LOGS_TABLE}
              where team_id is not null and team is not null
              group by cast(team_id as text)
            )
            insert into public.teams ({teams_uuid_col}, season, source_team_id, team_name, conference, short_name, created_at, updated_at)
            select
              gen_random_uuid(),
              %s,
              s.source_team_id,
              s.team_name,
              s.conference,
              s.short_name,
              now(),
              now()
            from src s
            on conflict (season, source_team_id)
            do update set
              team_name = excluded.team_name,
              short_name = excluded.short_name,
              updated_at = now();
            """
            upserted = _exec_rowcount(conn, sql, (SEASON,), "upsert teams with short_name")
        else:
            sql = f"""
            with src as (
              select
                cast(team_id as text) as source_team_id,
                max(team) as team_name
              from {RAW_SCHEMA}.{RAW_LOGS_TABLE}
              where team_id is not null and team is not null
              group by cast(team_id as text)
            )
            insert into public.teams ({teams_uuid_col}, season, source_team_id, team_name, created_at, updated_at)
            select
              gen_random_uuid(),
              %s,
              s.source_team_id,
              s.team_name,
              now(),
              now()
            from src s
            on conflict (season, source_team_id)
            do update set
              team_name = excluded.team_name,
              updated_at = now();
            """
            upserted = _exec_rowcount(conn, sql, (SEASON,), "upsert teams basic")

        return Counts(pulled=pulled, upserted=upserted, rejected=0)
    except Exception as e:
        _warn(f"Error upserting teams: {e}")
        traceback.print_exc()
        return Counts(pulled=pulled, rejected=pulled)


def upsert_games(conn: psycopg.Connection, teams_pk: str) -> Counts:
    """
    Upsert games.

    Authoritative game-level fields (scores, completed, status, datetime) come from raw.espn_games.
    Team IDs (home/away) come from raw.espn_team_game_logs.
    """
    # raw.espn_games is authoritative for scores/status/datetime
    if not _validate_raw_table(
        conn,
        RAW_SCHEMA,
        RAW_GAMES_TABLE,
        ["game_id", "game_datetime_utc", "home_team", "away_team", "completed", "pulled_at_utc"],
    ):
        return Counts(rejected=1)

    # raw logs provide team_id mapping for home/away
    if not _validate_raw_table(conn, RAW_SCHEMA, RAW_LOGS_TABLE, ["event_id", "team_id", "home_away"]):
        return Counts(rejected=1)

    # Robust numeric->int parsing (handles '79', '79.0', numeric types, text, etc.)
    def sql_int(expr: str, max_val: int = MAX_REASONABLE_SCORE) -> str:
        return f"""
        case
          when {expr} is null then null
          when btrim(({expr})::text) = '' then null
          when btrim(({expr})::text) ~ '^\\d+(\\.\\d+)?$' then
            case
              when (({expr})::numeric)::int between 0 and {max_val} then (({expr})::numeric)::int
              else null
            end
          else null
        end
        """

    # Date filter clause (parameterized) based on raw games datetime
    if SEASON_END_DATE:
        games_date_filter_sql = "and g.game_datetime_utc >= %s::timestamptz and g.game_datetime_utc < (%s::date + interval '1 day')"
        date_params: Tuple[object, ...] = (SEASON_START_DATE, SEASON_END_DATE)
    else:
        games_date_filter_sql = "and g.game_datetime_utc >= %s::timestamptz"
        date_params = (SEASON_START_DATE,)

    # Count pulled distinct games (within window)
    try:
        pulled = _count_rows(
            conn,
            f"""
            select count(distinct g.game_id)
            from {RAW_SCHEMA}.{RAW_GAMES_TABLE} g
            where g.game_id is not null
              and g.game_datetime_utc is not null
              {games_date_filter_sql}
            """,
            date_params,
        )
    except Exception as e:
        _warn(f"Error counting games: {e}")
        return Counts(rejected=1)

    # logs pulled_at (for dedup home/away team mapping)
    logs_pulled_at_col = _pick_existing_column(conn, RAW_SCHEMA, RAW_LOGS_TABLE, ["pulled_at_utc", "pulled_at"])
    if logs_pulled_at_col:
        logs_pulled_at_expr = f"COALESCE(r.{logs_pulled_at_col}, now())"
    else:
        logs_pulled_at_expr = "now()"

    completed_true_list_sql = ", ".join([f"'{t}'" for t in COMPLETED_TRUE_TOKENS])

    try:
        hs_expr = sql_int("g.home_score", MAX_REASONABLE_SCORE)
        as_expr = sql_int("g.away_score", MAX_REASONABLE_SCORE)

        # Prefer rows with scores, then latest pulled_at_utc (prevents score wipe)
        sql = f"""
        with games_base as (
          select
            cast(g.game_id as text) as event_id,
            g.game_datetime_utc,
            g.venue,
            g.home_team as home_team_name,
            g.away_team as away_team_name,

            case
              when g.completed is true then true
              when lower(coalesce(g.state::text,'')) in ({completed_true_list_sql}) then true
              when lower(coalesce(g.status_desc::text,'')) in ({completed_true_list_sql}) then true
              else false
            end as completed,

            {hs_expr} as home_score,
            {as_expr} as away_score,

            ({hs_expr} is not null and {as_expr} is not null) as has_scores,

            g.state as raw_state,
            g.status_desc as raw_status_desc,
            g.status_detail as raw_status_detail,
            g.pulled_at_utc as pulled_at
          from {RAW_SCHEMA}.{RAW_GAMES_TABLE} g
          where g.game_id is not null
            and g.game_datetime_utc is not null
            {games_date_filter_sql}
        ),
        games_dedup as (
          select *
          from (
            select
              gb.*,
              row_number() over (
                partition by gb.event_id
                order by gb.has_scores desc, gb.pulled_at desc nulls last
              ) as rn
            from games_base gb
          ) x
          where x.rn = 1
        ),

        logs_base as (
          select
            r.event_id,
            cast(r.team_id as text) as source_team_id,
            lower(r.home_away) as home_away,
            max(r.team) over (partition by r.event_id, lower(r.home_away)) as team_name,
            {logs_pulled_at_expr} as pulled_at
          from {RAW_SCHEMA}.{RAW_LOGS_TABLE} r
          where r.event_id is not null
            and r.team_id is not null
            and r.home_away is not null
            and btrim(r.home_away) <> ''
        ),
        logs_dedup as (
          select *
          from (
            select
              lb.*,
              row_number() over (
                partition by lb.event_id, lb.home_away
                order by lb.pulled_at desc nulls last
              ) as rn
            from logs_base lb
          ) x
          where x.rn = 1
        ),
        home as (
          select
            event_id,
            source_team_id as home_source_team_id,
            team_name as home_team_name_logs
          from logs_dedup
          where home_away = 'home'
        ),
        away as (
          select
            event_id,
            source_team_id as away_source_team_id,
            team_name as away_team_name_logs
          from logs_dedup
          where home_away = 'away'
        ),
        joined as (
          select
            g.event_id,
            g.game_datetime_utc,
            g.venue,
            g.completed,
            g.home_score,
            g.away_score,
            g.raw_state,
            g.raw_status_desc,
            g.raw_status_detail,

            h.home_source_team_id,
            a.away_source_team_id,

            -- Prefer espn_games names, fallback to logs
            coalesce(nullif(g.home_team_name,''), nullif(h.home_team_name_logs,'')) as home_team_name,
            coalesce(nullif(g.away_team_name,''), nullif(a.away_team_name_logs,'')) as away_team_name
          from games_dedup g
          join home h on h.event_id = g.event_id
          join away a on a.event_id = g.event_id
        )
        insert into public.games (
          game_id,
          season,
          source,
          external_game_id,
          game_datetime_utc,
          start_time_utc,
          game_date,
          game_date_date,
          home_team_id,
          away_team_id,
          home_team,
          away_team,
          home_score,
          away_score,
          venue,
          status,
          status_state,
          status_detail,
          verification_status,
          created_at,
          updated_at
        )
        select
          md5((%s::text) || '|' || lower(%s) || '|' || j.event_id::text) as game_id,
          %s as season,
          lower(%s) as source,
          j.event_id as external_game_id,
          j.game_datetime_utc as game_datetime_utc,
          j.game_datetime_utc as start_time_utc,
          (j.game_datetime_utc at time zone 'utc')::date as game_date,
          (j.game_datetime_utc at time zone 'utc')::date as game_date_date,
          ht.{teams_pk} as home_team_id,
          at.{teams_pk} as away_team_id,
          j.home_team_name as home_team,
          j.away_team_name as away_team,

          case when j.completed then j.home_score else null end as home_score,
          case when j.completed then j.away_score else null end as away_score,

          j.venue,

          -- status fields: prefer raw espn values, else derive
          case
            when lower(coalesce(j.raw_status_desc::text,'')) in ({completed_true_list_sql}) then 'final'
            when j.completed then 'final'
            else 'scheduled'
          end as status,
          case
            when lower(coalesce(j.raw_state::text,'')) in ({completed_true_list_sql}) then 'post'
            when j.completed then 'post'
            else 'pre'
          end as status_state,
          coalesce(nullif(j.raw_status_detail::text,''), case when j.completed then 'Final' else 'Scheduled' end) as status_detail,

          case
            when j.completed and j.home_score is not null and j.away_score is not null then 'verified'
            else 'partial'
          end as verification_status,
          now(),
          now()
        from joined j
        join public.teams ht
          on ht.season = %s and cast(ht.source_team_id as text) = j.home_source_team_id
        join public.teams at
          on at.season = %s and cast(at.source_team_id as text) = j.away_source_team_id
        on conflict (season, source, external_game_id)
        do update set
          game_datetime_utc = excluded.game_datetime_utc,
          start_time_utc = excluded.start_time_utc,
          game_date = excluded.game_date,
          game_date_date = excluded.game_date_date,
          home_team_id = excluded.home_team_id,
          away_team_id = excluded.away_team_id,
          home_team = excluded.home_team,
          away_team = excluded.away_team,

          -- critical: never overwrite existing scores with nulls
          home_score = coalesce(excluded.home_score, public.games.home_score),
          away_score = coalesce(excluded.away_score, public.games.away_score),

          venue = excluded.venue,
          status = excluded.status,
          status_state = excluded.status_state,
          status_detail = excluded.status_detail,

          -- critical: preserve/upgrade verification status
          verification_status =
            case
              when coalesce(excluded.home_score, public.games.home_score) is not null
               and coalesce(excluded.away_score, public.games.away_score) is not null
              then 'verified'
              else 'partial'
            end,

          updated_at = now();
        """

        # Params:
        # 1) date filter params used in games_base
        # 2) md5/season/source fields
        # 3) ht/at season match at end
        ordered: List[object] = list(date_params)
        ordered.extend([SEASON, SOURCE, SEASON, SOURCE])
        ordered.extend([SEASON, SEASON])

        upserted = _exec_rowcount(conn, sql, tuple(ordered), "upsert games (espn_games authoritative)")

    except Exception as e:
        _warn(f"Error upserting games: {e}")
        traceback.print_exc()
        return Counts(pulled=pulled, rejected=pulled)

    rejected = 0

    # DQ: missing away row (within window) based on logs table
    try:
        if SEASON_END_DATE:
            logs_date_filter_sql = "and r.event_id in (select game_id::text from {schema}.{games} g where g.game_datetime_utc >= %s::timestamptz and g.game_datetime_utc < (%s::date + interval '1 day'))"
            logs_params: Tuple[object, ...] = (SEASON_START_DATE, SEASON_END_DATE)
        else:
            logs_date_filter_sql = "and r.event_id in (select game_id::text from {schema}.{games} g where g.game_datetime_utc >= %s::timestamptz)"
            logs_params = (SEASON_START_DATE,)

        dq_sql = f"""
        with base as (
          select event_id, lower(home_away) as ha, {logs_pulled_at_expr} as pulled_at
          from {RAW_SCHEMA}.{RAW_LOGS_TABLE} r
          where event_id is not null
            and team_id is not null
            and home_away is not null
            and btrim(home_away) <> ''
            {logs_date_filter_sql.format(schema=RAW_SCHEMA, games=RAW_GAMES_TABLE)}
        ),
        dedup as (
          select *
          from (
            select b.*, row_number() over (partition by b.event_id, b.ha order by b.pulled_at desc nulls last) as rn
            from base b
          ) x where rn = 1
        ),
        home_only as (
          select h.event_id
          from (select event_id from dedup where ha='home') h
          left join (select event_id from dedup where ha='away') a using(event_id)
          where a.event_id is null
        )
        select event_id from home_only limit 50;
        """
        with conn.cursor() as cur:
            cur.execute(dq_sql, tuple(logs_params))
            for (event_id,) in cur.fetchall():
                rejected += _insert_dq(conn, "games", ["missing_away_row"], {"event_id": event_id})
    except Exception as e:
        _warn(f"Error during DQ audit for missing away rows: {e}")

    # DQ: completed/final but missing scores
    try:
        dq_scores_sql = """
        select external_game_id
        from public.games
        where season = %s and lower(source) = lower(%s)
          and game_datetime_utc >= %s::timestamptz
          and (%s = '' or game_datetime_utc < (%s::date + interval '1 day'))
          and status = 'final'
          and (home_score is null or away_score is null)
        limit 50;
        """
        end_for_sql = SEASON_END_DATE or ""
        with conn.cursor() as cur:
            cur.execute(dq_scores_sql, (SEASON, SOURCE, SEASON_START_DATE, end_for_sql, end_for_sql))
            for (event_id,) in cur.fetchall():
                rejected += _insert_dq(conn, "games", ["final_missing_scores"], {"event_id": event_id})
    except Exception as e:
        _warn(f"Error during DQ audit for final_missing_scores: {e}")

    return Counts(pulled=pulled, upserted=upserted, rejected=rejected)


def upsert_team_boxscores(conn: psycopg.Connection, teams_pk: str) -> Counts:
    """
    Upsert team boxscores from raw logs table.

    IMPORTANT:
    - public.team_boxscores.efg and public.team_boxscores.tov_pct are GENERATED ALWAYS in your schema.
      We do not insert/update them.
    - public.team_boxscores.team_id has an FK to public.teams(team_id).
      So we insert t.team_id (teams_pk), not the raw/source team_id.
    """
    if not _validate_raw_table(conn, RAW_SCHEMA, RAW_LOGS_TABLE, ["event_id", "team_id"]):
        return Counts(rejected=1)

    try:
        pulled = _count_rows(
            conn,
            f"""
            select count(*)
            from {RAW_SCHEMA}.{RAW_LOGS_TABLE} r
            where r.event_id is not null and r.team_id is not null
            """,
        )
    except Exception as e:
        _warn(f"Error counting team boxscores: {e}")
        return Counts(rejected=1)

    pulled_at_col = _pick_existing_column(conn, RAW_SCHEMA, RAW_LOGS_TABLE, ["pulled_at_utc", "pulled_at"])
    if pulled_at_col:
        pulled_at_expr = f"COALESCE(r.{pulled_at_col}, now())"
    else:
        pulled_at_expr = "now()"

    def raw_col(name: str) -> str:
        return f"r.{name}" if _has_column(conn, RAW_SCHEMA, RAW_LOGS_TABLE, name) else "null"

    # Robust numeric->int parsing (handles '79.0' etc.)
    def norm_int(expr: str, max_val: int = MAX_REASONABLE_SCORE) -> str:
        return f"""
        case
          when {expr} is null then null
          when btrim(({expr})::text) = '' then null
          when btrim(({expr})::text) ~ '^\\d+(\\.\\d+)?$' then
            case
              when (({expr})::numeric)::int between 0 and {max_val} then (({expr})::numeric)::int
              else null
            end
          else null
        end
        """

    try:
        sql = f"""
        with base as (
          select
            cast(r.event_id as text) as event_id,
            cast(r.team_id as text) as source_team_id,
            coalesce(r.team, '') as team_name,
            lower(r.home_away) as home_away_norm,
            {pulled_at_expr} as pulled_at,

            {norm_int(raw_col("points_for"), MAX_REASONABLE_SCORE)} as pts,
            {norm_int(raw_col("fgm"), 500)} as fgm,
            {norm_int(raw_col("fga"), 500)} as fga,
            {norm_int(raw_col("tpm"), 300)} as tpm,
            {norm_int(raw_col("tpa"), 300)} as tpa,
            {norm_int(raw_col("ftm"), 300)} as ftm,
            {norm_int(raw_col("fta"), 300)} as fta,
            {norm_int(raw_col("oreb"), 300)} as oreb,
            {norm_int(raw_col("dreb"), 300)} as dreb,
            {norm_int(raw_col("ast"), 300)} as ast,
            {norm_int(raw_col("tov"), 300)} as tov,
            {norm_int(raw_col("stl"), 300)} as stl,
            {norm_int(raw_col("blk"), 300)} as blk
          from {RAW_SCHEMA}.{RAW_LOGS_TABLE} r
          where r.event_id is not null
            and r.team_id is not null
            and r.home_away is not null
            and btrim(r.home_away) <> ''
        ),
        dedup as (
          select *
          from (
            select
              b.*,
              row_number() over (
                partition by b.event_id, b.source_team_id
                order by (b.pts is not null) desc, b.pulled_at desc nulls last
              ) as rn
            from base b
          ) x
          where rn = 1
        ),
        games as (
          select
            game_id,
            cast(external_game_id as text) as event_id
          from public.games
          where season = %s
            and lower(source) = lower(%s)
            and game_datetime_utc >= %s::timestamptz
            and (%s = '' or game_datetime_utc < (%s::date + interval '1 day'))
        )
        insert into public.team_boxscores (
          game_id,
          team,
          team_id,
          is_home,
          pts,
          fgm, fga,
          tpm, tpa,
          ftm, fta,
          oreb, dreb,
          ast, tov, stl, blk,
          pulled_at,
          created_at
        )
        select
          g.game_id,
          d.team_name as team,
          t.{teams_pk} as team_id,
          (d.home_away_norm = 'home') as is_home,
          d.pts,
          d.fgm, d.fga,
          d.tpm, d.tpa,
          d.ftm, d.fta,
          d.oreb, d.dreb,
          d.ast, d.tov, d.stl, d.blk,
          d.pulled_at,
          now()
        from dedup d
        join games g
          on g.event_id = d.event_id
        join public.teams t
          on t.season = %s and cast(t.source_team_id as text) = d.source_team_id
        on conflict (game_id, team_id)
        do update set
          team = excluded.team,
          is_home = excluded.is_home,
          pts = coalesce(excluded.pts, public.team_boxscores.pts),
          fgm = coalesce(excluded.fgm, public.team_boxscores.fgm),
          fga = coalesce(excluded.fga, public.team_boxscores.fga),
          tpm = coalesce(excluded.tpm, public.team_boxscores.tpm),
          tpa = coalesce(excluded.tpa, public.team_boxscores.tpa),
          ftm = coalesce(excluded.ftm, public.team_boxscores.ftm),
          fta = coalesce(excluded.fta, public.team_boxscores.fta),
          oreb = coalesce(excluded.oreb, public.team_boxscores.oreb),
          dreb = coalesce(excluded.dreb, public.team_boxscores.dreb),
          ast = coalesce(excluded.ast, public.team_boxscores.ast),
          tov = coalesce(excluded.tov, public.team_boxscores.tov),
          stl = coalesce(excluded.stl, public.team_boxscores.stl),
          blk = coalesce(excluded.blk, public.team_boxscores.blk),
          pulled_at = excluded.pulled_at;
        """
        end_for_sql = SEASON_END_DATE or ""
        upserted = _exec_rowcount(
            conn,
            sql,
            (SEASON, SOURCE, SEASON_START_DATE, end_for_sql, end_for_sql, SEASON),
            "upsert team_boxscores",
        )
        return Counts(pulled=pulled, upserted=upserted, rejected=0)
    except Exception as e:
        _warn(f"Error upserting team boxscores: {e}")
        traceback.print_exc()
        return Counts(pulled=pulled, rejected=pulled)


def upsert_team_game_features(conn: psycopg.Connection, teams_pk: str) -> Counts:
    """Upsert team game features from raw features table."""
    if not _validate_raw_table(conn, RAW_SCHEMA, RAW_FEATURES_TABLE, ["event_id", "team_id"]):
        return Counts(rejected=1)

    try:
        pulled = _count_rows(
            conn,
            f"select count(*) from {RAW_SCHEMA}.{RAW_FEATURES_TABLE} where event_id is not null and team_id is not null",
        )
    except Exception as e:
        _warn(f"Error counting team game features: {e}")
        return Counts(rejected=1)

    if not _has_column(conn, RAW_SCHEMA, RAW_FEATURES_TABLE, "features"):
        rejected = _insert_dq(
            conn, "team_game_features", ["missing_features_column"], {"table": f"{RAW_SCHEMA}.{RAW_FEATURES_TABLE}"}
        )
        return Counts(pulled=pulled, rejected=rejected)

    pulled_at_col = _pick_existing_column(conn, RAW_SCHEMA, RAW_FEATURES_TABLE, ["pulled_at_utc", "pulled_at"])
    if pulled_at_col:
        pulled_at_expr = f"COALESCE(r.{pulled_at_col}, now())"
    else:
        pulled_at_expr = "now()"

    try:
        sql = f"""
        with base as (
          select
            r.event_id,
            cast(r.team_id as text) as source_team_id,
            lower(coalesce(r.home_away,'home')) as home_away_norm,
            r.features,
            {pulled_at_expr} as pulled_at
          from {RAW_SCHEMA}.{RAW_FEATURES_TABLE} r
          where r.event_id is not null and r.team_id is not null
        ),
        dedup as (
          select *
          from (
            select
              b.*,
              row_number() over (
                partition by b.event_id, b.source_team_id
                order by b.pulled_at desc nulls last
              ) as rn
            from base b
          ) x
          where rn = 1
        ),
        games as (
          select game_id, external_game_id, game_datetime_utc
          from public.games
          where season = %s
            and lower(source) = lower(%s)
            and game_datetime_utc >= %s::timestamptz
            and (%s = '' or game_datetime_utc < (%s::date + interval '1 day'))
        )
        insert into public.team_game_features (
          game_id,
          team_id,
          home_away,
          feature_set,
          features,
          pulled_at,
          verification_status
        )
        select
          g.game_id,
          t.{teams_pk},
          d.home_away_norm,
          %s as feature_set,
          d.features,
          d.pulled_at,
          'partial' as verification_status
        from dedup d
        join games g on g.external_game_id = d.event_id
        join public.teams t on t.season = %s and cast(t.source_team_id as text) = d.source_team_id
        on conflict (game_id, team_id, feature_set)
        do update set
          home_away = excluded.home_away,
          features = excluded.features,
          pulled_at = excluded.pulled_at,
          verification_status = excluded.verification_status;
        """
        end_for_sql = SEASON_END_DATE or ""
        upserted = _exec_rowcount(
            conn,
            sql,
            (SEASON, SOURCE, SEASON_START_DATE, end_for_sql, end_for_sql, FEATURE_SET, SEASON),
            "upsert team_game_features",
        )
        return Counts(pulled=pulled, upserted=upserted, rejected=0)
    except Exception as e:
        _warn(f"Error upserting team game features: {e}")
        traceback.print_exc()
        return Counts(pulled=pulled, rejected=pulled)


def main() -> None:
    """Main normalization workflow."""
    _validate_env_vars()

    _info(
        f"Starting normalization: SEASON={SEASON}, SOURCE={SOURCE}, FEATURE_SET={FEATURE_SET}, "
        f"WINDOW=[{SEASON_START_DATE}{'..'+SEASON_END_DATE if SEASON_END_DATE else '..now'}]"
    )

    try:
        with psycopg.connect(SUPABASE_DB_URL) as conn:
            if not _table_exists(conn, "public", "teams"):
                _die("public.teams table does not exist. Run migrations first.")

            teams_pk = _teams_pk_column(conn)
            _info(f"Using teams PK column: {teams_pk}")

            if TEAMS_SEED_JSON:
                print(f"[STEP] Seed teams from JSON: {TEAMS_SEED_JSON}")
                c = seed_teams_from_json(conn, TEAMS_SEED_JSON)
                print(f"[OK] seed teams: pulled={c.pulled} upserted={c.upserted} rejected={c.rejected}")
                conn.commit()

            print("[STEP] Upsert teams")
            c = upsert_teams(conn)
            print(f"[OK] teams: pulled={c.pulled} upserted={c.upserted} rejected={c.rejected}")
            conn.commit()

            print("[STEP] Upsert games")
            c = upsert_games(conn, teams_pk)
            print(f"[OK] games: pulled={c.pulled} upserted={c.upserted} rejected={c.rejected}")
            conn.commit()

            print("[STEP] Upsert team_boxscores")
            c = upsert_team_boxscores(conn, teams_pk)
            print(f"[OK] team_boxscores: pulled={c.pulled} upserted={c.upserted} rejected={c.rejected}")
            conn.commit()

            print("[STEP] Upsert team_game_features")
            c = upsert_team_game_features(conn, teams_pk)
            print(f"[OK] team_game_features: pulled={c.pulled} upserted={c.upserted} rejected={c.rejected}")
            conn.commit()

            # Quick sanity summary (non-fatal)
            try:
                end_for_sql = SEASON_END_DATE or ""
                sanity_sql = """
                select
                  count(*) as games_total,
                  count(*) filter (where status='final') as games_final,
                  count(*) filter (where home_score is not null and away_score is not null) as games_with_scores,
                  count(*) filter (where verification_status='verified') as games_verified
                from public.games
                where season=%s and lower(source)=lower(%s)
                  and game_datetime_utc >= %s::timestamptz
                  and (%s = '' or game_datetime_utc < (%s::date + interval '1 day'));
                """
                with conn.cursor() as cur:
                    cur.execute(sanity_sql, (SEASON, SOURCE, SEASON_START_DATE, end_for_sql, end_for_sql))
                    row = cur.fetchone()
                if row:
                    _info(
                        f"Sanity: games_total={row[0]} games_final={row[1]} "
                        f"games_with_scores={row[2]} games_verified={row[3]}"
                    )
            except Exception as e:
                _warn(f"Sanity query failed (non-fatal): {e}")

            _info("Normalization completed successfully")

    except psycopg.OperationalError as e:
        _die(f"Database connection error: {e}")
    except Exception as e:
        _die(f"Unexpected error during normalization: {e}\n{traceback.format_exc()}")


if __name__ == "__main__":
    main()
