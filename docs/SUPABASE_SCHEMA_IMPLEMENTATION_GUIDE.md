# Supabase Schema Implementation Guide

## Overview

This guide provides instructions for implementing the comprehensive Supabase schema design for the CBB Betting Model.

## Files Created

1. **`docs/SUPABASE_SCHEMA_DESIGN.md`** - Complete schema design documentation (79KB)
   - Full table definitions with all columns, types, and constraints
   - Row Level Security (RLS) policies
   - Database rules and constraints
   - Edge functions and triggers
   - Indexes and performance optimization
   - Design rationale and decisions

2. **`supabase/migrations/20260318000000_complete_schema_design.sql`** - Implementation migration (46KB)
   - Creates all new tables and columns
   - Adds indexes for query performance
   - Sets up triggers for automatic timestamp updates
   - Implements RLS policies
   - Adds table comments for documentation

## Schema Architecture

### Three-Schema Strategy

```
┌─────────────────────────────────────┐
│ raw schema                          │
│ - Immutable source data             │
│ - Full API payloads (JSONB)         │
│ - Multi-source integrity tracking   │
└─────────────────────────────────────┘
              ↓
┌─────────────────────────────────────┐
│ public schema                       │
│ - Normalized application data       │
│ - Foreign key relationships         │
│ - Ready for app consumption         │
└─────────────────────────────────────┘
              ↓
┌─────────────────────────────────────┐
│ analytics schema                    │
│ - Computed metrics                  │
│ - Rolling windows                   │
│ - Performance tracking              │
└─────────────────────────────────────┘
```

## Key Design Principles

### 1. Aggressive Normalization
- **Raw data ≠ Derived calculations**: Box score stats live in `public.team_boxscores`, while efficiency metrics (ORtg, DRtg, eFG%) live in `analytics.team_game_metrics`
- **Each table has one clear purpose**: No bloated tables with mixed concerns
- **Foreign keys enforce relationships**: All relationships are explicit

### 2. Multi-Source Integrity
- `verification_status` column on all ingested tables
- Supports data from ESPN, NCAA Casablanca, Henry API, Barttorvik
- Conflict resolution workflows enabled

### 3. Query Performance
- Strategic indexes on frequently queried columns
- Partial indexes on filtered queries (e.g., only active injuries, only non-final games)
- Composite indexes for common multi-column queries
- GIN indexes for JSONB columns

### 4. Audit Trails
- All tables have `created_at` and `updated_at` timestamps
- `pulled_at` timestamps track when data was ingested
- `verification_notes` for human-readable tracking

## Implementation Steps

### Step 1: Backup Existing Database (CRITICAL)

```bash
# Export current database schema and data
pg_dump -h <host> -U <user> -d <database> -F c -b -v -f backup_before_migration.dump

# Or use Supabase dashboard: Database → Backups
```

### Step 2: Run Migration

#### Option A: Via Supabase CLI

```bash
# Install Supabase CLI if needed
npm install -g supabase

# Link to your project
supabase link --project-ref <your-project-ref>

# Run migration
supabase db push
```

#### Option B: Via SQL Editor in Supabase Dashboard

1. Go to **SQL Editor** in Supabase Dashboard
2. Create new query
3. Copy contents of `supabase/migrations/20260318000000_complete_schema_design.sql`
4. Click **Run**
5. Verify no errors in output

#### Option C: Manual psql

```bash
psql -h <host> -U postgres -d postgres -f supabase/migrations/20260318000000_complete_schema_design.sql
```

### Step 3: Verify Migration

```sql
-- Check that new tables exist
SELECT schemaname, tablename 
FROM pg_tables 
WHERE schemaname IN ('raw', 'public', 'analytics')
ORDER BY schemaname, tablename;

-- Check that indexes were created
SELECT schemaname, tablename, indexname 
FROM pg_indexes 
WHERE schemaname IN ('raw', 'public', 'analytics')
ORDER BY schemaname, tablename;

-- Check RLS is enabled
SELECT schemaname, tablename, rowsecurity 
FROM pg_tables 
WHERE schemaname IN ('raw', 'public', 'analytics')
ORDER BY schemaname, tablename;

-- Check triggers
SELECT tgname, tgrelid::regclass 
FROM pg_trigger 
WHERE tgrelid::regclass::text LIKE 'public.%' OR tgrelid::regclass::text LIKE 'raw.%'
ORDER BY tgrelid::regclass;
```

### Step 4: Populate New Tables

The migration creates schema only. You'll need to populate tables with data:

#### For analytics tables (computed from existing data):

```python
# Example: Compute team game metrics from box scores
from analytics_computation import compute_team_metrics

compute_team_metrics()  # Backfill analytics.team_game_metrics
compute_rolling_metrics()  # Backfill analytics.team_rolling_metrics
```

#### For raw tables (ingestion):

```python
# Your existing ingestion pipeline should work with minor updates
# Update your ingestion scripts to write to the new tables

# Example updates needed in ESPN/espn_boxscore_builder_modular.py:
# 1. Add writes to raw.barttorvik_teams when ingesting Barttorvik data
# 2. Ensure verification_status is set appropriately
```

### Step 5: Update Application Code

#### DataLoader Updates

**Before:**
```python
# Old: Reading from public.predictions
response = client.table("predictions").select("*").execute()
```

**After:**
```python
# New: Same table, but with additional columns available
response = client.table("predictions").select(
    "*, actual_spread, prediction_error_spread, bet_outcome, bet_pnl"
).execute()
```

#### New Tables to Integrate

```python
# Load player box scores
player_stats = client.table("player_boxscores").select("*").eq("game_id", game_id).execute()

# Load active injuries
injuries = client.table("injuries").select("*").eq("team_id", team_id).eq("is_active", True).execute()

# Load rolling metrics for team
rolling_metrics = client.table("team_rolling_metrics", schema="analytics").select("*").eq("team_id", team_id).eq("window_type", "L7").order("as_of_date", desc=True).limit(1).execute()

# Load model performance
performance = client.table("prediction_performance", schema="analytics").select("*").eq("model_id", model_id).execute()
```

## New Features Enabled by Schema

### 1. Player-Level Analysis
- `public.player_boxscores` enables player prop betting models
- Track individual player performance trends
- Analyze lineup combinations

### 2. Injury-Adjusted Predictions
- `public.injuries` tracks time-series injury data
- Model can adjust for missing key players
- Analyze historical injury impact

### 3. Advanced Performance Tracking
- `analytics.prediction_performance` tracks model accuracy over time
- Segment performance by confidence, spread range, game type
- ROI, Sharpe ratio, max drawdown calculations

### 4. Feature Importance Tracking
- `analytics.feature_importance` shows which features drive predictions
- Track importance evolution over time
- Enables model interpretability

### 5. Pipeline Monitoring
- `analytics.daily_pipeline_runs` tracks data pipeline health
- Alert on failures or performance degradation
- Track data quality trends

## Maintenance & Operations

### Regular Tasks

#### Daily (Automated)
- Run data ingestion pipeline
- Compute rolling metrics for all teams
- Generate predictions
- Update bet outcomes for completed games

#### Weekly
- Backfill any missing analytics data
- Review DQ audit entries
- Check pipeline performance trends

#### Monthly
- Retrain ML models
- Update feature importance
- Compute monthly performance reports
- Review and optimize slow queries

### Monitoring Queries

```sql
-- Check for unresolved data quality issues
SELECT * FROM public.dq_audit 
WHERE resolution_status = 'unresolved' 
ORDER BY created_at DESC;

-- Check recent pipeline runs
SELECT * FROM analytics.daily_pipeline_runs 
WHERE run_date >= CURRENT_DATE - 7
ORDER BY run_date DESC;

-- Check model performance
SELECT 
  model_id, 
  window_start, 
  window_end, 
  total_predictions, 
  roi, 
  win_rate
FROM analytics.prediction_performance
ORDER BY window_end DESC
LIMIT 20;

-- Find missing analytics data
SELECT t.team_name, t.season
FROM public.teams t
LEFT JOIN analytics.team_rolling_metrics trm 
  ON t.id = trm.team_id 
  AND trm.window_type = 'L7' 
  AND trm.as_of_date = CURRENT_DATE
WHERE trm.id IS NULL
  AND t.season = 2025;
```

### Performance Optimization

If queries become slow:

1. **Check index usage:**
```sql
SELECT schemaname, tablename, indexname, idx_scan
FROM pg_stat_user_indexes
WHERE schemaname IN ('raw', 'public', 'analytics')
ORDER BY idx_scan ASC;
```

2. **Analyze table statistics:**
```sql
ANALYZE public.games;
ANALYZE public.predictions;
ANALYZE analytics.team_rolling_metrics;
```

3. **Add missing indexes:**
```sql
-- Example: If filtering by specific columns frequently
CREATE INDEX idx_custom ON public.predictions (model_id, bet_signal) 
WHERE bet_signal = TRUE;
```

## Rollback Plan

If issues arise after migration:

### Option 1: Restore from Backup
```bash
pg_restore -h <host> -U <user> -d <database> -v backup_before_migration.dump
```

### Option 2: Drop New Tables
```sql
-- Drop analytics schema tables
DROP TABLE IF EXISTS analytics.daily_pipeline_runs CASCADE;
DROP TABLE IF EXISTS analytics.feature_importance CASCADE;
DROP TABLE IF EXISTS analytics.prediction_performance CASCADE;
DROP TABLE IF EXISTS analytics.team_strength_of_schedule CASCADE;
DROP TABLE IF EXISTS analytics.team_opponent_metrics CASCADE;
DROP TABLE IF EXISTS analytics.team_rolling_metrics CASCADE;
DROP TABLE IF EXISTS analytics.team_game_metrics CASCADE;

-- Drop new public schema tables
DROP TABLE IF EXISTS public.injuries CASCADE;
DROP TABLE IF EXISTS public.player_boxscores CASCADE;

-- Drop new raw schema tables
DROP TABLE IF EXISTS raw.barttorvik_teams CASCADE;

-- Note: This doesn't remove new columns added to existing tables
-- Those would need to be dropped individually if desired
```

## Support & Documentation

- **Full Schema Documentation**: `docs/SUPABASE_SCHEMA_DESIGN.md`
- **Migration File**: `supabase/migrations/20260318000000_complete_schema_design.sql`
- **Issues**: Report issues to the development team

## Next Steps After Implementation

1. **Create Data Population Scripts**
   - Backfill analytics tables from existing data
   - Create scheduled jobs for daily computation

2. **Update Application Code**
   - Integrate new tables (injuries, player_boxscores)
   - Use analytics tables for feature loading
   - Add performance monitoring dashboard

3. **Testing**
   - Validate data integrity
   - Test query performance
   - Verify RLS policies work correctly

4. **Documentation**
   - Update API documentation
   - Update developer onboarding docs
   - Create data dictionary for non-technical users

## Conclusion

This schema is designed to be:
- ✅ **Production-ready**: All constraints, indexes, and policies in place
- ✅ **Scalable**: Partitioning-ready with season fields
- ✅ **Maintainable**: Clear separation of concerns, comprehensive comments
- ✅ **Performant**: Strategic indexing, materialized views where needed
- ✅ **Auditable**: Complete trail of all data changes
- ✅ **Evolvable**: JSONB flexibility where appropriate for schema changes

The migration is **additive and non-destructive** - it enhances existing tables and adds new ones without dropping anything.
