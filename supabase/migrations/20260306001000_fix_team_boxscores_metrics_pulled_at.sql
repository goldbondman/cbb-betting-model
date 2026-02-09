alter table public.team_boxscores
  add column if not exists pulled_at timestamptz not null default now();

alter table public.team_metrics
  add column if not exists pulled_at timestamptz not null default now();

create index if not exists idx_team_boxscores_pulled_at on public.team_boxscores (pulled_at);
create index if not exists idx_team_metrics_pulled_at on public.team_metrics (pulled_at);
