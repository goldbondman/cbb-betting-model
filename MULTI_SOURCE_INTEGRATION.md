# Multi-Source Data Integration

This document describes the multi-source data integration system that fetches college basketball data from ESPN, NCAA Casablanca, and Henry API with integrity checks and conflict resolution.

## Overview

The multi-source integration system provides:

1. **Redundancy**: Not dependent on a single data source being available
2. **Data Quality**: Detects discrepancies between sources
3. **Integrity Merge**: Automatically resolves conflicts using voting and source priority
4. **Transparency**: Detailed conflict reports and source attribution

## Architecture

### Components

```
┌─────────────────────────────────────────────────────┐
│           Multi-Source Fetcher                      │
│  (core/multi_source_fetcher.py)                     │
└────────────────────┬────────────────────────────────┘
                     │
        ┌────────────┼────────────┐
        │            │            │
   ┌────▼───┐  ┌────▼───┐  ┌────▼───┐
   │  ESPN  │  │  NCAA  │  │ Henry  │
   │ Source │  │ Source │  │   API  │
   └────┬───┘  └────┬───┘  └────┬───┘
        │            │            │
        └────────────┼────────────┘
                     │
            ┌────────▼────────┐
            │ Integrity Merger│
            │ (conflict res.) │
            └────────┬────────┘
                     │
            ┌────────▼────────┐
            │  Merged Games   │
            │   + Report      │
            └─────────────────┘
```

### Key Modules

1. **data_sources.py** - Abstract base classes and data models
   - `GameData` - Standardized game data structure
   - `SourceResult` - Result from a single source fetch
   - `DataSource` - Abstract base class for sources

2. **source_implementations.py** - Concrete source implementations
   - `ESPNDataSource` - ESPN API integration
   - `NCAADataSource` - NCAA Casablanca integration
   - `HenryAPIDataSource` - Henry API integration

3. **integrity_merger.py** - Conflict detection and resolution
   - `IntegrityMerger` - Merges data from multiple sources
   - `GameConflict` - Represents a data conflict
   - `MergedGame` - Game with merged data and conflict info
   - `IntegrityReport` - Summary of merge operation

4. **multi_source_fetcher.py** - Orchestration layer
   - `MultiSourceFetcher` - Coordinates fetching and merging
   - CLI interface for testing

## Usage

### Python API

```python
from core.multi_source_fetcher import MultiSourceFetcher

# Initialize with all sources enabled
fetcher = MultiSourceFetcher()

# Fetch games for a specific date
games, report = fetcher.fetch_date("2024-01-15")

# Check for issues
if report.failed_sources:
    print(f"Warning: {report.failed_sources} failed")

# Access merged data
for merged_game in games:
    game = merged_game.game
    print(f"{game.home_team} vs {game.away_team}: {game.home_score}-{game.away_score}")
    print(f"  Sources: {merged_game.sources}")
    
    if merged_game.has_conflicts:
        print(f"  Conflicts: {len(merged_game.conflicts)}")
        for conflict in merged_game.conflicts:
            print(f"    {conflict.field_name}: {conflict.values}")

# Save to CSV
fetcher.save_to_csv(games, "output.csv")
```

### Command Line

```bash
# Fetch today's games from all sources
python core/multi_source_fetcher.py

# Fetch specific date
python core/multi_source_fetcher.py --date 2024-01-15

# Fetch date range
python core/multi_source_fetcher.py --start-date 2024-01-01 --end-date 2024-01-07

# Save to file
python core/multi_source_fetcher.py --date 2024-01-15 --output games.csv

# Disable specific sources
python core/multi_source_fetcher.py --disable-henry

# Verbose logging
python core/multi_source_fetcher.py --date 2024-01-15 --verbose
```

### Integration Script

The `scripts/refresh_multi_source.py` script provides a production-ready interface:

```bash
# Fetch last 7 days with all sources
python scripts/refresh_multi_source.py

# Fetch specific date
python scripts/refresh_multi_source.py --date 2024-01-15

# Fetch last 3 days
python scripts/refresh_multi_source.py --days-back 3

# Custom output path
python scripts/refresh_multi_source.py --output my_games.csv

# Fail if conflicts detected (for CI/CD)
python scripts/refresh_multi_source.py --fail-on-conflicts
```

## Integrity Merge Strategy

### Conflict Detection

The system checks for conflicts in critical fields:
- `home_score` and `away_score`
- `status` (game state)
- `home_team` and `away_team` (team names)
- `venue`

### Conflict Resolution

When conflicts are detected, the system uses a multi-step resolution strategy:

1. **Majority Vote**: If 2+ sources agree on a value, use that value
   ```
   ESPN:  home_score = 80
   NCAA:  home_score = 80
   Henry: home_score = 81
   
   Result: home_score = 80 (majority)
   ```

2. **Source Priority**: If no majority, use highest priority source
   ```
   Default priority: ESPN > NCAA > Henry
   
   ESPN:  home_score = 80
   NCAA:  home_score = 81
   
   Result: home_score = 80 (ESPN has priority)
   ```

3. **Data Completeness**: Fill missing fields from any source
   ```
   ESPN:  venue = "Cameron Indoor"
   NCAA:  venue = None
   
   Result: venue = "Cameron Indoor"
   ```

### Quality Scoring

Each game receives a quality score (0-1) based on field completeness:
- 1.0 = All fields present (100% complete)
- 0.8+ = High quality (most fields present)
- 0.5-0.8 = Medium quality (some fields missing)
- <0.5 = Low quality (many fields missing)

## Output Format

### Merged Games CSV

```csv
game_id,date,home_team,away_team,home_score,away_score,status,venue,source,source_count,has_conflicts,conflict_count,quality_score
401829197,2024-01-15,Duke,UNC,80,75,final,Cameron Indoor,merged:espn+ncaa+henry,3,True,1,0.95
```

Fields:
- Standard game fields (id, teams, scores, etc.)
- `source` - Source(s) used (e.g., "merged:espn+ncaa")
- `source_count` - Number of sources that provided data
- `has_conflicts` - Boolean indicating conflicts
- `conflict_count` - Number of conflicts detected
- `quality_score` - Data completeness score (0-1)

### Conflict Log CSV

```csv
date,game_id,field,values,resolved_value,resolution_method
2024-01-15,401829197,away_score,"{'espn': 75, 'ncaa': 76, 'henry': 75}",75,majority_vote
```

## Configuration

### Source Priority

Default priority order: ESPN → NCAA Casablanca → Henry API

To customize:

```python
from core.data_sources import SourceType

fetcher = MultiSourceFetcher(
    source_priority=[
        SourceType.NCAA_CASABLANCA,  # Highest priority
        SourceType.ESPN,
        SourceType.HENRY_API
    ]
)
```

### Enabling/Disabling Sources

```python
# Only use ESPN and NCAA
fetcher = MultiSourceFetcher(
    enable_espn=True,
    enable_ncaa=True,
    enable_henry=False  # Disabled
)
```

## Testing

Run the test suite:

```bash
python tests/test_multi_source_integration.py
```

Tests cover:
- GameData completeness scoring
- SourceResult quality assessment
- Integrity merge with no conflicts
- Integrity merge with score conflicts
- Source priority tie-breaking
- Single source handling
- Failed source handling
- Integrity report generation

## Error Handling

### Partial Failures

By default, the system continues if some sources fail:

```python
# This succeeds even if NCAA and Henry fail
games, report = fetcher.fetch_date("2024-01-15", allow_partial=True)

# Check which sources failed
if report.failed_sources:
    print(f"Failed: {report.failed_sources}")
```

### Complete Failures

If all sources fail:

```python
try:
    games, report = fetcher.fetch_date("2024-01-15", allow_partial=False)
except RuntimeError as e:
    print(f"All sources failed: {e}")
```

## Best Practices

1. **Always check the integrity report**
   ```python
   games, report = fetcher.fetch_date("2024-01-15")
   print(report.summary())
   ```

2. **Log conflicts for investigation**
   ```python
   for game in games:
       if game.has_conflicts:
           logger.warning(f"Game {game.game.game_id} has conflicts: {game.conflicts}")
   ```

3. **Monitor source health**
   ```python
   if len(report.failed_sources) >= 2:
       # Alert: Multiple sources failing
       send_alert("Data quality degraded")
   ```

4. **Use quality scores for filtering**
   ```python
   high_quality_games = [g for g in games if g.quality_score >= 0.8]
   ```

## Backward Compatibility

The system is designed to be backward compatible with the existing pipeline:

1. **CSV Format**: Output matches existing `espn_games.csv` format
2. **Field Names**: Uses same field names as existing code
3. **Fallback**: If multi-source fails, existing single-source pipeline still works

To gradually migrate:

```bash
# Phase 1: Run multi-source in parallel (different output file)
python scripts/refresh_multi_source.py --output ESPN/CSV/espn_games_merged.csv

# Phase 2: Compare outputs
diff ESPN/CSV/espn_games.csv ESPN/CSV/espn_games_merged.csv

# Phase 3: Replace single-source in production
# Update cron job or automation to use refresh_multi_source.py
```

## Troubleshooting

### High Conflict Rate

If seeing many conflicts:
1. Check if sources are using different game IDs
2. Verify timezone handling is consistent
3. Review source priority configuration

### Source Timeouts

If sources frequently timeout:
1. Increase timeout values in config
2. Add retry delays
3. Check network connectivity

### Missing Market Data

Only ESPN typically provides market lines. This is expected:
```python
# ESPN usually has market data
game.market_spread  # -5.5

# NCAA/Henry may not
game.market_spread  # None
```

The merger will use ESPN's market data when available.

## Future Enhancements

Potential improvements:
1. Parallel source fetching (threading/async)
2. Caching layer to reduce API calls
3. Machine learning for conflict resolution
4. Historical conflict analysis
5. Automatic source health monitoring
6. Real-time alerting for data quality issues
