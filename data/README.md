# Data Directory – CSV Fallback for Streamlit App

This directory holds CSV snapshots that the Streamlit app can use when
Supabase is unavailable (no credentials, network errors, etc.).

## Files

| File | Description | Refresh Script |
|------|-------------|----------------|
| `predictions.csv` | Latest model predictions (margin, total, edges) | `scripts/daily_auto_predict.py` or `ml/predict_ml.py` |

## How It Works

The `core/data_loader.py` module checks these CSV files as a fallback
when Supabase queries fail or credentials are missing.  The lookup order
for predictions is:

1. Supabase `predictions_latest` table
2. `data/predictions.csv` (committed to repo)
3. `ml/predictions_latest.csv` (generated locally)

For game data the order is:

1. `ESPN/CSV/espn_games.csv` (committed to repo)
2. `espn_games.csv` (local, gitignored)

## Refreshing Data

After running the daily pipeline or ML prediction step, copy the output
CSV into this directory so it is available as a committed fallback:

```bash
cp ml/predictions_latest.csv data/predictions.csv
```
