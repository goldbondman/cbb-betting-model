-- =====================================================================
-- Complete Supabase Schema Design Implementation
-- CBB Betting Model - Optimized, Normalized, Production-Ready
-- =====================================================================
-- 
-- This migration implements the complete schema design from
-- docs/SUPABASE_SCHEMA_DESIGN.md
--
-- Design Principles:
-- 1. Aggressive normalization (raw ≠ derived)
-- 2. Multi-source integrity with verification tracking
-- 3. Query performance via strategic indexing
-- 4. Complete audit trails
-- 5. Schema evolution support via JSONB where appropriate
--
-- Schemas:
-- - raw: Immutable source data with full provenance
-- - public: Normalized application data
-- - analytics: Computed metrics (regenerable from raw)
-- =====================================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Create schemas
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS analytics;

-- =====================================================================
-- RAW SCHEMA: Source Data Ingestion Layer
-- =====================================================================

-- 1. Raw Games (Complete API Payloads)
-- Already exists from previous migration - skip if exists
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'raw' AND tablename = 'raw_games') THEN
    CREATE TABLE raw.raw_games (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      season INTEGER NOT NULL,
      source TEXT NOT NULL CHECK (source IN ('ESPN', 'NCAA', 'HENRY', 'BARTTORVIK')),
      external_game_id TEXT NOT NULL,
      payload JSONB NOT NULL,
      pulled_at TIMESTAMPTZ NOT NULL,
      verification_status TEXT NOT NULL DEFAULT 'partial' 
        CHECK (verification_status IN ('verified', 'partial', 'conflict', 'rejected')),
      verification_notes TEXT,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      UNIQUE (season, source, external_game_id)
    );
    
    CREATE INDEX idx_raw_games_season_source ON raw.raw_games (season, source);
    CREATE INDEX idx_raw_games_pulled_at ON raw.raw_games (pulled_at DESC);
    CREATE INDEX idx_raw_games_verification ON raw.raw_games (verification_status) 
      WHERE verification_status != 'verified';
  END IF;
END $$;

-- 2. Barttorvik Teams (External Pre-Computed Metrics)
CREATE TABLE IF NOT EXISTS raw.barttorvik_teams (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  row_hash TEXT NOT NULL UNIQUE,
  season INTEGER NOT NULL,
  team TEXT NOT NULL,
  
  -- Barttorvik advanced metrics
  adj_oe NUMERIC(6,2),
  adj_de NUMERIC(6,2),
  adj_em NUMERIC(6,2),
  barthag NUMERIC(5,4),
  sos_adj_em NUMERIC(6,2),
  sos_opp_oe NUMERIC(6,2),
  sos_opp_de NUMERIC(6,2),
  rank INTEGER,
  
  -- Metadata
  pulled_at_utc TIMESTAMPTZ NOT NULL,
  source TEXT NOT NULL DEFAULT 'BARTTORVIK',
  parse_version TEXT NOT NULL,
  verification_status TEXT NOT NULL DEFAULT 'partial'
    CHECK (verification_status IN ('verified', 'partial', 'conflict', 'rejected')),
  verification_notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  
  UNIQUE (season, team, pulled_at_utc)
);

CREATE INDEX idx_barttorvik_season_team ON raw.barttorvik_teams (season, team);
CREATE INDEX idx_barttorvik_pulled ON raw.barttorvik_teams (pulled_at_utc DESC);

-- =====================================================================
-- PUBLIC SCHEMA: Normalized Application Data
-- =====================================================================

-- Enhance existing teams table with additional fields
DO $$ BEGIN
  -- Add espn_team_id if it doesn't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'teams' 
    AND column_name = 'espn_team_id'
  ) THEN
    ALTER TABLE public.teams ADD COLUMN espn_team_id TEXT;
  END IF;
  
  -- Add ncaa_team_id if it doesn't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'teams' 
    AND column_name = 'ncaa_team_id'
  ) THEN
    ALTER TABLE public.teams ADD COLUMN ncaa_team_id TEXT;
  END IF;
  
  -- Add barttorvik_team_name if it doesn't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'teams' 
    AND column_name = 'barttorvik_team_name'
  ) THEN
    ALTER TABLE public.teams ADD COLUMN barttorvik_team_name TEXT;
  END IF;
  
  -- Add display_name if it doesn't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'teams' 
    AND column_name = 'display_name'
  ) THEN
    ALTER TABLE public.teams ADD COLUMN display_name TEXT;
  END IF;
  
  -- Add short_name if it doesn't exist (may already exist from earlier migration)
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'teams' 
    AND column_name = 'short_name'
  ) THEN
    ALTER TABLE public.teams ADD COLUMN short_name TEXT;
  END IF;
  
  -- Add mascot if it doesn't exist (may already exist from earlier migration)
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'teams' 
    AND column_name = 'mascot'
  ) THEN
    ALTER TABLE public.teams ADD COLUMN mascot TEXT;
  END IF;
  
  -- Add logo_url if it doesn't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'teams' 
    AND column_name = 'logo_url'
  ) THEN
    ALTER TABLE public.teams ADD COLUMN logo_url TEXT;
  END IF;
  
  -- Add division if it doesn't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'teams' 
    AND column_name = 'division'
  ) THEN
    ALTER TABLE public.teams ADD COLUMN division TEXT;
  END IF;
END $$;

-- Add unique constraints for new external ID fields
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT FROM pg_constraint 
    WHERE conname = 'teams_season_espn_id_key'
  ) THEN
    ALTER TABLE public.teams ADD CONSTRAINT teams_season_espn_id_key 
      UNIQUE (season, espn_team_id);
  END IF;
  
  IF NOT EXISTS (
    SELECT FROM pg_constraint 
    WHERE conname = 'teams_season_ncaa_id_key'
  ) THEN
    ALTER TABLE public.teams ADD CONSTRAINT teams_season_ncaa_id_key 
      UNIQUE (season, ncaa_team_id);
  END IF;
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

-- Add indexes
CREATE INDEX IF NOT EXISTS idx_teams_season ON public.teams (season);
CREATE INDEX IF NOT EXISTS idx_teams_name ON public.teams (team_name);
CREATE INDEX IF NOT EXISTS idx_teams_conference ON public.teams (season, conference);
CREATE INDEX IF NOT EXISTS idx_teams_name_lower ON public.teams (LOWER(team_name));

-- Enhance existing games table
DO $$ BEGIN
  -- Add espn_game_id if it doesn't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'games' 
    AND column_name = 'espn_game_id'
  ) THEN
    ALTER TABLE public.games ADD COLUMN espn_game_id TEXT;
  END IF;
  
  -- Add ncaa_game_id if it doesn't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'games' 
    AND column_name = 'ncaa_game_id'
  ) THEN
    ALTER TABLE public.games ADD COLUMN ncaa_game_id TEXT;
  END IF;
  
  -- Add margin if it doesn't exist (as generated column if supported)
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'games' 
    AND column_name = 'margin'
  ) THEN
    ALTER TABLE public.games ADD COLUMN margin INTEGER;
  END IF;
  
  -- Add venue_name if it doesn't exist (rename from venue if needed)
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'games' 
    AND column_name = 'venue_name'
  ) AND EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'games' 
    AND column_name = 'venue'
  ) THEN
    ALTER TABLE public.games RENAME COLUMN venue TO venue_name;
  ELSIF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'games' 
    AND column_name = 'venue_name'
  ) THEN
    ALTER TABLE public.games ADD COLUMN venue_name TEXT;
  END IF;
  
  -- Add venue_city if it doesn't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'games' 
    AND column_name = 'venue_city'
  ) THEN
    ALTER TABLE public.games ADD COLUMN venue_city TEXT;
  END IF;
  
  -- Add venue_state if it doesn't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'games' 
    AND column_name = 'venue_state'
  ) THEN
    ALTER TABLE public.games ADD COLUMN venue_state TEXT;
  END IF;
  
  -- Add neutral_site if it doesn't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'games' 
    AND column_name = 'neutral_site'
  ) THEN
    ALTER TABLE public.games ADD COLUMN neutral_site BOOLEAN DEFAULT FALSE;
  END IF;
  
  -- Add game_type if it doesn't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'games' 
    AND column_name = 'game_type'
  ) THEN
    ALTER TABLE public.games ADD COLUMN game_type TEXT DEFAULT 'regular'
      CHECK (game_type IN ('regular', 'conference_tournament', 'ncaa_tournament', 'exhibition'));
  END IF;
  
  -- Add tournament if it doesn't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'games' 
    AND column_name = 'tournament'
  ) THEN
    ALTER TABLE public.games ADD COLUMN tournament TEXT;
  END IF;
  
  -- Add tournament_round if it doesn't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'games' 
    AND column_name = 'tournament_round'
  ) THEN
    ALTER TABLE public.games ADD COLUMN tournament_round TEXT;
  END IF;
END $$;

-- Add check constraint for games (team can't play itself)
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT FROM pg_constraint 
    WHERE conname = 'games_different_teams_check'
  ) THEN
    ALTER TABLE public.games ADD CONSTRAINT games_different_teams_check 
      CHECK (home_team_id != away_team_id);
  END IF;
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

-- Add indexes for games
CREATE INDEX IF NOT EXISTS idx_games_season_datetime ON public.games (season, game_datetime_utc);
CREATE INDEX IF NOT EXISTS idx_games_home_team_date ON public.games (home_team_id, game_datetime_utc DESC);
CREATE INDEX IF NOT EXISTS idx_games_away_team_date ON public.games (away_team_id, game_datetime_utc DESC);
CREATE INDEX IF NOT EXISTS idx_games_status_partial ON public.games (status) WHERE status != 'final';
CREATE INDEX IF NOT EXISTS idx_games_tournament ON public.games (season, tournament) WHERE tournament IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_games_date ON public.games ((game_datetime_utc::DATE));
CREATE INDEX IF NOT EXISTS idx_games_espn_id ON public.games (season, espn_game_id);
CREATE INDEX IF NOT EXISTS idx_games_ncaa_id ON public.games (season, ncaa_game_id);

-- Enhance market_lines table
DO $$ BEGIN
  -- Add line_type if it doesn't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'market_lines' 
    AND column_name = 'line_type'
  ) THEN
    ALTER TABLE public.market_lines ADD COLUMN line_type TEXT DEFAULT 'pregame'
      CHECK (line_type IN ('pregame', 'live', 'closing'));
  END IF;
  
  -- Add juice columns if they don't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'market_lines' 
    AND column_name = 'spread_juice_home'
  ) THEN
    ALTER TABLE public.market_lines ADD COLUMN spread_juice_home INTEGER;
  END IF;
  
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'market_lines' 
    AND column_name = 'spread_juice_away'
  ) THEN
    ALTER TABLE public.market_lines ADD COLUMN spread_juice_away INTEGER;
  END IF;
  
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'market_lines' 
    AND column_name = 'total_over_juice'
  ) THEN
    ALTER TABLE public.market_lines ADD COLUMN total_over_juice INTEGER;
  END IF;
  
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'market_lines' 
    AND column_name = 'total_under_juice'
  ) THEN
    ALTER TABLE public.market_lines ADD COLUMN total_under_juice INTEGER;
  END IF;
  
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'market_lines' 
    AND column_name = 'moneyline_home'
  ) THEN
    ALTER TABLE public.market_lines ADD COLUMN moneyline_home INTEGER;
  END IF;
  
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'market_lines' 
    AND column_name = 'moneyline_away'
  ) THEN
    ALTER TABLE public.market_lines ADD COLUMN moneyline_away INTEGER;
  END IF;
  
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'market_lines' 
    AND column_name = 'created_at'
  ) THEN
    ALTER TABLE public.market_lines ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
  END IF;
END $$;

-- Update market_lines unique constraint to include line_type
DO $$ BEGIN
  -- Drop old unique constraint if it exists
  IF EXISTS (
    SELECT FROM pg_constraint 
    WHERE conname = 'market_lines_game_id_book_pulled_at_key'
  ) THEN
    ALTER TABLE public.market_lines DROP CONSTRAINT market_lines_game_id_book_pulled_at_key;
  END IF;
  
  -- Add new unique constraint
  IF NOT EXISTS (
    SELECT FROM pg_constraint 
    WHERE conname = 'market_lines_game_book_type_pulled_key'
  ) THEN
    ALTER TABLE public.market_lines ADD CONSTRAINT market_lines_game_book_type_pulled_key 
      UNIQUE (game_id, book, line_type, pulled_at);
  END IF;
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

-- Add indexes for market_lines
CREATE INDEX IF NOT EXISTS idx_market_lines_game_pulled ON public.market_lines (game_id, pulled_at DESC);
CREATE INDEX IF NOT EXISTS idx_market_lines_book ON public.market_lines (book);
CREATE INDEX IF NOT EXISTS idx_market_lines_closing ON public.market_lines (game_id, line_type) 
  WHERE line_type = 'closing';

-- Enhance team_boxscores table
DO $$ BEGIN
  -- Rename stats to individual columns if needed (major schema change - skip for now)
  -- This would require data migration
  
  -- Add points column if it doesn't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'team_boxscores' 
    AND column_name = 'points'
  ) THEN
    ALTER TABLE public.team_boxscores ADD COLUMN points INTEGER;
  END IF;
  
  -- Add created_at if it doesn't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'team_boxscores' 
    AND column_name = 'created_at'
  ) THEN
    ALTER TABLE public.team_boxscores ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
  END IF;
  
  -- Add updated_at if it doesn't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'team_boxscores' 
    AND column_name = 'updated_at'
  ) THEN
    ALTER TABLE public.team_boxscores ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
  END IF;
END $$;

-- Player box scores table
CREATE TABLE IF NOT EXISTS public.player_boxscores (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  game_id UUID NOT NULL REFERENCES public.games(id) ON DELETE CASCADE,
  team_id UUID NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
  
  -- Player identity
  espn_athlete_id TEXT,
  ncaa_player_id TEXT,
  player_name TEXT NOT NULL,
  position TEXT,
  jersey_number TEXT,
  starter BOOLEAN,
  
  -- Playing time
  minutes_played NUMERIC(4,1),
  
  -- Box score stats
  points INTEGER,
  fgm INTEGER,
  fga INTEGER,
  tpm INTEGER,
  tpa INTEGER,
  ftm INTEGER,
  fta INTEGER,
  orb INTEGER,
  drb INTEGER,
  ast INTEGER,
  stl INTEGER,
  blk INTEGER,
  tov INTEGER,
  pf INTEGER,
  
  -- Source tracking
  source TEXT NOT NULL CHECK (source IN ('ESPN', 'NCAA', 'RESOLVED')),
  verification_status TEXT NOT NULL DEFAULT 'partial'
    CHECK (verification_status IN ('verified', 'partial', 'conflict', 'rejected')),
  
  -- Metadata
  pulled_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  
  UNIQUE (game_id, team_id, espn_athlete_id),
  UNIQUE (game_id, team_id, ncaa_player_id)
);

CREATE INDEX idx_player_boxscores_game ON public.player_boxscores (game_id);
CREATE INDEX idx_player_boxscores_team ON public.player_boxscores (team_id);
CREATE INDEX idx_player_boxscores_player_espn ON public.player_boxscores (espn_athlete_id);
CREATE INDEX idx_player_boxscores_player_ncaa ON public.player_boxscores (ncaa_player_id);

-- Injuries table
CREATE TABLE IF NOT EXISTS public.injuries (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  team_id UUID NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
  
  -- Player identity
  espn_athlete_id TEXT,
  player_name TEXT NOT NULL,
  position TEXT,
  
  -- Injury details
  status TEXT NOT NULL CHECK (status IN ('Out', 'Doubtful', 'Questionable', 'Probable', 'Day-To-Day')),
  injury_type TEXT,
  detail TEXT,
  side TEXT CHECK (side IN ('Left', 'Right', NULL)),
  return_date DATE,
  
  -- Status tracking
  injury_start_date DATE,
  injury_end_date DATE,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  
  -- Source tracking
  source TEXT NOT NULL CHECK (source IN ('ESPN', 'NCAA', 'MANUAL')),
  
  -- Metadata
  pulled_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_injuries_team ON public.injuries (team_id, is_active);
CREATE INDEX idx_injuries_athlete ON public.injuries (espn_athlete_id);
CREATE INDEX idx_injuries_status ON public.injuries (status, is_active);
CREATE INDEX idx_injuries_dates ON public.injuries (injury_start_date, injury_end_date);
CREATE INDEX idx_injuries_active ON public.injuries (team_id, is_active) WHERE is_active = TRUE;

-- Enhance model_registry table
DO $$ BEGIN
  -- Add model_category if it doesn't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'model_registry' 
    AND column_name = 'model_category'
  ) THEN
    ALTER TABLE public.model_registry ADD COLUMN model_category TEXT 
      CHECK (model_category IN ('spread', 'total', 'margin', 'win_prob'));
  END IF;
  
  -- Add weights if it doesn't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'model_registry' 
    AND column_name = 'weights'
  ) THEN
    ALTER TABLE public.model_registry ADD COLUMN weights JSONB;
  END IF;
  
  -- Add parent_model_id if it doesn't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'model_registry' 
    AND column_name = 'parent_model_id'
  ) THEN
    ALTER TABLE public.model_registry ADD COLUMN parent_model_id TEXT;
  END IF;
  
  -- Add is_production if it doesn't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'model_registry' 
    AND column_name = 'is_production'
  ) THEN
    ALTER TABLE public.model_registry ADD COLUMN is_production BOOLEAN NOT NULL DEFAULT FALSE;
  END IF;
  
  -- Add backtest metrics if they don't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'model_registry' 
    AND column_name = 'backtest_mae'
  ) THEN
    ALTER TABLE public.model_registry ADD COLUMN backtest_mae NUMERIC(6,4);
  END IF;
  
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'model_registry' 
    AND column_name = 'backtest_rmse'
  ) THEN
    ALTER TABLE public.model_registry ADD COLUMN backtest_rmse NUMERIC(6,4);
  END IF;
  
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'model_registry' 
    AND column_name = 'backtest_r2'
  ) THEN
    ALTER TABLE public.model_registry ADD COLUMN backtest_r2 NUMERIC(6,4);
  END IF;
  
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'model_registry' 
    AND column_name = 'backtest_roi'
  ) THEN
    ALTER TABLE public.model_registry ADD COLUMN backtest_roi NUMERIC(6,4);
  END IF;
  
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'model_registry' 
    AND column_name = 'backtest_win_rate'
  ) THEN
    ALTER TABLE public.model_registry ADD COLUMN backtest_win_rate NUMERIC(5,4);
  END IF;
  
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'model_registry' 
    AND column_name = 'backtest_sample_size'
  ) THEN
    ALTER TABLE public.model_registry ADD COLUMN backtest_sample_size INTEGER;
  END IF;
  
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'model_registry' 
    AND column_name = 'backtest_date_range'
  ) THEN
    ALTER TABLE public.model_registry ADD COLUMN backtest_date_range DATERANGE;
  END IF;
  
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'model_registry' 
    AND column_name = 'created_by'
  ) THEN
    ALTER TABLE public.model_registry ADD COLUMN created_by TEXT;
  END IF;
  
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'model_registry' 
    AND column_name = 'notes'
  ) THEN
    ALTER TABLE public.model_registry ADD COLUMN notes TEXT;
  END IF;
END $$;

-- Add indexes for model_registry
CREATE INDEX IF NOT EXISTS idx_model_registry_type ON public.model_registry (model_type, model_category);
CREATE INDEX IF NOT EXISTS idx_model_registry_active ON public.model_registry (is_active, model_category);
CREATE INDEX IF NOT EXISTS idx_model_registry_production ON public.model_registry (is_production) 
  WHERE is_production = TRUE;
CREATE INDEX IF NOT EXISTS idx_model_registry_version ON public.model_registry (model_id, model_version);
CREATE INDEX IF NOT EXISTS idx_model_registry_params_gin ON public.model_registry USING GIN (params);

-- Enhance predictions table
DO $$ BEGIN
  -- Add prediction_key if it doesn't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'predictions' 
    AND column_name = 'prediction_key'
  ) THEN
    ALTER TABLE public.predictions ADD COLUMN prediction_key TEXT;
  END IF;
  
  -- Add model_version_id if it doesn't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'predictions' 
    AND column_name = 'model_version_id'
  ) THEN
    ALTER TABLE public.predictions ADD COLUMN model_version_id UUID;
  END IF;
  
  -- Add game_date if it doesn't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'predictions' 
    AND column_name = 'game_date'
  ) THEN
    ALTER TABLE public.predictions ADD COLUMN game_date DATE;
  END IF;
  
  -- Add bet_tier if it doesn't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'predictions' 
    AND column_name = 'bet_tier'
  ) THEN
    ALTER TABLE public.predictions ADD COLUMN bet_tier TEXT 
      CHECK (bet_tier IN ('tier1', 'tier2', 'tier3', NULL));
  END IF;
  
  -- Add ensemble_breakdown if it doesn't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'predictions' 
    AND column_name = 'ensemble_breakdown'
  ) THEN
    ALTER TABLE public.predictions ADD COLUMN ensemble_breakdown JSONB;
  END IF;
  
  -- Add outcome tracking columns
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'predictions' 
    AND column_name = 'actual_spread'
  ) THEN
    ALTER TABLE public.predictions ADD COLUMN actual_spread INTEGER;
  END IF;
  
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'predictions' 
    AND column_name = 'actual_total'
  ) THEN
    ALTER TABLE public.predictions ADD COLUMN actual_total INTEGER;
  END IF;
  
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'predictions' 
    AND column_name = 'prediction_error_spread'
  ) THEN
    ALTER TABLE public.predictions ADD COLUMN prediction_error_spread NUMERIC(6,2);
  END IF;
  
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'predictions' 
    AND column_name = 'prediction_error_total'
  ) THEN
    ALTER TABLE public.predictions ADD COLUMN prediction_error_total NUMERIC(6,2);
  END IF;
  
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'predictions' 
    AND column_name = 'bet_outcome'
  ) THEN
    ALTER TABLE public.predictions ADD COLUMN bet_outcome TEXT 
      CHECK (bet_outcome IN ('win', 'loss', 'push', 'no_bet', NULL));
  END IF;
  
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'predictions' 
    AND column_name = 'bet_pnl'
  ) THEN
    ALTER TABLE public.predictions ADD COLUMN bet_pnl NUMERIC(10,2);
  END IF;
  
  -- Add updated_at if it doesn't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'predictions' 
    AND column_name = 'updated_at'
  ) THEN
    ALTER TABLE public.predictions ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
  END IF;
END $$;

-- Add indexes for predictions
CREATE INDEX IF NOT EXISTS idx_predictions_date ON public.predictions (game_date);
CREATE INDEX IF NOT EXISTS idx_predictions_signal ON public.predictions (bet_signal, game_datetime_utc) 
  WHERE bet_signal = TRUE;
CREATE INDEX IF NOT EXISTS idx_predictions_key ON public.predictions (prediction_key);
CREATE INDEX IF NOT EXISTS idx_predictions_inputs_gin ON public.predictions USING GIN (model_inputs);
CREATE INDEX IF NOT EXISTS idx_predictions_game_date ON public.predictions ((game_datetime_utc::DATE));

-- Enhance bet_ledger table
DO $$ BEGIN
  -- Add model_id if it doesn't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'bet_ledger' 
    AND column_name = 'model_id'
  ) THEN
    ALTER TABLE public.bet_ledger ADD COLUMN model_id TEXT;
  END IF;
  
  -- Add prediction_id if it doesn't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'bet_ledger' 
    AND column_name = 'prediction_id'
  ) THEN
    ALTER TABLE public.bet_ledger ADD COLUMN prediction_id UUID 
      REFERENCES public.predictions(id) ON DELETE SET NULL;
  END IF;
  
  -- Add placed if it doesn't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'bet_ledger' 
    AND column_name = 'placed'
  ) THEN
    ALTER TABLE public.bet_ledger ADD COLUMN placed BOOLEAN NOT NULL DEFAULT FALSE;
  END IF;
  
  -- Add actual_score columns if they don't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'bet_ledger' 
    AND column_name = 'actual_score_home'
  ) THEN
    ALTER TABLE public.bet_ledger ADD COLUMN actual_score_home INTEGER;
  END IF;
  
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'bet_ledger' 
    AND column_name = 'actual_score_away'
  ) THEN
    ALTER TABLE public.bet_ledger ADD COLUMN actual_score_away INTEGER;
  END IF;
  
  -- Add updated_at if it doesn't exist
  IF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'bet_ledger' 
    AND column_name = 'updated_at'
  ) THEN
    ALTER TABLE public.bet_ledger ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
  END IF;
  
  -- Rename conf to confidence if needed
  IF EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'bet_ledger' 
    AND column_name = 'conf'
  ) AND NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'bet_ledger' 
    AND column_name = 'confidence'
  ) THEN
    ALTER TABLE public.bet_ledger RENAME COLUMN conf TO confidence;
  ELSIF NOT EXISTS (
    SELECT FROM information_schema.columns 
    WHERE table_schema = 'public' 
    AND table_name = 'bet_ledger' 
    AND column_name = 'confidence'
  ) THEN
    ALTER TABLE public.bet_ledger ADD COLUMN confidence NUMERIC(5,4);
  END IF;
END $$;

-- Add indexes for bet_ledger
CREATE INDEX IF NOT EXISTS idx_bet_ledger_model_result ON public.bet_ledger (model_version, result);

-- =====================================================================
-- ANALYTICS SCHEMA: Computed Metrics Layer
-- =====================================================================

-- Team game metrics (derived from box scores)
CREATE TABLE IF NOT EXISTS analytics.team_game_metrics (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  game_id UUID NOT NULL REFERENCES public.games(id) ON DELETE CASCADE,
  team_id UUID NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
  home_away TEXT NOT NULL CHECK (home_away IN ('home', 'away')),
  
  -- Reference to raw box score
  boxscore_id UUID REFERENCES public.team_boxscores(id) ON DELETE CASCADE,
  
  -- Possessions
  possessions NUMERIC(6,2) NOT NULL,
  
  -- Four Factors
  efg_pct NUMERIC(5,4),
  tov_pct NUMERIC(5,4),
  orb_pct NUMERIC(5,4),
  drb_pct NUMERIC(5,4),
  ftr NUMERIC(5,4),
  
  -- Shooting metrics
  ts_pct NUMERIC(5,4),
  three_par NUMERIC(5,4),
  
  -- Efficiency (per 100 possessions)
  ortg NUMERIC(6,2),
  drtg NUMERIC(6,2),
  
  -- Pace
  pace NUMERIC(6,2),
  
  -- Computation metadata
  computation_version TEXT NOT NULL,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  
  UNIQUE (game_id, team_id)
);

CREATE INDEX idx_team_metrics_game ON analytics.team_game_metrics (game_id);
CREATE INDEX idx_team_metrics_team ON analytics.team_game_metrics (team_id);
CREATE INDEX idx_team_metrics_boxscore ON analytics.team_game_metrics (boxscore_id);

-- Team rolling metrics
CREATE TABLE IF NOT EXISTS analytics.team_rolling_metrics (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  team_id UUID NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
  season INTEGER NOT NULL,
  as_of_date DATE NOT NULL,
  
  -- Rolling windows
  window_type TEXT NOT NULL CHECK (window_type IN ('L3', 'L5', 'L7', 'L10', 'season')),
  games_played INTEGER NOT NULL,
  
  -- Aggregated efficiency metrics
  avg_ortg NUMERIC(6,2),
  avg_drtg NUMERIC(6,2),
  avg_netrtg NUMERIC(7,2),
  avg_pace NUMERIC(6,2),
  
  -- Four Factors averages
  avg_efg NUMERIC(5,4),
  avg_tov_pct NUMERIC(5,4),
  avg_orb_pct NUMERIC(5,4),
  avg_drb_pct NUMERIC(5,4),
  avg_ftr NUMERIC(5,4),
  avg_three_par NUMERIC(5,4),
  
  -- Variance metrics
  std_ortg NUMERIC(6,2),
  std_drtg NUMERIC(6,2),
  
  -- Computation metadata
  computation_version TEXT NOT NULL,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  
  UNIQUE (team_id, window_type, as_of_date)
);

CREATE INDEX idx_rolling_metrics_team_date ON analytics.team_rolling_metrics (team_id, as_of_date DESC);
CREATE INDEX idx_rolling_metrics_season ON analytics.team_rolling_metrics (season, team_id);
CREATE INDEX idx_rolling_metrics_window ON analytics.team_rolling_metrics (window_type);

-- Team opponent metrics
CREATE TABLE IF NOT EXISTS analytics.team_opponent_metrics (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  game_id UUID NOT NULL REFERENCES public.games(id) ON DELETE CASCADE,
  team_id UUID NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
  opponent_id UUID NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
  
  -- Reference to base metrics
  base_metrics_id UUID REFERENCES analytics.team_game_metrics(id) ON DELETE CASCADE,
  
  -- Opponent's defensive profile
  opponent_avg_drtg_allowed NUMERIC(6,2),
  opponent_avg_ortg_allowed NUMERIC(6,2),
  
  -- Performance vs expectation
  ortg_vs_expectation NUMERIC(7,2),
  drtg_vs_expectation NUMERIC(7,2),
  
  -- Opponent strength context
  opponent_sos_rank INTEGER,
  opponent_adj_em NUMERIC(6,2),
  
  -- Computation metadata
  computation_version TEXT NOT NULL,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  
  UNIQUE (game_id, team_id)
);

CREATE INDEX idx_opponent_metrics_game ON analytics.team_opponent_metrics (game_id);
CREATE INDEX idx_opponent_metrics_team ON analytics.team_opponent_metrics (team_id);
CREATE INDEX idx_opponent_metrics_opponent ON analytics.team_opponent_metrics (opponent_id);

-- Team strength of schedule
CREATE TABLE IF NOT EXISTS analytics.team_strength_of_schedule (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  team_id UUID NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
  season INTEGER NOT NULL,
  as_of_date DATE NOT NULL,
  
  -- SOS metrics
  sos_avg_opp_netrtg NUMERIC(7,2),
  sos_avg_opp_adj_em NUMERIC(7,2),
  sos_rank INTEGER,
  
  -- Schedule composition
  games_vs_top25 INTEGER,
  games_vs_top50 INTEGER,
  games_vs_quadrant1 INTEGER,
  games_vs_quadrant2 INTEGER,
  
  -- Home/Away/Neutral splits
  sos_home NUMERIC(7,2),
  sos_away NUMERIC(7,2),
  sos_neutral NUMERIC(7,2),
  
  -- Computation metadata
  computation_version TEXT NOT NULL,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  
  UNIQUE (team_id, season, as_of_date)
);

CREATE INDEX idx_sos_team_season ON analytics.team_strength_of_schedule (team_id, season, as_of_date DESC);
CREATE INDEX idx_sos_rank ON analytics.team_strength_of_schedule (season, sos_rank);

-- Prediction performance tracking
CREATE TABLE IF NOT EXISTS analytics.prediction_performance (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  model_id TEXT NOT NULL,
  
  -- Performance window
  window_start DATE NOT NULL,
  window_end DATE NOT NULL,
  total_predictions INTEGER NOT NULL,
  
  -- Accuracy metrics
  mae_spread NUMERIC(6,4),
  rmse_spread NUMERIC(6,4),
  mae_total NUMERIC(6,4),
  rmse_total NUMERIC(6,4),
  
  -- Betting performance
  total_bets INTEGER,
  wins INTEGER,
  losses INTEGER,
  pushes INTEGER,
  roi NUMERIC(7,4),
  sharpe_ratio NUMERIC(7,4),
  max_drawdown NUMERIC(7,4),
  
  -- Edge accuracy
  avg_predicted_edge NUMERIC(6,2),
  avg_realized_edge NUMERIC(6,2),
  edge_correlation NUMERIC(6,4),
  
  -- Calibration
  brier_score NUMERIC(6,4),
  log_loss NUMERIC(6,4),
  
  -- Segment breakdowns
  performance_by_spread_range JSONB,
  performance_by_confidence JSONB,
  performance_by_game_type JSONB,
  
  -- Computation metadata
  computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  
  UNIQUE (model_id, window_start, window_end)
);

CREATE INDEX idx_perf_model ON analytics.prediction_performance (model_id, window_end DESC);
CREATE INDEX idx_perf_window ON analytics.prediction_performance (window_start, window_end);
CREATE INDEX idx_perf_roi ON analytics.prediction_performance (roi DESC);
CREATE INDEX idx_perf_segments_gin ON analytics.prediction_performance 
  USING GIN (performance_by_spread_range, performance_by_confidence);

-- Feature importance tracking
CREATE TABLE IF NOT EXISTS analytics.feature_importance (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  model_id TEXT NOT NULL,
  model_version TEXT NOT NULL,
  
  -- Feature details
  feature_name TEXT NOT NULL,
  feature_group TEXT,
  
  -- Importance metrics
  importance_score NUMERIC(8,6) NOT NULL,
  importance_rank INTEGER,
  importance_percentile NUMERIC(5,2),
  
  -- SHAP values
  shap_mean_abs NUMERIC(8,6),
  shap_std NUMERIC(8,6),
  
  -- Computation metadata
  training_date DATE NOT NULL,
  training_sample_size INTEGER,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  
  UNIQUE (model_id, model_version, feature_name, training_date)
);

CREATE INDEX idx_feature_imp_model ON analytics.feature_importance (model_id, model_version);
CREATE INDEX idx_feature_imp_rank ON analytics.feature_importance (model_id, importance_rank);
CREATE INDEX idx_feature_imp_group ON analytics.feature_importance (feature_group);

-- Daily pipeline runs tracking
CREATE TABLE IF NOT EXISTS analytics.daily_pipeline_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_date DATE NOT NULL,
  run_start TIMESTAMPTZ NOT NULL,
  run_end TIMESTAMPTZ,
  
  -- Pipeline stages
  stage TEXT NOT NULL CHECK (stage IN ('ingestion', 'feature_engineering', 'prediction', 'upload', 'complete')),
  status TEXT NOT NULL CHECK (status IN ('running', 'success', 'failure', 'partial')),
  
  -- Metrics
  games_processed INTEGER,
  predictions_generated INTEGER,
  errors_count INTEGER,
  warnings_count INTEGER,
  
  -- Details
  error_details JSONB,
  performance_metrics JSONB,
  
  -- Metadata
  triggered_by TEXT,
  git_commit TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_pipeline_runs_date ON analytics.daily_pipeline_runs (run_date DESC);
CREATE INDEX idx_pipeline_runs_status ON analytics.daily_pipeline_runs (status) 
  WHERE status != 'success';
CREATE INDEX idx_pipeline_runs_stage ON analytics.daily_pipeline_runs (stage, status);

-- =====================================================================
-- TRIGGERS & FUNCTIONS
-- =====================================================================

-- Auto-update timestamps
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply to tables with updated_at
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_trigger WHERE tgname = 'update_teams_updated_at') THEN
    CREATE TRIGGER update_teams_updated_at
      BEFORE UPDATE ON public.teams
      FOR EACH ROW
      EXECUTE FUNCTION update_updated_at_column();
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_trigger WHERE tgname = 'update_games_updated_at') THEN
    CREATE TRIGGER update_games_updated_at
      BEFORE UPDATE ON public.games
      FOR EACH ROW
      EXECUTE FUNCTION update_updated_at_column();
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_trigger WHERE tgname = 'update_model_registry_updated_at') THEN
    CREATE TRIGGER update_model_registry_updated_at
      BEFORE UPDATE ON public.model_registry
      FOR EACH ROW
      EXECUTE FUNCTION update_updated_at_column();
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_trigger WHERE tgname = 'update_predictions_updated_at') THEN
    CREATE TRIGGER update_predictions_updated_at
      BEFORE UPDATE ON public.predictions
      FOR EACH ROW
      EXECUTE FUNCTION update_updated_at_column();
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_trigger WHERE tgname = 'update_bet_ledger_updated_at') THEN
    CREATE TRIGGER update_bet_ledger_updated_at
      BEFORE UPDATE ON public.bet_ledger
      FOR EACH ROW
      EXECUTE FUNCTION update_updated_at_column();
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_trigger WHERE tgname = 'update_injuries_updated_at') THEN
    CREATE TRIGGER update_injuries_updated_at
      BEFORE UPDATE ON public.injuries
      FOR EACH ROW
      EXECUTE FUNCTION update_updated_at_column();
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_trigger WHERE tgname = 'update_team_boxscores_updated_at') THEN
    CREATE TRIGGER update_team_boxscores_updated_at
      BEFORE UPDATE ON public.team_boxscores
      FOR EACH ROW
      EXECUTE FUNCTION update_updated_at_column();
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_trigger WHERE tgname = 'update_raw_games_updated_at') THEN
    CREATE TRIGGER update_raw_games_updated_at
      BEFORE UPDATE ON raw.raw_games
      FOR EACH ROW
      EXECUTE FUNCTION update_updated_at_column();
  END IF;
END $$;

-- Utility function: Calculate edge tier
CREATE OR REPLACE FUNCTION calculate_edge_tier(
  p_edge_magnitude NUMERIC,
  p_confidence NUMERIC
)
RETURNS TEXT AS $$
BEGIN
  IF p_edge_magnitude >= 6.0 AND p_confidence >= 0.75 THEN
    RETURN 'tier1';
  ELSIF p_edge_magnitude >= 4.0 AND p_confidence >= 0.65 THEN
    RETURN 'tier2';
  ELSIF p_edge_magnitude >= 2.5 AND p_confidence >= 0.55 THEN
    RETURN 'tier3';
  ELSE
    RETURN NULL;
  END IF;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- =====================================================================
-- RLS POLICIES
-- =====================================================================

-- Enable RLS on analytics tables
ALTER TABLE analytics.team_game_metrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE analytics.team_rolling_metrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE analytics.team_opponent_metrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE analytics.team_strength_of_schedule ENABLE ROW LEVEL SECURITY;
ALTER TABLE analytics.prediction_performance ENABLE ROW LEVEL SECURITY;
ALTER TABLE analytics.feature_importance ENABLE ROW LEVEL SECURITY;
ALTER TABLE analytics.daily_pipeline_runs ENABLE ROW LEVEL SECURITY;

-- Public read access for analytics tables
DROP POLICY IF EXISTS team_metrics_read_all ON analytics.team_game_metrics;
CREATE POLICY team_metrics_read_all ON analytics.team_game_metrics
  FOR SELECT TO anon, authenticated USING (true);

DROP POLICY IF EXISTS rolling_metrics_read_all ON analytics.team_rolling_metrics;
CREATE POLICY rolling_metrics_read_all ON analytics.team_rolling_metrics
  FOR SELECT TO anon, authenticated USING (true);

DROP POLICY IF EXISTS opponent_metrics_read_all ON analytics.team_opponent_metrics;
CREATE POLICY opponent_metrics_read_all ON analytics.team_opponent_metrics
  FOR SELECT TO anon, authenticated USING (true);

DROP POLICY IF EXISTS sos_read_all ON analytics.team_strength_of_schedule;
CREATE POLICY sos_read_all ON analytics.team_strength_of_schedule
  FOR SELECT TO anon, authenticated USING (true);

DROP POLICY IF EXISTS perf_read_all ON analytics.prediction_performance;
CREATE POLICY perf_read_all ON analytics.prediction_performance
  FOR SELECT TO anon, authenticated USING (true);

DROP POLICY IF EXISTS feature_imp_read_all ON analytics.feature_importance;
CREATE POLICY feature_imp_read_all ON analytics.feature_importance
  FOR SELECT TO anon, authenticated USING (true);

DROP POLICY IF EXISTS pipeline_runs_read_all ON analytics.daily_pipeline_runs;
CREATE POLICY pipeline_runs_read_all ON analytics.daily_pipeline_runs
  FOR SELECT TO anon, authenticated USING (true);

-- Enable RLS on new tables
ALTER TABLE public.player_boxscores ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.injuries ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS player_boxscores_read_all ON public.player_boxscores;
CREATE POLICY player_boxscores_read_all ON public.player_boxscores
  FOR SELECT TO anon, authenticated USING (true);

DROP POLICY IF EXISTS injuries_read_all ON public.injuries;
CREATE POLICY injuries_read_all ON public.injuries
  FOR SELECT TO anon, authenticated USING (true);

-- Enable RLS on raw.barttorvik_teams
ALTER TABLE raw.barttorvik_teams ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS raw_barttorvik_read ON raw.barttorvik_teams;
CREATE POLICY raw_barttorvik_read ON raw.barttorvik_teams
  FOR SELECT TO authenticated USING (true);

-- =====================================================================
-- COMMENTS (Documentation)
-- =====================================================================

COMMENT ON SCHEMA raw IS 'Raw ingestion layer: immutable source data with full provenance';
COMMENT ON SCHEMA analytics IS 'Computed metrics layer: regenerable from raw data';

COMMENT ON TABLE raw.raw_games IS 'Complete API payloads from all sources (ESPN, NCAA, Henry, Barttorvik)';
COMMENT ON TABLE raw.barttorvik_teams IS 'Pre-computed advanced metrics from Barttorvik (external source)';

COMMENT ON TABLE public.teams IS 'Canonical team reference table with mappings to all external sources';
COMMENT ON TABLE public.games IS 'Canonical game schedule and results';
COMMENT ON TABLE public.market_lines IS 'Time-series tracking of Vegas/sportsbook betting lines';
COMMENT ON TABLE public.player_boxscores IS 'Player-level box score statistics';
COMMENT ON TABLE public.injuries IS 'Player injury status tracking with time-series history';

COMMENT ON TABLE analytics.team_game_metrics IS 'Derived efficiency metrics computed from raw box scores';
COMMENT ON TABLE analytics.team_rolling_metrics IS 'Rolling window statistics (L3, L5, L7, L10, season)';
COMMENT ON TABLE analytics.team_opponent_metrics IS 'Opponent-adjusted performance metrics';
COMMENT ON TABLE analytics.team_strength_of_schedule IS 'Strength of schedule calculations';
COMMENT ON TABLE analytics.prediction_performance IS 'Model performance tracking and backtesting results';
COMMENT ON TABLE analytics.feature_importance IS 'ML model feature importance tracking';
COMMENT ON TABLE analytics.daily_pipeline_runs IS 'Data pipeline execution health monitoring';

-- =====================================================================
-- COMPLETE
-- =====================================================================
