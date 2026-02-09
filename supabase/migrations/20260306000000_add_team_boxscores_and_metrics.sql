create extension if not exists "pgcrypto";

create table if not exists public.team_boxscores (
  id uuid primary key default gen_random_uuid(),
  game_id uuid not null references public.games(id),
  team_id uuid not null references public.teams(id),
  home_away text not null check (home_away in ('home', 'away')),
  source text not null default 'espn',
  pulled_at timestamptz not null default now(),
  stats jsonb not null default '{}'::jsonb,
  verification_status text not null default 'partial',
  verification_notes text null,
  unique (game_id, team_id)
);

create table if not exists public.team_metrics (
  id uuid primary key default gen_random_uuid(),
  season int not null,
  team_id uuid not null references public.teams(id),
  metric_set text not null,
  source text not null default 'espn',
  pulled_at timestamptz not null default now(),
  metrics jsonb not null default '{}'::jsonb,
  verification_status text not null default 'partial',
  verification_notes text null,
  unique (season, team_id, metric_set)
);

create index if not exists idx_team_boxscores_game on public.team_boxscores (game_id);
create index if not exists idx_team_boxscores_team on public.team_boxscores (team_id);
create index if not exists idx_team_boxscores_pulled_at on public.team_boxscores (pulled_at);

create index if not exists idx_team_metrics_team on public.team_metrics (team_id);
create index if not exists idx_team_metrics_season on public.team_metrics (season);
create index if not exists idx_team_metrics_pulled_at on public.team_metrics (pulled_at);

alter table public.team_boxscores enable row level security;
alter table public.team_metrics enable row level security;

drop policy if exists team_boxscores_read on public.team_boxscores;
create policy team_boxscores_read on public.team_boxscores
for select to authenticated
using (true);

drop policy if exists team_metrics_read on public.team_metrics;
create policy team_metrics_read on public.team_metrics
for select to authenticated
using (true);
