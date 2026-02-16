-- Create raw ingestion bridge tables for newly produced CSV artifacts.
-- These tables preserve provenance and verification metadata so CSV->Supabase
-- ingestion is deterministic and auditable.

create table if not exists raw.espn_teams (
  id uuid primary key default gen_random_uuid(),
  row_hash text not null,
  espn_id text not null,
  name text not null,
  abbreviation text null,
  logo text null,
  pulled_at_utc timestamptz not null,
  source text not null,
  parse_version text not null,
  verification_status text not null default 'partial'
    check (verification_status in ('verified', 'partial', 'conflict', 'rejected')),
  verification_notes text null,
  created_at timestamptz not null default now(),
  unique (espn_id),
  unique (row_hash)
);

create index if not exists idx_espn_teams_source_pulled on raw.espn_teams (source, pulled_at_utc desc);

create table if not exists raw.espn_injuries (
  id uuid primary key default gen_random_uuid(),
  row_hash text not null,
  team_id text not null,
  team text null,
  athlete_id text not null,
  player text null,
  position text null,
  status text not null,
  injury_type text null,
  detail text null,
  side text null,
  return_date text null,
  pulled_at_utc timestamptz not null,
  source text not null,
  parse_version text not null,
  verification_status text not null default 'partial'
    check (verification_status in ('verified', 'partial', 'conflict', 'rejected')),
  verification_notes text null,
  created_at timestamptz not null default now(),
  unique (row_hash),
  unique (team_id, athlete_id, status, pulled_at_utc)
);

create index if not exists idx_espn_injuries_team on raw.espn_injuries (team_id);
create index if not exists idx_espn_injuries_athlete on raw.espn_injuries (athlete_id);
create index if not exists idx_espn_injuries_pulled on raw.espn_injuries (pulled_at_utc desc);

create table if not exists raw.espn_dq_audit (
  id uuid primary key default gen_random_uuid(),
  row_hash text not null,
  event_id text not null,
  team_id text not null,
  team text null,
  home_away text null check (home_away in ('home', 'away')),
  dq_missing_fields text null,
  dq_reason_codes text null,
  dq_action_plan text null,
  dq_repair_success boolean null,
  dq_repair_actions_taken text null,
  pulled_at_utc timestamptz not null,
  source text not null,
  parse_version text not null,
  verification_status text not null default 'partial'
    check (verification_status in ('verified', 'partial', 'conflict', 'rejected')),
  verification_notes text null,
  created_at timestamptz not null default now(),
  unique (row_hash)
);

create index if not exists idx_espn_dq_audit_event_team on raw.espn_dq_audit (event_id, team_id);
create index if not exists idx_espn_dq_audit_pulled on raw.espn_dq_audit (pulled_at_utc desc);

create table if not exists raw.espn_feature_diagnostics (
  id uuid primary key default gen_random_uuid(),
  row_hash text not null,
  event_id text not null,
  team_id text not null,
  team text null,
  diagnostic_reason text not null,
  pulled_at_utc timestamptz not null,
  source text not null,
  parse_version text not null,
  verification_status text not null default 'partial'
    check (verification_status in ('verified', 'partial', 'conflict', 'rejected')),
  verification_notes text null,
  created_at timestamptz not null default now(),
  unique (row_hash),
  unique (event_id, team_id, diagnostic_reason, pulled_at_utc)
);

create index if not exists idx_espn_feature_diag_event_team on raw.espn_feature_diagnostics (event_id, team_id);
create index if not exists idx_espn_feature_diag_pulled on raw.espn_feature_diagnostics (pulled_at_utc desc);

create table if not exists raw.ncaa_team_game_logs (
  id uuid primary key default gen_random_uuid(),
  row_hash text not null,
  game_id text not null,
  team text not null,
  opponent text not null,
  home_away text not null check (home_away in ('home', 'away')),
  game_date date null,
  game_datetime timestamptz not null,
  venue text null,
  points_for integer null,
  points_against integer null,
  margin integer null,
  fgm integer null,
  fga integer null,
  fg_pct numeric null,
  tpm integer null,
  tpa integer null,
  tp_pct numeric null,
  ftm integer null,
  fta integer null,
  ft_pct numeric null,
  reb integer null,
  orb integer null,
  drb integer null,
  ast integer null,
  stl integer null,
  blk integer null,
  tov integer null,
  pf integer null,
  pulled_at_utc timestamptz not null,
  source text not null,
  parse_version text not null,
  verification_status text not null default 'partial'
    check (verification_status in ('verified', 'partial', 'conflict', 'rejected')),
  verification_notes text null,
  created_at timestamptz not null default now(),
  unique (row_hash),
  unique (game_id, team, home_away)
);

create index if not exists idx_ncaa_team_logs_game on raw.ncaa_team_game_logs (game_id);
create index if not exists idx_ncaa_team_logs_team_datetime on raw.ncaa_team_game_logs (team, game_datetime);

create table if not exists raw.ncaa_games (
  id uuid primary key default gen_random_uuid(),
  row_hash text not null,
  game_id text not null,
  date date null,
  game_datetime timestamptz not null,
  home_team text not null,
  away_team text not null,
  home_score integer null,
  away_score integer null,
  status text null,
  venue text null,
  pulled_at_utc timestamptz not null,
  source text not null,
  parse_version text not null,
  verification_status text not null default 'partial'
    check (verification_status in ('verified', 'partial', 'conflict', 'rejected')),
  verification_notes text null,
  created_at timestamptz not null default now(),
  unique (row_hash),
  unique (game_id)
);

create index if not exists idx_ncaa_games_datetime on raw.ncaa_games (game_datetime);
create index if not exists idx_ncaa_games_date on raw.ncaa_games (date);

create table if not exists raw.ncaa_player_boxscores (
  id uuid primary key default gen_random_uuid(),
  row_hash text not null,
  game_id text not null,
  team text not null,
  player_name text not null,
  player_id text null,
  starter boolean null,
  minutes numeric null,
  points integer null,
  fgm integer null,
  fga integer null,
  fg_pct numeric null,
  tpm integer null,
  tpa integer null,
  tp_pct numeric null,
  ftm integer null,
  fta integer null,
  ft_pct numeric null,
  reb integer null,
  orb integer null,
  drb integer null,
  ast integer null,
  stl integer null,
  blk integer null,
  tov integer null,
  pf integer null,
  pulled_at_utc timestamptz not null,
  source text not null,
  parse_version text not null,
  verification_status text not null default 'partial'
    check (verification_status in ('verified', 'partial', 'conflict', 'rejected')),
  verification_notes text null,
  created_at timestamptz not null default now(),
  unique (row_hash),
  unique (game_id, team, player_name)
);

create index if not exists idx_ncaa_player_boxscores_game on raw.ncaa_player_boxscores (game_id);
create index if not exists idx_ncaa_player_boxscores_player on raw.ncaa_player_boxscores (player_id);

create table if not exists raw.haslametrics (
  id uuid primary key default gen_random_uuid(),
  row_hash text not null,
  pulled_at_utc timestamptz not null,
  source text not null,
  parse_version text not null,
  verification_status text not null default 'partial'
    check (verification_status in ('verified', 'partial', 'conflict', 'rejected')),
  verification_notes text null,
  created_at timestamptz not null default now(),
  unique (row_hash)
);

create index if not exists idx_haslametrics_pulled on raw.haslametrics (pulled_at_utc desc);

alter table raw.espn_teams enable row level security;
alter table raw.espn_injuries enable row level security;
alter table raw.espn_dq_audit enable row level security;
alter table raw.espn_feature_diagnostics enable row level security;
alter table raw.ncaa_team_game_logs enable row level security;
alter table raw.ncaa_games enable row level security;
alter table raw.ncaa_player_boxscores enable row level security;
alter table raw.haslametrics enable row level security;

drop policy if exists raw_espn_teams_read_authenticated on raw.espn_teams;
create policy raw_espn_teams_read_authenticated
on raw.espn_teams
for select
to authenticated
using (true);

drop policy if exists raw_espn_injuries_read_authenticated on raw.espn_injuries;
create policy raw_espn_injuries_read_authenticated
on raw.espn_injuries
for select
to authenticated
using (true);

drop policy if exists raw_espn_dq_audit_read_authenticated on raw.espn_dq_audit;
create policy raw_espn_dq_audit_read_authenticated
on raw.espn_dq_audit
for select
to authenticated
using (true);

drop policy if exists raw_espn_feature_diagnostics_read_authenticated on raw.espn_feature_diagnostics;
create policy raw_espn_feature_diagnostics_read_authenticated
on raw.espn_feature_diagnostics
for select
to authenticated
using (true);

drop policy if exists raw_ncaa_team_game_logs_read_authenticated on raw.ncaa_team_game_logs;
create policy raw_ncaa_team_game_logs_read_authenticated
on raw.ncaa_team_game_logs
for select
to authenticated
using (true);

drop policy if exists raw_ncaa_games_read_authenticated on raw.ncaa_games;
create policy raw_ncaa_games_read_authenticated
on raw.ncaa_games
for select
to authenticated
using (true);

drop policy if exists raw_ncaa_player_boxscores_read_authenticated on raw.ncaa_player_boxscores;
create policy raw_ncaa_player_boxscores_read_authenticated
on raw.ncaa_player_boxscores
for select
to authenticated
using (true);

drop policy if exists raw_haslametrics_read_authenticated on raw.haslametrics;
create policy raw_haslametrics_read_authenticated
on raw.haslametrics
for select
to authenticated
using (true);
