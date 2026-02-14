# Prediction Loading Troubleshooting Guide

## Problem
Supabase is connected to Streamlit, but predictions are not loading in the UI.

## Solution Summary
This update implements **multiple redundancy layers** to ensure predictions load successfully from various sources, with automatic fallbacks and detailed diagnostics.

## What Was Fixed

### 1. Multiple Data Source Redundancies

The application now tries multiple sources in order until predictions are found:

#### Daily Dashboard (`pages/1_Daily_Dashboard.py`)
1. **Primary**: `public.predictions` (filtered by today's date)
2. **Fallback 1**: `raw.predictions_latest` (source table)
3. **Fallback 2**: `public.predictions` (unfiltered, last 7 days)
4. **Fallback 3**: CSV files (`data/predictions.csv`, `ml/predictions_latest.csv`)

#### Core Data Loader (`core/data_loader.py`)
1. **Primary**: `public.predictions`
2. **Fallback 1**: `raw.predictions_latest`
3. **Fallback 2**: CSV files

#### Model Reports (`pages/2_Model_Reports.py`)
1. **Primary**: Reporting view
2. **Fallback 1**: `public.predictions`
3. **Fallback 2**: `raw.predictions_latest`
4. **Fallback 3**: CSV files

### 2. Column Name Normalization

Added automatic column mapping to handle naming variations across different data sources:
- `pred_spread` / `ensemble_prediction` → `pred_margin_home`
- `predicted_total` → `pred_total`
- `team_a` / `team_home` → `home_team`
- `team_b` / `team_away` → `away_team`
- `game_id` → `event_id`

### 3. Enhanced Error Handling

- Detailed logging at each fallback attempt
- Clear error messages indicating which source failed and why
- User-friendly troubleshooting guide in the UI
- Connection status indicators

### 4. RLS Policy Fix

New migration: `supabase/migrations/20260314000000_ensure_predictions_anon_read.sql`

Ensures both `public.predictions` and `raw.predictions_latest` allow anonymous reads, which is required for Streamlit (using the anonymous/public key).

### 5. Diagnostic Tool

New script: `scripts/diagnose_predictions.py`

Run this to diagnose prediction loading issues:
```bash
python scripts/diagnose_predictions.py
```

The script checks:
- ✓ Supabase credentials are set
- ✓ Client can connect to Supabase
- ✓ Tables exist and contain data
- ✓ RLS policies allow access
- ✓ Shows available columns and sample data
- ✓ CSV fallback files exist

## How to Use

### Quick Test
1. Run the diagnostic script:
   ```bash
   python scripts/diagnose_predictions.py
   ```

2. Check output for any issues

### If Predictions Still Don't Load

#### Check 1: Verify Credentials
```bash
echo $SUPABASE_URL
echo $SUPABASE_ANON_KEY
```
Both should be set. If using Streamlit Cloud, check your secrets configuration.

#### Check 2: Run the Daily Pipeline
The daily pipeline (`scripts/daily_auto_predict.py`) reads predictions from `raw.predictions_latest` and writes to `public.predictions`. Ensure it has run at least once:

```bash
python scripts/daily_auto_predict.py
```

#### Check 3: Verify RLS Policies
Run this SQL in Supabase SQL Editor:
```sql
-- Check predictions table policies
SELECT * FROM pg_policies WHERE tablename = 'predictions';

-- Should show a policy allowing 'anon' role to select
```

If no policy exists or it doesn't allow 'anon', apply the migration:
```bash
# In Supabase Dashboard > SQL Editor, run:
# supabase/migrations/20260314000000_ensure_predictions_anon_read.sql
```

#### Check 4: Verify Data Exists
Run in Supabase SQL Editor:
```sql
-- Check public.predictions
SELECT COUNT(*) FROM public.predictions;

-- Check raw.predictions_latest
SELECT COUNT(*) FROM raw.predictions_latest;
```

If both are empty, you need to:
1. Generate predictions (ML pipeline or CSV)
2. Load them to `raw.predictions_latest` using `load_csv_to_db.py`
3. Run `daily_auto_predict.py` to sync to `public.predictions`

#### Check 5: Use CSV Fallback
As a temporary workaround, place a predictions CSV file in:
- `data/predictions.csv`, or
- `ml/predictions_latest.csv`

The app will automatically use it if Supabase queries fail.

## Data Flow

```
┌─────────────────────┐
│  ML Pipeline        │
│  (generates CSV)    │
└──────┬──────────────┘
       │
       ▼
┌─────────────────────┐
│ load_csv_to_db.py   │
│ (loads CSV to DB)   │
└──────┬──────────────┘
       │
       ▼
┌─────────────────────────┐
│ raw.predictions_latest  │ ◄─── Source table (ML predictions)
└──────┬──────────────────┘
       │
       ▼
┌─────────────────────┐
│ daily_auto_predict  │
│ (joins with games)  │
└──────┬──────────────┘
       │
       ▼
┌─────────────────────┐
│ public.predictions  │ ◄─── Primary table (Streamlit reads from here)
└─────────────────────┘
       │
       ▼
┌─────────────────────┐
│ Streamlit App       │
│ (displays in UI)    │
└─────────────────────┘
```

## Fallback Chain

```
Streamlit App tries in order:
1. public.predictions (with date filter) ─┐
   └─ FAIL                                 │
                                           │
2. raw.predictions_latest ────────────────┤
   └─ FAIL                                 │
                                           ├─ First success wins
3. public.predictions (no filter) ────────┤
   └─ FAIL                                 │
                                           │
4. CSV files (data/ or ml/) ──────────────┘
   └─ FAIL
   
   → Show "No predictions" message
```

## Testing Checklist

After applying these fixes:
- [ ] Run `python scripts/diagnose_predictions.py`
- [ ] Verify Supabase credentials are set
- [ ] Check that at least one table has data
- [ ] Open Daily Dashboard in Streamlit
- [ ] Verify predictions load (or see helpful error)
- [ ] Check browser console for any errors
- [ ] Test with empty database (should fall back to CSV)

## Common Issues & Solutions

### Issue: "Permission denied" error
**Cause**: RLS policy blocks anonymous access  
**Solution**: Apply migration `20260314000000_ensure_predictions_anon_read.sql`

### Issue: "No predictions found"
**Cause**: Both Supabase tables and CSV files are empty  
**Solution**: Run ML pipeline and `daily_auto_predict.py`

### Issue: "Column not found" error
**Cause**: Column name mismatch between sources  
**Solution**: Already fixed - column normalization handles this automatically

### Issue: Data in `raw.predictions_latest` but not `public.predictions`
**Cause**: Daily pipeline hasn't synced the data  
**Solution**: Run `python scripts/daily_auto_predict.py`

## Files Changed

1. **pages/1_Daily_Dashboard.py** - Added redundancies and column normalization
2. **pages/2_Model_Reports.py** - Added redundancies
3. **core/data_loader.py** - Added redundancies
4. **supabase/migrations/20260314000000_ensure_predictions_anon_read.sql** - RLS policy fix
5. **scripts/diagnose_predictions.py** - New diagnostic tool
6. **TROUBLESHOOTING_PREDICTIONS.md** - This guide

## Support

If predictions still don't load after following this guide:
1. Share the output of `python scripts/diagnose_predictions.py`
2. Check Supabase logs for any errors
3. Verify the daily pipeline ran successfully (GitHub Actions logs)
4. Check if games are scheduled for today (ESPN API may return empty)
