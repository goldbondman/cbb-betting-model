create extension if not exists "pgcrypto";

create table if not exists public.model_registry (
  id uuid primary key default gen_random_uuid(),
  model_id text not null,
  model_name text not null,
  model_type text not null,
  feature_set text null,
  model_version text null,
  params jsonb null,
  is_active boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (model_id)
);

create index if not exists idx_model_registry_model_id on public.model_registry (model_id);
create index if not exists idx_model_registry_is_active on public.model_registry (is_active);

alter table public.model_registry enable row level security;

drop policy if exists model_registry_read on public.model_registry;
create policy model_registry_read on public.model_registry
for select to authenticated
using (true);

drop policy if exists model_registry_write on public.model_registry;
create policy model_registry_write on public.model_registry
for all to authenticated
using (true)
with check (true);
