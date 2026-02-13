-- Streamlined raw ingestion table for ESPN team-game boxscore + score primitives.
-- Purpose: keep only essential raw inputs in DB; compute rolling/derived ML features in Python.

create table if not exists raw.espn_team_game_core (
  id uuid primary key default gen_random_uuid(),
  event_id text not null,
  team_id text not null,
  team text null,
  home_away text not null check (home_away in ('home', 'away')),
  game_datetime_utc timestamptz not null,

  -- score primitives
  points_for double precision null,
  points_against double precision null,

  -- boxscore primitives
  fgm double precision null,
  fga double precision null,
  tpm double precision null,
  tpa double precision null,
  ftm double precision null,
  fta double precision null,
  tov double precision null,
  orb double precision null,
  drb double precision null,

  pulled_at_utc timestamptz not null,
  source text not null,
  parse_version text not null,
  verification_status text not null default 'partial',
  verification_notes text null,

  unique (event_id, team_id, home_away)
);

create index if not exists idx_espn_team_game_core_event on raw.espn_team_game_core (event_id);
create index if not exists idx_espn_team_game_core_team_dt on raw.espn_team_game_core (team_id, game_datetime_utc);
create index if not exists idx_espn_team_game_core_dt on raw.espn_team_game_core (game_datetime_utc);

alter table raw.espn_team_game_core enable row level security;

drop policy if exists raw_espn_team_game_core_read_authenticated on raw.espn_team_game_core;
create policy raw_espn_team_game_core_read_authenticated
on raw.espn_team_game_core
for select
to authenticated
using (true);
