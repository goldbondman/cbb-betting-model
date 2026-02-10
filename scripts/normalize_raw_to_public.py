#!/usr/bin/env python3
"""
Normalize raw ESPN ingestion tables into public model tables.

Targets:
- public.teams
- public.games
- public.team_boxscores
- public.team_game_features
- public.dq_audit (for data quality issues)

Requires:
- SUPABASE_DB_URL

Behavior:
- Idempotent upserts with deterministic conflict keys.
- Dedupe raw sources BEFORE insert/upsert to avoid Postgres cardinality violations
  (multiple proposed rows hitting the same ON CONFLICT target in a single INSERT).
- Optional: seed public.teams from a local JSON file (ex: teams_2026.json)

Future-proofing:
- Normalize types coming from raw logs (completed, scores) defensively.
- Support multiple possible datetime column names in raw logs.
- Normalize source casing consistently to avoid join/filter mismatches.
- Detect public.games key columns (game_id/id). Populate required UUID keys explicitly.
- Add DQ checks for common ingestion cracks (missing away row, missing venue, completed but missing scores).
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

import psycopg

SUPABASE_DB_URL = (os.getenv("SUPABASE_DB_URL") or "").strip()
SEASON = int(os.getenv("SEASON", "2025"))
SOURCE = (os.getenv("SOURCE", "espn").strip().lower() or "espn")
FEATURE_SET = (os.getenv("FEATURE_SET", "espn_v1").strip() or "espn_v1")

RAW_SCHEMA = (os.getenv("RAW_SCHEMA", "raw").strip() or "raw")
RAW_LOGS_TABLE = (os.getenv("RAW_LOGS_TABLE", "espn_team_game_logs").strip() or "espn_team_game_logs")
RAW_FEATURES_TABLE = (os.getenv("RAW_FEATURES_TABLE", "espn_team_game_features").strip() or "espn_team_game_features")

TEAMS_SEED_JSON = (os.getenv("TEAMS_SEED_JSON") or "").strip()

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
)


@dataclass(frozen=True)
class Counts:
    pulled: int = 0
    upserted: int = 0
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


def _column_meta(conn: psycopg.Connection, schema: str, table: str, column: str) -> Optional[Tuple[str, str, Optional[str]]]:
    """
    Returns (udt_name, is_nullable, column_default) from information_schema, or None.
    """
    q = """
    select udt_name, is_nullable, column_default
    from information_schema.columns
    where table_schema = %s and table_name = %s and column_name = %s
    limit 1
    """
    with conn.cursor() as cur:
        cur.execute(q, (schema, table, column))
        row = cur.fetchone()
    if not row:
        return None
    udt_name = str(row[0]) if row[0] is not None else ""
    is_nullable = str(row[1]) if row[1] is not None else "YES"
    col_default = row[2]  # can be None
    return (udt_name, is_nullable, str(col_default) if col_default is not None else None)


def _pick_existing_column(conn: psycopg.Connection, schema: str, table: str, candidates: Sequence[str]) -> Optional[str]:
    for col in candidates:
        if _has_column(conn, schema, table, col):
            return col
    return None


def _exec_rowcount(conn: psycopg.Connection, sql: str, params: Optional[Iterable[object]] = None) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.rowcount


def _count_rows(conn: psycopg.Connection, sql: str, params: Optional[Iterable[object]] = None) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return int(cur.fetchone()[0])


def json_dump(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _dq_id(entity_type: str, reason_codes: List[str], details: dict) -> str:
    payload = f"{entity_type}|{','.join(reason_codes)}|{json_dump(details)}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, payload))


def _insert_dq(conn: psycopg.Connection, entity_type: str, reason_codes: List[str], details: dict) -> int:
    dqid = _dq_id(entity_type, reason_codes, details)
    sql = """
    insert into public.dq_audit (id, entity_type, entity_id, severity, reason_codes, details, created_at)
    values (%s, %s, null, %s, %s, %s::jsonb, now())
    on conflict (id) do nothing
    """
    severity = "error"
    return _exec_rowcount(conn, sql, (dqid, entity_type, severity, reason_codes, json_dump(details)))


def _teams_pk_column(conn: psycopg.Connection) -> str:
    if _has_column(conn, "public", "teams", "team_id"):
        return "team_id"
    return "id"


def _teams_uuid_col(conn: psycopg.Connection) -> Optional[str]:
    if _has_column(conn, "public", "teams", "team_id"):
        return "team_id"
    if _has_column(conn, "public", "teams", "id"):
        return "id"
    return None


def _games_join_key(conn: psycopg.Connection) -> str:
    """
    Which column to use when referencing public.games rows.
    Prefer game_id if it exists (very common in your schema), else id.
    """
    if _has_column(conn, "public", "games", "game_id"):
        return "game_id"
    if _has_column(conn, "public", "games", "id"):
        return "id"
    return "game_id"  # fallback name; will fail later with clear error


def _games_uuid_keys_to_populate(conn: psycopg.Connection) -> List[str]:
    """
    Return a list of games key columns to explicitly populate with gen_random_uuid(),
    if they are UUID-typed and either:
      - NOT NULL with no default, OR
      - present (even if nullable) but you want deterministic creation for inserts
        (we keep it conservative to avoid messing with sequences).

    Most important: if game_id is NOT NULL and has no default, this MUST be populated.
    """
    keys: List[str] = []
    for col in ("game_id", "id"):
        if not _has_column(conn, "public", "games", col):
            continue
        meta = _column_meta(conn, "public", "games", col)
        if not meta:
            continue
        udt, is_nullable, col_default = meta
        if udt != "uuid":
            continue

        must_populate = (is_nullable == "NO" and (col_default is None or col_default.strip() == ""))
        if must_populate:
            keys.append(col)

    # If neither was "must", we still prefer populating game_id if it exists and is uuid,
    # because your downstream tables use game_id. This avoids silent NULLs when DB expects it.
    if "game_id" not in keys and _has_column(conn, "public", "games", "game_id"):
        meta = _column_meta(conn, "public", "games", "game_id")
        if meta and meta[0] == "uuid":
            # Only add if there is no default (if default exists, DB can handle it)
            if meta[2] is None or meta[2].strip() == "":
                keys.append("game_id")

    return keys


def seed_teams_from_json(conn: psycopg.Connection, path: str) -> Counts:
    if not path:
        return Counts()

    if not os.path.exists(path):
        _insert_dq(conn, "teams", ["seed_file_missing"], {"seed_path": path})
        return Counts(rejected=1)

    teams_uuid_col = _teams_uuid_col(conn)
    if not teams_uuid_col:
        _insert_dq(conn, "teams", ["public_teams_missing_pk"], {"note": "Expected public.teams to have team_id or id PK."})
        return Counts(rejected=1)

    has_conference = _has_column(conn, "public", "teams", "conference")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        _insert_dq(conn, "teams", ["seed_file_invalid_json"], {"seed_path": path, "note": "Expected JSON array."})
        return Counts(rejected=1)

    seen = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        source_id = row.get("sourceId")
        if source_id is None or str(source_id).strip() == "":
            continue
        team_name = row.get("shortDisplayName") or row.get("school") or row.get("displayName")
        if not team_name:
            continue
        conf = row.get("conference")
        seen[str(source_id)] = (str(source_id), str(team_name), (str(conf) if conf is not None else None))

    pulled = len(seen)
    if pulled == 0:
        return Counts()

    if has_conference:
        sql = f"""
        with src as (
          select * from unnest(%s::text[], %s::text[], %s::text[])
            as s(source_team_id, team_name, conference)
        )
        insert into public.teams ({teams_uuid_col}, season, source_team_id, team_name, conference, created_at, updated_at)
        select
          gen_random_uuid() as {teams_uuid_col},
          %s as season,
          s.source_team_id,
          s.team_name,
          nullif(s.conference, '') as conference,
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
        upserted = _exec_rowcount(conn, sql, (source_ids, names, confs, SEASON))
    else:
        sql = f"""
        with src as (
          select * from unnest(%s::text[], %s::text[])
            as s(source_team_id, team_name)
        )
        insert into public.teams ({teams_uuid_col}, season, source_team_id, team_name, created_at, updated_at)
        select
          gen_random_uuid() as {teams_uuid_col},
          %s as season,
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
        upserted = _exec_rowcount(conn, sql, (source_ids, names, SEASON))

    return Counts(pulled=pulled, upserted=upserted, rejected=0)


def upsert_teams(conn: psycopg.Connection, pulled_at_col: Optional[str]) -> Counts:
    pulled = _count_rows(
        conn,
        f"""
        select count(distinct team_id)
        from {RAW_SCHEMA}.{RAW_LOGS_TABLE}
        where team_id is not null and team is not null
        """,
    )

    teams_uuid_col = _teams_uuid_col(conn)

    if not teams_uuid_col:
        sql = f"""
        with src as (
          select
            cast(team_id as text) as source_team_id,
            max(team) as team_name
          from {RAW_SCHEMA}.{RAW_LOGS_TABLE}
          where team_id is not null and team is not null
          group by cast(team_id as text)
        )
        insert into public.teams (season, source_team_id, team_name, conference, created_at, updated_at)
        select
          %s as season,
          s.source_team_id,
          s.team_name,
          null as conference,
          now(),
          now()
        from src s
        on conflict (season, source_team_id)
        do update set
          team_name = excluded.team_name,
          updated_at = now();
        """
        upserted = _exec_rowcount(conn, sql, (SEASON,))
        return Counts(pulled=pulled, upserted=upserted)

    sql = f"""
    with src as (
      select
        cast(team_id as text) as source_team_id,
        max(team) as team_name
      from {RAW_SCHEMA}.{RAW_LOGS_TABLE}
      where team_id is not null and team is not null
      group by cast(team_id as text)
    )
    insert into public.teams ({teams_uuid_col}, season, source_team_id, team_name, conference, created_at, updated_at)
    select
      gen_random_uuid() as {teams_uuid_col},
      %s as season,
      s.source_team_id,
      s.team_name,
      null as conference,
      now(),
      now()
    from src s
    on conflict (season, source_team_id)
    do update set
      team_name = excluded.team_name,
      updated_at = now();
    """
    upserted = _exec_rowcount(conn, sql, (SEASON,))
    return Counts(pulled=pulled, upserted=upserted)


def upsert_games(conn: psycopg.Connection, teams_pk: str, pulled_at_col: Optional[str], join_key: str, uuid_keys_to_populate: List[str]) -> Counts:
    pulled = _count_rows(
        conn,
        f"""
        select count(distinct event_id)
        from {RAW_SCHEMA}.{RAW_LOGS_TABLE}
        where event_id is not null
        """,
    )

    pulled_at_expr = f"r.{pulled_at_col}" if pulled_at_col else "now()"

    game_dt_col = _pick_existing_column(
        conn,
        RAW_SCHEMA,
        RAW_LOGS_TABLE,
        ["game_datetime_utc", "game_date_utc", "start_time_utc", "game_time_utc"],
    )
    if not game_dt_col:
        _insert_dq(
            conn,
            "games",
            ["missing_datetime_column"],
            {"table": f"{RAW_SCHEMA}.{RAW_LOGS_TABLE}", "candidates": ["game_datetime_utc", "game_date_utc", "start_time_utc", "game_time_utc"]},
        )
        return Counts(pulled=pulled, rejected=1)

    completed_true_list_sql = ", ".join([f"'{t}'" for t in COMPLETED_TRUE_TOKENS])

    # Build dynamic PK insert/select lists (so we can populate game_id even if id also exists)
    pk_insert_cols = ""
    pk_select_exprs = ""
    if uuid_keys_to_populate:
        pk_insert_cols = ", ".join(uuid_keys_to_populate) + ","
        pk_select_exprs = ", ".join([f"gen_random_uuid() as {c}" for c in uuid_keys_to_populate]) + ","

    sql = f"""
    with base as (
      select
        event_id,
        cast(team_id as text) as source_team_id,
        lower(home_away) as home_away,
        r.{game_dt_col} as game_datetime_utc,
        venue,
        case
          when lower(coalesce(completed::text, '')) in ({completed_true_list_sql}) then true
          else false
        end as completed,
        case when points_for::text ~ '^\\d+$' then points_for::text::int else null end as points_for,
        case when points_against::text ~ '^\\d+$' then points_against::text::int else null end as points_against,
        {pulled_at_expr} as pulled_at
      from {RAW_SCHEMA}.{RAW_LOGS_TABLE} r
      where event_id is not null
        and team_id is not null
        and home_away is not null
        and btrim(home_away) <> ''
    ),
    dedup as (
      select *
      from (
        select
          b.*,
          row_number() over (
            partition by b.event_id, b.home_away
            order by b.pulled_at desc nulls last
          ) as rn
        from base b
      ) x
      where x.rn = 1
    ),
    home as (
      select
        event_id,
        source_team_id as home_source_team_id,
        game_datetime_utc,
        venue,
        completed,
        points_for as home_score,
        points_against as away_score
      from dedup
      where home_away = 'home'
    ),
    away as (
      select
        event_id,
        source_team_id as away_source_team_id
      from dedup
      where home_away = 'away'
    ),
    joined as (
      select
        h.event_id,
        h.game_datetime_utc,
        h.venue,
        h.completed,
        h.home_score,
        h.away_score,
        h.home_source_team_id,
        a.away_source_team_id
      from home h
      left join away a on a.event_id = h.event_id
    )
    insert into public.games (
      {pk_insert_cols}
      season,
      game_datetime_utc,
      home_team_id,
      away_team_id,
      home_score,
      away_score,
      status,
      venue,
      source,
      external_game_id,
      verification_status,
      created_at,
      updated_at
    )
    select
      {pk_select_exprs}
      %s as season,
      j.game_datetime_utc,
      ht.{teams_pk} as home_team_id,
      at.{teams_pk} as away_team_id,
      case when j.completed then j.home_score else null end as home_score,
      case when j.completed then j.away_score else null end as away_score,
      case when j.completed then 'final' else 'scheduled' end as status,
      j.venue,
      lower(%s) as source,
      j.event_id as external_game_id,
      case
        when j.completed and j.home_score is not null and j.away_score is not null then 'verified'
        else 'partial'
      end as verification_status,
      now(),
      now()
    from joined j
    join public.teams ht
      on ht.season = %s and cast(ht.source_team_id as text) = j.home_source_team_id
    left join public.teams at
      on at.season = %s and cast(at.source_team_id as text) = j.away_source_team_id
    where j.game_datetime_utc is not null
      and j.away_source_team_id is not null
      and at.{teams_pk} is not null
    on conflict (season, source, external_game_id)
    do update set
      game_datetime_utc = excluded.game_datetime_utc,
      home_team_id = excluded.home_team_id,
      away_team_id = excluded.away_team_id,
      home_score = excluded.home_score,
      away_score = excluded.away_score,
      status = excluded.status,
      venue = excluded.venue,
      verification_status = excluded.verification_status,
      updated_at = now();
    """
    upserted = _exec_rowcount(conn, sql, (SEASON, SOURCE, SEASON, SEASON))

    rejected = 0

    # DQ: completed but missing scores
    dq_completed_missing_scores_sql = f"""
    with base as (
      select
        event_id,
        lower(home_away) as home_away,
        case
          when lower(coalesce(completed::text, '')) in ({completed_true_list_sql}) then true
          else false
        end as completed,
        case when points_for::text ~ '^\\d+$' then points_for::text::int else null end as points_for,
        case when points_against::text ~ '^\\d+$' then points_against::text::int else null end as points_against,
        {pulled_at_expr} as pulled_at
      from {RAW_SCHEMA}.{RAW_LOGS_TABLE} r
      where event_id is not null and team_id is not null and home_away is not null and btrim(home_away) <> ''
    ),
    dedup as (
      select *
      from (
        select
          b.*,
          row_number() over (
            partition by b.event_id, b.home_away
            order by b.pulled_at desc nulls last
          ) as rn
        from base b
      ) x
      where x.rn = 1
    )
    select event_id, home_away, points_for, points_against
    from dedup
    where completed is true
      and (points_for is null or points_against is null)
    limit 50;
    """
    with conn.cursor() as cur:
        cur.execute(dq_completed_missing_scores_sql)
        rows = cur.fetchall()
    for (event_id, home_away, pf, pa) in rows:
        rejected += _insert_dq(conn, "games", ["completed_missing_scores"], {"event_id": event_id, "home_away": home_away, "points_for": pf, "points_against": pa})

    # DQ: missing away row
    dq_missing_away_row_sql = f"""
    with base as (
      select event_id, lower(home_away) as home_away, {pulled_at_expr} as pulled_at
      from {RAW_SCHEMA}.{RAW_LOGS_TABLE} r
      where event_id is not null and team_id is not null and home_away is not null and btrim(home_away) <> ''
    ),
    dedup as (
      select *
      from (
        select b.*, row_number() over (partition by b.event_id, b.home_away order by b.pulled_at desc nulls last) as rn
        from base b
      ) x
      where x.rn = 1
    ),
    home as (select event_id from dedup where home_away = 'home'),
    away as (select event_id from dedup where home_away = 'away')
    select h.event_id
    from home h
    left join away a on a.event_id = h.event_id
    where a.event_id is null
    limit 50;
    """
    with conn.cursor() as cur:
        cur.execute(dq_missing_away_row_sql)
        rows = cur.fetchall()
    for (event_id,) in rows:
        rejected += _insert_dq(conn, "games", ["missing_away_row"], {"event_id": event_id})

    # DQ: missing venue
    dq_missing_venue_sql = f"""
    with base as (
      select event_id, lower(home_away) as home_away, venue, {pulled_at_expr} as pulled_at
      from {RAW_SCHEMA}.{RAW_LOGS_TABLE} r
      where event_id is not null and team_id is not null and home_away is not null and btrim(home_away) <> ''
    ),
    dedup as (
      select *
      from (
        select b.*, row_number() over (partition by b.event_id, b.home_away order by b.pulled_at desc nulls last) as rn
        from base b
      ) x
      where x.rn = 1
    )
    select event_id, home_away, venue
    from dedup
    where venue is null or btrim(venue) = ''
    limit 50;
    """
    with conn.cursor() as cur:
        cur.execute(dq_missing_venue_sql)
        rows = cur.fetchall()
    for (event_id, home_away, venue) in rows:
        rejected += _insert_dq(conn, "games", ["missing_venue"], {"event_id": event_id, "home_away": home_away, "venue": venue})

    return Counts(pulled=pulled, upserted=upserted, rejected=rejected)


def upsert_team_boxscores(conn: psycopg.Connection, pulled_at_col: Optional[str], has_data_ok: bool, teams_pk: str, games_join_key: str) -> Counts:
    pulled = _count_rows(
        conn,
        f"""
        select count(*)
        from {RAW_SCHEMA}.{RAW_LOGS_TABLE}
        where event_id is not null and team_id is not null
        """,
    )

    pulled_at_expr = f"r.{pulled_at_col}" if pulled_at_col else "now()"
    verification_expr = "'partial'"
    if has_data_ok:
        verification_expr = "case when r.data_ok then 'verified' else 'partial' end"

    sql = f"""
    with base as (
      select
        r.*,
        {pulled_at_expr} as pulled_at,
        cast(r.team_id as text) as source_team_id,
        lower(r.home_away) as home_away_norm
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
            order by b.pulled_at desc nulls last
          ) as rn
        from base b
      ) x
      where x.rn = 1
    )
    insert into public.team_boxscores (
      game_id,
      team_id,
      home_away,
      source,
      pulled_at,
      stats,
      verification_status,
      verification_notes
    )
    select
      g.{games_join_key} as game_id,
      t.{teams_pk} as team_id,
      d.home_away_norm as home_away,
      lower(%s) as source,
      d.pulled_at as pulled_at,
      to_jsonb(d) as stats,
      {verification_expr} as verification_status,
      null as verification_notes
    from dedup d
    join public.games g
      on g.season = %s and lower(g.source) = lower(%s) and g.external_game_id = d.event_id
    join public.teams t
      on t.season = %s and cast(t.source_team_id as text) = d.source_team_id
    on conflict (game_id, team_id)
    do update set
      home_away = excluded.home_away,
      pulled_at = excluded.pulled_at,
      stats = excluded.stats,
      verification_status = excluded.verification_status,
      verification_notes = excluded.verification_notes;
    """
    upserted = _exec_rowcount(conn, sql, (SOURCE, SEASON, SOURCE, SEASON))
    return Counts(pulled=pulled, upserted=upserted, rejected=0)


def upsert_team_game_features(conn: psycopg.Connection, pulled_at_col: Optional[str], has_features_col: bool, teams_pk: str, games_join_key: str) -> Counts:
    pulled = _count_rows(
        conn,
        f"""
        select count(*)
        from {RAW_SCHEMA}.{RAW_FEATURES_TABLE}
        where event_id is not null and team_id is not null
        """,
    )

    if not has_features_col:
        rejected = _insert_dq(conn, "team_game_features", ["missing_features_column"], {"table": f"{RAW_SCHEMA}.{RAW_FEATURES_TABLE}", "note": "Expected features jsonb column for normalization."})
        return Counts(pulled=pulled, upserted=0, rejected=rejected)

    pulled_at_expr = f"r.{pulled_at_col}" if pulled_at_col else "now()"

    sql = f"""
    with base as (
      select
        r.*,
        {pulled_at_expr} as pulled_at,
        cast(r.team_id as text) as source_team_id,
        lower(r.home_away) as home_away_norm
      from {RAW_SCHEMA}.{RAW_FEATURES_TABLE} r
      where r.event_id is not null
        and r.team_id is not null
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
      where x.rn = 1
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
      g.{games_join_key} as game_id,
      t.{teams_pk} as team_id,
      d.home_away_norm as home_away,
      %s as feature_set,
      d.features as features,
      d.pulled_at as pulled_at,
      'partial' as verification_status
    from dedup d
    join public.games g
      on g.season = %s and lower(g.source) = lower(%s) and g.external_game_id = d.event_id
    join public.teams t
      on t.season = %s and cast(t.source_team_id as text) = d.source_team_id
    on conflict (game_id, team_id, feature_set)
    do update set
      home_away = excluded.home_away,
      features = excluded.features,
      pulled_at = excluded.pulled_at,
      verification_status = excluded.verification_status;
    """
    upserted = _exec_rowcount(conn, sql, (FEATURE_SET, SEASON, SOURCE, SEASON))
    return Counts(pulled=pulled, upserted=upserted, rejected=0)


def main() -> None:
    if not SUPABASE_DB_URL:
        _die("SUPABASE_DB_URL is missing/empty.")

    with psycopg.connect(SUPABASE_DB_URL) as conn:
        pulled_at_logs = _pick_existing_column(conn, RAW_SCHEMA, RAW_LOGS_TABLE, ["pulled_at_utc", "pulled_at"])
        pulled_at_features = _pick_existing_column(conn, RAW_SCHEMA, RAW_FEATURES_TABLE, ["pulled_at_utc", "pulled_at"])
        has_data_ok = _has_column(conn, RAW_SCHEMA, RAW_LOGS_TABLE, "data_ok")
        has_features_col = _has_column(conn, RAW_SCHEMA, RAW_FEATURES_TABLE, "features")
        teams_pk = _teams_pk_column(conn)

        games_join_key = _games_join_key(conn)
        if not _has_column(conn, "public", "games", games_join_key):
            _die("public.games missing expected key column (game_id or id).")

        uuid_keys_to_populate = _games_uuid_keys_to_populate(conn)
        if not uuid_keys_to_populate and _has_column(conn, "public", "games", "game_id"):
            # If game_id exists and is NOT NULL but we didn't detect it, log a DQ and fail fast.
            meta = _column_meta(conn, "public", "games", "game_id")
            if meta and meta[1] == "NO":
                _die("public.games.game_id is NOT NULL but script could not determine how to populate it (check type/default).")

        if TEAMS_SEED_JSON:
            print(f"[STEP] Seed teams from JSON: {TEAMS_SEED_JSON}")
            counts = seed_teams_from_json(conn, TEAMS_SEED_JSON)
            print(f"[OK] seed teams: pulled={counts.pulled} upserted={counts.upserted} rejected={counts.rejected}")

        print("[STEP] Upsert teams (deduped from raw logs)")
        counts = upsert_teams(conn, pulled_at_logs)
        print(f"[OK] teams: pulled={counts.pulled} upserted={counts.upserted} rejected={counts.rejected}")

        print("[STEP] Upsert games (deduped home/away per event)")
        counts = upsert_games(conn, teams_pk, pulled_at_logs, games_join_key, uuid_keys_to_populate)
        print(f"[OK] games: pulled={counts.pulled} upserted={counts.upserted} rejected={counts.rejected}")

        print("[STEP] Upsert team_boxscores (deduped per event+team)")
        counts = upsert_team_boxscores(conn, pulled_at_logs, has_data_ok, teams_pk, games_join_key)
        print(f"[OK] team_boxscores: pulled={counts.pulled} upserted={counts.upserted} rejected={counts.rejected}")

        print("[STEP] Upsert team_game_features (deduped per event+team)")
        counts = upsert_team_game_features(conn, pulled_at_features, has_features_col, teams_pk, games_join_key)
        print(f"[OK] team_game_features: pulled={counts.pulled} upserted={counts.upserted} rejected={counts.rejected}")

        conn.commit()


if __name__ == "__main__":
    main()
