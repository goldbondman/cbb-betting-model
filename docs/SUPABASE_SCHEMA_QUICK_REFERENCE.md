# Supabase Schema Quick Reference

## Table Overview

### RAW Schema (13 tables)
Source data ingestion with full provenance tracking.

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `raw.raw_games` | Complete API payloads | `season`, `source`, `external_game_id`, `payload` (JSONB) |
| `raw.espn_team_game_core` | Team-game box score primitives | `event_id`, `team_id`, `fgm`, `fga`, `tpm`, `tpa`, `ftm`, `fta`, `orb`, `drb` |
| `raw.espn_player_boxscores` | Player-level stats | `event_id`, `athlete_id`, `pts`, `reb`, `ast` |
| `raw.espn_teams` | Team reference data | `espn_id`, `name`, `conference`, `logo` |
| `raw.espn_injuries` | Injury reports | `athlete_id`, `status`, `injury_type`, `return_date` |
| `raw.espn_dq_audit` | Data quality repairs | `event_id`, `dq_missing_fields`, `dq_repair_success` |
| `raw.espn_feature_diagnostics` | Feature engineering issues | `event_id`, `diagnostic_reason` |
| `raw.ncaa_team_game_logs` | NCAA team stats | `game_id`, `team`, `points_for`, `fgm`, `fga` |
| `raw.ncaa_games` | NCAA game data | `game_id`, `home_team`, `away_team`, `home_score` |
| `raw.ncaa_player_boxscores` | NCAA player stats | `game_id`, `player_name`, `points`, `reb` |
| `raw.barttorvik_teams` | External advanced metrics | `season`, `team`, `adj_oe`, `adj_de`, `adj_em` |
| `raw.haslametrics` | HaslaMetrics data | `season`, `team`, `metrics` (JSONB) |
| `raw.predictions_latest` | Raw ML model outputs | `model_id`, `event_id`, `pred_spread`, `pred_total` |

### PUBLIC Schema (13 tables)
Normalized application data with enforced relationships.

| Table | Purpose | Key Columns | Foreign Keys |
|-------|---------|-------------|--------------|
| `public.teams` | Canonical team reference | `id`, `season`, `team_name`, `conference`, `espn_team_id`, `ncaa_team_id` | - |
| `public.games` | Game schedule & results | `id`, `season`, `game_datetime_utc`, `home_team_id`, `away_team_id`, `home_score`, `away_score`, `margin` | `→ teams` |
| `public.market_lines` | Vegas betting lines (time-series) | `game_id`, `book`, `line_type`, `spread_home`, `total`, `pulled_at` | `→ games` |
| `public.team_boxscores` | Team box scores (raw stats only) | `game_id`, `team_id`, `points`, `fgm`, `fga`, `orb`, `drb` | `→ games, teams` |
| `public.player_boxscores` | Player box scores | `game_id`, `team_id`, `player_name`, `pts`, `reb`, `ast` | `→ games, teams` |
| `public.injuries` | Injury tracking | `team_id`, `player_name`, `status`, `injury_type`, `is_active` | `→ teams` |
| `public.model_registry` | Model definitions | `model_id`, `model_type`, `model_category`, `params` (JSONB), `is_active`, `is_production` | - |
| `public.predictions` | Model predictions + outcomes | `id`, `model_version_id`, `game_id`, `pred_spread`, `pred_total`, `market_spread`, `edge_spread`, `bet_signal`, `actual_spread` | `→ games, model_registry` |
| `public.bet_ledger` | Bet tracking | `id`, `event_id`, `market`, `side`, `units`, `edge`, `result`, `pnl` | `→ predictions` |
| `public.dq_audit` | Data quality issues | `entity_type`, `entity_id`, `severity`, `reason_codes`, `resolution_status` | - |
| `public.team_game_features` | Pregame feature snapshots | `game_id`, `team_id`, `feature_set`, `features` (JSONB) | `→ games, teams` |
| `public.team_metrics` | Team season metrics | `season`, `team_id`, `metric_set`, `metrics` (JSONB) | `→ teams` |

### ANALYTICS Schema (7 tables)
Computed metrics regenerable from raw data.

| Table | Purpose | Key Columns | Foreign Keys |
|-------|---------|-------------|--------------|
| `analytics.team_game_metrics` | Game-level efficiency metrics | `game_id`, `team_id`, `possessions`, `ortg`, `drtg`, `efg_pct`, `tov_pct`, `pace` | `→ games, teams, team_boxscores` |
| `analytics.team_rolling_metrics` | Rolling window stats (L3, L5, L7, L10, season) | `team_id`, `season`, `as_of_date`, `window_type`, `avg_ortg`, `avg_drtg`, `avg_pace` | `→ teams` |
| `analytics.team_opponent_metrics` | Opponent-adjusted performance | `game_id`, `team_id`, `opponent_id`, `ortg_vs_expectation`, `opponent_adj_em` | `→ games, teams` |
| `analytics.team_strength_of_schedule` | SOS calculations | `team_id`, `season`, `as_of_date`, `sos_avg_opp_netrtg`, `sos_rank` | `→ teams` |
| `analytics.prediction_performance` | Model performance tracking | `model_id`, `window_start`, `window_end`, `mae_spread`, `rmse_spread`, `roi`, `win_rate` | - |
| `analytics.feature_importance` | ML feature importance | `model_id`, `feature_name`, `importance_score`, `shap_mean_abs` | - |
| `analytics.daily_pipeline_runs` | Pipeline execution logs | `run_date`, `stage`, `status`, `games_processed`, `predictions_generated` | - |

## Entity Relationships

```
┌─────────────┐
│   teams     │◄─────┐
└─────────────┘      │
       ▲             │
       │ 1:N         │
       │             │
┌─────────────┐      │
│   games     │      │
└─────────────┘      │
       ▲             │
       │             │
       │             │
       ├─────────────┤
       │             │
┌─────────────────┐  │         ┌──────────────────┐
│ team_boxscores  │  │         │ market_lines     │
└─────────────────┘  │         └──────────────────┘
       │             │                 ▲
       │             │                 │
       ▼             │                 │
┌──────────────────────┐               │
│ team_game_metrics    │               │
│ (analytics)          │               │
└──────────────────────┘               │
                                       │
┌─────────────────┐    ┌──────────────────┐
│ model_registry  │◄───│  predictions     │
└─────────────────┘    └──────────────────┘
                              │
                              │
                              ▼
                       ┌──────────────────┐
                       │  bet_ledger      │
                       └──────────────────┘
```

## Common Query Patterns

### 1. Get Latest Team Metrics
```sql
SELECT 
  t.team_name,
  trm.avg_ortg,
  trm.avg_drtg,
  trm.avg_netrtg,
  trm.avg_pace
FROM analytics.team_rolling_metrics trm
JOIN public.teams t ON trm.team_id = t.id
WHERE trm.window_type = 'L7'
  AND trm.as_of_date = CURRENT_DATE
  AND t.season = 2025;
```

### 2. Get Today's Predictions with Bet Signals
```sql
SELECT 
  p.home_team,
  p.away_team,
  p.pred_spread,
  p.market_spread,
  p.edge_spread,
  p.bet_market,
  p.bet_side,
  p.bet_units,
  p.confidence
FROM public.predictions p
WHERE p.game_date = CURRENT_DATE
  AND p.bet_signal = TRUE
ORDER BY p.edge_magnitude DESC;
```

### 3. Get Game with All Context
```sql
SELECT 
  g.game_datetime_utc,
  ht.team_name AS home_team,
  at.team_name AS away_team,
  g.home_score,
  g.away_score,
  g.margin,
  ml.spread_home,
  ml.total,
  p.pred_spread,
  p.edge_spread
FROM public.games g
JOIN public.teams ht ON g.home_team_id = ht.id
JOIN public.teams at ON g.away_team_id = at.id
LEFT JOIN public.market_lines ml ON g.id = ml.game_id AND ml.line_type = 'closing'
LEFT JOIN public.predictions p ON g.id = p.game_id
WHERE g.espn_game_id = '401234567';
```

### 4. Get Model Performance
```sql
SELECT 
  model_id,
  window_end,
  total_predictions,
  total_bets,
  win_rate,
  roi,
  mae_spread,
  sharpe_ratio
FROM analytics.prediction_performance
WHERE model_id = 'ensemble_v3'
ORDER BY window_end DESC
LIMIT 10;
```

### 5. Get Active Injuries for Team
```sql
SELECT 
  i.player_name,
  i.status,
  i.injury_type,
  i.detail,
  i.return_date
FROM public.injuries i
JOIN public.teams t ON i.team_id = t.id
WHERE t.team_name = 'North Carolina'
  AND i.is_active = TRUE
ORDER BY 
  CASE i.status
    WHEN 'Out' THEN 1
    WHEN 'Doubtful' THEN 2
    WHEN 'Questionable' THEN 3
    ELSE 4
  END;
```

### 6. Backtest Model Performance
```sql
WITH predictions_with_outcomes AS (
  SELECT 
    p.model_id,
    p.pred_spread,
    p.actual_spread,
    p.prediction_error_spread,
    p.market_spread,
    p.edge_spread,
    p.bet_outcome,
    p.bet_pnl
  FROM public.predictions p
  WHERE p.game_date BETWEEN '2024-11-01' AND '2024-12-31'
    AND p.actual_spread IS NOT NULL
)
SELECT 
  model_id,
  COUNT(*) AS total_predictions,
  AVG(ABS(prediction_error_spread)) AS mae,
  SQRT(AVG(prediction_error_spread ^ 2)) AS rmse,
  COUNT(*) FILTER (WHERE bet_outcome = 'win') AS wins,
  COUNT(*) FILTER (WHERE bet_outcome = 'loss') AS losses,
  COUNT(*) FILTER (WHERE bet_outcome = 'push') AS pushes,
  ROUND(
    COUNT(*) FILTER (WHERE bet_outcome = 'win')::NUMERIC / 
    NULLIF(COUNT(*) FILTER (WHERE bet_outcome IN ('win', 'loss')), 0), 
    4
  ) AS win_rate,
  SUM(bet_pnl) AS total_pnl,
  ROUND(SUM(bet_pnl) / COUNT(*), 4) AS roi
FROM predictions_with_outcomes
GROUP BY model_id;
```

## Index Reference

### Most Important Indexes

**For Predictions Dashboard:**
- `idx_predictions_signal` - Fast "today's picks" query
- `idx_predictions_date` - Filter by game date
- `idx_predictions_game` - Join to games table

**For Team Analysis:**
- `idx_rolling_metrics_team_date` - Fast team metrics lookup
- `idx_team_metrics_team` - Team game-by-game efficiency
- `idx_games_home_team_date` - Team schedule queries
- `idx_games_away_team_date` - Team schedule queries

**For Backtesting:**
- `idx_predictions_model` - Model-specific performance queries
- `idx_market_lines_closing` - Closing line lookup
- `idx_bet_ledger_model_result` - ROI calculations

**For Monitoring:**
- `idx_pipeline_runs_status` - Failed pipeline runs
- `idx_dq_audit_unresolved` - Open data quality issues

## Data Types Reference

### Numeric Precision Standards

| Metric Type | Data Type | Example | Rationale |
|-------------|-----------|---------|-----------|
| **Percentages** | `NUMERIC(5,4)` | 0.5432 (54.32%) | 4 decimal places for precision |
| **Efficiency Ratings** | `NUMERIC(6,2)` | 112.45 | Sufficient for ORtg/DRtg (80-130 range) |
| **Spreads** | `NUMERIC(6,2)` | -12.50 | 2 decimal places standard |
| **Totals** | `NUMERIC(6,2)` | 156.50 | 2 decimal places standard |
| **Probabilities** | `NUMERIC(5,4)` | 0.6523 (65.23%) | 4 decimal places for precision |
| **Currency/PnL** | `NUMERIC(10,2)` | 1234.56 | 2 decimal places, large range |
| **Possessions** | `NUMERIC(6,2)` | 68.75 | Can be fractional |
| **Minutes** | `NUMERIC(4,1)` | 32.5 | 1 decimal place sufficient |

### Text Field Standards

| Field Type | Data Type | Max Length | Example |
|------------|-----------|------------|---------|
| **Team Names** | `TEXT` | ~100 chars | "North Carolina Tar Heels" |
| **Player Names** | `TEXT` | ~100 chars | "Armando Bacot Jr." |
| **External IDs** | `TEXT` | ~50 chars | "401234567" |
| **Status Enums** | `TEXT` + CHECK | ~20 chars | "scheduled", "final" |
| **Notes/Details** | `TEXT` | Unlimited | Full descriptions |

## Verification Status Enum

All ingested tables use this standard:

| Status | Meaning | When Used |
|--------|---------|-----------|
| `verified` | Data confirmed from multiple sources or manually verified | After successful conflict resolution |
| `partial` | Data from single source, not yet cross-checked | Initial ingestion |
| `conflict` | Multiple sources disagree on values | When ESPN ≠ NCAA for same game |
| `rejected` | Data failed validation, should not be used | Quality checks failed |

## Best Practices

### DO
✅ Use `verification_status` to filter production queries
✅ Always include `pulled_at` when inserting raw data
✅ Use foreign keys to maintain referential integrity
✅ Leverage generated columns for computed values (`margin`, `edge_spread`)
✅ Use partial indexes for frequently filtered queries
✅ Store raw data in `raw.*`, derived metrics in `analytics.*`

### DON'T
❌ Mix raw and derived data in same table
❌ Hardcode model parameters in application code (use `model_registry`)
❌ Skip `verification_status` checks in queries
❌ Recompute efficiency metrics on every query (use `analytics.*` tables)
❌ Delete raw data (mark as `rejected` instead)
❌ Bypass RLS policies with `service_role` key in client code

## Troubleshooting

### Slow Queries?
1. Check `EXPLAIN ANALYZE` output
2. Verify indexes are being used
3. Run `ANALYZE` on large tables
4. Consider partial indexes for filtered queries

### Data Conflicts?
1. Query `raw.raw_games` to see all source payloads
2. Check `verification_status` and `verification_notes`
3. Use `raw.espn_dq_audit` to see repair attempts

### Missing Data?
1. Check `public.dq_audit` for warnings
2. Query `analytics.daily_pipeline_runs` for pipeline failures
3. Check `raw.espn_feature_diagnostics` for feature engineering issues

## Migration Checklist

- [ ] Backup database
- [ ] Run migration SQL
- [ ] Verify all tables created
- [ ] Verify all indexes created
- [ ] Verify RLS policies enabled
- [ ] Test read access as `anon` role
- [ ] Test write access as `authenticated` role
- [ ] Backfill analytics tables
- [ ] Update application code
- [ ] Test end-to-end data flow
- [ ] Monitor for performance issues

## Quick Stats

- **Total Tables**: 33 (13 raw, 13 public, 7 analytics)
- **Total Indexes**: ~100
- **Foreign Key Relationships**: ~30
- **RLS Policies**: ~33
- **Triggers**: 8 (auto-update timestamps)
- **Custom Functions**: 2 (edge_tier, pregame_features)

## Support

For questions or issues:
1. Review `docs/SUPABASE_SCHEMA_DESIGN.md` for full details
2. Check `docs/SUPABASE_SCHEMA_IMPLEMENTATION_GUIDE.md` for implementation steps
3. Consult migration file: `supabase/migrations/20260318000000_complete_schema_design.sql`
