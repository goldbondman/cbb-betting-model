# ESPN Data Storage

This directory contains raw JSON responses from ESPN API calls, stored for archival, debugging, and potential reprocessing.

## Directory Structure

```
ESPN/raw_json/
├── scoreboard/           # Scoreboard API responses
│   └── YYYY/
│       └── MM/
│           └── scoreboard_YYYYMMDD_timestamp.json
└── summary/              # Game summary/boxscore API responses
    ├── YYYY/
    │   └── MM/
    │       └── summary_EVENTID_timestamp.json
    └── unknown/          # Summaries without parseable game dates
        └── summary_EVENTID_timestamp.json
```

## File Format

Each JSON file contains:
- **metadata**: Information about when and what was fetched
- **data**: The raw ESPN API response

### Scoreboard File Example

```json
{
  "metadata": {
    "date": "20240215",
    "fetched_at_utc": "2024-02-15T10:30:00Z",
    "api_endpoint": "scoreboard"
  },
  "data": {
    "events": [...],
    ...
  }
}
```

### Summary File Example

```json
{
  "metadata": {
    "event_id": "401234567",
    "game_date": "20240215",
    "fetched_at_utc": "2024-02-15T10:30:00Z",
    "api_endpoint": "summary"
  },
  "data": {
    "header": {...},
    "boxscore": {...},
    ...
  }
}
```

## Configuration

JSON storage is controlled by environment variables in `ESPN/espn_config.py`:

- **SAVE_RAW_JSON**: Enable/disable JSON storage (default: `"1"` - enabled)
- **ESPN_JSON_DIR**: Directory for JSON storage (default: `"ESPN/raw_json"`)

To disable JSON storage:
```bash
export SAVE_RAW_JSON=0
```

To use a custom directory:
```bash
export ESPN_JSON_DIR=/path/to/json/storage
```

## Usage

JSON storage is automatically handled by `ESPN/espn_boxscore_builder_modular.py`. No manual intervention is needed.

The storage functions are in `ESPN/json_storage.py`:
- `save_scoreboard_json(date, data)` - Save scoreboard response
- `save_summary_json(event_id, data)` - Save summary/boxscore response
- `get_json_storage_stats()` - Get storage statistics

## Data Captured

### From Scoreboard API
- Game schedules
- Scores (live and final)
- **Market lines (betting odds)**: spread, over/under, moneylines
- Game status
- Teams
- Venues

### From Summary API
- Detailed box scores
- **Player statistics**: points, rebounds, assists, shooting percentages, etc.
- Team statistics
- Game flow data
- Advanced metrics

## Git Integration

Raw JSON files in this directory are **tracked by git** (not in .gitignore). This ensures:
- Historical data is preserved
- Reprocessing is possible without re-fetching from ESPN
- Data provenance and audit trail

## File Retention

Consider implementing a cleanup policy for old JSON files to manage repository size:
- Keep recent season (current + previous year)
- Archive older seasons to external storage
- Compress older files if needed

## Related Files

- `ESPN/espn_config.py` - Configuration settings
- `ESPN/json_storage.py` - Storage implementation
- `ESPN/espn_boxscore_builder_modular.py` - Main pipeline (uses storage)
- `ESPN/espn_http_client.py` - API fetch functions
- `ESPN/espn_parsers.py` - JSON parsing logic
