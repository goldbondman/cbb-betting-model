# Full Season Box Score Collection

## Overview

The `collect_full_season_boxscores.py` script is designed to collect a complete season's worth of college basketball box score data with full audit tracking and reliability guarantees.

## Key Features

### Day-by-Day Collection
- Iterates through each individual day in the date range (no bulk queries)
- Prevents data loss that has occurred with bulk date range queries in the past
- Processes Nov 1, 2025 through April 1, 2026 (inclusive) by default

### Comprehensive Logging
- Logs each date being processed in real-time
- Shows progress counters (e.g., "Day 15/152")
- Displays games found per day

### Audit Tracking
- Records whether data was found for each day
- Tracks the number of games returned per day
- Flags days that returned empty or errored out
- Stores fetch timestamp for each day

### Output Files

The script generates three key files in `data/season_boxscores/`:

1. **season_collection_audit.csv** - Audit report with one row per day:
   - `date` - Human-readable date (YYYY-MM-DD)
   - `date_yyyymmdd` - ESPN API format date
   - `games_found` - Total games discovered
   - `games_completed` - Number of completed games
   - `games_in_progress` - Number of in-progress games
   - `status` - success / empty / error
   - `error_message` - Error details if status=error
   - `fetch_timestamp` - When the data was fetched (UTC)

2. **season_boxscores_full.csv** - Complete dataset of all games:
   - All game data from ESPN scoreboard API
   - Includes metadata like collection_date and collection_timestamp
   - One row per game

3. **season_collection_errors.json** - Error log (only if errors occur):
   - Detailed error information for troubleshooting
   - Date, error type, error message, timestamp

## Usage

### Default Run (Full Season)
```bash
cd /home/runner/work/cbb-betting-model/cbb-betting-model
python3 scripts/collect_full_season_boxscores.py
```

This will collect data from November 1, 2025 to April 1, 2026.

### Custom Date Range

Edit the script and modify these constants:
```python
START_DATE = datetime(2025, 11, 1)  # Change start date
END_DATE = datetime(2026, 4, 1)     # Change end date
```

Or use it as a module:
```python
from datetime import datetime
from pathlib import Path
from scripts.collect_full_season_boxscores import SeasonBoxScoreCollector

collector = SeasonBoxScoreCollector(
    start_date=datetime(2025, 11, 1),
    end_date=datetime(2026, 4, 1),
    output_dir=Path("data/season_boxscores")
)
collector.collect_all_days()
collector.save_audit_report()
collector.save_full_dataset()
collector.save_error_log()
collector.print_summary_report()
```

## Runtime

- Processing time: ~3 seconds per day (due to API rate limiting)
- For full season (152 days): approximately 7-8 minutes
- Progress is saved incrementally every 10 days

## Verification

The script provides a comprehensive summary report at the end:

```
============================================================
SEASON COLLECTION SUMMARY
============================================================
Date range: 2025-11-01 to 2026-04-01
Total days processed: 152

Status breakdown:
  ✓ Success: 120 days (78.9%)
  ○ Empty:   30 days (19.7%)
  ✗ Error:   2 days (1.3%)

Games collected:
  Total games found: 5,432
  Completed games: 5,200

✓ All 152 days in date range were processed
============================================================
```

## Identifying Coverage Gaps

The audit CSV makes it easy to identify gaps:

```bash
# Find all days with errors
grep ",error," data/season_boxscores/season_collection_audit.csv

# Find all empty days
grep ",empty," data/season_boxscores/season_collection_audit.csv

# Count total games by month
awk -F',' 'NR>1 {split($1,d,"-"); sum[d[2]]+=$3} END {for(m in sum) print m, sum[m]}' \
  data/season_boxscores/season_collection_audit.csv
```

## Resuming Failed Runs

If the script is interrupted, simply run it again. The script will:
- Re-fetch all days (no checkpoint/resume mechanism currently)
- Overwrite previous partial results
- Generate fresh audit reports

For a checkpoint/resume capability, the incremental audit saves can be used to identify where to restart.

## Dependencies

- pandas
- numpy
- requests
- ESPN boxscore builder modules (from ESPN/ directory)

## Design Philosophy

**Reliability over speed**: The script prioritizes data completeness and visibility over performance. Each day is fetched individually to ensure no data is lost, and comprehensive audit logs are maintained to provide full confidence in the collected data.
