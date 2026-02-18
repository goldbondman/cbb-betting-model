# ESPN CBB Pipeline Architecture

## Overview
The ESPN College Basketball pipeline has been refactored into modular components to improve maintainability, testability, and debuggability. Each module has a single, well-defined responsibility.

---

## File Structure & Responsibilities

### 1. `espn_config.py` - Configuration & Constants
**Purpose**: Centralize all configuration, environment variables, and constants

**Contents**:
- API endpoints (`ESPN_SUMMARY_URL`, `ESPN_SCOREBOARD_URL`)
- HTTP configuration (headers, timeouts, retry settings)
- Pipeline metadata (`PARSE_VERSION`, `SOURCE_NAME`)
- File paths (`OUT_GAMES`, `OUT_TEAM_LOGS`, `OUT_TEAM_FEATURES`, etc.)
- Feature flags (`WRITE_DIAGNOSTICS`, `DQRG_ENABLE`, `DRY_RUN`)
- Data quality gate thresholds
- CSV schema definitions for all output files

**Why separate**: 
- Single source of truth for all configuration
- Easy to adjust thresholds/settings without touching business logic
- Environment-specific overrides via env vars are centralized
- Clear contract for what outputs the pipeline produces

**Dependencies**: None

---

### 2. `data_utils.py` - Data Manipulation Utilities
**Purpose**: Generic data helpers (not ESPN-specific, reusable across data sources)

**Contents**:
- `_to_int()`, `_to_float()` - Type coercion with fallbacks
- `_safe_div()` - Safe division handling zero denominators
- `_parse_made_attempt()` - Parse "X-Y" format strings (e.g., "5-10" FG)
- `_normalize_home_away_series()` - Clean home/away column values
- `_normalize_id_series()` - Normalize game_id, event_id, team_id
- `_stable_row_hash()` - Deterministic hashing for deduplication
- `_utc_now_iso()` - Generate UTC timestamps
- `_completeness_score_row()` - Score row quality for smart deduplication
- `_estimate_possessions()` - Basketball possession formula
- `_flip_home_away()` - Flip home ↔ away for opponent matching

**Why separate**: 
- Pure functions with no side effects - highly testable
- Reusable across any basketball data source
- Clear separation of generic utilities from domain logic
- No ESPN-specific assumptions

**Dependencies**: 
- `pandas`, `numpy` (standard data science libs)

---

### 3. `espn_http_client.py` - Network & HTTP Layer
**Purpose**: Handle all ESPN API interactions with robust error handling

**Contents**:
- `fetch_with_retry()` - Core HTTP client with exponential backoff
- `fetch_scoreboard_games()` - Fetch daily scoreboard data
- `fetch_and_parse_espn_summary()` - Fetch game summary with retry logic
- HTTP error handling (429 rate limits, 5xx errors, timeouts)
- ESPN Recovery attempts (retry when base totals missing on completed games)

**Why separate**: 
- Isolates all network I/O concerns
- Easy to mock for testing (no real HTTP calls needed)
- Retry logic is complex and deserves focus
- Clear boundary between I/O and business logic

**Dependencies**: 
- `espn_config` (URLs, headers, timeouts)
- `data_utils` (parsing helpers)
- `requests` (HTTP library)

---

### 4. `espn_parsers.py` - Raw Data Parsing
**Purpose**: Transform ESPN JSON responses into structured DataFrames

**Contents**:
- `_extract_odds_from_comp()` - Parse market lines (spread, total, moneylines)
- `_extract_players()` - Extract player box score data
- `_sum_player_totals()` - Aggregate player stats to team level
- `summary_to_team_rows()` - Convert summary JSON to home/away team rows
- `_stat_map()` - Map ESPN stat arrays to dictionaries
- `_iso_to_game_dates()` - Parse ISO timestamps to PST/UTC dates

**Why separate**: 
- Pure transformation logic (JSON → DataFrame)
- No side effects or I/O
- Highly testable with sample JSON fixtures
- ESPN schema changes are isolated here

**Dependencies**: 
- `espn_config` (constants, version info)
- `data_utils` (type conversion, parsing)

---

### 5. `file_io.py` - File Operations & Persistence
**Purpose**: All CSV read/write operations with safety guarantees

**Contents**:
- `_atomic_csv_write()` - Safe file writing with backup/rollback
- `_read_csv_if_exists()` - Safe reading with normalization
- `_append_dedupe_write()` - Append new data with smart deduplication
- `_ensure_csv_exists()` - Initialize CSV files with schema
- Checkpoint management (`load_checkpoint()`, `save_checkpoint()`, `clear_checkpoint()`)
- Error logging (`log_error()`, `write_error_summary()`)

**Why separate**: 
- Consolidates all I/O operations
- Atomic writes prevent data corruption
- Easy to swap storage backends (e.g., database instead of CSV)
- Checkpoint recovery for long-running jobs

**Dependencies**: 
- `espn_config` (file paths, schemas)
- `data_utils` (normalization, completeness scoring)

---

### 6. `metrics_calculator.py` - Metric Computation
**Purpose**: Calculate derived basketball metrics and rolling features

**Contents**:
- `_compute_per_game_advanced_metrics()` - Game-level metrics (ORtg, DRtg, pace, eFG%, etc.)
- `_add_rolling_pack()` - Rolling averages (L3, L7, season)
- `_add_noblow_rollups()` - Non-blowout filtered metrics
- `_add_allowed_forced_pack()` - Defensive metrics (what opponents did)
- `_time_window_counts_per_team()` - Time-based features (rest days, back-to-backs, 3-in-6)
- `_group_shift_rolling()` - Leak-free rolling calculations
- `_add_coverage_counts()` - Track sample sizes for features

**Why separate**: 
- Core domain logic for basketball analytics
- Complex rolling window logic deserves focus
- Leak-free shifting ensures pregame features only
- Single responsibility: metric calculation

**Dependencies**: 
- `data_utils` (safe division, normalization)
- `opponent_merge` (for defensive metrics)

---

### 7. `opponent_merge.py` - Opponent Matching
**Purpose**: Join opponent data for each game (symmetric merge logic)

**Contents**:
- `_flip_home_away()` - Convert home ↔ away
- `_merge_opponent_rows()` - Symmetric opponent join with validation
- Merge key construction (event_id + home_away)
- Opponent join validation and diagnostics

**Why separate**: 
- Complex enough to deserve isolation
- Has subtle logic (symmetric joins) that benefits from focus
- Merge validation is critical for data quality
- Used by multiple downstream modules

**Dependencies**: 
- `data_utils` (normalization, flipping home/away)

---

### 8. `data_quality.py` - Data Quality & Repair
**Purpose**: Validate data integrity and attempt self-healing

**Contents**:
- `verify_dataframe_integrity()` - Validate required columns, data types, value ranges
- `_dqrg_find_issues()` - Detect completed games with missing derived fields
- `_dqrg_repair_in_place()` - Attempt to recompute metrics from base inputs
- `_drop_bad_event_ids_keep_good()` - Remove asymmetric events (not exactly 2 rows)
- `_dedupe_by_completeness()` - Smart deduplication using quality scoring
- Data Quality Repair Gate (DQRG) workflow with audit trail

**Why separate**: 
- Data quality is a distinct domain that will grow over time
- Currently ~200 lines, will likely expand with more validation rules
- Self-healing logic is complex and benefits from isolation
- Produces audit outputs (DQ reports)

**Dependencies**: 
- `data_utils` (normalization, completeness scoring)
- `espn_http_client` (refetch on repair attempts)
- `espn_parsers` (reparse after refetch)
- `espn_config` (DQRG settings)

---

### 9. `matchup_builder.py` - Matchup Table Construction
**Purpose**: Build final model-ready matchup table (one row per game, home/away features side-by-side)

**Contents**:
- `build_matchups_model_ready()` - Pivot home/away rows to single matchup row
- Feature selection (keep pregame features, drop game stats)
- Home/away feature prefixing (h_*, a_*)
- Outcome labels (home_win, home_points, away_points)
- Row hash generation for matchup-level deduplication

**Why separate**: 
- Final aggregation step with different structure than team logs
- Clear boundary between features (team-level) and model inputs (matchup-level)
- Distinct output schema and validation needs

**Dependencies**: 
- `data_utils` (normalization, hashing)

---

### 10. `espn_boxscore_builder.py` - Main Pipeline Orchestrator
**Purpose**: Orchestrate the end-to-end pipeline workflow

**Contents**:
- `run_pipeline()` - Main workflow (PASS 0 through PASS 5)
- `build_espn_games_csv()` - Scoreboard ingestion
- High-level pass structure:
  - **PASS 0**: Fetch scoreboard games
  - **PASS 1**: Fetch summaries, compute metrics, apply DQRG, write team logs
  - **PASS 2**: Load historical logs, normalize, filter
  - **PASS 3**: Compute rolling features (all games + home/away splits)
  - **PASS 4**: Add time-based features (rest, back-to-backs)
  - **PASS 5**: Opponent merge, defensive metrics, weights/plus metrics, advanced metrics
- Gate checks (opponent join rate, poss present, expected features)
- Diagnostics generation
- Checkpoint management for resume capability

**Why separate**: 
- Orchestration layer only - delegates to specialized modules
- Clear pipeline phases with explicit data contracts
- Easy to add new passes or reorder steps
- Testable workflow without running full pipeline

**Lines**: Reduced from ~1350 to ~200-300 (orchestration only)

**Dependencies**: 
- `espn_config` (all configuration)
- `espn_http_client` (API fetching)
- `espn_parsers` (JSON parsing)
- `file_io` (read/write operations)
- `data_utils` (utilities)
- `metrics_calculator` (feature engineering)
- `opponent_merge` (opponent joins)
- `data_quality` (validation, repair)
- `matchup_builder` (final aggregation)
- `weights` (weighting schemes - external module)
- `plus_and_fit` (plus metrics - external module)
- `cbb_advanced_metrics` (GPS, expected margin - external module)
- `rolling_features` (additional rollups - external module)

---

## Dependency Graph

```
espn_boxscore_builder.py (main orchestrator)
    ├─ espn_config.py (no dependencies)
    │
    ├─ espn_http_client.py 
    │   ├─ espn_config.py
    │   └─ data_utils.py
    │
    ├─ espn_parsers.py
    │   ├─ espn_config.py
    │   └─ data_utils.py
    │
    ├─ file_io.py
    │   ├─ espn_config.py
    │   └─ data_utils.py
    │
    ├─ metrics_calculator.py
    │   ├─ data_utils.py
    │   └─ opponent_merge.py
    │
    ├─ opponent_merge.py
    │   └─ data_utils.py
    │
    ├─ data_quality.py
    │   ├─ data_utils.py
    │   ├─ espn_http_client.py
    │   ├─ espn_parsers.py
    │   └─ espn_config.py
    │
    ├─ matchup_builder.py
    │   └─ data_utils.py
    │
    ├─ data_utils.py (no dependencies)
    │
    └─ External feature modules:
        ├─ weights.py
        ├─ plus_and_fit.py
        ├─ cbb_advanced_metrics.py
        └─ rolling_features.py
```

**Dependency Layers** (bottom to top):
1. **Layer 0**: `espn_config`, `data_utils` (no dependencies)
2. **Layer 1**: `espn_http_client`, `espn_parsers`, `file_io` (depend on Layer 0)
3. **Layer 2**: `opponent_merge` (depends on Layer 0)
4. **Layer 3**: `metrics_calculator` (depends on Layers 0-2)
5. **Layer 4**: `data_quality`, `matchup_builder` (depend on Layers 0-3)
6. **Layer 5**: `espn_boxscore_builder` (orchestrates all layers)

---

## Key Benefits

### 1. **Single Responsibility Principle**
Each file has one clear job. If there's a bug in parsing, check `espn_parsers.py`. If there's a network issue, check `espn_http_client.py`.

### 2. **Testability**
- Pure functions (parsers, calculators) are easily unit tested
- HTTP layer can be mocked for integration tests
- File I/O can be tested with temp directories

### 3. **Debugging**
- Stack traces clearly indicate which module failed
- Modules are small enough (~150-200 lines) to read in one sitting
- Clear boundaries make it obvious where to add logging

### 4. **Reusability**
- `data_utils` and `metrics_calculator` can be used by other scrapers (e.g., KenPom, BartTorvik)
- HTTP client pattern can be copied for new APIs

### 5. **Maintainability**
- Changes are localized (e.g., ESPN schema change only touches `espn_parsers.py`)
- Adding new metrics only touches `metrics_calculator.py`
- Configuration changes don't require code review

### 6. **Onboarding**
- New developers can understand one module at a time
- Clear imports show explicit dependencies
- Architecture doc provides roadmap

---

## Migration Strategy

### Phase 1: Foundation (Low Risk)
1. ✅ `espn_config.py` - Move all constants
2. ✅ `data_utils.py` - Move pure utility functions

### Phase 2: I/O & Parsing (Medium Risk)
3. `espn_http_client.py` - Extract HTTP logic
4. `espn_parsers.py` - Extract JSON parsing
5. `file_io.py` - Extract CSV operations

### Phase 3: Business Logic (Higher Risk)
6. `opponent_merge.py` - Extract merge logic
7. `metrics_calculator.py` - Extract metric computation

### Phase 4: Quality & Output (High Risk)
8. `data_quality.py` - Extract validation/repair
9. `matchup_builder.py` - Extract final aggregation

### Phase 5: Orchestration
10. Update `espn_boxscore_builder.py` - Replace inline code with imports

**Testing Strategy**: After each phase, run full pipeline on historical data and verify outputs are byte-identical to original.

---

## File Size Summary

| File | Lines | Responsibility |
|------|-------|----------------|
| `espn_config.py` | ~125 | Configuration constants |
| `data_utils.py` | ~220 | Generic utilities |
| `espn_http_client.py` | ~150 | HTTP/API layer |
| `espn_parsers.py` | ~200 | JSON parsing |
| `file_io.py` | ~200 | File operations |
| `opponent_merge.py` | ~100 | Opponent matching |
| `metrics_calculator.py` | ~250 | Metric computation |
| `data_quality.py` | ~200 | Validation/repair |
| `matchup_builder.py` | ~100 | Matchup aggregation |
| `espn_boxscore_builder.py` | ~250 | Pipeline orchestration |
| **Total** | **~1,795** | *vs. original 1,350 (includes better docs/structure)* |

---

## Output Files Produced

| File | Description | Key Columns |
|------|-------------|-------------|
| `espn_games.csv` | Scoreboard snapshot (one row per game) | `game_id`, `home_team`, `away_team`, `completed`, `market_spread` |
| `espn_team_game_logs.csv` | Team-game rows with per-game metrics | `event_id`, `team_id`, `home_away`, `ortg`, `drtg`, `poss` |
| `espn_team_game_features.csv` | Core pregame rolling features + opponent joins | `event_id`, `team_id`, `ortg_l7_pre`, `opp_drtg_l7_pre` |
| `espn_team_game_extras.csv` | Supplementary analytics (weights, composites, rf10) | `event_id`, `team_id`, `w_g`, `pwr`, `gps`, `rf10_*` |
| `espn_matchups_model_ready.csv` | Model inputs (one row per game, home/away features) | `event_id`, `h_ortg_l7_pre`, `a_drtg_l7_pre`, `home_win` |
| `espn_feature_diagnostics.csv` | Row-level diagnostics for sparse/NaN fields | `event_id`, `team_id`, `diagnostic_reason` |
| `espn_dq_audit.csv` | Data Quality Repair Gate audit trail | `event_id`, `dq_missing_fields`, `dq_repair_success` |
| `espn_player_boxscores.csv` | Player box scores (one row per player per game) | `event_id`, `team_id`, `player`, `pts`, `fgm`, `fga` |

---

## Version History

- **v1.4.2** (Current): Modular architecture, DQRG self-healing, market lines
- **v1.4.1**: Added player box scores, OT detection
- **v1.4.0**: Atomic writes, checkpointing, retry hardening
- **v1.3.x**: Initial rolling features and opponent merge
