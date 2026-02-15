-- Raw ingestion table for ESPN player-level boxscore data.
-- Purpose: store per-player per-game stat lines from ESPN summaries.

create table if not exists raw.espn_player_boxscores (
  id uuid primary key default gen_random_uuid(),
  row_hash text not null,
  event_id text not null,
  game_datetime_utc timestamptz not null,
  team_id text not null,
  team text null,
  home_away text not null check (home_away in ('home', 'away')),
  athlete_id text not null,
  player text null,
  starter text null,

  -- box score stats
  min double precision null,
  pts double precision null,
  fgm double precision null,
  fga double precision null,
  tpm double precision null,
  tpa double precision null,
  ftm double precision null,
  fta double precision null,
  reb double precision null,
  orb double precision null,
  drb double precision null,
  ast double precision null,
  stl double precision null,
  blk double precision null,
  tov double precision null,
  pf double precision null,

  pulled_at_utc timestamptz not null,
  source text not null,
  parse_version text not null,

  unique (event_id, team_id, athlete_id)
);

create index if not exists idx_espn_player_boxscores_event on raw.espn_player_boxscores (event_id);
create index if not exists idx_espn_player_boxscores_athlete on raw.espn_player_boxscores (athlete_id);
create index if not exists idx_espn_player_boxscores_team_dt on raw.espn_player_boxscores (team_id, game_datetime_utc);

alter table raw.espn_player_boxscores enable row level security;

drop policy if exists raw_espn_player_boxscores_read_authenticated on raw.espn_player_boxscores;
create policy raw_espn_player_boxscores_read_authenticated
on raw.espn_player_boxscores
for select
to authenticated
using (true);
