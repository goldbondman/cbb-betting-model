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
- Logs counts for pulled/upserted/rejected.
"""
from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

import psycopg

SUPABASE_DB_URL = (os.getenv("SUPABASE_DB_URL") or "").strip()
SEASON = int(os.getenv("SEASON", "2025"))
SOURCE = (os.getenv("SOURCE", "espn").strip().lower() or "espn")
FEATURE_SET = (os.getenv("FEATURE_SET", "espn_v1").strip() or "espn_v1")

RAW_SCHEMA = (os.getenv("RAW_SCHEMA", "raw").strip() or "raw")
RAW_LOGS_TABLE = (os.getenv("RAW_LOGS_TABLE", "espn_team_game_logs").strip() or "espn_team_game_logs")
RAW_FEATURES_TABLE = (os.getenv("RAW_FEATURES_TABLE", "espn_team_game_features").strip() or "espn_team_game_features")


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


def _insert_dq(conn: psycopg.Connection, entity_type: str, reason_codes: List[str], details: dict) -> int:
    sql = """
    insert into public.dq_audit (id, entity_type, entity_id, severity, reason_codes, details, created_at)
    values (%s, %s, null, %s, %s, %s::jsonb, now())
    """
    severity = "error"
    return _exec_rowcount(conn, sql, (str(uuid.uuid4()), entity_type, severity, reason_codes, json_dump(details)))


def json_dump(obj: dict) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)


def upsert_teams(conn: psycopg.Connection, pulled_at_col: Optional[str]) -> Counts:
    pulled = _count_rows(
        conn,
        f"""
        select count(distinct team_id)
        from {RAW_SCHEMA}.{RAW_LOGS_TABLE}
        where team_id is not null and team is not null
        """,
    )

    has_team_id = _has_column(conn, "public", "teams", "team_id")
    has_id = _has_column(conn, "public", "teams", "id")
    if has_team_id:
        sql = f"""
        insert into public.teams (team_id, season, source_team_id, team_name, conference, created_at, updated_at)
        select distinct
          gen_random_uuid() as team_id,
          %s as season,
          team_id as source_team_id,
          team as team_name,
          null as conference,
          now() as created_at,
          now() as updated_at
        from {RAW_SCHEMA}.{RAW_LOGS_TABLE}
        where team_id is not null and team is not null
        on conflict (season, source_team_id)
        do update set team_name = excluded.team_name,
                      updated_at = now();
        """
        upserted = _exec_rowcount(conn, sql, (SEASON,))
    elif has_id:
        sql = f"""
        insert into public.teams (id, season, source_team_id, team_name, conference, created_at, updated_at)
        select distinct
          gen_random_uuid() as id,
          %s as season,
          team_id as source_team_id,
          team as team_name,
          null as conference,
          now() as created_at,
          now() as updated_at
        from {RAW_SCHEMA}.{RAW_LOGS_TABLE}
        where team_id is not null and team is not null
        on conflict (season, source_team_id)
        do update set team_name = excluded.team_name,
                      updated_at = now();
        """
        upserted = _exec_rowcount(conn, sql, (SEASON,))
    else:
        sql = f"""
        insert into public.teams (season, source_team_id, team_name, conference, created_at, updated_at)
        select distinct
          %s as season,
          team_id as source_team_id,
          team as team_name,
          null as conference,
          now() as created_at,
          now() as updated_at
        from {RAW_SCHEMA}.{RAW_LOGS_TABLE}
        where team_id is not null and team is not null
        on conflict (season, source_team_id)
        do update set team_name = excluded.team_name,
                      updated_at = now();
        """
        upserted = _exec_rowcount(conn, sql, (SEASON,))
    return Counts(pulled=pulled, upserted=upserted, rejected=0)


def _teams_pk_column(conn: psycopg.Connection) -> str:
    if _has_column(conn, "public", "teams", "team_id"):
        return "team_id"
    return "id"


def upsert_games(conn: psycopg.Connection, teams_pk: str) -> Counts:
    pulled = _count_rows(
        conn,
        f"""
        select count(distinct event_id)
        from {RAW_SCHEMA}.{RAW_LOGS_TABLE}
        where event_id is not null
        """,
    )

    sql = f"""
    with home as (
      select
        event_id,
        team_id as home_source_team_id,
        game_datetime_utc,
        venue,
        completed,
        points_for as home_score,
        points_against as away_score
      from {RAW_SCHEMA}.{RAW_LOGS_TABLE}
      where lower(home_away) = 'home'
    ),
    away as (
      select
        event_id,
        team_id as away_source_team_id
      from {RAW_SCHEMA}.{RAW_LOGS_TABLE}
      where lower(home_away) = 'away'
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
      join away a on a.event_id = h.event_id
    )
    insert into public.games (
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
      %s as season,
      j.game_datetime_utc,
      ht.{teams_pk} as home_team_id,
      at.{teams_pk} as away_team_id,
      case when j.completed then j.home_score else null end as home_score,
      case when j.completed then j.away_score else null end as away_score,
      case when j.completed then 'final' else 'scheduled' end as status,
      j.venue,
      %s as source,
      j.event_id as external_game_id,
      case when j.completed then 'verified' else 'partial' end as verification_status,
      now(),
      now()
    from joined j
    join public.teams ht
      on ht.season = %s and ht.source_team_id = j.home_source_team_id
    join public.teams at
      on at.season = %s and at.source_team_id = j.away_source_team_id
    where j.game_datetime_utc is not null
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
    rejected += _exec_rowcount(
        conn,
        f"""
        insert into public.dq_audit (id, entity_type, entity_id, severity, reason_codes, details, created_at)
        select gen_random_uuid(), 'games', null, 'error', array['missing_game_datetime'],
               jsonb_build_object('event_id', event_id), now()
        from {RAW_SCHEMA}.{RAW_LOGS_TABLE}
        where event_id is not null and game_datetime_utc is null
        on conflict do nothing;
        """,
    )

    rejected += _exec_rowcount(
        conn,
        f"""
        with home as (
          select event_id, team_id as home_source_team_id
          from {RAW_SCHEMA}.{RAW_LOGS_TABLE}
          where lower(home_away) = 'home'
        ),
        away as (
          select event_id, team_id as away_source_team_id
          from {RAW_SCHEMA}.{RAW_LOGS_TABLE}
          where lower(home_away) = 'away'
        ),
        joined as (
          select h.event_id, h.home_source_team_id, a.away_source_team_id
          from home h
          join away a on a.event_id = h.event_id
        )
        insert into public.dq_audit (id, entity_type, entity_id, severity, reason_codes, details, created_at)
        select gen_random_uuid(), 'games', null, 'error', array['missing_team_mapping'],
               jsonb_build_object('event_id', j.event_id,
                                  'home_source_team_id', j.home_source_team_id,
                                  'away_source_team_id', j.away_source_team_id),
               now()
        from joined j
        left join public.teams ht
          on ht.season = {SEASON} and ht.source_team_id = j.home_source_team_id
        left join public.teams at
          on at.season = {SEASON} and at.source_team_id = j.away_source_team_id
        where ht.{teams_pk} is null or at.{teams_pk} is null
        on conflict do nothing;
        """,
    )

    return Counts(pulled=pulled, upserted=upserted, rejected=rejected)


def upsert_team_boxscores(
    conn: psycopg.Connection,
    pulled_at_col: Optional[str],
    has_data_ok: bool,
    teams_pk: str,
) -> Counts:
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

    rejected = 0
    rejected += _exec_rowcount(
        conn,
        f"""
        insert into public.dq_audit (id, entity_type, entity_id, severity, reason_codes, details, created_at)
        select gen_random_uuid(), 'team_boxscores', null, 'error', array['missing_home_away'],
               jsonb_build_object('event_id', event_id, 'team_id', team_id), now()
        from {RAW_SCHEMA}.{RAW_LOGS_TABLE}
        where event_id is not null
          and team_id is not null
          and (home_away is null or btrim(home_away) = '')
        on conflict do nothing;
        """,
    )

    sql = f"""
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
      g.id as game_id,
      t.{teams_pk} as team_id,
      lower(r.home_away) as home_away,
      %s as source,
      {pulled_at_expr} as pulled_at,
      to_jsonb(r) as stats,
      {verification_expr} as verification_status,
      null as verification_notes
    from {RAW_SCHEMA}.{RAW_LOGS_TABLE} r
    join public.games g
      on g.season = %s and g.source = %s and g.external_game_id = r.event_id
    join public.teams t
      on t.season = %s and t.source_team_id = r.team_id
    where r.home_away is not null
      and btrim(r.home_away) <> ''
    on conflict (game_id, team_id)
    do update set
      home_away = excluded.home_away,
      pulled_at = excluded.pulled_at,
      stats = excluded.stats,
      verification_status = excluded.verification_status,
      verification_notes = excluded.verification_notes;
    """
    upserted = _exec_rowcount(conn, sql, (SOURCE, SEASON, SOURCE, SEASON))
    return Counts(pulled=pulled, upserted=upserted, rejected=rejected)


def upsert_team_game_features(
    conn: psycopg.Connection,
    pulled_at_col: Optional[str],
    has_features_col: bool,
    teams_pk: str,
) -> Counts:
    pulled = _count_rows(
        conn,
        f"""
        select count(*)
        from {RAW_SCHEMA}.{RAW_FEATURES_TABLE}
        where event_id is not null and team_id is not null
        """,
    )

    if not has_features_col:
        rejected = _insert_dq(
            conn,
            "team_game_features",
            ["missing_features_column"],
            {
                "table": f"{RAW_SCHEMA}.{RAW_FEATURES_TABLE}",
                "note": "Expected features jsonb column for normalization.",
            },
        )
        return Counts(pulled=pulled, upserted=0, rejected=rejected)

    pulled_at_expr = f"r.{pulled_at_col}" if pulled_at_col else "now()"

    sql = f"""
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
      g.id as game_id,
      t.{teams_pk} as team_id,
      lower(r.home_away) as home_away,
      %s as feature_set,
      r.features as features,
      {pulled_at_expr} as pulled_at,
      'partial' as verification_status
    from {RAW_SCHEMA}.{RAW_FEATURES_TABLE} r
    join public.games g
      on g.season = %s and g.source = %s and g.external_game_id = r.event_id
    join public.teams t
      on t.season = %s and t.source_team_id = r.team_id
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

        print("[STEP] Upsert teams")
        counts = upsert_teams(conn, pulled_at_logs)
        print(f"[OK] teams: pulled={counts.pulled} upserted={counts.upserted}")

        print("[STEP] Upsert games")
        counts = upsert_games(conn, teams_pk)
        print(f"[OK] games: pulled={counts.pulled} upserted={counts.upserted} rejected={counts.rejected}")

        print("[STEP] Upsert team_boxscores")
        counts = upsert_team_boxscores(conn, pulled_at_logs, has_data_ok, teams_pk)
        print(f"[OK] team_boxscores: pulled={counts.pulled} upserted={counts.upserted}")

        print("[STEP] Upsert team_game_features")
        counts = upsert_team_game_features(conn, pulled_at_features, has_features_col, teams_pk)
        print(f"[OK] team_game_features: pulled={counts.pulled} upserted={counts.upserted} rejected={counts.rejected}")

        conn.commit()


if __name__ == "__main__":
    main()
