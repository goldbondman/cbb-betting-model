-- RLS policy review cleanup: remove anonymous writes and tighten reads to authenticated.

-- data_quality_logs should not be public-writeable.
drop policy if exists "Allow anonymous insert data_quality_logs" on public.data_quality_logs;
drop policy if exists "Allow anonymous read data_quality_logs" on public.data_quality_logs;

-- model tables: remove anonymous writes.
drop policy if exists "Allow anonymous insert model_versions" on public.model_versions;
drop policy if exists "Allow anonymous insert predictions" on public.predictions;
drop policy if exists "Allow anonymous update predictions" on public.predictions;
drop policy if exists "Allow anonymous insert team_metrics" on public.team_metrics;
drop policy if exists "Allow anonymous insert training_runs" on public.training_runs;

-- user tables: remove anonymous ALL policies.
drop policy if exists "Allow anonymous all user_preferences" on public.user_preferences;
drop policy if exists "Allow anonymous all wagers" on public.wagers;

-- Ensure authenticated read for data_quality_logs.
do $$
begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public'
      and tablename = 'data_quality_logs'
      and policyname = 'data_quality_logs_read'
  ) then
    create policy "data_quality_logs_read" on public.data_quality_logs
    for select to authenticated
    using (true);
  end if;
end
$$;

-- Ensure authenticated read for model_versions.
do $$
begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public'
      and tablename = 'model_versions'
      and policyname = 'model_versions_read'
  ) then
    create policy "model_versions_read" on public.model_versions
    for select to authenticated
    using (true);
  end if;
end
$$;

-- Ensure authenticated write for model_versions (Streamlit auth writes).
do $$
begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public'
      and tablename = 'model_versions'
      and policyname = 'model_versions_write'
  ) then
    create policy "model_versions_write" on public.model_versions
    for all to authenticated
    using (true)
    with check (true);
  end if;
end
$$;

-- Ensure authenticated read for predictions.
do $$
begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public'
      and tablename = 'predictions'
      and policyname = 'predictions_read'
  ) then
    create policy "predictions_read" on public.predictions
    for select to authenticated
    using (true);
  end if;
end
$$;

-- Ensure authenticated read for team_metrics.
do $$
begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public'
      and tablename = 'team_metrics'
      and policyname = 'team_metrics_read'
  ) then
    create policy "team_metrics_read" on public.team_metrics
    for select to authenticated
    using (true);
  end if;
end
$$;

-- Ensure authenticated read for training_runs.
do $$
begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public'
      and tablename = 'training_runs'
      and policyname = 'training_runs_read'
  ) then
    create policy "training_runs_read" on public.training_runs
    for select to authenticated
    using (true);
  end if;
end
$$;
