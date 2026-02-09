alter table public.teams
  add column if not exists short_name text;

alter table public.teams
  add column if not exists mascot text;
