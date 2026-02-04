-- Enable Streamlit writes for bet_ledger and user_preferences with auth-only policies.

-- Ensure RLS is enabled.
alter table public.bet_ledger enable row level security;
alter table public.user_preferences enable row level security;

-- Remove permissive anonymous policy for user preferences.
drop policy if exists "Allow anonymous all user_preferences" on public.user_preferences;

-- Add/ensure owner-only policy for user_preferences.
do $$
begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public'
      and tablename = 'user_preferences'
      and policyname = 'user_preferences_owner'
  ) then
    create policy "user_preferences_owner" on public.user_preferences
    for all to authenticated
    using (user_id = auth.uid())
    with check (user_id = auth.uid());
  end if;
end
$$;

-- Allow authenticated inserts to bet_ledger.
do $$
begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public'
      and tablename = 'bet_ledger'
      and policyname = 'bet_ledger_write'
  ) then
    create policy "bet_ledger_write" on public.bet_ledger
    for insert to authenticated
    with check (true);
  end if;
end
$$;

-- Ensure authenticated read policy exists for bet_ledger.
do $$
begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public'
      and tablename = 'bet_ledger'
      and policyname = 'bet_ledger_read'
  ) then
    create policy "bet_ledger_read" on public.bet_ledger
    for select to authenticated
    using (true);
  end if;
end
$$;
