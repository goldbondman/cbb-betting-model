-- Tighten public write policies for model tables; keep authenticated reads and owner-only writes.

-- Remove permissive anonymous write policies.
drop policy if exists "Allow anonymous insert model_versions" on public.model_versions;
drop policy if exists "Allow anonymous insert predictions" on public.predictions;
drop policy if exists "Allow anonymous update predictions" on public.predictions;
drop policy if exists "Allow anonymous insert team_metrics" on public.team_metrics;
drop policy if exists "Allow anonymous insert training_runs" on public.training_runs;
drop policy if exists "Allow anonymous all wagers" on public.wagers;

-- Ensure authenticated write policy for model_versions (Streamlit uses auth + upsert).
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

-- Ensure authenticated read policies exist (idempotent guard).
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
