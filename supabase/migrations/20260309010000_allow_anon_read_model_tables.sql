-- Allow Streamlit (anon key) read access for model-facing tables.
-- Keeps write operations restricted to service role / backend jobs.

alter table if exists public.teams enable row level security;
alter table if exists public.games enable row level security;
alter table if exists public.market_lines enable row level security;
alter table if exists public.team_game_features enable row level security;
alter table if exists public.predictions enable row level security;

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

-- Explicitly deny direct client writes by only granting select policies above.
