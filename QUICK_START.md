# Quick Start Guide - Prediction Loading Fix

## Problem Solved
✅ Supabase is connected but predictions were not loading in Streamlit

## Solution Implemented
**Multiple redundancies** to ensure predictions load from various sources with automatic fallbacks.

## Quick Fix Steps

### 1. Deploy the Changes (Already Done ✅)
All code changes have been committed to the PR branch.

### 2. Apply RLS Migration
In Supabase SQL Editor, run:
```sql
-- From: supabase/migrations/20260314000000_ensure_predictions_anon_read.sql

alter table if exists public.predictions enable row level security;

drop policy if exists predictions_read on public.predictions;
create policy predictions_read on public.predictions
for select to anon, authenticated
using (true);
```

### 3. Verify Setup
```bash
# Check credentials and connectivity
python scripts/diagnose_predictions.py

# Verify all redundancies work
python scripts/verify_redundancies.py
```

### 4. Test in Streamlit
- Open the Daily Dashboard page
- Predictions should now load from one of the 4 sources
- Check console/logs for which source was used

## What Was Fixed

### 4 Layers of Redundancy
1. **Primary**: `public.predictions` (today's games)
2. **Fallback 1**: `raw.predictions_latest` (all predictions)
3. **Fallback 2**: `public.predictions` (no date filter)
4. **Fallback 3**: CSV files

### Automatic Column Mapping
Different column names work automatically:
- `pred_spread` → `pred_margin_home`
- `team_a` → `home_team`
- `game_id` → `event_id`

### Better Error Messages
Clear troubleshooting guide shown when predictions don't load.

## Troubleshooting

### Still Not Loading?

**Step 1**: Run diagnostic
```bash
python scripts/diagnose_predictions.py
```

**Step 2**: Check what it found
- ✅ Credentials OK → Continue
- ❌ Credentials missing → Set `SUPABASE_URL` and `SUPABASE_ANON_KEY`

**Step 3**: Check tables
```sql
-- In Supabase SQL Editor
SELECT COUNT(*) as public_count FROM public.predictions;
SELECT COUNT(*) as raw_count FROM raw.predictions_latest;
```

**Step 4**: If both empty
```bash
# Run the daily prediction pipeline
python scripts/daily_auto_predict.py
```

**Step 5**: If permission denied
- RLS policy blocks anon access
- Apply the migration (see Step 2 above)

### Common Issues

| Issue | Cause | Fix |
|-------|-------|-----|
| "No predictions found" | Tables empty | Run daily pipeline |
| "Permission denied" | RLS blocks anon | Apply RLS migration |
| "Column not found" | Name mismatch | Already fixed (auto-mapped) |
| Stale data | Cache issue | Restart Streamlit |

## Verify It's Working

### Expected Logs (Success)
```
INFO: Attempting to load predictions from public.predictions
INFO: ✓ Loaded 15 predictions from public.predictions
```

### Expected Logs (Fallback)
```
INFO: Attempting to load predictions from public.predictions
INFO: No predictions in public.predictions for today
INFO: Attempting fallback to raw.predictions_latest
INFO: ✓ Loaded 15 predictions from raw.predictions_latest
```

### Expected UI (Success)
- Daily Dashboard shows games with predictions
- No warning messages
- Data displays correctly

### Expected UI (All Sources Empty)
```
⚠️ No predictions found

🔍 Troubleshooting Information
[Helpful guide with next steps]
```

## Files to Know

**For Users:**
- `TROUBLESHOOTING_PREDICTIONS.md` - Complete troubleshooting guide
- `scripts/diagnose_predictions.py` - Diagnostic tool

**For Developers:**
- `REDUNDANCY_IMPLEMENTATION.md` - Technical implementation details
- `tests/test_prediction_column_normalization.py` - Unit tests
- `scripts/verify_redundancies.py` - Integration tests

## Testing Checklist

- [ ] Run `python scripts/diagnose_predictions.py`
- [ ] Run `python scripts/verify_redundancies.py`
- [ ] Apply RLS migration in Supabase
- [ ] Open Daily Dashboard
- [ ] Verify predictions display
- [ ] Check logs for which source was used
- [ ] Try with empty tables (should show helpful error)

## Support

If you need help:
1. Share output of `diagnose_predictions.py`
2. Check `TROUBLESHOOTING_PREDICTIONS.md`
3. Review Streamlit logs
4. Check Supabase logs

## Success! 🎉

When working correctly, you should see:
- ✅ Predictions load in Daily Dashboard
- ✅ No error messages
- ✅ Automatic fallback if primary source fails
- ✅ Helpful error if all sources empty
- ✅ Clear logs showing which source was used
