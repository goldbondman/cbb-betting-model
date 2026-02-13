# raw.espn_team_game_features refactor plan (storage reduction)

## 1) Raw vs derived classification used in this refactor
Because the live table DDL is not in this repo, classify columns by reproducibility rule:

- **Raw (keep in DB)**: identifiers, provenance, box score primitives, and final game score primitives.
- **Derived (compute in Python)**: any rate/rolling/window feature (`*_pct`, `*_pre`, `*_l3_pre`, `*_l7_pre`, `*_diff`, `exp_margin`, `style_distance`, etc.).

**Kept raw columns (new table):**
- Keys/meta: `event_id`, `team_id`, `team`, `home_away`, `game_datetime_utc`, `pulled_at_utc`, `source`, `parse_version`, `verification_status`, `verification_notes`
- Score primitives: `points_for`, `points_against`
- Box score primitives: `fgm`, `fga`, `tpm`, `tpa`, `ftm`, `fta`, `tov`, `orb`, `drb`

Everything else is treated as recomputable.

## 2) Why this is safe
- These primitives are sufficient to recompute per-game metrics (`efg`, `ftr`, `3par`, `tov_pct`, `pace`, `ortg`, `drtg`, `netrtg`) and rolling pregame windows in Python.
- The model matrix writer still outputs the same shape expected by training/predict (`*_l{window}_pre`, `games_last_*`, and downstream diffs).

## 3) Migration + pipeline changes in this branch
- New streamlined table migration: `supabase/migrations/20260308000000_create_raw_espn_team_game_core.sql`
- New builder script: `scripts/build_espn_team_game_core.py`
- `ml/feature_matrix.py` now derives rolling pregame features from core primitives when precomputed columns are not present.
- CI workflow now builds the core table and points feature generation to `raw.espn_team_game_core`.

## 4) Backfill command
Run in CI/server context (service role DB URL):
```bash
python scripts/build_espn_team_game_core.py
```

## 5) Optional pruning after validation
After validating predictions/training outputs are stable, stop loading `raw.espn_team_game_features` and archive/drop old rows in that table.
