alter table public.teams
  add column if not exists season int;

alter table public.teams
  add column if not exists source_team_id text;

alter table public.teams
  add column if not exists team_name text;

alter table public.teams
  add column if not exists conference text;

alter table public.teams
  add column if not exists created_at timestamptz not null default now();

alter table public.teams
  add column if not exists updated_at timestamptz not null default now();

create unique index if not exists idx_teams_unique_source
  on public.teams (season, source_team_id);

create unique index if not exists idx_teams_unique_name
  on public.teams (season, team_name);
