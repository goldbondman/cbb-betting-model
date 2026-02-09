alter table public.team_metrics
  add column if not exists season int;

alter table public.team_metrics
  add column if not exists metric_set text;

alter table public.team_metrics
  add column if not exists verification_status text not null default 'partial';

alter table public.team_metrics
  add column if not exists verification_notes text;

create index if not exists idx_team_metrics_unique_fallback
  on public.team_metrics (season, team_id, metric_set);
