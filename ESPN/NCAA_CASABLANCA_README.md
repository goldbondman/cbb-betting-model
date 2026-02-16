# NCAA Casablanca JSON Feed Integration

This module provides integration with NCAA's unofficial but widely used Casablanca JSON feeds for men's college basketball data.

## Overview

The NCAA Casablanca API provides:
- **Scoreboard Data**: Game schedules, scores, and status by date
- **Box Score Data**: Detailed team and player statistics for completed games

## API Endpoints

### Scoreboard
```
https://ncaa-api.henrygd.me/casablanca/scoreboard/basketball-men/d1/YYYY/MM/DD/scoreboard.json
```
Returns all Division I men's basketball games for a specific date.

### Box Score
```
https://ncaa-api.henrygd.me/casablanca/game/<gameId>/boxscore.json
```
Returns detailed team and player statistics for a specific game.

## Files

### Core Modules

#### `ncaa_casablanca_config.py`
Configuration file containing:
- API endpoint URL templates
- HTTP configuration (headers, timeouts, retry settings)
- Output CSV paths and schemas
- Pipeline metadata (version, source name)

#### `ncaa_casablanca_http_client.py`
HTTP client for fetching data from NCAA Casablanca API with:
- Exponential backoff retry logic
- Rate limiting support (429 handling)
- Timeout handling
- Functions:
  - `fetch_scoreboard()`: Fetch scoreboard for a date
  - `fetch_scoreboard_by_date()`: Fetch scoreboard using YYYY-MM-DD format
  - `fetch_boxscore()`: Fetch box score for a game ID

#### `ncaa_casablanca_parsers.py`
Parser functions to transform JSON responses into structured data:
- `parse_scoreboard_game()`: Parse individual game from scoreboard
- `parse_team_stats()`: Parse team statistics from box score
- `parse_player_stats()`: Parse player statistics from box score
- `parse_boxscore_json()`: Parse complete box score JSON

#### `ncaa_casablanca_builder.py`
Main orchestrator that:
- Fetches scoreboard data for date range
- Fetches box scores for all games
- Writes data to CSV files
- Handles deduplication and error recovery

### Output Files

All CSV files are stored in the `ESPN/CSV/` directory:

#### `ncaa_games.csv`
Scoreboard data with one row per game:
- `game_id`: Unique NCAA game identifier
- `date`: Game date (YYYY-MM-DD)
- `game_datetime`: Game start time
- `home_team`, `away_team`: Team names
- `home_score`, `away_score`: Final scores
- `status`: Game status (e.g., "final", "in progress")
- `venue`: Game location

#### `ncaa_team_game_logs.csv`
Team-level statistics with two rows per game (home and away):
- Identifiers: `game_id`, `team`, `opponent`, `home_away`
- Game info: `game_date`, `game_datetime`, `venue`
- Score: `points_for`, `points_against`, `margin`
- Shooting: `fgm`, `fga`, `fg_pct`, `tpm`, `tpa`, `tp_pct`, `ftm`, `fta`, `ft_pct`
- Other stats: `reb`, `orb`, `drb`, `ast`, `stl`, `blk`, `tov`, `pf`

#### `ncaa_player_boxscores.csv`
Player-level statistics with one row per player per game:
- Identifiers: `game_id`, `team`, `player_name`, `player_id`
- Info: `starter`, `minutes`, `points`
- Shooting: `fgm`, `fga`, `fg_pct`, `tpm`, `tpa`, `tp_pct`, `ftm`, `fta`, `ft_pct`
- Other stats: `reb`, `orb`, `drb`, `ast`, `stl`, `blk`, `tov`, `pf`

## Usage

### Command Line

Run the full pipeline to fetch data for the last 3 days:

```bash
cd ESPN
python ncaa_casablanca_builder.py
```

Customize the date range:

```bash
cd ESPN
DAYS_BACK=7 python ncaa_casablanca_builder.py
```

### Python API

```python
from ncaa_casablanca_builder import run_pipeline

# Fetch data for the last 7 days
run_pipeline(days_back=7, verbose=True)
```

Fetch specific components:

```python
from ncaa_casablanca_builder import (
    build_ncaa_games_csv,
    build_ncaa_boxscore_csvs
)

# Fetch only scoreboard data
games_df = build_ncaa_games_csv(days_back=3, verbose=True)

# Fetch box scores for specific game IDs
game_ids = ["12345", "12346", "12347"]
build_ncaa_boxscore_csvs(game_ids, verbose=True)
```

Low-level API usage:

```python
from ncaa_casablanca_http_client import (
    fetch_scoreboard,
    fetch_boxscore
)
from ncaa_casablanca_parsers import (
    parse_scoreboard_game,
    parse_boxscore_json
)

# Fetch and parse scoreboard
scoreboard_data = fetch_scoreboard(year=2024, month=2, day=15)
for game in scoreboard_data.get("games", []):
    parsed = parse_scoreboard_game(game)
    print(parsed)

# Fetch and parse box score
boxscore_data = fetch_boxscore(game_id="12345")
parsed_boxscore = parse_boxscore_json(boxscore_data, "12345")
print(parsed_boxscore)
```

## Testing

Tests are located in `tests/test_ncaa_casablanca_parsers.py`.

Run tests:
```bash
pytest tests/test_ncaa_casablanca_parsers.py -v
```

## Data Quality Notes

### NCAA JSON Structure Variations
The NCAA Casablanca API structure can vary:
- Field names may differ (e.g., `fieldGoalsMade` vs `fgm`)
- Some fields may be missing for certain games
- The parser is defensive and handles multiple field name variations

### Deduplication
- Games CSV: Deduplicated by `game_id`
- Team logs CSV: Deduplicated by `game_id` + `team`
- Player box scores CSV: Deduplicated by `game_id` + `team` + `player_name`

### Rate Limiting
The builder includes rate limiting (0.5s delay between requests) to avoid overwhelming the NCAA API.

## Configuration

Environment variables (optional):
- `DAYS_BACK`: Number of days to fetch (default: 3)
- `NCAA_API_BASE_URL`: NCAA API base URL (default: `https://ncaa-api.henrygd.me`)
- `NCAA_TIMEOUT`: Request timeout in seconds (default: 30)
- `NCAA_MAX_RETRIES`: Maximum retry attempts (default: 3)
- `NCAA_RETRY_INITIAL_DELAY`: Initial retry delay in seconds (default: 1.0)
- `NCAA_RETRY_BACKOFF`: Backoff multiplier for retries (default: 2.0)

## Integration with Existing Pipeline

The NCAA Casablanca module is designed to complement the existing ESPN data pipeline:

1. **Separate CSVs**: NCAA data is stored in separate CSV files to avoid mixing data sources
2. **Compatible Schema**: The CSV schemas are similar to ESPN's for easy integration
3. **Independent Operation**: Can be run independently without affecting ESPN pipeline
4. **Same Directory**: CSVs are stored in `ESPN/CSV/` as requested

## Version History

- **v1.0.0** (2024-02-15): Initial implementation
  - Scoreboard fetching
  - Box score fetching with team and player statistics
  - Retry logic and error handling
  - Comprehensive test coverage

## Future Enhancements

Potential improvements:
- Add caching layer to reduce API calls
- Implement incremental updates (only fetch new games)
- Add data validation and quality checks
- Support for other divisions (D2, D3)
- Integration with existing feature engineering pipeline
