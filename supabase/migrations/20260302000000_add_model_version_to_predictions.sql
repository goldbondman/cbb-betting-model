do $$
begin
  if not exists (
    select 1
    from information_schema.columns
    where table_schema = 'public'
      and table_name = 'predictions'
      and column_name = 'model_version'
  ) then
    alter table public.predictions
      add column model_version text;
  end if;
end
$$;

update public.predictions
set model_version = 'legacy'
where model_version is null;

create index if not exists idx_predictions_model_version on public.predictions (model_version);
