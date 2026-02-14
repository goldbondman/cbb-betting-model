# ESPN Game Logs CSV Fix - Documentation

## Problem Summary

The ESPN game logs CSV (`ESPN/CSV/espn_team_game_logs.csv`) had two main issues:

### Issue 1: Empty game_date Field
The `game_date` field was showing as NaN/empty for most rows (1254 out of 1337).

**Root Cause**: Column misalignment in older data where `game_date` contained datetime values and `game_datetime_utc` was NaN, suggesting the columns were swapped or in the wrong order.

**Solution**: 
- Created `fix_team_logs_csv.py` script that recalculates `game_date` from `game_datetime_utc`
- Script converts UTC timestamps to PST local dates (YYYY-MM-DD format)
- All 1337 rows now have valid `game_date` values

### Issue 2: Box Score Data Showing Zeros
All box score fields (fgm, fga, tpm, tpa, ftm, fta, tov, orb, drb, reb) were showing zeros for 1254 rows.

**Root Cause**: ESPN API not returning team box score statistics. Analysis showed:
- All affected rows were pulled on 2026-01-21 with parse_version v1.3.2
- Includes 1240 completed games (should have box scores)
- Both team stats AND player stats were missing from API responses
- Player fallback mechanism couldn't compensate

**Solution**:
- Added debug logging to `espn_parsers.py` (enable with `ESPN_DEBUG_MISSING_STATS=1`)
- Created `test_espn_api.py` diagnostic tool to test ESPN API responses
- Added validation warnings for completed games with zero box scores
- **Note**: Requires re-fetching data from ESPN API when it's available

### Issue 3: Column Order Inconsistency
CSV columns didn't match the schema definition in `espn_config.py`, and user requested `game_date` to be in "far left" position.

**Root Cause**: CSV was created with ad-hoc column ordering, not enforced by schema.

**Solution**:
- Updated `espn_config.py` schema to put `game_date` early (after identifiers)
- New order: `event_id`, `team_id`, `team`, `opponent`, `home_away`, `game_date`, ...
- Modified `file_io.py` to enforce column order from schema when writing
- Added `_enforce_column_order()` function
- Future CSV writes will maintain correct column order

## Files Changed

### 1. `ESPN/espn_config.py`
- Updated `team_logs` schema definition
- Moved `game_date` to position 6 (after identifiers, before datetime)
- Added missing fields: `opponent`, `home_team`, `away_team`, `blowout`, `row_hash`

### 2. `ESPN/file_io.py`
- Added `_enforce_column_order()` function
- Modified `_append_dedupe_write()` to call column ordering before writing
- Ensures all CSV writes follow the schema column order

### 3. `ESPN/espn_parsers.py`
- Added debug logging for missing team statistics
- Added warning when completed games have zero box scores
- Enable with environment variable: `ESPN_DEBUG_MISSING_STATS=1`

### 4. `ESPN/fix_team_logs_csv.py` (NEW)
- One-time fix script for existing CSV
- Recalculates `game_date` and `game_date_utc` from `game_datetime_utc`
- Fixes column misalignment from older data
- Reorders columns to match new schema
- Creates backup before modifying

### 5. `ESPN/test_espn_api.py` (NEW)
- Diagnostic tool to test ESPN API responses
- Usage: `python test_espn_api.py <event_id>`
- Saves raw JSON response for inspection
- Analyzes what stats are present/missing
- Helps diagnose box score parsing issues

## How to Use

### Fix Existing CSV (One-Time)
```bash
cd ESPN
python fix_team_logs_csv.py
```

This will:
- Create a backup at `CSV/espn_team_game_logs.csv.backup`
- Fix all `game_date` values
- Reorder columns to match schema
- Write fixed CSV

### Test ESPN API Response
```bash
cd ESPN
python test_espn_api.py 401820577
```

This will:
- Fetch the game summary from ESPN
- Save raw JSON to `test_game_<event_id>.json`
- Show what stats are present/missing
- Parse and display box score data
- Warn if box scores are zero

### Enable Debug Logging
```bash
export ESPN_DEBUG_MISSING_STATS=1
python ESPN/espn_boxscore_builder_modular.py
```

This will print debug messages when:
- Team statistics are missing from API response
- Completed games have zero box scores
- Shows available keys in team_entry

## Current Status

### ✅ Fixed Issues
1. **game_date field**: All rows now have valid date values
2. **Column ordering**: Schema updated, enforcement added
3. **game_date position**: Now in "far left" (column 6 after identifiers)

### ⚠️ Partial Solution - Box Score Zeros
The box score zeros issue is **partially addressed**:
- Added diagnostic tools and logging
- Root cause identified (ESPN API not returning stats)
- **Still requires**: Re-running the builder to fetch fresh data

**Next Steps**:
1. Wait for ESPN API to return box score stats (may be temporary API issue)
2. Run builder script: `python ESPN/espn_boxscore_builder_modular.py`
3. Use `test_espn_api.py` to verify a sample game returns box scores
4. If still failing, inspect the saved JSON to see ESPN's response structure

## CSV Schema (Final)

The `espn_team_game_logs.csv` now has this column order:

```
1. event_id          - Game identifier
2. team_id           - Team identifier  
3. team              - Team name
4. opponent          - Opponent team name
5. home_away         - 'home' or 'away'
6. game_date         - Game date in PST (YYYY-MM-DD) ⭐ FAR LEFT as requested
7. game_date_utc     - Game date in UTC (YYYY-MM-DD)
8. game_datetime_utc - Full datetime in UTC (ISO 8601)
9. venue             - Arena name
10. points_for       - Team's score
11. points_against   - Opponent's score
12. margin           - Point differential
13-22. Box score stats (fgm, fga, tpm, tpa, ftm, fta, tov, orb, drb, reb)
23-31. Derived metrics (poss, efg, ftr, 3par, shooting %, rebounding %, etc.)
32-35. Ratings (ortg, drtg, netrtg, pace)
36-43. Game metadata (neutral_site, is_ot, data_ok, completed, status, etc.)
44-46. Technical (pulled_at_utc, source, parse_version)
47-50. Additional (home_team, away_team, blowout, row_hash)
```

## Verification Commands

### Check game_date is populated
```bash
cd ESPN
python -c "import pandas as pd; df = pd.read_csv('CSV/espn_team_game_logs.csv'); print(f'Rows with game_date: {df[\"game_date\"].notna().sum()}/{len(df)}')"
```

### Check column order
```bash
cd ESPN
python -c "import pandas as pd; df = pd.read_csv('CSV/espn_team_game_logs.csv'); print(', '.join(df.columns[:10]))"
```

### Check box score stats
```bash
cd ESPN
python -c "import pandas as pd; df = pd.read_csv('CSV/espn_team_game_logs.csv'); print(f'Rows with box scores (fga>0): {(df[\"fga\"]>0).sum()}/{len(df)}')"
```
