alter table public.model_registry enable row level security;

drop policy if exists model_registry_read_anon on public.model_registry;
create policy model_registry_read_anon on public.model_registry
for select to anon
using (true);
