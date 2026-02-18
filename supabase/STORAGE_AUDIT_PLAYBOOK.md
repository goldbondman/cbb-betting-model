# Supabase Storage Audit + Reduction Playbook (CBB model)

## Why this exists
Your largest tables (`raw.espn_matchups_model_ready`, `public.team_game_features`, `raw.espn_team_game_features`) are feature-heavy and can grow fast.
Supplementary analytics are stored separately in `raw.espn_team_game_extras` (joined via `event_id` + `team_id`).
This playbook gives safe SQL checks before dropping/migrating anything.

## 1) Confirm what is actually large (table vs indexes vs TOAST)
```sql
select
  n.nspname as schema_name,
  c.relname as table_name,
  pg_size_pretty(pg_table_size(c.oid)) as table_heap,
  pg_size_pretty(pg_indexes_size(c.oid)) as indexes,
  pg_size_pretty(pg_total_relation_size(c.oid)) as total,
  pg_size_pretty(pg_total_relation_size(c.reltoastrelid)) as toast_total
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where c.relkind = 'r'
  and (n.nspname, c.relname) in (
    ('raw','espn_matchups_model_ready'),
    ('raw','espn_team_game_features'),
    ('public','team_game_features')
  )
order by pg_total_relation_size(c.oid) desc;
```

## 2) Find large JSON/text columns in those tables
```sql
select
  table_schema,
  table_name,
  column_name,
  data_type,
  udt_name
from information_schema.columns
where (table_schema, table_name) in (
    ('raw','espn_matchups_model_ready'),
    ('raw','espn_team_game_features'),
    ('public','team_game_features')
  )
  and udt_name in ('json', 'jsonb', 'text', 'varchar')
order by table_schema, table_name, column_name;
```

## 3) Measure per-row payload weight (estimated average bytes)
> Run one table at a time and adjust the selected columns to what exists.
```sql
-- Example for raw.espn_team_game_features
select
  count(*) as rows,
  avg(pg_column_size(features))::bigint as avg_features_bytes,
  avg(pg_column_size(t.*))::bigint as avg_row_bytes
from raw.espn_team_game_features t;
```

```sql
-- Example for public.team_game_features
select
  count(*) as rows,
  avg(pg_column_size(features))::bigint as avg_features_bytes,
  avg(pg_column_size(t.*))::bigint as avg_row_bytes
from public.team_game_features t;
```

## 4) Redundancy checks before dropping raw staging tables
```sql
-- Are normalized features present for the same game/team pairs?
select
  count(*) as raw_rows,
  count(distinct (event_id, team_id)) as raw_game_team_pairs
from raw.espn_team_game_features;
```

```sql
select
  count(*) as public_rows,
  count(distinct (game_id, team_id)) as public_game_team_pairs
from public.team_game_features;
```

```sql
-- Missing normalized rows by external_game_id mapping (spot check)
select count(*) as missing_in_public
from raw.espn_team_game_features r
left join public.games g
  on g.external_game_id = r.event_id
left join public.team_game_features f
  on f.game_id = g.id and f.team_id::text = r.team_id
where f.id is null;
```

## 5) Archive old seasons (recommended first move)
Create archive schema and move old rows (example cutoff before 2024-07-01 UTC):
```sql
create schema if not exists raw_archive;

create table if not exists raw_archive.espn_team_game_features as
select * from raw.espn_team_game_features where false;

insert into raw_archive.espn_team_game_features
select *
from raw.espn_team_game_features
where game_datetime_utc < '2024-07-01'::timestamptz;

-- Validate row count before delete
select count(*) from raw_archive.espn_team_game_features;

-- Then prune primary table
-- delete from raw.espn_team_game_features where game_datetime_utc < '2024-07-01'::timestamptz;
```

Do the same for `raw.espn_matchups_model_ready` if your current pipeline does not need historical rows in primary storage.

## 6) Recommended target architecture (safe + under 500MB)
1. Keep `public.team_game_features` as canonical model-ready store (jsonb `features` only).
2. Keep `raw.*` as short-retention staging (e.g., current season + prior season).
3. Archive older `raw.*` rows to `raw_archive.*` in DB or to Supabase Storage parquet/csv.
4. Do **not** drop `raw.espn_team_game_features` immediately; `ml/feature_matrix.py` currently reads from it by default.
5. If you later switch ML to read from `public.team_game_features`, then raw retention can be reduced further.

## 7) Optional maintenance after large deletes
```sql
vacuum (analyze) raw.espn_team_game_features;
vacuum (analyze) raw.espn_matchups_model_ready;
```
(Use `vacuum full` only during maintenance windows; it locks tables.)
