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
    "post",
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
    return _exec_rowcount(conn, sql, (dqid, entity_type, "error", reason_codes, json_dump(details)))


def _teams_pk_column(conn: psycopg.Connection) -> str:
    # your schema shows teams.team_id exists
    return "team_id" if _has_column(conn, "public", "teams", "team_id") else "id"


def _teams_uuid_col(conn: psycopg.Connection) -> Optional[str]:
    if _has_column(conn, "public", "teams", "team_id"):
        return "team_id"
    if _has_column(conn, "public", "teams", "id"):
        return "id"
    return None


def seed_teams_from_json(conn: psycopg.Connection, path: str) -> Counts:
    if not path:
        return Counts()

    if not os.path.exists(path):
        _insert_dq(conn, "teams", ["seed_file_missing"], {"seed_path": path})
        return Counts(rejected=1)

    teams_uuid_col = _teams_uuid_col(conn)
    if not teams_uuid_col:
        _insert_dq(conn, "teams", ["public_teams_missing_pk"], {"note": "Expected public.teams to have team_id or id."})
        return Counts(rejected=1)

    has_conference = _has_column(conn, "public", "teams", "conference")
    has_short_name = _has_column(conn, "public", "teams", "short_name")

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
        short_name = row.get("shortDisplayName") or row.get("school") or None
        seen[str(source_id)] = (str(source_id), str(team_name), (str(conf) if conf is not None else None), (str(short_name) if short_name else None))

    pulled = len(seen)
    if pulled == 0:
        return Counts()

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
        upserted = _exec_rowcount(conn, sql, (source_ids, names, confs, shorts, SEASON))
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
        upserted = _exec_rowcount(conn, sql, (source_ids, names, confs, SEASON))
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
        upserted = _exec_rowcount(conn, sql, (source_ids, names, SEASON))

    return Counts(pulled=pulled, upserted=upserted, rejected=0)


def upsert_teams(conn: psycopg.Connection) -> Counts:
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
        _insert_dq(conn, "teams", ["public_teams_missing_pk"], {"note": "Expected public.teams to have team_id or id."})
        return Counts(pulled=pulled, rejected=1)

    # note: your teams table has unique (season, source_team_id)
    has_conference = _has_column(conn, "public", "teams", "conference")
    has_short_name = _has_column(conn, "public", "teams", "short_name")

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
        upserted = _exec_rowcount(conn, sql, (SEASON,))
    else:
        # minimal safe path
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
        upserted = _exec_rowcount(conn, sql, (SEASON,))

    return Counts(pulled=pulled, upserted=upserted, rejected=0)


def upsert_games(conn: psycopg.Connection, teams_pk: str) -> Counts:
    pulled = _count_rows(
        conn,
        f"select count(distinct event_id) from {RAW_SCHEMA}.{RAW_LOGS_TABLE} where event_id is not null",
    )

    pulled_at_col = _pick_existing_column(conn, RAW_SCHEMA, RAW_LOGS_TABLE, ["pulled_at_utc", "pulled_at"])
    pulled_at_expr = f"r.{pulled_at_col}" if pulled_at_col else "now()"

    game_dt_col = _pick_existing_column(
        conn,
        RAW_SCHEMA,
        RAW_LOGS_TABLE,
        ["game_datetime_utc", "start_time_utc", "game_date_utc", "game_time_utc"],
    )
    if not game_dt_col:
        _insert_dq(conn, "games", ["missing_datetime_column"], {"table": f"{RAW_SCHEMA}.{RAW_LOGS_TABLE}"})
        return Counts(pulled=pulled, rejected=1)

    completed_true_list_sql = ", ".join([f"'{t}'" for t in COMPLETED_TRUE_TOKENS])

    # Deterministic text game_id: md5(season|source|external_game_id)
    # This satisfies PK(game_id) without DB defaults.
    sql = f"""
    with base as (
      select
        r.event_id,
        cast(r.team_id as text) as source_team_id,
        lower(r.home_away) as home_away,
        r.{game_dt_col} as game_datetime_utc,
        r.venue,
        max(r.team) over (partition by r.event_id, lower(r.home_away)) as team_name,

        case
          when lower(coalesce(r.completed::text, '')) in ({completed_true_list_sql}) then true
          else false
        end as completed,

        case when r.points_for::text ~ '^\\d+$' then r.points_for::text::int else null end as points_for,
        case when r.points_against::text ~ '^\\d+$' then r.points_against::text::int else null end as points_against,

        {pulled_at_expr} as pulled_at
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
        team_name as home_team_name,
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
        source_team_id as away_source_team_id,
        team_name as away_team_name
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
        h.home_team_name,
        a.away_source_team_id,
        a.away_team_name
      from home h
      join away a on a.event_id = h.event_id
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
      case when j.completed then 'final' else 'scheduled' end as status,
      case when j.completed then 'post' else 'pre' end as status_state,
      case when j.completed then 'Final' else 'Scheduled' end as status_detail,
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
      home_score = excluded.home_score,
      away_score = excluded.away_score,
      venue = excluded.venue,
      status = excluded.status,
      status_state = excluded.status_state,
      status_detail = excluded.status_detail,
      verification_status = excluded.verification_status,
      updated_at = now();
    """
    upserted = _exec_rowcount(conn, sql, (SEASON, SOURCE, SEASON, SOURCE, SEASON, SEASON))

    rejected = 0

    # DQ: missing away row (should be rare after join, but raw timing can cause it)
    # We can detect it pre-join by looking for events with home but no away.
    dq_missing_away_sql = f"""
    with base as (
      select event_id, lower(home_away) as ha, {pulled_at_expr} as pulled_at
      from {RAW_SCHEMA}.{RAW_LOGS_TABLE} r
      where event_id is not null and team_id is not null and home_away is not null and btrim(home_away) <> ''
    ),
    dedup as (
      select *
      from (
        select b.*, row_number() over (partition by b.event_id, b.ha order by b.pulled_at desc nulls last) as rn
        from base b
      ) x where rn = 1
    )
    select d.event_id
    from (select event_id from dedup where ha='home') h
    left join (select event_id from dedup where ha='away') a using(event_id)
    where a.event_id is null
    limit 50;
    """
    with conn.cursor() as cur:
        cur.execute(dq_missing_away_sql)
        for (event_id,) in cur.fetchall():
            rejected += _insert_dq(conn, "games", ["missing_away_row"], {"event_id": event_id})

    return Counts(pulled=pulled, upserted=upserted, rejected=rejected)


def upsert_team_boxscores(conn: psycopg.Connection, teams_pk: str) -> Counts:
    pulled = _count_rows(
        conn,
        f"select count(*) from {RAW_SCHEMA}.{RAW_LOGS_TABLE} where event_id is not null and team_id is not null",
    )

    pulled_at_col = _pick_existing_column(conn, RAW_SCHEMA, RAW_LOGS_TABLE, ["pulled_at_utc", "pulled_at"])
    pulled_at_expr = f"r.{pulled_at_col}" if pulled_at_col else "now()"

    # Helper: only select raw columns if they exist
    def raw_col(name: str) -> str:
        return f"r.{name}" if _has_column(conn, RAW_SCHEMA, RAW_LOGS_TABLE, name) else "null"

    # numeric normalization
    def norm_int(expr: str) -> str:
        return f"case when ({expr})::text ~ '^\\d+$' then ({expr})::text::int else null end"

    def norm_num(expr: str) -> str:
        # allows decimals
        return f"case when ({expr}) is null then null when ({expr})::text ~ '^-?\\d+(\\.\\d+)?$' then ({expr})::text::numeric else null end"

    sql = f"""
    with base as (
      select
        r.event_id,
        cast(r.team_id as text) as source_team_id,
        coalesce(r.team, '') as team_name,
        lower(r.home_away) as home_away_norm,
        {pulled_at_expr} as pulled_at,

        {norm_int(raw_col("points_for"))} as pts,
        {norm_int(raw_col("fgm"))} as fgm,
        {norm_int(raw_col("fga"))} as fga,
        {norm_int(raw_col("tpm"))} as tpm,
        {norm_int(raw_col("tpa"))} as tpa,
        {norm_int(raw_col("ftm"))} as ftm,
        {norm_int(raw_col("fta"))} as fta,
        {norm_int(raw_col("oreb"))} as oreb,
        {norm_int(raw_col("dreb"))} as dreb,
        {norm_int(raw_col("ast"))} as ast,
        {norm_int(raw_col("tov"))} as tov,
        {norm_int(raw_col("stl"))} as stl,
        {norm_int(raw_col("blk"))} as blk,
        {norm_num(raw_col("efg"))} as efg,
        {norm_num(raw_col("tov_pct"))} as tov_pct
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
      where rn = 1
    ),
    games as (
      select
        game_id,
        season,
        source,
        external_game_id
      from public.games
      where season = %s and lower(source) = lower(%s)
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
      efg,
      tov_pct,
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
      d.efg,
      d.tov_pct,
      d.pulled_at,
      now()
    from dedup d
    join games g
      on g.external_game_id = d.event_id
    join public.teams t
      on t.season = %s and cast(t.source_team_id as text) = d.source_team_id
    on conflict (game_id, team_id)
    do update set
      team = excluded.team,
      is_home = excluded.is_home,
      pts = excluded.pts,
      fgm = excluded.fgm,
      fga = excluded.fga,
      tpm = excluded.tpm,
      tpa = excluded.tpa,
      ftm = excluded.ftm,
      fta = excluded.fta,
      oreb = excluded.oreb,
      dreb = excluded.dreb,
      ast = excluded.ast,
      tov = excluded.tov,
      stl = excluded.stl,
      blk = excluded.blk,
      efg = excluded.efg,
      tov_pct = excluded.tov_pct,
      pulled_at = excluded.pulled_at;
    """
    upserted = _exec_rowcount(conn, sql, (SEASON, SOURCE, SEASON))
    return Counts(pulled=pulled, upserted=upserted, rejected=0)


def upsert_team_game_features(conn: psycopg.Connection, teams_pk: str) -> Counts:
    pulled = _count_rows(
        conn,
        f"select count(*) from {RAW_SCHEMA}.{RAW_FEATURES_TABLE} where event_id is not null and team_id is not null",
    )

    if not _has_column(conn, RAW_SCHEMA, RAW_FEATURES_TABLE, "features"):
        rejected = _insert_dq(conn, "team_game_features", ["missing_features_column"], {"table": f"{RAW_SCHEMA}.{RAW_FEATURES_TABLE}"})
        return Counts(pulled=pulled, rejected=rejected)

    pulled_at_col = _pick_existing_column(conn, RAW_SCHEMA, RAW_FEATURES_TABLE, ["pulled_at_utc", "pulled_at"])
    pulled_at_expr = f"r.{pulled_at_col}" if pulled_at_col else "now()"

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
      select game_id, external_game_id
      from public.games
      where season = %s and lower(source) = lower(%s)
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
    upserted = _exec_rowcount(conn, sql, (SEASON, SOURCE, FEATURE_SET, SEASON))
    return Counts(pulled=pulled, upserted=upserted, rejected=0)


def main() -> None:
    if not SUPABASE_DB_URL:
        _die("SUPABASE_DB_URL is missing/empty.")

    with psycopg.connect(SUPABASE_DB_URL) as conn:
        teams_pk = _teams_pk_column(conn)

        if TEAMS_SEED_JSON:
            print(f"[STEP] Seed teams from JSON: {TEAMS_SEED_JSON}")
            c = seed_teams_from_json(conn, TEAMS_SEED_JSON)
            print(f"[OK] seed teams: pulled={c.pulled} upserted={c.upserted} rejected={c.rejected}")

        print("[STEP] Upsert teams")
        c = upsert_teams(conn)
        print(f"[OK] teams: pulled={c.pulled} upserted={c.upserted} rejected={c.rejected}")

        print("[STEP] Upsert games")
        c = upsert_games(conn, teams_pk)
        print(f"[OK] games: pulled={c.pulled} upserted={c.upserted} rejected={c.rejected}")

        print("[STEP] Upsert team_boxscores")
        c = upsert_team_boxscores(conn, teams_pk)
        print(f"[OK] team_boxscores: pulled={c.pulled} upserted={c.upserted} rejected={c.rejected}")

        print("[STEP] Upsert team_game_features")
        c = upsert_team_game_features(conn, teams_pk)
        print(f"[OK] team_game_features: pulled={c.pulled} upserted={c.upserted} rejected={c.rejected}")

        conn.commit()


if __name__ == "__main__":
    main()
