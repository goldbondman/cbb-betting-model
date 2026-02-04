-- Create bet_ledger table used by streamlit_app.py for paper bet tracking.

create table if not exists public.bet_ledger (
  id text primary key,
  run_date text not null,
  game_date text not null,
  event_id text not null,
  home_team text not null,
  away_team text not null,
  market text not null,
  side text not null,
  model_value numeric null,
  vegas_value numeric null,
  edge numeric null,
  conf numeric null,
  recommended boolean not null default true,
  units numeric null,
  result text null,
  pnl numeric null,
  model_version text null,
  meta jsonb null,
  created_at timestamptz not null default now()
);

create index if not exists idx_bet_ledger_run_date on public.bet_ledger (run_date);
create index if not exists idx_bet_ledger_game_date on public.bet_ledger (game_date);
create index if not exists idx_bet_ledger_event_id on public.bet_ledger (event_id);

alter table public.bet_ledger enable row level security;

create policy "bet_ledger_read" on public.bet_ledger
for select to authenticated using (true);
