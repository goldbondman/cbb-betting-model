# RT-0024 / RT-0026 / RT-0029 — score-only recovery and certification protocol

Status: RESEARCH BRANCH ONLY. No champion change, deployment, or empirical certification.

## Input and identity
Use an immutable game-level CSV/Parquet snapshot with SHA256, schema, date range, row count, season counts and source commit. One row per game; reject duplicate game IDs, missing team IDs, ties, impossible scores and games not final. Reconcile game counts against DATA001's reported 28,996 games; do not silently equate ESPN CSV to DATA001.

## Strict PIT
Forecast timestamp must precede tipoff; each historical training result must have finalization timestamp earlier than forecast timestamp. Date-only comparisons must conservatively exclude same-date games. No retrospective feature snapshots unless effective/published timestamps prove availability. Never include realized current-game box statistics, blowout, outcome, status, market lines, or closing odds in Stage A.

## B0/B1 score-only models
B0: prior completed-game team mean points scored/allowed with shrinkage to league mean, cold-start fallback preregistered. B1: independent offense/defense ratings, home court intercept fit only on training data. Elo: chronological score-result updates after finalization. Recency: L3/L5/L7 and EWMA tuned within nested prior-season folds. All models produce two expected scores, margin and total; win probabilities only with distribution parameters fitted without test leakage.

## Evaluation
Rolling-origin by game and leave-one-future-season-out. Predeclare training minimum, season cutoff, hyperparameter grid, loss functions and exclusion reasons. Primary: mean team-score MAE and margin MAE; secondary: total MAE, bias, Brier/log loss, interval coverage. Compare paired game losses, season-wise signs, confidence intervals and multiple comparisons; abstain if power insufficient.

## Coverage
DATA013 denominator: all scheduled/final games by season. Flags: score_complete, box_complete, pit_eligible, source_vintage_certified, model_evaluable, exclusion_reason. Report by season, conference, neutral-site and phase. Never infer certification from the existence of a final score.

## Hard stops
Missing canonical DATA001 snapshot or checksum => HOLD_DATA for champion comparison. Missing frozen blind forecasts => no retrospective champion comparison or probability calibration claim. Market fields remain Stage B only. Never promote challenger based on in-sample or exploratory metrics.

## Ten acceptance checks
1. Immutable source snapshot and SHA recorded.
2. Unique stable game identity and score coherence verified.
3. Season counts and completeness reconciled with DATA001.
4. Exact PIT cutoff, source vintage and finalization rule verified.
5. Current-game outcome and market leakage negative tests pass.
6. Cold-start prior and fallback contract frozen.
7. B0/B1/Elo/recency candidates reproducibly implemented.
8. Rolling-origin and held-out-season folds executed.
9. Paired losses, uncertainty, subgroup coverage and multiplicity audited.
10. Independent audit and promotion decision; default HOLD until 1–9 pass.
