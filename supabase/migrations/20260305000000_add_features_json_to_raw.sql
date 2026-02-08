-- Add JSONB feature payload column to avoid row-size limits for wide feature tables.
alter table if exists raw.espn_team_game_features
  add column if not exists features jsonb;

comment on column raw.espn_team_game_features.features is
  'Packed feature payload for wide ESPN team-game features (JSONB).';
