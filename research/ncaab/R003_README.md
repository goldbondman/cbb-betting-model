# NCAAB R003 research branch: PIT forecast locks

This research branch does not alter production R1/R2 and must not be merged or deployed without governance approval.

## What was tested on November 5, 2026

- Cold start: 2024-25 prior-season adjusted O/D with 0-4 2025-26 prior D-I games. k=4/10/20 posterior updates. This uses a repeatedly examined 2025-26 retrospective holdout.
- Extreme mismatch: 2024-25 team-strength rating trained on inferred D-I games. Separate game grouping for D-I-inferred vs division-unverified opponents; **not certified as lower-division**.
- M5: multivariate Student-t vs Gaussian on *joint total and home-margin residuals*, fitted on 2024-25 Feb-Apr calibration and assessed on 2025-26. Student-t(30) best calibration-period joint likelihood; very small 2025 improvement, no model promotion.
- Official NCAA Sept 2025 membership report confirms 361 active D-I institutions and specific reclassifying programs. Individual team-season crosswalk remains incomplete.

## Input/output contract

The `forecast_lock_v1.py` tool freezes a supplied pregame **market-blind** forecast and its actual, source-versioned input values. It does **not** make the forecast.

Required event fields: `event_id`, `tipoff_at`, `forecast_at`, `data_cutoff`, `model_version`, `population_version`, `source_dataset_sha256`, `expected_home_score`, `expected_away_score`, `expected_margin_home`, `expected_total`, `input_records`.

Each input record has `source_id`, `published_at` (first-known availability, not retrospective retrieval), `content_sha256`, and `features` (the actual values used, not just column names). Every source timestamp must precede `data_cutoff`.

Use `python research/ncaab/forecast_lock_v1.py --input game_manifest.json --archive-dir research_forecast_locks/`.

The file is stored by exclusive-create and hashes the canonical inputs and forecast fields with SHA-256. Commit frozen files to a versioned research branch or protected artifact store promptly. File creation alone is not an independently authenticated timestamp, and a Git commit made after tipoff is not proof of an actual pregame freeze.

## Fail closed

Reject future or missing timestamps, unavailable or duplicated sources, unversioned inputs, unrecognized forecast/score arithmetic, outcomes and market-related predictor fields, or existing file path. Do not use downstream sportsbook data to fill missing Stage A inputs.

Existing Nov 6 2026 `R2_2026_FORECASTS` has eight market-blind prospectively dated means and a reported CSV hash, but lacks a complete verified first-publication input manifest. Keep those rows unchanged and classify full source-vintage authentication as pending.

Data and results: NCAAB Master Team Box Scores Google Sheet, tabs `COLDSTART_R003_RESULTS`, `COLDSTART_R003_REPLAY`, `EXTREME_R003_RESULTS`, and `M5_TAIL_R003_RESULTS`. The retrospective 992-game replay ledger is **NOT** an original pregame forecast lock.

Next: Run this validator on new 2026-27 forecasts before tipoff, with true source timestamps, and append results separately after games end. Independent verification required before any production or betting promotion.
