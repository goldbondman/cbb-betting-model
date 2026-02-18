# CBBpy Integration Guide

## Overview

The ESPN data ingestion pipeline now integrates the **CBBpy library** as the primary data source, with automatic fallback to direct ESPN API calls when needed. This integration provides:

- **Better resilience**: CBBpy handles ESPN API changes and provides retry logic
- **Stable interface**: Your code doesn't break when ESPN changes their endpoints
- **Active maintenance**: CBBpy library maintainer absorbs ESPN breakage
- **Seamless fallback**: Automatically falls back to direct ESPN API if CBBpy fails

## What Changed

### New Files

1. **`ESPN/cbbpy_client.py`** - Wrapper module that integrates CBBpy with the existing ESPN client interface
2. **`tests/test_cbbpy_integration.py`** - Tests for the CBBpy integration

### Modified Files

1. **`requirements.txt`** - Added `cbbpy` dependency
2. **`ESPN/espn_config.py`** - Added CBBpy configuration options
3. **`ESPN/espn_http_client.py`** - Updated to optionally use CBBpy

## Configuration

The integration is controlled by environment variables:

### `ENABLE_CBBPY`
- **Default**: `1` (enabled)
- **Purpose**: Enable/disable CBBpy library usage
- **Values**: `1`, `true`, `yes` (enabled) or `0`, `false`, `no` (disabled)

### `CBBPY_FALLBACK_TO_ESPN`
- **Default**: `1` (enabled)
- **Purpose**: Automatically fall back to direct ESPN API if CBBpy fails
- **Values**: `1`, `true`, `yes` (enabled) or `0`, `false`, `no` (disabled)

### Example Usage

```bash
# Use CBBpy with fallback (recommended - default)
export ENABLE_CBBPY=1
export CBBPY_FALLBACK_TO_ESPN=1

# Use CBBpy without fallback (fail if CBBpy fails)
export ENABLE_CBBPY=1
export CBBPY_FALLBACK_TO_ESPN=0

# Disable CBBpy (use direct ESPN API only)
export ENABLE_CBBPY=0
```

## How It Works

### Data Flow

1. **ESPN HTTP Client** (`espn_http_client.py`)
   - Checks if `ENABLE_CBBPY` is enabled
   - If yes, attempts to use CBBpy via `cbbpy_client.py`
   - If CBBpy fails and `CBBPY_FALLBACK_TO_ESPN` is enabled, falls back to direct ESPN API
   - If no, uses direct ESPN API

2. **CBBpy Client Wrapper** (`cbbpy_client.py`)
   - `fetch_scoreboard_cbbpy()` - Fetches game IDs for a date using CBBpy
   - `fetch_summary_cbbpy()` - Fetches game details using CBBpy
   - Converts CBBpy DataFrame format to ESPN API JSON format
   - Maintains compatibility with existing parsers

3. **Data Conversion**
   - CBBpy returns pandas DataFrames
   - Wrapper converts them to ESPN API JSON format
   - Existing ESPN parsers work without modification

### Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│  ESPN Boxscore Builder (espn_boxscore_builder_modular.py)  │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        v
┌─────────────────────────────────────────────────────────────┐
│           ESPN HTTP Client (espn_http_client.py)            │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ fetch_scoreboard() / fetch_summary()                   │ │
│  │                                                        │ │
│  │ if ENABLE_CBBPY:                                       │ │
│  │   try CBBpy                                            │ │
│  │   if fail and CBBPY_FALLBACK_TO_ESPN:                 │ │
│  │     use direct ESPN API                                │ │
│  │ else:                                                  │ │
│  │   use direct ESPN API                                  │ │
│  └────────────────────────────────────────────────────────┘ │
└───────────┬────────────────────────────────┬────────────────┘
            │                                │
            v                                v
┌───────────────────────┐      ┌───────────────────────────┐
│  CBBpy Client Wrapper │      │   Direct ESPN API         │
│  (cbbpy_client.py)    │      │   (fetch_with_retry)      │
│                       │      │                           │
│  • fetch via CBBpy    │      │  • HTTP requests          │
│  • convert to ESPN    │      │  • retry logic            │
│    JSON format        │      │  • rate limiting          │
└───────┬───────────────┘      └───────────────────────────┘
        │
        v
┌───────────────────────┐
│   CBBpy Library       │
│   (cbbpy.mens_scraper)│
│                       │
│  • ESPN API wrapper   │
│  • retry logic        │
│  • error handling     │
│  • returns DataFrames │
└───────────────────────┘
```

## Benefits

### 1. **Resilience to ESPN API Changes**
   - CBBpy library maintainer updates endpoints when ESPN changes them
   - Your code stays the same
   - No more broken pipelines due to ESPN URL changes

### 2. **Better Error Handling**
   - CBBpy has exponential backoff
   - Handles rate limiting automatically
   - Normalized error messages

### 3. **Automatic Fallback**
   - If CBBpy fails, automatically falls back to direct ESPN API
   - No data loss
   - Transparent to existing code

### 4. **No Breaking Changes**
   - Existing parsers work without modification
   - Same data format (ESPN JSON)
   - Drop-in replacement

## Testing

Run the CBBpy integration tests:

```bash
python -m pytest tests/test_cbbpy_integration.py -v
```

### Test Coverage

- Module imports correctly
- Configuration values are read
- DataFrame to JSON conversion works
- Empty data is handled gracefully
- Fallback mechanism works

## Installation

The CBBpy library is automatically installed via `requirements.txt`:

```bash
pip install -r requirements.txt
```

Or install manually:

```bash
pip install cbbpy
```

## Troubleshooting

### CBBpy Not Working

If CBBpy is not working:
1. Check that `cbbpy` is installed: `pip list | grep cbbpy`
2. Check logs for error messages
3. Set `ENABLE_CBBPY=0` to disable and use direct ESPN API
4. Ensure `CBBPY_FALLBACK_TO_ESPN=1` for automatic fallback

### Data Format Issues

If you see data format issues:
1. Check the conversion functions in `cbbpy_client.py`
2. Verify CBBpy returns expected DataFrame columns
3. Check ESPN parsers are compatible with converted data

### Import Errors

If you see import errors:
```python
ImportError: No module named 'cbbpy'
```

Solution:
```bash
pip install cbbpy
```

## Migration Notes

### For Existing Code

**No changes required!** The integration is designed to be backward compatible.

All existing code that uses:
- `espn_http_client.fetch_scoreboard()`
- `espn_http_client.fetch_summary()`

Will automatically use CBBpy if enabled, or fall back to direct ESPN API.

### For New Code

Continue using the same functions:

```python
from espn_http_client import fetch_scoreboard, fetch_summary

# This will use CBBpy if enabled, or direct ESPN API otherwise
scoreboard = fetch_scoreboard("20240115")
summary = fetch_summary("401479097")
```

## Performance

CBBpy adds minimal overhead:
- Same data source (ESPN API)
- Similar or better response times
- Reduced failures due to better retry logic

## Support

### CBBpy Library
- **GitHub**: https://github.com/dcstats/CBBpy
- **PyPI**: https://pypi.org/project/CBBpy/
- **Issues**: https://github.com/dcstats/CBBpy/issues

### This Integration
- Check `tests/test_cbbpy_integration.py` for examples
- Review `ESPN/cbbpy_client.py` for implementation details
- Consult `ESPN/espn_config.py` for configuration options

## Future Enhancements

Potential improvements:
1. Add caching layer for frequently accessed games
2. Implement parallel fetching for multiple games
3. Add metrics/monitoring for CBBpy vs ESPN API performance
4. Extend integration to injury data fetching
5. Add support for women's basketball via `cbbpy.womens_scraper`

## Summary

The CBBpy integration provides a more resilient data ingestion pipeline that:
- ✅ Reduces data gaps from ESPN API issues
- ✅ Maintains backward compatibility
- ✅ Provides automatic fallback
- ✅ Is easy to configure and disable if needed
- ✅ Is well-tested and documented

The integration is **production-ready** and can be deployed immediately.
