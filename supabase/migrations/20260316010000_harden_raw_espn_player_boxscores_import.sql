-- Harden raw ESPN player boxscore ingestion to reduce CSV import failures
-- and preserve full stat vectors from ESPN payload.

alter table if exists raw.espn_player_boxscores
  add column if not exists raw_stat_labels jsonb,
  add column if not exists raw_stat_values jsonb;

-- Keep the existing unique key and add import-friendly helper indexes.
create index if not exists idx_espn_player_boxscores_event_team
  on raw.espn_player_boxscores (event_id, team_id);

create index if not exists idx_espn_player_boxscores_row_hash
  on raw.espn_player_boxscores (row_hash);

-- Data-quality check: avoid blank athlete IDs on future writes.
alter table if exists raw.espn_player_boxscores
  add constraint espn_player_boxscores_athlete_id_nonblank_chk
  check (length(btrim(athlete_id)) > 0) not valid;

alter table if exists raw.espn_player_boxscores enable row level security;

drop policy if exists raw_espn_player_boxscores_read_authenticated on raw.espn_player_boxscores;
create policy raw_espn_player_boxscores_read_authenticated
on raw.espn_player_boxscores
for select
to authenticated
using (true);
