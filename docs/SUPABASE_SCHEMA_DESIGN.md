# Comprehensive Supabase Schema Design for CBB Betting Model

## Table of Contents
1. [Overview & Design Philosophy](#overview--design-philosophy)
2. [Schema Architecture](#schema-architecture)
3. [Complete Table Definitions](#complete-table-definitions)
4. [Row Level Security Policies](#row-level-security-policies)
5. [Database Rules & Constraints](#database-rules--constraints)
6. [Edge Functions & Triggers](#edge-functions--triggers)
7. [Indexes & Performance Optimization](#indexes--performance-optimization)
8. [Design Decisions & Rationale](#design-decisions--rationale)

---

## Overview & Design Philosophy

This schema is optimized for a **college basketball betting prediction system** that:
- Ingests data from multiple sources (ESPN, NCAA, Barttorvik, Henry API)
- Performs aggressive normalization to separate raw data from derived calculations
- Maintains data integrity with comprehensive foreign key relationships
- Supports high-query performance with strategic indexing
- Implements row-level security for multi-tenant access patterns

### Core Design Principles

1. **Aggressive Normalization**: Raw ingested data and computed/aggregated values never coexist in the same table
2. **Clear Separation of Concerns**: Distinct schemas for raw ingestion (`raw`), production data (`public`), and analytics (`analytics`)
3. **Audit Trail**: All data changes tracked with timestamps and verification status
4. **Multi-Source Integrity**: Supports data from multiple APIs with conflict resolution
5. **Query Performance**: Strategic denormalization only where read performance demands it

---

## Schema Architecture

### Three-Schema Strategy

```
┌─────────────────────────────────────────────────────────────┐
│ raw schema (Ingestion Layer)                                │
│ - Raw API responses (JSON payloads)                         │
│ - Source-specific tables (ESPN, NCAA, Barttorvik)          │
│ - Minimal transformation, maximum fidelity                   │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ public schema (Application Layer)                           │
│ - Normalized, cleaned data                                  │
│ - Foreign key relationships enforced                         │
│ - Ready for application consumption                          │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ analytics schema (Computed Layer)                           │
│ - Aggregated statistics                                     │
│ - Rolling windows & derived metrics                          │
│ - Pre-computed views for performance                         │
└─────────────────────────────────────────────────────────────┘
```

---

## Complete Table Definitions

### RAW Schema (Ingestion Layer)

#### 1. `raw.raw_games`
**Purpose**: Store complete raw JSON payloads from source APIs with provenance tracking

```sql
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
```

**Design Notes**:
- JSONB allows full fidelity storage of source API responses
- `verification_status` enables multi-source conflict resolution
- Unique constraint prevents duplicate ingestion from same source
- Partial index on verification_status for efficient monitoring of unresolved conflicts

---

#### 2. `raw.espn_team_game_core`
**Purpose**: Streamlined raw team-game primitives (box score stats only, no derived metrics)

```sql
CREATE TABLE raw.espn_team_game_core (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id TEXT NOT NULL,
  team_id TEXT NOT NULL,
  team TEXT,
  home_away TEXT NOT NULL CHECK (home_away IN ('home', 'away')),
  game_datetime_utc TIMESTAMPTZ NOT NULL,
  
  -- Score primitives (raw outcomes)
  points_for DOUBLE PRECISION,
  points_against DOUBLE PRECISION,
  
  -- Box score primitives (counting stats only)
  fgm DOUBLE PRECISION,
  fga DOUBLE PRECISION,
  tpm DOUBLE PRECISION,  -- Three-pointers made
  tpa DOUBLE PRECISION,  -- Three-pointers attempted
  ftm DOUBLE PRECISION,
  fta DOUBLE PRECISION,
  tov DOUBLE PRECISION,  -- Turnovers
  orb DOUBLE PRECISION,  -- Offensive rebounds
  drb DOUBLE PRECISION,  -- Defensive rebounds
  ast DOUBLE PRECISION,  -- Assists
  stl DOUBLE PRECISION,  -- Steals
  blk DOUBLE PRECISION,  -- Blocks
  pf DOUBLE PRECISION,   -- Personal fouls
  
  -- Metadata
  pulled_at_utc TIMESTAMPTZ NOT NULL,
  source TEXT NOT NULL DEFAULT 'ESPN',
  parse_version TEXT NOT NULL,
  verification_status TEXT NOT NULL DEFAULT 'partial'
    CHECK (verification_status IN ('verified', 'partial', 'conflict', 'rejected')),
  verification_notes TEXT,
  
  UNIQUE (event_id, team_id, home_away)
);

CREATE INDEX idx_espn_team_core_event ON raw.espn_team_game_core (event_id);
CREATE INDEX idx_espn_team_core_team_dt ON raw.espn_team_game_core (team_id, game_datetime_utc);
CREATE INDEX idx_espn_team_core_pulled ON raw.espn_team_game_core (pulled_at_utc DESC);
```

**Design Notes**:
- Contains ONLY raw counting stats from box scores
- NO derived metrics (eFG%, ORtg, DRtg, pace) - those belong in analytics schema
- Enables recalculation of all derived metrics if formulas change
- Double precision for statistical calculations

---

#### 3. `raw.espn_player_boxscores`
**Purpose**: Per-player per-game statistics from ESPN

```sql
CREATE TABLE raw.espn_player_boxscores (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  row_hash TEXT NOT NULL UNIQUE,  -- Content-based deduplication
  event_id TEXT NOT NULL,
  game_datetime_utc TIMESTAMPTZ NOT NULL,
  team_id TEXT NOT NULL,
  team TEXT,
  home_away TEXT NOT NULL CHECK (home_away IN ('home', 'away')),
  athlete_id TEXT NOT NULL,
  player TEXT,
  starter TEXT,  -- 'Yes'/'No' from ESPN
  
  -- Per-player box score stats
  min DOUBLE PRECISION,  -- Minutes played
  pts DOUBLE PRECISION,
  fgm DOUBLE PRECISION,
  fga DOUBLE PRECISION,
  tpm DOUBLE PRECISION,
  tpa DOUBLE PRECISION,
  ftm DOUBLE PRECISION,
  fta DOUBLE PRECISION,
  reb DOUBLE PRECISION,
  orb DOUBLE PRECISION,
  drb DOUBLE PRECISION,
  ast DOUBLE PRECISION,
  stl DOUBLE PRECISION,
  blk DOUBLE PRECISION,
  tov DOUBLE PRECISION,
  pf DOUBLE PRECISION,
  
  -- Metadata
  pulled_at_utc TIMESTAMPTZ NOT NULL,
  source TEXT NOT NULL DEFAULT 'ESPN',
  parse_version TEXT NOT NULL,
  
  UNIQUE (event_id, team_id, athlete_id)
);

CREATE INDEX idx_espn_player_event ON raw.espn_player_boxscores (event_id);
CREATE INDEX idx_espn_player_athlete ON raw.espn_player_boxscores (athlete_id);
CREATE INDEX idx_espn_player_team_dt ON raw.espn_player_boxscores (team_id, game_datetime_utc);
CREATE INDEX idx_espn_player_pulled ON raw.espn_player_boxscores (pulled_at_utc DESC);
```

**Design Notes**:
- `row_hash` enables idempotent ingestion (reprocessing same data won't create duplicates)
- Player performance can be aggregated for team-level features or individual analysis
- Enables future player-level modeling without schema changes

---

#### 4. `raw.espn_teams`
**Purpose**: Team reference data from ESPN

```sql
CREATE TABLE raw.espn_teams (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  row_hash TEXT NOT NULL UNIQUE,
  espn_id TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  abbreviation TEXT,
  display_name TEXT,
  short_name TEXT,
  mascot TEXT,
  logo TEXT,  -- URL to team logo
  conference TEXT,
  
  -- Metadata
  pulled_at_utc TIMESTAMPTZ NOT NULL,
  source TEXT NOT NULL DEFAULT 'ESPN',
  parse_version TEXT NOT NULL,
  verification_status TEXT NOT NULL DEFAULT 'partial'
    CHECK (verification_status IN ('verified', 'partial', 'conflict', 'rejected')),
  verification_notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_espn_teams_name ON raw.espn_teams (name);
CREATE INDEX idx_espn_teams_source_pulled ON raw.espn_teams (source, pulled_at_utc DESC);
```

**Design Notes**:
- Tracks team metadata evolution over time (conference changes, rebranding)
- Multiple rows per team across seasons allows historical analysis

---

#### 5. `raw.espn_injuries`
**Purpose**: Player injury status tracking

```sql
CREATE TABLE raw.espn_injuries (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  row_hash TEXT NOT NULL UNIQUE,
  team_id TEXT NOT NULL,
  team TEXT,
  athlete_id TEXT NOT NULL,
  player TEXT,
  position TEXT,
  status TEXT NOT NULL,  -- 'Out', 'Questionable', 'Probable', 'Day-To-Day'
  injury_type TEXT,      -- 'Ankle', 'Knee', 'Concussion', etc.
  detail TEXT,           -- Human-readable description
  side TEXT,             -- 'Left', 'Right'
  return_date TEXT,      -- Expected return date (if known)
  
  -- Metadata
  pulled_at_utc TIMESTAMPTZ NOT NULL,
  source TEXT NOT NULL DEFAULT 'ESPN',
  parse_version TEXT NOT NULL,
  verification_status TEXT NOT NULL DEFAULT 'partial'
    CHECK (verification_status IN ('verified', 'partial', 'conflict', 'rejected')),
  verification_notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  
  UNIQUE (team_id, athlete_id, status, pulled_at_utc)
);

CREATE INDEX idx_espn_injuries_team ON raw.espn_injuries (team_id);
CREATE INDEX idx_espn_injuries_athlete ON raw.espn_injuries (athlete_id);
CREATE INDEX idx_espn_injuries_pulled ON raw.espn_injuries (pulled_at_utc DESC);
CREATE INDEX idx_espn_injuries_status ON raw.espn_injuries (status);
```

**Design Notes**:
- Time-series injury tracking enables retrospective analysis of injury impact
- Critical for understanding performance variance in predictions

---

#### 6. `raw.espn_dq_audit`
**Purpose**: Data quality audit trail for repairs and validations

```sql
CREATE TABLE raw.espn_dq_audit (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  row_hash TEXT NOT NULL UNIQUE,
  event_id TEXT NOT NULL,
  team_id TEXT NOT NULL,
  team TEXT,
  home_away TEXT CHECK (home_away IN ('home', 'away')),
  dq_missing_fields TEXT,        -- Comma-separated list of missing fields
  dq_reason_codes TEXT,          -- Structured reason codes
  dq_action_plan TEXT,           -- What repair was attempted
  dq_repair_success BOOLEAN,     -- Did repair succeed?
  dq_repair_actions_taken TEXT,  -- What was done
  
  -- Metadata
  pulled_at_utc TIMESTAMPTZ NOT NULL,
  source TEXT NOT NULL DEFAULT 'ESPN',
  parse_version TEXT NOT NULL,
  verification_status TEXT NOT NULL DEFAULT 'partial'
    CHECK (verification_status IN ('verified', 'partial', 'conflict', 'rejected')),
  verification_notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_espn_dq_event_team ON raw.espn_dq_audit (event_id, team_id);
CREATE INDEX idx_espn_dq_pulled ON raw.espn_dq_audit (pulled_at_utc DESC);
CREATE INDEX idx_espn_dq_repair_success ON raw.espn_dq_audit (dq_repair_success) 
  WHERE dq_repair_success = FALSE;
```

**Design Notes**:
- Enables monitoring of data quality trends over time
- Partial index on failed repairs for operations monitoring

---

#### 7. `raw.espn_feature_diagnostics`
**Purpose**: Diagnostic logging for feature engineering issues

```sql
CREATE TABLE raw.espn_feature_diagnostics (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  row_hash TEXT NOT NULL UNIQUE,
  event_id TEXT NOT NULL,
  team_id TEXT NOT NULL,
  team TEXT,
  diagnostic_reason TEXT NOT NULL,  -- Why this game was flagged
  diagnostic_details JSONB,         -- Structured diagnostic data
  
  -- Metadata
  pulled_at_utc TIMESTAMPTZ NOT NULL,
  source TEXT NOT NULL DEFAULT 'ESPN',
  parse_version TEXT NOT NULL,
  verification_status TEXT NOT NULL DEFAULT 'partial'
    CHECK (verification_status IN ('verified', 'partial', 'conflict', 'rejected')),
  verification_notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  
  UNIQUE (event_id, team_id, diagnostic_reason, pulled_at_utc)
);

CREATE INDEX idx_espn_diag_event_team ON raw.espn_feature_diagnostics (event_id, team_id);
CREATE INDEX idx_espn_diag_pulled ON raw.espn_feature_diagnostics (pulled_at_utc DESC);
CREATE INDEX idx_espn_diag_reason ON raw.espn_feature_diagnostics (diagnostic_reason);
```

**Design Notes**:
- Helps identify systematic issues in feature engineering pipeline
- JSONB details field allows flexible diagnostic data structure

---

#### 8. `raw.ncaa_team_game_logs`
**Purpose**: NCAA Casablanca API team-game data

```sql
CREATE TABLE raw.ncaa_team_game_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  row_hash TEXT NOT NULL UNIQUE,
  game_id TEXT NOT NULL,
  team TEXT NOT NULL,
  opponent TEXT NOT NULL,
  home_away TEXT NOT NULL CHECK (home_away IN ('home', 'away')),
  game_date DATE,
  game_datetime TIMESTAMPTZ NOT NULL,
  venue TEXT,
  
  -- Box score stats (raw counts only)
  points_for INTEGER,
  points_against INTEGER,
  margin INTEGER,
  fgm INTEGER,
  fga INTEGER,
  fg_pct NUMERIC(5,3),  -- Store as percentage for NCAA compatibility
  tpm INTEGER,
  tpa INTEGER,
  tp_pct NUMERIC(5,3),
  ftm INTEGER,
  fta INTEGER,
  ft_pct NUMERIC(5,3),
  reb INTEGER,
  orb INTEGER,
  drb INTEGER,
  ast INTEGER,
  stl INTEGER,
  blk INTEGER,
  tov INTEGER,
  pf INTEGER,
  
  -- Metadata
  pulled_at_utc TIMESTAMPTZ NOT NULL,
  source TEXT NOT NULL DEFAULT 'NCAA',
  parse_version TEXT NOT NULL,
  verification_status TEXT NOT NULL DEFAULT 'partial'
    CHECK (verification_status IN ('verified', 'partial', 'conflict', 'rejected')),
  verification_notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  
  UNIQUE (game_id, team, home_away)
);

CREATE INDEX idx_ncaa_logs_game ON raw.ncaa_team_game_logs (game_id);
CREATE INDEX idx_ncaa_logs_team_dt ON raw.ncaa_team_game_logs (team, game_datetime);
CREATE INDEX idx_ncaa_logs_date ON raw.ncaa_team_game_logs (game_date);
```

**Design Notes**:
- Parallel structure to ESPN data enables cross-validation
- `verification_status` used for conflict resolution when ESPN and NCAA disagree

---

#### 9. `raw.ncaa_games`
**Purpose**: NCAA game reference data

```sql
CREATE TABLE raw.ncaa_games (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  row_hash TEXT NOT NULL UNIQUE,
  game_id TEXT NOT NULL UNIQUE,
  date DATE,
  game_datetime TIMESTAMPTZ NOT NULL,
  home_team TEXT NOT NULL,
  away_team TEXT NOT NULL,
  home_score INTEGER,
  away_score INTEGER,
  status TEXT,
  venue TEXT,
  
  -- Metadata
  pulled_at_utc TIMESTAMPTZ NOT NULL,
  source TEXT NOT NULL DEFAULT 'NCAA',
  parse_version TEXT NOT NULL,
  verification_status TEXT NOT NULL DEFAULT 'partial'
    CHECK (verification_status IN ('verified', 'partial', 'conflict', 'rejected')),
  verification_notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_ncaa_games_datetime ON raw.ncaa_games (game_datetime);
CREATE INDEX idx_ncaa_games_date ON raw.ncaa_games (date);
CREATE INDEX idx_ncaa_games_teams ON raw.ncaa_games (home_team, away_team);
```

---

#### 10. `raw.ncaa_player_boxscores`
**Purpose**: Per-player stats from NCAA API

```sql
CREATE TABLE raw.ncaa_player_boxscores (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  row_hash TEXT NOT NULL UNIQUE,
  game_id TEXT NOT NULL,
  team TEXT NOT NULL,
  player_name TEXT NOT NULL,
  player_id TEXT,
  starter BOOLEAN,
  minutes NUMERIC(4,1),  -- Can have fractional minutes
  
  -- Box score stats
  points INTEGER,
  fgm INTEGER,
  fga INTEGER,
  fg_pct NUMERIC(5,3),
  tpm INTEGER,
  tpa INTEGER,
  tp_pct NUMERIC(5,3),
  ftm INTEGER,
  fta INTEGER,
  ft_pct NUMERIC(5,3),
  reb INTEGER,
  orb INTEGER,
  drb INTEGER,
  ast INTEGER,
  stl INTEGER,
  blk INTEGER,
  tov INTEGER,
  pf INTEGER,
  
  -- Metadata
  pulled_at_utc TIMESTAMPTZ NOT NULL,
  source TEXT NOT NULL DEFAULT 'NCAA',
  parse_version TEXT NOT NULL,
  verification_status TEXT NOT NULL DEFAULT 'partial'
    CHECK (verification_status IN ('verified', 'partial', 'conflict', 'rejected')),
  verification_notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  
  UNIQUE (game_id, team, player_name)
);

CREATE INDEX idx_ncaa_player_game ON raw.ncaa_player_boxscores (game_id);
CREATE INDEX idx_ncaa_player_id ON raw.ncaa_player_boxscores (player_id);
CREATE INDEX idx_ncaa_player_team ON raw.ncaa_player_boxscores (team);
```

---

#### 11. `raw.barttorvik_teams`
**Purpose**: Pre-computed advanced metrics from Barttorvik

```sql
CREATE TABLE raw.barttorvik_teams (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  row_hash TEXT NOT NULL UNIQUE,
  season INTEGER NOT NULL,
  team TEXT NOT NULL,
  
  -- Barttorvik advanced metrics (pre-computed by external source)
  adj_oe NUMERIC(6,2),    -- Adjusted Offensive Efficiency
  adj_de NUMERIC(6,2),    -- Adjusted Defensive Efficiency
  adj_em NUMERIC(6,2),    -- Adjusted Efficiency Margin
  barthag NUMERIC(5,4),   -- Power rating (Barttorvik version)
  sos_adj_em NUMERIC(6,2), -- Strength of Schedule (Efficiency Margin)
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
```

**Design Notes**:
- These are EXTERNALLY computed metrics (not derived by us)
- Stored in raw schema because they're ingested, not calculated
- Used as input features to our models

---

#### 12. `raw.haslametrics`
**Purpose**: HaslaMetrics advanced stats (if used)

```sql
CREATE TABLE raw.haslametrics (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  row_hash TEXT NOT NULL UNIQUE,
  season INTEGER NOT NULL,
  team TEXT NOT NULL,
  
  -- Store as JSONB for flexibility (HaslaMetrics schema varies)
  metrics JSONB NOT NULL,
  
  -- Metadata
  pulled_at_utc TIMESTAMPTZ NOT NULL,
  source TEXT NOT NULL DEFAULT 'HASLA',
  parse_version TEXT NOT NULL,
  verification_status TEXT NOT NULL DEFAULT 'partial'
    CHECK (verification_status IN ('verified', 'partial', 'conflict', 'rejected')),
  verification_notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  
  UNIQUE (season, team, pulled_at_utc)
);

CREATE INDEX idx_hasla_season_team ON raw.haslametrics (season, team);
CREATE INDEX idx_hasla_pulled ON raw.haslametrics (pulled_at_utc DESC);
```

---

#### 13. `raw.predictions_latest`
**Purpose**: ML model raw output before enrichment

```sql
CREATE TABLE raw.predictions_latest (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  model_id TEXT NOT NULL,
  model_version TEXT NOT NULL,
  event_id TEXT NOT NULL,
  game_datetime_utc TIMESTAMPTZ NOT NULL,
  home_team TEXT NOT NULL,
  away_team TEXT NOT NULL,
  
  -- Raw model outputs (before Vegas line comparison)
  pred_spread NUMERIC(6,2),
  pred_total NUMERIC(6,2),
  win_prob_home NUMERIC(5,4),
  
  -- Model metadata
  model_confidence NUMERIC(5,4),
  model_inputs JSONB,  -- Feature vector used
  
  -- Provenance
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  prediction_run_id UUID,  -- Links multiple predictions from same run
  
  UNIQUE (model_id, event_id, created_at)
);

CREATE INDEX idx_predictions_latest_event ON raw.predictions_latest (event_id);
CREATE INDEX idx_predictions_latest_datetime ON raw.predictions_latest (game_datetime_utc);
CREATE INDEX idx_predictions_latest_run ON raw.predictions_latest (prediction_run_id);
```

**Design Notes**:
- Raw model outputs BEFORE enrichment with market lines
- Multiple models can predict same game
- `prediction_run_id` enables batch tracking

---

### PUBLIC Schema (Application Layer)

#### 14. `public.teams`
**Purpose**: Canonical team reference table

```sql
CREATE TABLE public.teams (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  season INTEGER NOT NULL,
  
  -- Source identifiers (for join-backs to raw data)
  espn_team_id TEXT,
  ncaa_team_id TEXT,
  barttorvik_team_name TEXT,
  
  -- Canonical team name (resolved from multiple sources)
  team_name TEXT NOT NULL,
  team_abbreviation TEXT,
  display_name TEXT,
  short_name TEXT,
  mascot TEXT,
  logo_url TEXT,
  
  -- Conference affiliation
  conference TEXT,
  division TEXT,  -- e.g., 'I' for Division I
  
  -- Metadata
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  
  UNIQUE (season, espn_team_id),
  UNIQUE (season, team_name),
  UNIQUE (season, ncaa_team_id)
);

CREATE INDEX idx_teams_season ON public.teams (season);
CREATE INDEX idx_teams_name ON public.teams (team_name);
CREATE INDEX idx_teams_conference ON public.teams (season, conference);
```

**Design Notes**:
- Single source of truth for team identity
- Maps external IDs from all sources to canonical ID
- Season-scoped (team names/conferences can change year-over-year)

---

#### 15. `public.games`
**Purpose**: Canonical game schedule and results

```sql
CREATE TABLE public.games (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  season INTEGER NOT NULL,
  
  -- Game identification
  espn_game_id TEXT,
  ncaa_game_id TEXT,
  game_datetime_utc TIMESTAMPTZ NOT NULL,
  
  -- Team references (properly normalized)
  home_team_id UUID NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
  away_team_id UUID NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
  
  -- Game outcome (NULL until game completes)
  home_score INTEGER,
  away_score INTEGER,
  margin INTEGER GENERATED ALWAYS AS (home_score - away_score) STORED,
  
  -- Game status
  status TEXT NOT NULL DEFAULT 'scheduled'
    CHECK (status IN ('scheduled', 'in_progress', 'final', 'postponed', 'cancelled')),
  
  -- Venue
  venue_name TEXT,
  venue_city TEXT,
  venue_state TEXT,
  neutral_site BOOLEAN DEFAULT FALSE,
  
  -- Game type
  game_type TEXT DEFAULT 'regular'
    CHECK (game_type IN ('regular', 'conference_tournament', 'ncaa_tournament', 'exhibition')),
  tournament TEXT,  -- Tournament name if applicable
  tournament_round TEXT,  -- Round if tournament game
  
  -- Multi-source verification
  verification_status TEXT NOT NULL DEFAULT 'partial'
    CHECK (verification_status IN ('verified', 'partial', 'conflict', 'rejected')),
  verification_notes TEXT,
  
  -- Metadata
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  
  UNIQUE (season, espn_game_id),
  UNIQUE (season, ncaa_game_id),
  UNIQUE (season, game_datetime_utc, home_team_id, away_team_id),
  
  CHECK (home_team_id != away_team_id)  -- Can't play yourself
);

CREATE INDEX idx_games_season_datetime ON public.games (season, game_datetime_utc);
CREATE INDEX idx_games_home_team ON public.games (home_team_id, game_datetime_utc);
CREATE INDEX idx_games_away_team ON public.games (away_team_id, game_datetime_utc);
CREATE INDEX idx_games_status ON public.games (status) WHERE status != 'final';
CREATE INDEX idx_games_tournament ON public.games (season, tournament) WHERE tournament IS NOT NULL;
```

**Design Notes**:
- Computed column for `margin` ensures consistency
- Foreign keys to `teams` table ensure referential integrity
- Check constraint prevents impossible matchups
- Partial index on non-final games for "upcoming games" queries

---

#### 16. `public.market_lines`
**Purpose**: Vegas/sportsbook betting lines (time-series)

```sql
CREATE TABLE public.market_lines (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  game_id UUID NOT NULL REFERENCES public.games(id) ON DELETE CASCADE,
  
  -- Line source
  book TEXT NOT NULL,  -- 'consensus', 'draftkings', 'fanduel', etc.
  line_type TEXT NOT NULL DEFAULT 'pregame'
    CHECK (line_type IN ('pregame', 'live', 'closing')),
  
  -- Lines (from home team perspective)
  spread_home NUMERIC(5,2),
  spread_juice_home INTEGER,  -- e.g., -110
  spread_juice_away INTEGER,
  
  total NUMERIC(5,1),
  total_over_juice INTEGER,
  total_under_juice INTEGER,
  
  moneyline_home INTEGER,
  moneyline_away INTEGER,
  
  -- Timestamp (critical for line movement tracking)
  pulled_at TIMESTAMPTZ NOT NULL,
  
  -- Metadata
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  
  UNIQUE (game_id, book, line_type, pulled_at)
);

CREATE INDEX idx_market_lines_game ON public.market_lines (game_id, pulled_at DESC);
CREATE INDEX idx_market_lines_pulled ON public.market_lines (pulled_at DESC);
CREATE INDEX idx_market_lines_book ON public.market_lines (book);
CREATE INDEX idx_market_lines_closing ON public.market_lines (game_id, line_type) 
  WHERE line_type = 'closing';
```

**Design Notes**:
- Time-series tracking enables line movement analysis
- Separate juice fields for accurately calculating no-vig lines
- `line_type` distinguishes pregame vs closing vs live odds
- Partial index on closing lines for backtesting queries

---

#### 17. `public.team_boxscores`
**Purpose**: Normalized team-level box scores (resolved from raw sources)

```sql
CREATE TABLE public.team_boxscores (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  game_id UUID NOT NULL REFERENCES public.games(id) ON DELETE CASCADE,
  team_id UUID NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
  home_away TEXT NOT NULL CHECK (home_away IN ('home', 'away')),
  
  -- Box score stats (raw counts only - NO DERIVED METRICS)
  points INTEGER NOT NULL,
  fgm INTEGER,
  fga INTEGER,
  tpm INTEGER,  -- Three-pointers made
  tpa INTEGER,
  ftm INTEGER,
  fta INTEGER,
  orb INTEGER,
  drb INTEGER,
  reb INTEGER GENERATED ALWAYS AS (COALESCE(orb, 0) + COALESCE(drb, 0)) STORED,
  ast INTEGER,
  stl INTEGER,
  blk INTEGER,
  tov INTEGER,
  pf INTEGER,
  
  -- Source tracking
  source TEXT NOT NULL CHECK (source IN ('ESPN', 'NCAA', 'RESOLVED')),
  verification_status TEXT NOT NULL DEFAULT 'partial'
    CHECK (verification_status IN ('verified', 'partial', 'conflict', 'rejected')),
  verification_notes TEXT,
  
  -- Metadata
  pulled_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  
  UNIQUE (game_id, team_id)
);

CREATE INDEX idx_team_boxscores_game ON public.team_boxscores (game_id);
CREATE INDEX idx_team_boxscores_team ON public.team_boxscores (team_id);
CREATE INDEX idx_team_boxscores_source ON public.team_boxscores (source);
```

**Design Notes**:
- Contains ONLY raw counting stats
- `reb` computed column for convenience
- `source = 'RESOLVED'` when multiple sources reconciled
- NO efficiency metrics (eFG%, ORtg, etc.) - those belong in `analytics.team_game_metrics`

---

#### 18. `public.player_boxscores`
**Purpose**: Normalized player-level box scores

```sql
CREATE TABLE public.player_boxscores (
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
  seconds_played INTEGER GENERATED ALWAYS AS (ROUND(minutes_played * 60)) STORED,
  
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
  reb INTEGER GENERATED ALWAYS AS (COALESCE(orb, 0) + COALESCE(drb, 0)) STORED,
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
```

**Design Notes**:
- Separate from team boxscores (normalization)
- Computed columns for convenience (`reb`, `seconds_played`)
- Enables player-level analysis and lineup optimization features

---

#### 19. `public.injuries`
**Purpose**: Current injury status (resolved from raw sources with time-series history)

```sql
CREATE TABLE public.injuries (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  team_id UUID NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
  
  -- Player identity
  espn_athlete_id TEXT,
  player_name TEXT NOT NULL,
  position TEXT,
  
  -- Injury details
  status TEXT NOT NULL CHECK (status IN ('Out', 'Doubtful', 'Questionable', 'Probable', 'Day-To-Day')),
  injury_type TEXT,  -- Body part: 'Ankle', 'Knee', 'Concussion'
  detail TEXT,
  side TEXT CHECK (side IN ('Left', 'Right', NULL)),
  return_date DATE,
  
  -- Status tracking
  injury_start_date DATE,
  injury_end_date DATE,  -- NULL if still injured
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
```

**Design Notes**:
- Time-bounded injury records enable historical analysis
- `is_active` flag for current injuries
- Enables injury-adjusted predictions

---

#### 20. `public.model_registry`
**Purpose**: Model definitions and configurations

```sql
CREATE TABLE public.model_registry (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  model_id TEXT NOT NULL UNIQUE,
  model_name TEXT NOT NULL,
  model_type TEXT NOT NULL CHECK (model_type IN ('formula', 'ml', 'ensemble', 'external')),
  model_category TEXT NOT NULL CHECK (model_category IN ('spread', 'total', 'margin', 'win_prob')),
  
  -- Model configuration
  params JSONB NOT NULL DEFAULT '{}'::JSONB,  -- Model-specific parameters
  feature_set TEXT,  -- Reference to feature definition
  weights JSONB,     -- For ensemble models
  
  -- Model versioning
  model_version TEXT,
  parent_model_id TEXT REFERENCES public.model_registry(model_id),  -- For model lineage
  
  -- Status
  is_active BOOLEAN NOT NULL DEFAULT FALSE,
  is_production BOOLEAN NOT NULL DEFAULT FALSE,
  
  -- Performance tracking (populated by backtest jobs)
  backtest_mae NUMERIC(6,4),
  backtest_rmse NUMERIC(6,4),
  backtest_r2 NUMERIC(6,4),
  backtest_roi NUMERIC(6,4),
  backtest_win_rate NUMERIC(5,4),
  backtest_sample_size INTEGER,
  backtest_date_range DATERANGE,
  
  -- Metadata
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_by TEXT,  -- User ID or system identifier
  notes TEXT
);

CREATE INDEX idx_model_registry_type ON public.model_registry (model_type, model_category);
CREATE INDEX idx_model_registry_active ON public.model_registry (is_active, model_category);
CREATE INDEX idx_model_registry_production ON public.model_registry (is_production);
CREATE INDEX idx_model_registry_version ON public.model_registry (model_id, model_version);
```

**Design Notes**:
- Central registry for all prediction models (formula, ML, ensemble)
- Performance metrics stored here (populated by backtest jobs)
- `parent_model_id` enables tracking model lineage and A/B testing
- Only one model per category should have `is_production = TRUE` at a time

---

#### 21. `public.predictions`
**Purpose**: Enriched predictions with market comparison

```sql
CREATE TABLE public.predictions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  prediction_key TEXT NOT NULL UNIQUE,  -- Composite: model_version + game_id + created_at
  
  -- Model reference
  model_version_id UUID NOT NULL REFERENCES public.model_registry(id) ON DELETE CASCADE,
  model_id TEXT NOT NULL,  -- Denormalized for query performance
  
  -- Game reference
  game_id UUID NOT NULL REFERENCES public.games(id) ON DELETE CASCADE,
  event_id TEXT NOT NULL,  -- External ID for join-backs
  game_date DATE NOT NULL,
  game_datetime_utc TIMESTAMPTZ NOT NULL,
  home_team TEXT NOT NULL,
  away_team TEXT NOT NULL,
  
  -- Model predictions
  pred_spread NUMERIC(6,2),      -- Predicted spread (positive = home favored)
  pred_total NUMERIC(6,2),       -- Predicted total points
  win_prob_home NUMERIC(5,4),    -- Win probability for home team [0-1]
  
  -- Market comparison
  market_spread NUMERIC(5,2),    -- Closing line or latest available
  market_total NUMERIC(5,1),
  
  -- Edge calculation (model vs market)
  edge_spread NUMERIC(6,2) GENERATED ALWAYS AS (pred_spread - market_spread) STORED,
  edge_total NUMERIC(6,2) GENERATED ALWAYS AS (pred_total - market_total) STORED,
  edge_magnitude NUMERIC(6,2) GENERATED ALWAYS AS (
    GREATEST(ABS(COALESCE(edge_spread, 0)), ABS(COALESCE(edge_total, 0)))
  ) STORED,
  
  -- Recommendation (computed by betting engine)
  bet_signal BOOLEAN NOT NULL DEFAULT FALSE,
  bet_market TEXT CHECK (bet_market IN ('spread', 'total', 'moneyline', NULL)),
  bet_side TEXT,  -- 'home', 'away', 'over', 'under'
  bet_units NUMERIC(6,4),  -- Kelly Criterion sizing
  bet_tier TEXT CHECK (bet_tier IN ('tier1', 'tier2', 'tier3', NULL)),
  
  -- Confidence & metadata
  confidence NUMERIC(5,4),  -- Model confidence [0-1]
  model_inputs JSONB,       -- Feature vector snapshot
  ensemble_breakdown JSONB, -- If ensemble, show component model predictions
  
  -- Outcome tracking (populated post-game)
  actual_spread INTEGER,     -- home_score - away_score
  actual_total INTEGER,      -- home_score + away_score
  prediction_error_spread NUMERIC(6,2),
  prediction_error_total NUMERIC(6,2),
  bet_outcome TEXT CHECK (bet_outcome IN ('win', 'loss', 'push', 'no_bet', NULL)),
  bet_pnl NUMERIC(10,2),     -- Profit/loss if bet was placed
  
  -- Metadata
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_predictions_game ON public.predictions (game_id);
CREATE INDEX idx_predictions_model ON public.predictions (model_id, game_datetime_utc DESC);
CREATE INDEX idx_predictions_date ON public.predictions (game_date);
CREATE INDEX idx_predictions_signal ON public.predictions (bet_signal, game_datetime_utc) 
  WHERE bet_signal = TRUE;
CREATE INDEX idx_predictions_edge ON public.predictions (edge_magnitude DESC);
CREATE INDEX idx_predictions_key ON public.predictions (prediction_key);
```

**Design Notes**:
- Computed columns for edge calculations ensure consistency
- `prediction_key` enables idempotent prediction storage
- Outcome fields populated by post-game job for backtesting
- Partial index on `bet_signal` for "today's picks" queries

---

#### 22. `public.bet_ledger`
**Purpose**: Bet tracking for paper trading and backtesting

```sql
CREATE TABLE public.bet_ledger (
  id TEXT PRIMARY KEY,  -- Format: {run_date}_{event_id}_{market}
  
  -- Bet identification
  run_date DATE NOT NULL,      -- When prediction was generated
  game_date DATE NOT NULL,     -- When game was played
  event_id TEXT NOT NULL,
  
  -- Game details
  home_team TEXT NOT NULL,
  away_team TEXT NOT NULL,
  
  -- Bet details
  market TEXT NOT NULL CHECK (market IN ('spread', 'total', 'moneyline')),
  side TEXT NOT NULL,          -- 'home', 'away', 'over', 'under'
  units NUMERIC(6,4) NOT NULL,
  
  -- Lines
  model_value NUMERIC(6,2),    -- Our predicted value
  vegas_value NUMERIC(6,2),    -- Market line
  edge NUMERIC(6,2),           -- Our edge
  confidence NUMERIC(5,4),     -- Model confidence
  
  -- Bet outcome (populated post-game)
  result TEXT CHECK (result IN ('win', 'loss', 'push', NULL)),
  actual_score_home INTEGER,
  actual_score_away INTEGER,
  pnl NUMERIC(10,2),           -- Profit/loss in units
  
  -- Status
  recommended BOOLEAN NOT NULL DEFAULT TRUE,
  placed BOOLEAN NOT NULL DEFAULT FALSE,  -- For live betting integration
  
  -- Model tracking
  model_version TEXT,
  model_id TEXT,
  prediction_id UUID REFERENCES public.predictions(id) ON DELETE SET NULL,
  
  -- Metadata
  meta JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_bet_ledger_run_date ON public.bet_ledger (run_date);
CREATE INDEX idx_bet_ledger_game_date ON public.bet_ledger (game_date);
CREATE INDEX idx_bet_ledger_event ON public.bet_ledger (event_id);
CREATE INDEX idx_bet_ledger_result ON public.bet_ledger (result, game_date);
CREATE INDEX idx_bet_ledger_model ON public.bet_ledger (model_version, result);
```

**Design Notes**:
- Separate from predictions for clean separation of concerns
- Enables ROI tracking and bankroll simulation
- `placed` field for future live betting integration

---

#### 23. `public.dq_audit`
**Purpose**: Data quality audit trail (public-facing for app diagnostics)

```sql
CREATE TABLE public.dq_audit (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_type TEXT NOT NULL,  -- 'game', 'team', 'boxscore', 'prediction'
  entity_id UUID,
  severity TEXT NOT NULL CHECK (severity IN ('error', 'warning', 'info')),
  reason_codes TEXT[] NOT NULL,
  details JSONB NOT NULL DEFAULT '{}'::JSONB,
  resolution_status TEXT NOT NULL DEFAULT 'unresolved'
    CHECK (resolution_status IN ('unresolved', 'resolved', 'ignored', 'escalated')),
  resolution_notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  resolved_at TIMESTAMPTZ
);

CREATE INDEX idx_dq_audit_entity ON public.dq_audit (entity_type, entity_id);
CREATE INDEX idx_dq_audit_severity ON public.dq_audit (severity, resolution_status);
CREATE INDEX idx_dq_audit_created ON public.dq_audit (created_at DESC);
CREATE INDEX idx_dq_audit_unresolved ON public.dq_audit (resolution_status, created_at) 
  WHERE resolution_status = 'unresolved';
```

**Design Notes**:
- Central audit trail for data quality issues across all entities
- `reason_codes` array enables multi-dimensional filtering
- Partial index on unresolved issues for operations dashboard

---

### ANALYTICS Schema (Computed Layer)

#### 24. `analytics.team_game_metrics`
**Purpose**: Derived efficiency metrics (computed from raw box scores)

```sql
CREATE TABLE analytics.team_game_metrics (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  game_id UUID NOT NULL REFERENCES public.games(id) ON DELETE CASCADE,
  team_id UUID NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
  home_away TEXT NOT NULL CHECK (home_away IN ('home', 'away')),
  
  -- Reference to raw box score
  boxscore_id UUID NOT NULL REFERENCES public.team_boxscores(id) ON DELETE CASCADE,
  
  -- Possessions (foundational for all efficiency metrics)
  possessions NUMERIC(6,2) NOT NULL,
  
  -- Four Factors
  efg_pct NUMERIC(5,4),      -- Effective FG%: (FGM + 0.5*3PM) / FGA
  tov_pct NUMERIC(5,4),      -- Turnover %: TOV / (FGA + 0.44*FTA + TOV)
  orb_pct NUMERIC(5,4),      -- Offensive Rebound %
  drb_pct NUMERIC(5,4),      -- Defensive Rebound %
  ftr NUMERIC(5,4),          -- Free Throw Rate: FTA / FGA
  
  -- Shooting metrics
  ts_pct NUMERIC(5,4),       -- True Shooting %: PTS / (2 * (FGA + 0.44*FTA))
  three_par NUMERIC(5,4),    -- 3-Point Attempt Rate: 3PA / FGA
  
  -- Efficiency (per 100 possessions)
  ortg NUMERIC(6,2),         -- Offensive Rating: 100 * Points / Possessions
  drtg NUMERIC(6,2),         -- Defensive Rating: 100 * Opp Points / Possessions
  netrtg NUMERIC(7,2) GENERATED ALWAYS AS (ortg - drtg) STORED,
  
  -- Pace
  pace NUMERIC(6,2),         -- Possessions per 40 minutes
  
  -- Computation metadata
  computation_version TEXT NOT NULL,  -- For reproducibility
  computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  
  UNIQUE (game_id, team_id)
);

CREATE INDEX idx_team_metrics_game ON analytics.team_game_metrics (game_id);
CREATE INDEX idx_team_metrics_team ON analytics.team_game_metrics (team_id);
CREATE INDEX idx_team_metrics_boxscore ON analytics.team_game_metrics (boxscore_id);
```

**Design Notes**:
- **PURE DERIVATION**: All metrics computed from `public.team_boxscores`
- Stored for performance (avoid recomputing on every query)
- `computation_version` enables schema evolution and backfills
- Can be regenerated from raw data if formulas change

---

#### 25. `analytics.team_rolling_metrics`
**Purpose**: Rolling window statistics (L3, L5, L7, L10, season-to-date)

```sql
CREATE TABLE analytics.team_rolling_metrics (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  team_id UUID NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
  season INTEGER NOT NULL,
  as_of_date DATE NOT NULL,  -- Snapshot date (for pregame predictions)
  
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
  
  -- Variance metrics (for confidence intervals)
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
```

**Design Notes**:
- Enables fast pregame feature loading (no on-the-fly aggregation)
- `as_of_date` critical for preventing look-ahead bias
- Multiple window types in same table (normalized by `window_type`)

---

#### 26. `analytics.team_opponent_metrics`
**Purpose**: Opponent-adjusted performance (e.g., "vs expectation")

```sql
CREATE TABLE analytics.team_opponent_metrics (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  game_id UUID NOT NULL REFERENCES public.games(id) ON DELETE CASCADE,
  team_id UUID NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
  opponent_id UUID NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
  
  -- Reference to base metrics
  base_metrics_id UUID NOT NULL REFERENCES analytics.team_game_metrics(id) ON DELETE CASCADE,
  
  -- Opponent's defensive profile (as of game date)
  opponent_avg_drtg_allowed NUMERIC(6,2),  -- What the opponent typically allows
  opponent_avg_ortg_allowed NUMERIC(6,2),
  
  -- Performance vs expectation
  ortg_vs_expectation NUMERIC(7,2),  -- Our ORtg - Opponent's typical ORtg allowed
  drtg_vs_expectation NUMERIC(7,2),
  
  -- Opponent strength context
  opponent_sos_rank INTEGER,
  opponent_adj_em NUMERIC(6,2),  -- From Barttorvik or computed
  
  -- Computation metadata
  computation_version TEXT NOT NULL,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  
  UNIQUE (game_id, team_id)
);

CREATE INDEX idx_opponent_metrics_game ON analytics.team_opponent_metrics (game_id);
CREATE INDEX idx_opponent_metrics_team ON analytics.team_opponent_metrics (team_id);
CREATE INDEX idx_opponent_metrics_opponent ON analytics.team_opponent_metrics (opponent_id);
```

**Design Notes**:
- Enables context-aware features like "vs expectation" performance
- Critical for models that adjust for opponent strength

---

#### 27. `analytics.team_strength_of_schedule`
**Purpose**: Strength of schedule calculations

```sql
CREATE TABLE analytics.team_strength_of_schedule (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  team_id UUID NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
  season INTEGER NOT NULL,
  as_of_date DATE NOT NULL,
  
  -- SOS metrics
  sos_avg_opp_netrtg NUMERIC(7,2),  -- Average opponent net rating
  sos_avg_opp_adj_em NUMERIC(7,2),  -- Average opponent adjusted efficiency margin
  sos_rank INTEGER,                 -- Rank among all teams (1 = hardest schedule)
  
  -- Schedule composition
  games_vs_top25 INTEGER,
  games_vs_top50 INTEGER,
  games_vs_quadrant1 INTEGER,  -- NCAA Quadrant 1 games
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
```

**Design Notes**:
- Time-series tracking enables "schedule difficulty remaining" analysis
- Used as input feature for predictions

---

#### 28. `analytics.prediction_performance`
**Purpose**: Model performance tracking (aggregated backtest results)

```sql
CREATE TABLE analytics.prediction_performance (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  model_id TEXT NOT NULL REFERENCES public.model_registry(model_id) ON DELETE CASCADE,
  
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
  win_rate NUMERIC(5,4) GENERATED ALWAYS AS (
    CASE WHEN (wins + losses) > 0 
    THEN CAST(wins AS NUMERIC) / (wins + losses) 
    ELSE NULL END
  ) STORED,
  roi NUMERIC(7,4),             -- Return on investment
  sharpe_ratio NUMERIC(7,4),    -- Risk-adjusted return
  max_drawdown NUMERIC(7,4),
  
  -- Edge accuracy (how well did we identify edges?)
  avg_predicted_edge NUMERIC(6,2),
  avg_realized_edge NUMERIC(6,2),
  edge_correlation NUMERIC(6,4),  -- Correlation between predicted and realized edges
  
  -- Calibration (for probability predictions)
  brier_score NUMERIC(6,4),
  log_loss NUMERIC(6,4),
  
  -- Segment breakdowns (stored as JSONB for flexibility)
  performance_by_spread_range JSONB,  -- e.g., {"-3 to -1": {mae: 2.5, roi: 0.08}, ...}
  performance_by_confidence JSONB,
  performance_by_game_type JSONB,
  
  -- Computation metadata
  computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  
  UNIQUE (model_id, window_start, window_end)
);

CREATE INDEX idx_perf_model ON analytics.prediction_performance (model_id, window_end DESC);
CREATE INDEX idx_perf_window ON analytics.prediction_performance (window_start, window_end);
CREATE INDEX idx_perf_roi ON analytics.prediction_performance (roi DESC);
```

**Design Notes**:
- Aggregated view of model performance over time windows
- Used for model comparison dashboard
- JSONB segment breakdowns allow flexible slicing without schema changes

---

#### 29. `analytics.feature_importance`
**Purpose**: Track feature importance for ML models

```sql
CREATE TABLE analytics.feature_importance (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  model_id TEXT NOT NULL REFERENCES public.model_registry(model_id) ON DELETE CASCADE,
  model_version TEXT NOT NULL,
  
  -- Feature details
  feature_name TEXT NOT NULL,
  feature_group TEXT,  -- e.g., 'four_factors', 'rolling_metrics', 'opponent_adjusted'
  
  -- Importance metrics
  importance_score NUMERIC(8,6) NOT NULL,
  importance_rank INTEGER,
  importance_percentile NUMERIC(5,2),
  
  -- SHAP values (if applicable)
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
```

**Design Notes**:
- Enables model interpretability and feature selection
- Tracks importance evolution over time (as model retrains)

---

#### 30. `analytics.daily_pipeline_runs`
**Purpose**: Track data pipeline execution health

```sql
CREATE TABLE analytics.daily_pipeline_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_date DATE NOT NULL,
  run_start TIMESTAMPTZ NOT NULL,
  run_end TIMESTAMPTZ,
  duration_seconds INTEGER GENERATED ALWAYS AS (
    EXTRACT(EPOCH FROM (run_end - run_start))::INTEGER
  ) STORED,
  
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
  performance_metrics JSONB,  -- Stage-specific timings, row counts, etc.
  
  -- Metadata
  triggered_by TEXT,  -- 'cron', 'manual', 'api'
  git_commit TEXT,    -- For reproducibility
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_pipeline_runs_date ON analytics.daily_pipeline_runs (run_date DESC);
CREATE INDEX idx_pipeline_runs_status ON analytics.daily_pipeline_runs (status) 
  WHERE status != 'success';
CREATE INDEX idx_pipeline_runs_stage ON analytics.daily_pipeline_runs (stage, status);
```

**Design Notes**:
- Operations monitoring and debugging
- Performance tracking for pipeline optimization
- Partial index on failures for alerting

---

## Row Level Security Policies

### Authentication Roles

```sql
-- Define custom roles (if not using Supabase auth defaults)
DO $$ 
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'anon') THEN
    CREATE ROLE anon;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'authenticated') THEN
    CREATE ROLE authenticated;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'service_role') THEN
    CREATE ROLE service_role;
  END IF;
END $$;
```

### Policy Philosophy

1. **anon (unauthenticated users)**: Read-only access to public-facing data (teams, games, predictions, market lines)
2. **authenticated**: Full read access, limited write access (bet_ledger for tracking paper trades)
3. **service_role**: Full access for backend services and data pipelines

### RAW Schema Policies

```sql
-- Enable RLS on all raw tables
ALTER TABLE raw.raw_games ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw.espn_team_game_core ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw.espn_player_boxscores ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw.espn_teams ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw.espn_injuries ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw.espn_dq_audit ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw.espn_feature_diagnostics ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw.ncaa_team_game_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw.ncaa_games ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw.ncaa_player_boxscores ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw.barttorvik_teams ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw.haslametrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw.predictions_latest ENABLE ROW LEVEL SECURITY;

-- RAW schema: authenticated read-only (service_role has implicit full access)
CREATE POLICY "raw_read_authenticated" ON raw.raw_games
  FOR SELECT TO authenticated USING (true);

CREATE POLICY "raw_espn_core_read" ON raw.espn_team_game_core
  FOR SELECT TO authenticated USING (true);

CREATE POLICY "raw_espn_player_read" ON raw.espn_player_boxscores
  FOR SELECT TO authenticated USING (true);

CREATE POLICY "raw_espn_teams_read" ON raw.espn_teams
  FOR SELECT TO authenticated USING (true);

CREATE POLICY "raw_espn_injuries_read" ON raw.espn_injuries
  FOR SELECT TO authenticated USING (true);

CREATE POLICY "raw_espn_dq_read" ON raw.espn_dq_audit
  FOR SELECT TO authenticated USING (true);

CREATE POLICY "raw_espn_diag_read" ON raw.espn_feature_diagnostics
  FOR SELECT TO authenticated USING (true);

CREATE POLICY "raw_ncaa_logs_read" ON raw.ncaa_team_game_logs
  FOR SELECT TO authenticated USING (true);

CREATE POLICY "raw_ncaa_games_read" ON raw.ncaa_games
  FOR SELECT TO authenticated USING (true);

CREATE POLICY "raw_ncaa_player_read" ON raw.ncaa_player_boxscores
  FOR SELECT TO authenticated USING (true);

CREATE POLICY "raw_barttorvik_read" ON raw.barttorvik_teams
  FOR SELECT TO authenticated USING (true);

CREATE POLICY "raw_hasla_read" ON raw.haslametrics
  FOR SELECT TO authenticated USING (true);

CREATE POLICY "raw_predictions_read" ON raw.predictions_latest
  FOR SELECT TO authenticated USING (true);
```

### PUBLIC Schema Policies

```sql
-- Enable RLS on all public tables
ALTER TABLE public.teams ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.games ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.market_lines ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.team_boxscores ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.player_boxscores ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.injuries ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.model_registry ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.predictions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.bet_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.dq_audit ENABLE ROW LEVEL SECURITY;

-- Public read access for core reference data
CREATE POLICY "teams_read_all" ON public.teams
  FOR SELECT TO anon, authenticated USING (true);

CREATE POLICY "games_read_all" ON public.games
  FOR SELECT TO anon, authenticated USING (true);

CREATE POLICY "market_lines_read_all" ON public.market_lines
  FOR SELECT TO anon, authenticated USING (true);

CREATE POLICY "team_boxscores_read_all" ON public.team_boxscores
  FOR SELECT TO anon, authenticated USING (true);

CREATE POLICY "player_boxscores_read_all" ON public.player_boxscores
  FOR SELECT TO anon, authenticated USING (true);

CREATE POLICY "injuries_read_all" ON public.injuries
  FOR SELECT TO anon, authenticated USING (true);

CREATE POLICY "model_registry_read_all" ON public.model_registry
  FOR SELECT TO anon, authenticated USING (true);

CREATE POLICY "predictions_read_all" ON public.predictions
  FOR SELECT TO anon, authenticated USING (true);

-- Bet ledger: read all, write for authenticated users only
CREATE POLICY "bet_ledger_read_all" ON public.bet_ledger
  FOR SELECT TO anon, authenticated USING (true);

CREATE POLICY "bet_ledger_write_authenticated" ON public.bet_ledger
  FOR INSERT TO authenticated WITH CHECK (true);

CREATE POLICY "bet_ledger_update_authenticated" ON public.bet_ledger
  FOR UPDATE TO authenticated USING (true) WITH CHECK (true);

-- DQ audit: read all (transparency)
CREATE POLICY "dq_audit_read_all" ON public.dq_audit
  FOR SELECT TO anon, authenticated USING (true);
```

### ANALYTICS Schema Policies

```sql
-- Enable RLS on all analytics tables
ALTER TABLE analytics.team_game_metrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE analytics.team_rolling_metrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE analytics.team_opponent_metrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE analytics.team_strength_of_schedule ENABLE ROW LEVEL SECURITY;
ALTER TABLE analytics.prediction_performance ENABLE ROW LEVEL SECURITY;
ALTER TABLE analytics.feature_importance ENABLE ROW LEVEL SECURITY;
ALTER TABLE analytics.daily_pipeline_runs ENABLE ROW LEVEL SECURITY;

-- Public read access (computed metrics are public)
CREATE POLICY "team_metrics_read_all" ON analytics.team_game_metrics
  FOR SELECT TO anon, authenticated USING (true);

CREATE POLICY "rolling_metrics_read_all" ON analytics.team_rolling_metrics
  FOR SELECT TO anon, authenticated USING (true);

CREATE POLICY "opponent_metrics_read_all" ON analytics.team_opponent_metrics
  FOR SELECT TO anon, authenticated USING (true);

CREATE POLICY "sos_read_all" ON analytics.team_strength_of_schedule
  FOR SELECT TO anon, authenticated USING (true);

CREATE POLICY "perf_read_all" ON analytics.prediction_performance
  FOR SELECT TO anon, authenticated USING (true);

CREATE POLICY "feature_imp_read_all" ON analytics.feature_importance
  FOR SELECT TO anon, authenticated USING (true);

CREATE POLICY "pipeline_runs_read_all" ON analytics.daily_pipeline_runs
  FOR SELECT TO anon, authenticated USING (true);
```

---

## Database Rules & Constraints

### Nullability Rules

**Core principle**: Required fields for data integrity should be NOT NULL. Optional/future fields can be NULL.

Applied throughout schema:
- Primary keys: `NOT NULL` (enforced by `PRIMARY KEY`)
- Foreign keys: `NOT NULL` (except when optional relationships)
- Timestamps: `NOT NULL DEFAULT NOW()`
- Status fields: `NOT NULL` with CHECK constraints
- Statistical fields: `NULL` allowed (may not always be calculable)

### Uniqueness Constraints

**Multi-column unique constraints** prevent duplicate ingestion:
```sql
-- Example from raw.raw_games
UNIQUE (season, source, external_game_id)

-- Example from public.games
UNIQUE (season, game_datetime_utc, home_team_id, away_team_id)

-- Example from public.market_lines
UNIQUE (game_id, book, line_type, pulled_at)
```

### Check Constraints

**Enumerated values** for data consistency:
```sql
-- Status fields
CHECK (status IN ('scheduled', 'in_progress', 'final', 'postponed', 'cancelled'))

-- Home/away designation
CHECK (home_away IN ('home', 'away'))

-- Verification status
CHECK (verification_status IN ('verified', 'partial', 'conflict', 'rejected'))

-- Severity levels
CHECK (severity IN ('error', 'warning', 'info'))
```

**Logical constraints**:
```sql
-- Team can't play itself
CHECK (home_team_id != away_team_id)

-- Probabilities must be between 0 and 1
CHECK (win_prob_home >= 0 AND win_prob_home <= 1)

-- Confidence must be between 0 and 1
CHECK (confidence >= 0 AND confidence <= 1)
```

### Default Values

**Timestamps**:
```sql
created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
```

**Status fields**:
```sql
status TEXT NOT NULL DEFAULT 'scheduled'
verification_status TEXT NOT NULL DEFAULT 'partial'
is_active BOOLEAN NOT NULL DEFAULT TRUE
```

**JSONB fields**:
```sql
params JSONB NOT NULL DEFAULT '{}'::JSONB
details JSONB NOT NULL DEFAULT '{}'::JSONB
```

### Foreign Key Actions

**Cascade deletions** where appropriate:
```sql
-- When a game is deleted, delete all related data
game_id UUID REFERENCES public.games(id) ON DELETE CASCADE

-- When a team is deleted, delete all related data
team_id UUID REFERENCES public.teams(id) ON DELETE CASCADE
```

**Set NULL** for optional references:
```sql
-- When a prediction is deleted, don't delete bet ledger entries
prediction_id UUID REFERENCES public.predictions(id) ON DELETE SET NULL
```

---

## Edge Functions & Triggers

### 1. Auto-Update Timestamps Trigger

**Purpose**: Automatically update `updated_at` on row modification

```sql
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply to all tables with updated_at
CREATE TRIGGER update_teams_updated_at
  BEFORE UPDATE ON public.teams
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_games_updated_at
  BEFORE UPDATE ON public.games
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_model_registry_updated_at
  BEFORE UPDATE ON public.model_registry
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_predictions_updated_at
  BEFORE UPDATE ON public.predictions
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_bet_ledger_updated_at
  BEFORE UPDATE ON public.bet_ledger
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_injuries_updated_at
  BEFORE UPDATE ON public.injuries
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();
```

---

### 2. Compute Game Margin on Score Update

**Purpose**: Automatically compute margin when scores are entered

```sql
CREATE OR REPLACE FUNCTION compute_game_margin()
RETURNS TRIGGER AS $$
BEGIN
  -- Margin is already a GENERATED column, so this is just for validation
  IF NEW.home_score IS NOT NULL AND NEW.away_score IS NOT NULL THEN
    IF NEW.status = 'final' THEN
      -- Mark as verified when both scores present and game is final
      NEW.verification_status = 'verified';
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER compute_game_margin_trigger
  BEFORE INSERT OR UPDATE ON public.games
  FOR EACH ROW
  EXECUTE FUNCTION compute_game_margin();
```

---

### 3. Populate Prediction Outcomes Post-Game

**Purpose**: Automatically populate prediction outcomes when game completes

```sql
CREATE OR REPLACE FUNCTION populate_prediction_outcomes()
RETURNS TRIGGER AS $$
BEGIN
  -- Only run when game status changes to 'final' and scores are present
  IF NEW.status = 'final' AND NEW.home_score IS NOT NULL AND NEW.away_score IS NOT NULL
     AND (OLD.status IS NULL OR OLD.status != 'final') THEN
    
    -- Update all predictions for this game
    UPDATE public.predictions
    SET
      actual_spread = NEW.home_score - NEW.away_score,
      actual_total = NEW.home_score + NEW.away_score,
      prediction_error_spread = (NEW.home_score - NEW.away_score) - pred_spread,
      prediction_error_total = (NEW.home_score + NEW.away_score) - pred_total,
      updated_at = NOW()
    WHERE game_id = NEW.id;
    
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER populate_prediction_outcomes_trigger
  AFTER UPDATE ON public.games
  FOR EACH ROW
  EXECUTE FUNCTION populate_prediction_outcomes();
```

---

### 4. Compute Bet Outcomes Post-Game

**Purpose**: Determine win/loss/push for bet ledger entries

```sql
CREATE OR REPLACE FUNCTION compute_bet_outcomes()
RETURNS TRIGGER AS $$
DECLARE
  bet RECORD;
  outcome TEXT;
  pnl_value NUMERIC;
BEGIN
  -- Only run when game status changes to 'final'
  IF NEW.status = 'final' AND NEW.home_score IS NOT NULL AND NEW.away_score IS NOT NULL
     AND (OLD.status IS NULL OR OLD.status != 'final') THEN
    
    -- Update all bet ledger entries for this game
    FOR bet IN 
      SELECT * FROM public.bet_ledger 
      WHERE event_id = NEW.espn_game_id AND result IS NULL
    LOOP
      -- Compute outcome based on market type
      IF bet.market = 'spread' THEN
        DECLARE
          actual_spread NUMERIC := NEW.home_score - NEW.away_score;
          line_value NUMERIC := bet.vegas_value;
          cover_margin NUMERIC;
        BEGIN
          IF bet.side = 'home' THEN
            cover_margin := actual_spread - line_value;
          ELSE  -- away
            cover_margin := line_value - actual_spread;
          END IF;
          
          IF ABS(cover_margin) < 0.01 THEN
            outcome := 'push';
            pnl_value := 0;
          ELSIF cover_margin > 0 THEN
            outcome := 'win';
            pnl_value := bet.units * 0.909;  -- Standard -110 juice
          ELSE
            outcome := 'loss';
            pnl_value := -bet.units;
          END IF;
        END;
        
      ELSIF bet.market = 'total' THEN
        DECLARE
          actual_total NUMERIC := NEW.home_score + NEW.away_score;
          line_value NUMERIC := bet.vegas_value;
        BEGIN
          IF ABS(actual_total - line_value) < 0.01 THEN
            outcome := 'push';
            pnl_value := 0;
          ELSIF (bet.side = 'over' AND actual_total > line_value) OR
                (bet.side = 'under' AND actual_total < line_value) THEN
            outcome := 'win';
            pnl_value := bet.units * 0.909;
          ELSE
            outcome := 'loss';
            pnl_value := -bet.units;
          END IF;
        END;
        
      ELSIF bet.market = 'moneyline' THEN
        DECLARE
          home_won BOOLEAN := NEW.home_score > NEW.away_score;
        BEGIN
          IF (bet.side = 'home' AND home_won) OR (bet.side = 'away' AND NOT home_won) THEN
            outcome := 'win';
            -- Calculate ML payout (simplified - would need actual odds)
            pnl_value := bet.units;
          ELSE
            outcome := 'loss';
            pnl_value := -bet.units;
          END IF;
        END;
      END IF;
      
      -- Update bet ledger entry
      UPDATE public.bet_ledger
      SET
        result = outcome,
        pnl = pnl_value,
        actual_score_home = NEW.home_score,
        actual_score_away = NEW.away_score,
        updated_at = NOW()
      WHERE id = bet.id;
      
    END LOOP;
    
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER compute_bet_outcomes_trigger
  AFTER UPDATE ON public.games
  FOR EACH ROW
  EXECUTE FUNCTION compute_bet_outcomes();
```

---

### 5. Validate Only One Production Model Per Category

**Purpose**: Ensure only one model is marked as production for each category

```sql
CREATE OR REPLACE FUNCTION enforce_single_production_model()
RETURNS TRIGGER AS $$
BEGIN
  -- If setting is_production to TRUE
  IF NEW.is_production = TRUE THEN
    -- Set all other models in same category to is_production = FALSE
    UPDATE public.model_registry
    SET is_production = FALSE
    WHERE model_category = NEW.model_category
      AND id != NEW.id
      AND is_production = TRUE;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER enforce_single_production_model_trigger
  BEFORE INSERT OR UPDATE ON public.model_registry
  FOR EACH ROW
  WHEN (NEW.is_production = TRUE)
  EXECUTE FUNCTION enforce_single_production_model();
```

---

### 6. Auto-Compute Possessions for Team Box Scores

**Purpose**: Calculate possessions when box score is inserted (enables immediate metric computation)

```sql
CREATE OR REPLACE FUNCTION compute_possessions()
RETURNS TRIGGER AS $$
BEGIN
  -- Store as extended property or in analytics table
  -- Possessions formula: FGA - ORB + TOV + 0.44*FTA
  -- This is a placeholder - actual implementation would write to analytics.team_game_metrics
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER compute_possessions_trigger
  AFTER INSERT ON public.team_boxscores
  FOR EACH ROW
  EXECUTE FUNCTION compute_possessions();
```

---

### 7. Deactivate Old Injuries When New Status Updates

**Purpose**: Close old injury records when player status changes

```sql
CREATE OR REPLACE FUNCTION deactivate_old_injuries()
RETURNS TRIGGER AS $$
BEGIN
  -- When a new injury record is inserted for a player
  -- Deactivate all previous active injuries for that player
  UPDATE public.injuries
  SET
    is_active = FALSE,
    injury_end_date = NEW.injury_start_date,
    updated_at = NOW()
  WHERE espn_athlete_id = NEW.espn_athlete_id
    AND team_id = NEW.team_id
    AND is_active = TRUE
    AND id != NEW.id;
  
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER deactivate_old_injuries_trigger
  AFTER INSERT ON public.injuries
  FOR EACH ROW
  EXECUTE FUNCTION deactivate_old_injuries();
```

---

### 8. Materialized View Refresh Function

**Purpose**: Scheduled refresh of materialized views for performance

```sql
-- Create materialized view for latest team metrics
CREATE MATERIALIZED VIEW IF NOT EXISTS analytics.latest_team_metrics AS
SELECT
  tr.team_id,
  t.team_name,
  t.season,
  tr.as_of_date,
  tr.avg_ortg,
  tr.avg_drtg,
  tr.avg_netrtg,
  tr.avg_pace,
  tr.avg_efg,
  tr.avg_tov_pct,
  tr.avg_orb_pct,
  tr.avg_drb_pct
FROM analytics.team_rolling_metrics tr
JOIN public.teams t ON tr.team_id = t.id
WHERE tr.window_type = 'L7'
  AND tr.as_of_date = (
    SELECT MAX(as_of_date)
    FROM analytics.team_rolling_metrics
    WHERE team_id = tr.team_id AND window_type = 'L7'
  );

CREATE UNIQUE INDEX ON analytics.latest_team_metrics (team_id);

-- Refresh function (called by cron or manually)
CREATE OR REPLACE FUNCTION refresh_latest_team_metrics()
RETURNS VOID AS $$
BEGIN
  REFRESH MATERIALIZED VIEW CONCURRENTLY analytics.latest_team_metrics;
END;
$$ LANGUAGE plpgsql;
```

---

### 9. Database Function: Get Pregame Features

**Purpose**: Retrieve feature vector for a team as of a specific date (for predictions)

```sql
CREATE OR REPLACE FUNCTION get_pregame_features(
  p_team_id UUID,
  p_as_of_date DATE
)
RETURNS JSONB AS $$
DECLARE
  result JSONB;
BEGIN
  SELECT jsonb_build_object(
    'team_id', tr.team_id,
    'as_of_date', tr.as_of_date,
    'l7_ortg', tr.avg_ortg,
    'l7_drtg', tr.avg_drtg,
    'l7_netrtg', tr.avg_netrtg,
    'l7_pace', tr.avg_pace,
    'l7_efg', tr.avg_efg,
    'l7_tov_pct', tr.avg_tov_pct,
    'l7_orb_pct', tr.avg_orb_pct,
    'l7_drb_pct', tr.avg_drb_pct,
    'l7_ftr', tr.avg_ftr,
    'sos_rank', sos.sos_rank,
    'sos_avg_opp_netrtg', sos.sos_avg_opp_netrtg
  ) INTO result
  FROM analytics.team_rolling_metrics tr
  LEFT JOIN analytics.team_strength_of_schedule sos 
    ON tr.team_id = sos.team_id AND tr.as_of_date = sos.as_of_date
  WHERE tr.team_id = p_team_id
    AND tr.window_type = 'L7'
    AND tr.as_of_date = p_as_of_date;
  
  RETURN result;
END;
$$ LANGUAGE plpgsql;
```

---

### 10. Database Function: Calculate Edge Tier

**Purpose**: Categorize predictions into betting tiers based on edge magnitude

```sql
CREATE OR REPLACE FUNCTION calculate_edge_tier(
  p_edge_magnitude NUMERIC,
  p_confidence NUMERIC
)
RETURNS TEXT AS $$
BEGIN
  -- Tier 1: High edge, high confidence
  IF p_edge_magnitude >= 6.0 AND p_confidence >= 0.75 THEN
    RETURN 'tier1';
  -- Tier 2: Medium edge, good confidence
  ELSIF p_edge_magnitude >= 4.0 AND p_confidence >= 0.65 THEN
    RETURN 'tier2';
  -- Tier 3: Small edge, decent confidence
  ELSIF p_edge_magnitude >= 2.5 AND p_confidence >= 0.55 THEN
    RETURN 'tier3';
  ELSE
    RETURN NULL;
  END IF;
END;
$$ LANGUAGE plpgsql IMMUTABLE;
```

---

## Indexes & Performance Optimization

### Index Strategy

1. **Primary Keys**: Automatically indexed
2. **Foreign Keys**: Always indexed for join performance
3. **Timestamp Columns**: Indexed for time-range queries (especially `DESC` for "latest" queries)
4. **Status/Boolean Filters**: Partial indexes on non-default values
5. **Composite Indexes**: For common multi-column queries
6. **Covering Indexes**: For frequently accessed read-only queries

### Partial Indexes

**Rationale**: Index only rows that are frequently queried

```sql
-- Index only non-final games (most queries are for upcoming games)
CREATE INDEX idx_games_status_partial ON public.games (status) 
WHERE status != 'final';

-- Index only active injuries
CREATE INDEX idx_injuries_active ON public.injuries (team_id, is_active) 
WHERE is_active = TRUE;

-- Index only unresolved DQ issues
CREATE INDEX idx_dq_audit_unresolved ON public.dq_audit (resolution_status, created_at) 
WHERE resolution_status = 'unresolved';

-- Index only bet signals (most predictions don't trigger bets)
CREATE INDEX idx_predictions_signal ON public.predictions (bet_signal, game_datetime_utc) 
WHERE bet_signal = TRUE;

-- Index only production models
CREATE INDEX idx_model_registry_production ON public.model_registry (is_production) 
WHERE is_production = TRUE;
```

### Composite Indexes

**Rationale**: Optimize multi-column queries

```sql
-- Team + date queries (common for rolling metrics)
CREATE INDEX idx_rolling_metrics_team_date ON analytics.team_rolling_metrics (team_id, as_of_date DESC);

-- Game lookups by team and date
CREATE INDEX idx_games_home_team_date ON public.games (home_team_id, game_datetime_utc DESC);
CREATE INDEX idx_games_away_team_date ON public.games (away_team_id, game_datetime_utc DESC);

-- Season + team queries
CREATE INDEX idx_teams_season_name ON public.teams (season, team_name);

-- Model performance queries
CREATE INDEX idx_predictions_model_date ON public.predictions (model_id, game_datetime_utc DESC);

-- Bet ledger performance queries
CREATE INDEX idx_bet_ledger_model_result ON public.bet_ledger (model_version, result);
```

### GIN Indexes for JSONB

**Rationale**: Enable fast querying of JSONB columns

```sql
-- Model params querying
CREATE INDEX idx_model_registry_params_gin ON public.model_registry USING GIN (params);

-- Prediction model_inputs querying
CREATE INDEX idx_predictions_inputs_gin ON public.predictions USING GIN (model_inputs);

-- Performance segment breakdowns
CREATE INDEX idx_perf_segments_gin ON analytics.prediction_performance 
USING GIN (performance_by_spread_range, performance_by_confidence);
```

### Expression Indexes

**Rationale**: Index computed values for filter queries

```sql
-- Index game date (extracted from datetime)
CREATE INDEX idx_games_date ON public.games ((game_datetime_utc::DATE));

-- Index predictions by game date
CREATE INDEX idx_predictions_game_date ON public.predictions ((game_datetime_utc::DATE));

-- Lowercase team names for case-insensitive search
CREATE INDEX idx_teams_name_lower ON public.teams (LOWER(team_name));
```

---

## Design Decisions & Rationale

### 1. Why Three Schemas (raw, public, analytics)?

**Separation of concerns**:
- `raw`: Immutable source data with full provenance
- `public`: Normalized application data with enforced relationships
- `analytics`: Computed metrics that can be regenerated

**Benefits**:
- Clear data lineage
- Safe to recompute analytics without touching source data
- Easier debugging (inspect raw payloads when issues arise)
- Enables schema evolution (change analytics without re-ingesting)

---

### 2. Why JSONB for Raw Payloads?

**Flexibility + performance**:
- Source APIs change schemas frequently
- JSONB allows schema evolution without migrations
- GIN indexes enable fast querying
- Can extract new fields retroactively from historical payloads

**Trade-offs accepted**:
- Less type safety than structured columns
- Requires careful validation in application layer
- Offset by: `verification_status` tracking and structured extraction to `public` schema

---

### 3. Why Separate Box Scores from Efficiency Metrics?

**Principle: Raw data ≠ derived calculations**

**Benefits**:
- Formula changes don't require re-ingestion
- Multiple metric versions can coexist (A/B testing formulas)
- Clear audit trail (which formula version produced which metrics)
- Efficiency metrics can be regenerated from scratch if needed

---

### 4. Why Time-Series Market Lines?

**Line movement is signal**:
- Closing line is most efficient price (better baseline than opening line)
- Line movement indicates sharp money vs public action
- Enables "closing line value" backtest metric (gold standard in betting)

**Schema design**:
- `pulled_at` timestamp enables time-series tracking
- `line_type` distinguishes pregame vs closing vs live
- Unique constraint on `(game_id, book, line_type, pulled_at)` prevents duplicates

---

### 5. Why Separate `predictions` and `bet_ledger`?

**Different domains**:
- **Predictions**: Model output (analytical)
- **Bet Ledger**: Bet tracking (operational)

**Benefits**:
- Multiple models can predict same game → multiple prediction rows
- Bet ledger tracks "bets actually placed" (subset of predictions)
- Clean separation for backtesting vs live betting
- Enables Kelly Criterion adjustments without modifying prediction history

---

### 6. Why `verification_status` on Almost Everything?

**Multi-source integrity**:
- System ingests from ESPN, NCAA, Barttorvik, etc.
- Sources sometimes conflict (different scores, different timestamps)
- `verification_status` enables conflict resolution workflows

**Workflow**:
1. Ingest from source A → `status = 'partial'`
2. Ingest from source B → detect conflict → `status = 'conflict'`
3. Resolution logic (manual or automated) → `status = 'verified'`

---

### 7. Why Computed Columns (`GENERATED ALWAYS AS`)?

**Consistency + performance**:
- Margin, reb, edge calculations always in sync
- No risk of stale computed values
- Indexed like regular columns (fast queries)

**When to use**:
- Deterministic calculations from same-row data
- Avoid for complex calculations (use analytics schema instead)

---

### 8. Why `row_hash` for Deduplication?

**Idempotent ingestion**:
- Hash of row content (deterministic)
- Re-running same ingestion won't create duplicates
- Enables "upsert" logic: if hash exists, skip or update

**Implementation**:
```sql
row_hash = SHA256(CONCAT(event_id, team_id, points_for, ...))
```

---

### 9. Why Separate Player Box Scores?

**Normalization**:
- Team box scores are aggregated (one row per team per game)
- Player box scores are granular (N rows per game)
- Separate tables prevent bloat and enable player-level analysis

**Future-proofing**:
- Enables player prop betting models
- Enables lineup optimization
- Enables injury impact analysis

---

### 10. Why Materialized Views for Latest Metrics?

**Query performance**:
- "Latest L7 metrics for all teams" is a common query
- Without materialization: full table scan + window function
- With materialization: index lookup (instant)

**Refresh strategy**:
- Scheduled refresh (e.g., daily after feature engineering runs)
- `CONCURRENTLY` flag allows reads during refresh

---

### 11. Why Enum-Style CHECK Constraints?

**Data quality**:
- Prevents typos (`'scheduled'` vs `'schedueled'`)
- Application-level validation can be bypassed (SQL can't)
- Clear documentation (constraint shows valid values)

**Trade-offs**:
- Adding new enum value requires migration
- Offset by: rarely-changing enums only (status, source, etc.)

---

### 12. Why Foreign Key `ON DELETE CASCADE`?

**Referential integrity**:
- If a game is deleted, all related data should be cleaned up
- Prevents orphaned records
- Simplifies data management

**When to use**:
- Child records have no independent meaning without parent
- **When NOT to use**: Optional references (use `ON DELETE SET NULL`)

---

### Summary

This schema is designed for:
✅ **Aggressive normalization** (raw ≠ derived)
✅ **Multi-source integrity** (verification statuses)
✅ **Query performance** (strategic indexes + materialized views)
✅ **Audit trails** (timestamps + provenance tracking)
✅ **Schema evolution** (JSONB flexibility where appropriate)
✅ **Data quality** (constraints + triggers)
✅ **Scalability** (partitioning-ready with season fields)

**Next Steps**:
1. Implement schema via migrations
2. Build ingestion pipelines
3. Create analytics computation jobs
4. Implement monitoring dashboard (using `dq_audit` and `daily_pipeline_runs`)
