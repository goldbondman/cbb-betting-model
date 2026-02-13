-- Align model_registry to formula-model definitions used by Model Lab.

alter table if exists public.model_registry
  add column if not exists params jsonb not null default '{}'::jsonb,
  add column if not exists created_at timestamptz not null default now(),
  add column if not exists updated_at timestamptz not null default now(),
  add column if not exists is_active boolean not null default false,
  add column if not exists model_name text,
  add column if not exists model_type text;

-- Ensure required model metadata is present.
update public.model_registry
set model_name = coalesce(model_name, model_id),
    model_type = coalesce(model_type, 'spread')
where model_name is null or model_type is null;

alter table public.model_registry
  alter column model_name set not null,
  alter column model_type set not null,
  alter column params set not null;

-- Enforce supported model types only.
alter table public.model_registry
  drop constraint if exists model_registry_model_type_check;
alter table public.model_registry
  add constraint model_registry_model_type_check
  check (model_type in ('spread', 'total'));

-- Keep model_id as canonical unique key and optimize active lookups.
create unique index if not exists uq_model_registry_model_id on public.model_registry (model_id);
create index if not exists idx_model_registry_model_type_active
  on public.model_registry (model_type, is_active, updated_at desc);

alter table public.model_registry enable row level security;

drop policy if exists model_registry_read on public.model_registry;
create policy model_registry_read on public.model_registry
for select to anon, authenticated
using (true);

drop policy if exists model_registry_write_authenticated on public.model_registry;
create policy model_registry_write_authenticated on public.model_registry
for all to authenticated
using (true)
with check (true);
