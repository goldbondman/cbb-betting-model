-- Condense existing Supabase data footprint safely:
-- 1) deterministic de-duplication
-- 2) orphan cleanup
-- 3) retention pruning for old raw payload rows already normalized
-- 4) enforce critical constraints/indexes and refresh planner stats

create extension if not exists "pgcrypto";
create schema if not exists raw;

-- Ensure dq_audit exists before writing audit rows.
create table if not exists public.dq_audit (
  id uuid primary key default gen_random_uuid(),
  entity_type text not null,
  entity_id uuid null,
  severity text not null,
  reason_codes text[] not null,
  details jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

-- =====================================================
-- A) DEDUPE: raw.raw_games (keep most recent pulled_at)
-- =====================================================
with duplicate_rows as (
  select id
  from (
    select
      id,
      row_number() over (
        partition by season, source, external_game_id
        order by pulled_at desc, id desc
      ) as rn
    from raw.raw_games
  ) ranked
  where rn > 1
), audit_rows as (
  insert into public.dq_audit (entity_type, entity_id, severity, reason_codes, details)
  select
    'raw_games'::text,
    id,
    'warning'::text,
    array['duplicate_removed']::text[],
    jsonb_build_object('merge_strategy', 'keep_latest_pulled_at')
  from duplicate_rows
  returning 1
)
delete from raw.raw_games rg
using duplicate_rows d
where rg.id = d.id;

-- =====================================================
-- B) DEDUPE: public.games primary key tuple (keep latest updated_at)
-- =====================================================
with duplicate_rows as (
  select id
  from (
    select
      id,
      row_number() over (
        partition by season, source, external_game_id
        order by updated_at desc, created_at desc, id desc
      ) as rn
    from public.games
  ) ranked
  where rn > 1
), audit_rows as (
  insert into public.dq_audit (entity_type, entity_id, severity, reason_codes, details)
  select
    'games'::text,
    id,
    'warning'::text,
    array['duplicate_removed_primary_key']::text[],
    jsonb_build_object('merge_strategy', 'keep_latest_updated_at')
  from duplicate_rows
  returning 1
)
delete from public.games g
using duplicate_rows d
where g.id = d.id;

-- =====================================================
-- C) DEDUPE: public.games secondary tuple (date + matchup)
-- =====================================================
with duplicate_rows as (
  select id
  from (
    select
      id,
      row_number() over (
        partition by season, game_datetime_utc, home_team_id, away_team_id
        order by updated_at desc, created_at desc, id desc
      ) as rn
    from public.games
  ) ranked
  where rn > 1
), audit_rows as (
  insert into public.dq_audit (entity_type, entity_id, severity, reason_codes, details)
  select
    'games'::text,
    id,
    'warning'::text,
    array['duplicate_removed_secondary_key']::text[],
    jsonb_build_object('merge_strategy', 'keep_latest_updated_at')
  from duplicate_rows
  returning 1
)
delete from public.games g
using duplicate_rows d
where g.id = d.id;

-- =====================================================
-- D) DEDUPE: public.market_lines (keep latest id)
-- =====================================================
with duplicate_rows as (
  select id
  from (
    select
      id,
      row_number() over (
        partition by game_id, book, pulled_at
        order by id desc
      ) as rn
    from public.market_lines
  ) ranked
  where rn > 1
), audit_rows as (
  insert into public.dq_audit (entity_type, entity_id, severity, reason_codes, details)
  select
    'market_lines'::text,
    id,
    'warning'::text,
    array['duplicate_removed']::text[],
    jsonb_build_object('merge_strategy', 'keep_latest_id')
  from duplicate_rows
  returning 1
)
delete from public.market_lines ml
using duplicate_rows d
where ml.id = d.id;

-- =====================================================
-- E) DEDUPE: public.team_game_features (keep latest pulled_at)
-- =====================================================
with duplicate_rows as (
  select id
  from (
    select
      id,
      row_number() over (
        partition by game_id, team_id, feature_set
        order by pulled_at desc, id desc
      ) as rn
    from public.team_game_features
  ) ranked
  where rn > 1
), audit_rows as (
  insert into public.dq_audit (entity_type, entity_id, severity, reason_codes, details)
  select
    'team_game_features'::text,
    id,
    'warning'::text,
    array['duplicate_removed']::text[],
    jsonb_build_object('merge_strategy', 'keep_latest_pulled_at')
  from duplicate_rows
  returning 1
)
delete from public.team_game_features tgf
using duplicate_rows d
where tgf.id = d.id;

-- =====================================================
-- F) DEDUPE: public.predictions by append-only key (keep latest created_at)
-- =====================================================
with duplicate_rows as (
  select id
  from (
    select
      id,
      row_number() over (
        partition by model_version, game_id
        order by created_at desc, id desc
      ) as rn
    from public.predictions
  ) ranked
  where rn > 1
), audit_rows as (
  insert into public.dq_audit (entity_type, entity_id, severity, reason_codes, details)
  select
    'predictions'::text,
    id,
    'warning'::text,
    array['duplicate_removed']::text[],
    jsonb_build_object('merge_strategy', 'keep_latest_created_at')
  from duplicate_rows
  returning 1
)
delete from public.predictions p
using duplicate_rows d
where p.id = d.id;

-- =====================================================
-- G) ORPHAN CLEANUP: remove child rows with no game parent
-- =====================================================
with orphan_lines as (
  select ml.id
  from public.market_lines ml
  left join public.games g on g.id = ml.game_id
  where g.id is null
), audit_rows as (
  insert into public.dq_audit (entity_type, entity_id, severity, reason_codes, details)
  select
    'market_lines'::text,
    id,
    'warning'::text,
    array['orphan_removed']::text[],
    jsonb_build_object('reason', 'missing_parent_game')
  from orphan_lines
  returning 1
)
delete from public.market_lines ml
using orphan_lines o
where ml.id = o.id;

with orphan_features as (
  select tgf.id
  from public.team_game_features tgf
  left join public.games g on g.id = tgf.game_id
  where g.id is null
), audit_rows as (
  insert into public.dq_audit (entity_type, entity_id, severity, reason_codes, details)
  select
    'team_game_features'::text,
    id,
    'warning'::text,
    array['orphan_removed']::text[],
    jsonb_build_object('reason', 'missing_parent_game')
  from orphan_features
  returning 1
)
delete from public.team_game_features tgf
using orphan_features o
where tgf.id = o.id;

with orphan_predictions as (
  select p.id
  from public.predictions p
  left join public.games g on g.id = p.game_id
  where g.id is null
), audit_rows as (
  insert into public.dq_audit (entity_type, entity_id, severity, reason_codes, details)
  select
    'predictions'::text,
    id,
    'warning'::text,
    array['orphan_removed']::text[],
    jsonb_build_object('reason', 'missing_parent_game')
  from orphan_predictions
  returning 1
)
delete from public.predictions p
using orphan_predictions o
where p.id = o.id;

-- =====================================================
-- H) RETENTION PRUNE: very old raw payload rows that are normalized + final
-- =====================================================
with prune_candidates as (
  select rg.id
  from raw.raw_games rg
  join public.games g
    on g.season = rg.season
   and g.source = rg.source
   and g.external_game_id = rg.external_game_id
  where rg.pulled_at < now() - interval '540 days'
    and g.status = 'final'
), audit_rows as (
  insert into public.dq_audit (entity_type, entity_id, severity, reason_codes, details)
  select
    'raw_games'::text,
    id,
    'info'::text,
    array['retention_prune']::text[],
    jsonb_build_object('retention_days', 540, 'condition', 'normalized_final_exists')
  from prune_candidates
  returning 1
)
delete from raw.raw_games rg
using prune_candidates pc
where rg.id = pc.id;

-- =====================================================
-- I) CONSTRAINT + INDEX HARDENING (idempotent)
-- =====================================================
alter table if exists raw.raw_games
  add constraint raw_games_verification_status_chk
  check (verification_status in ('verified','partial','conflict','rejected')) not valid;

alter table if exists public.games
  add constraint games_verification_status_chk
  check (verification_status in ('verified','partial','conflict','rejected')) not valid;

alter table if exists public.games
  add constraint games_status_chk
  check (status in ('scheduled','live','final')) not valid;

alter table if exists public.team_game_features
  add constraint team_game_features_verification_status_chk
  check (verification_status in ('verified','partial','conflict','rejected')) not valid;

create unique index if not exists uq_raw_games_source_ext
  on raw.raw_games (season, source, external_game_id);

create unique index if not exists uq_games_source_ext
  on public.games (season, source, external_game_id);

create unique index if not exists uq_games_dt_matchup
  on public.games (season, game_datetime_utc, home_team_id, away_team_id);

create unique index if not exists uq_market_lines_snapshot
  on public.market_lines (game_id, book, pulled_at);

create unique index if not exists uq_team_game_features_key
  on public.team_game_features (game_id, team_id, feature_set);

create unique index if not exists uq_predictions_model_game
  on public.predictions (model_version, game_id);

create index if not exists idx_games_season_datetime on public.games (season, game_datetime_utc);
create index if not exists idx_games_home_team on public.games (home_team_id);
create index if not exists idx_games_away_team on public.games (away_team_id);
create index if not exists idx_market_lines_game on public.market_lines (game_id);
create index if not exists idx_market_lines_pulled_at on public.market_lines (pulled_at);
create index if not exists idx_team_game_features_game on public.team_game_features (game_id);
create index if not exists idx_team_game_features_team on public.team_game_features (team_id);
create index if not exists idx_predictions_model_version on public.predictions (model_version);
create index if not exists idx_predictions_game on public.predictions (game_id);
create index if not exists idx_dq_audit_entity_created_at on public.dq_audit (entity_type, created_at desc);

-- =====================================================
-- J) RLS baseline (read-only for anon+authenticated on model tables)
-- =====================================================
alter table if exists public.teams enable row level security;
alter table if exists public.games enable row level security;
alter table if exists public.market_lines enable row level security;
alter table if exists public.team_game_features enable row level security;
alter table if exists public.predictions enable row level security;
alter table if exists raw.raw_games enable row level security;
alter table if exists public.dq_audit enable row level security;

drop policy if exists teams_read on public.teams;
create policy teams_read on public.teams
for select to anon, authenticated
using (true);

drop policy if exists games_read on public.games;
create policy games_read on public.games
for select to anon, authenticated
using (true);

drop policy if exists market_lines_read on public.market_lines;
create policy market_lines_read on public.market_lines
for select to anon, authenticated
using (true);

drop policy if exists team_game_features_read on public.team_game_features;
create policy team_game_features_read on public.team_game_features
for select to anon, authenticated
using (true);

drop policy if exists predictions_read on public.predictions;
create policy predictions_read on public.predictions
for select to anon, authenticated
using (true);

drop policy if exists raw_games_read on raw.raw_games;
create policy raw_games_read on raw.raw_games
for select to authenticated
using (true);

drop policy if exists dq_audit_read on public.dq_audit;
create policy dq_audit_read on public.dq_audit
for select to authenticated
using (true);

-- NOTE: run VACUUM (VERBOSE, ANALYZE) on large tables from Supabase SQL editor
-- after this migration for maximum space reclamation.
analyze public.games;
analyze raw.raw_games;
analyze public.market_lines;
analyze public.team_game_features;
analyze public.predictions;
analyze public.dq_audit;
