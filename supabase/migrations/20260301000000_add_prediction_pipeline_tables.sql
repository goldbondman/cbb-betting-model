create extension if not exists "pgcrypto";
create schema if not exists raw;

create table if not exists public.teams (
  id uuid primary key default gen_random_uuid(),
  season int not null,
  source_team_id text null,
  team_name text not null,
  conference text null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (season, source_team_id),
  unique (season, team_name)
);

create table if not exists raw.raw_games (
  id uuid primary key default gen_random_uuid(),
  season int not null,
  source text not null,
  external_game_id text not null,
  payload jsonb not null,
  pulled_at timestamptz not null,
  verification_status text not null default 'partial',
  verification_notes text null,
  unique (season, source, external_game_id)
);

create table if not exists public.games (
  id uuid primary key default gen_random_uuid(),
  season int not null,
  game_datetime_utc timestamptz not null,
  home_team_id uuid not null references public.teams(id),
  away_team_id uuid not null references public.teams(id),
  home_score int null,
  away_score int null,
  status text not null default 'scheduled',
  venue text null,
  source text not null,
  external_game_id text not null,
  verification_status text not null default 'partial',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (season, source, external_game_id),
  unique (season, game_datetime_utc, home_team_id, away_team_id)
);

create table if not exists public.market_lines (
  id uuid primary key default gen_random_uuid(),
  game_id uuid not null references public.games(id),
  book text not null,
  pulled_at timestamptz not null,
  spread_home numeric null,
  total numeric null,
  ml_home int null,
  ml_away int null,
  unique (game_id, book, pulled_at)
);

create table if not exists public.team_game_features (
  id uuid primary key default gen_random_uuid(),
  game_id uuid not null references public.games(id),
  team_id uuid not null references public.teams(id),
  home_away text not null check (home_away in ('home', 'away')),
  feature_set text not null,
  features jsonb not null,
  pulled_at timestamptz not null,
  verification_status text not null default 'partial',
  unique (game_id, team_id, feature_set)
);

create table if not exists public.predictions (
  id uuid primary key default gen_random_uuid(),
  model_version text not null,
  created_at timestamptz not null default now(),
  game_id uuid not null references public.games(id),
  source text not null,
  external_game_id text not null,
  game_datetime_utc timestamptz not null,
  home_team text not null,
  away_team text not null,
  pred_spread numeric null,
  pred_total numeric null,
  win_prob_home numeric null,
  market_spread numeric null,
  market_total numeric null,
  edge_spread numeric null,
  edge_total numeric null,
  bet_side text null,
  bet_units numeric null,
  bet_signal boolean not null default false,
  confidence numeric null,
  notes text null,
  model_inputs jsonb null,
  unique (model_version, external_game_id)
);

create table if not exists public.dq_audit (
  id uuid primary key default gen_random_uuid(),
  entity_type text not null,
  entity_id uuid null,
  severity text not null,
  reason_codes text[] not null,
  details jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_games_season_datetime on public.games (season, game_datetime_utc);
create index if not exists idx_games_home_team on public.games (home_team_id);
create index if not exists idx_games_away_team on public.games (away_team_id);

create index if not exists idx_market_lines_game on public.market_lines (game_id);
create index if not exists idx_market_lines_pulled_at on public.market_lines (pulled_at);

create index if not exists idx_team_game_features_game on public.team_game_features (game_id);
create index if not exists idx_team_game_features_team on public.team_game_features (team_id);

create index if not exists idx_predictions_model_version on public.predictions (model_version);
create index if not exists idx_predictions_game on public.predictions (game_id);

create index if not exists idx_dq_audit_entity on public.dq_audit (entity_type);

alter table public.teams enable row level security;
alter table raw.raw_games enable row level security;
alter table public.games enable row level security;
alter table public.market_lines enable row level security;
alter table public.team_game_features enable row level security;
alter table public.predictions enable row level security;
alter table public.dq_audit enable row level security;

drop policy if exists teams_read on public.teams;
create policy teams_read on public.teams
for select to authenticated
using (true);

drop policy if exists raw_games_read on raw.raw_games;
create policy raw_games_read on raw.raw_games
for select to authenticated
using (true);

drop policy if exists games_read on public.games;
create policy games_read on public.games
for select to authenticated
using (true);

drop policy if exists market_lines_read on public.market_lines;
create policy market_lines_read on public.market_lines
for select to authenticated
using (true);

drop policy if exists team_game_features_read on public.team_game_features;
create policy team_game_features_read on public.team_game_features
for select to authenticated
using (true);

drop policy if exists predictions_read on public.predictions;
create policy predictions_read on public.predictions
for select to authenticated
using (true);

drop policy if exists dq_audit_read on public.dq_audit;
create policy dq_audit_read on public.dq_audit
for select to authenticated
using (true);
