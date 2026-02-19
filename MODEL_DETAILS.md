# Model Details

Comprehensive reference for every prediction model in the CBB Betting Model system. Each section covers the model's purpose, algorithm, features, outputs, key parameters, and performance tracking.

---

## Table of Contents

1. [Primary Prediction Model (v2.0)](#1-primary-prediction-model-v20)
2. [Recursive Prediction Engine](#2-recursive-prediction-engine)
3. [Formula Prediction Engine](#3-formula-prediction-engine)
4. [Ridge Linear Regression Models](#4-ridge-linear-regression-models)
5. [Ensemble Stacker](#5-ensemble-stacker)
6. [Formula Model Lab](#6-formula-model-lab)
7. [Totals Model](#7-totals-model)
8. [Tournament Quant Pipeline](#8-tournament-quant-pipeline)
9. [Conference Tournament Model](#9-conference-tournament-model)
10. [Quant 1 – Archeologist (Team Archetype Profiler)](#10-quant-1--archeologist-team-archetype-profiler)
11. [Quant 2 – Upset Hunter](#11-quant-2--upset-hunter)
12. [Quant 3 – Executioner (Favorite Fragility)](#12-quant-3--executioner-favorite-fragility)
13. [Quant 4 – Situationist](#13-quant-4--situationist)
14. [Quant 5 – Mathematician (Integration Engine)](#14-quant-5--mathematician-integration-engine)
15. [Variant Models (Lookback Window Ridge)](#15-variant-models-lookback-window-ridge)
16. [Supporting Components](#16-supporting-components)

---

## 1. Primary Prediction Model (v2.0)

**File:** `primary_prediction_model.py`, `core/primary_prediction_engine.py`
**Model ID:** `primary_v2_normalized_bidirectional`
**Status:** Production

### Algorithm

Normalized bidirectional analysis that compares each team's performance against opponent baselines rather than raw stats. The model builds a three-layer normalized baseline for every opponent to determine how a team truly performs relative to expectation.

**Three-Layer Normalized Baseline:**

| Layer | Description | Method |
|-------|-------------|--------|
| Raw Baseline | Simple average of points an opponent allowed across their L5 games | Arithmetic mean |
| Opponent-Quality Weighted | Weight each game by quality of opponent faced | Elite opponent (+10 net eff) → 1.5× weight; weak opponent (−10 net eff) → 0.5× weight |
| Schedule-Adjusted | Account for strength of schedule in the baseline | `adjusted = weighted_baseline − (avg_opp_off_eff − 105) × 0.5` |

**Prediction Flow:**

1. Collect L5 and L10 game histories for each team (with exponential decay weighting)
2. Build three-layer normalized baselines for each opponent in those histories
3. Calculate raw metrics and performance-vs-expectation for Dean Oliver Four Factors and efficiency
4. Blend: **70% vs-expectation + 30% raw stats**
5. Compute weighted composite edge across all components
6. Combine efficiency edge (60%) and composite edge (40%)
7. Apply home court advantage (spread only, not pace-scaled)
8. Calculate total via pace-adjusted expected points

### Features

- **Box Score Stats:** FGM/FGA, 3PM/3PA, FTM/FTA, ORB, DRB, TOV
- **Dean Oliver Four Factors:** eFG%, TOV%, ORB%, DRB%, FTR%
- **Efficiency Metrics:** Offensive/defensive efficiency (pts per 100 possessions)
- **Possession Estimates:** Averaged across both teams (Dean Oliver formula)
- **Recursive Opponent Context:** Opponent's L5 pre-game opponent box scores
- **Home/Away Designation:** Neutral site indicator

**Component Weights:**

| Component | Weight |
|-----------|--------|
| eFG% | 28% |
| TOV% | 22% |
| ORB% | 18% |
| DRB% | 16% |
| FTR% | 16% |

### Outputs

| Field | Description |
|-------|-------------|
| `predicted_spread` | Negative value = home favored |
| `predicted_total` | Combined game total (pace-adjusted) |
| `confidence` | 0.05–0.95 range |
| `home_net_eff` / `away_net_eff` | Net efficiency ratings |
| `pace` | Expected possessions |
| `breakdown` | Component-level analysis (raw_edge, eff_edge, composite_edge, per-factor deltas, HCA) |

### Key Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `decay_type` | `smooth` | Game weighting curve (smooth / plateau / simple) |
| `default_hca` | 3.2 | Home court advantage in points |
| `min_games_for_full_confidence` | 5 | Games needed for 100% sample confidence |
| `consistency_threshold` | 20.0 | L5-vs-L10 net eff delta cap |
| `avg_pace` | 70.0 | League-average possessions |
| `league_avg_off_eff` | 105.0 | League-average offensive efficiency |
| `schedule_adjustment_factor` | 0.5 | SOS baseline correction strength |
| `min_opp_weight` / `max_opp_weight` | 0.5 / 2.0 | Opponent quality weight bounds |

### Confidence Calculation

- **60%** sample size component (min 5 games per team for full credit)
- **40%** consistency component (L5 vs L10 net efficiency delta / 20 threshold)
- Clamped to 5%–95%

---

## 2. Recursive Prediction Engine

**File:** `core/recursive_prediction_engine.py`
**Status:** Enhanced

### Algorithm

Recursive bidirectional analysis that establishes what each opponent typically allows and then measures how the team performed relative to that expectation. This is the foundational insight behind the primary model: context-dependent performance evaluation.

**Process:**

1. Get team's L5 and L10 game logs
2. For each game, establish what the opponent typically allows (opponent baseline from their L5 history)
3. Calculate team's performance vs that expectation (the edge)
4. Aggregate with opponent context embedded
5. Blend L5 (70%) and L10 (30%) windows
6. Blend vs-expectation (70%) and raw stats (30%)

### Features

- **Raw Performance:** Four Factors (eFG%, TOV%, ORB%, DRB%, FTR%), efficiency metrics
- **Opponent Baselines:** What opponent allows/forces based on their L5 history
- **Performance vs Expectation:** Raw metrics minus opponent's typical defensive baseline

### Outputs

| Field | Description |
|-------|-------------|
| `predicted_spread` | Point spread prediction |
| `confidence` | Sample + consistency based |
| `breakdown.raw_edge` | Raw performance edge |
| `breakdown.eff_edge` | Efficiency-based edge |
| `breakdown.composite_edge` | Combined component edge |
| `breakdown.sos_factor` | Strength of schedule adjustment |
| `breakdown.hca` | Home court advantage applied |

### Key Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `l5_weight` | 0.70 | Weight for L5 window |
| `l10_weight` | 0.30 | Weight for L10 window |
| `vs_exp_weight` | 0.70 | Weight for performance vs opponent baseline |
| `raw_weight` | 0.30 | Weight for raw stats |
| `sos_strength` | 0.35 | Strength of schedule adjustment factor |
| `default_hca` | 3.2 | Home court advantage in points |

---

## 3. Formula Prediction Engine

**File:** `core/prediction_engine.py`
**Status:** Fallback / Registry-Driven

### Algorithm

Weighted linear combination of four core metrics. Used as a fallback when the primary model is unavailable, and powers the Model Registry for user-created formula models.

**Prediction:**
```
spread = w1 × torvik_edge + w2 × recent_edge + w3 × ff_edge + w4 × sos_edge + HCA
```

### Features

| Feature | Default Weight | Calculation |
|---------|---------------|-------------|
| Torvik AdjEM | 55% | `home_torvik_adj_em − away_torvik_adj_em` |
| Recent Net Rating (L7) | 25% | `home_netrtg_l7_pre − away_netrtg_l7_pre` |
| Four Factors Edge | 20% | `(eFG − TOV% + ORB% + FTR) × 10` for each team, then difference |
| SOS Weighted Margin (L10) | 0% | `(home_sos_margin − away_sos_margin)` |

### Outputs

| Field | Description |
|-------|-------------|
| `predicted_spread` | Point spread prediction |
| `confidence` | 0.50–0.95 based on sample size |
| `model_id` | Registry model ID or `"fallback"` |
| `breakdown` | Per-component edges, weights used, HCA |

### Key Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `kelly_fraction` | 0.25 | Bet sizing fraction |
| `max_units` | 3.0 | Maximum units per bet |
| `min_edge` | 0.03 | Minimum edge to trigger bet |
| `min_confidence` | 0.60 | Minimum confidence to trigger bet |

---

## 4. Ridge Linear Regression Models

**Files:** `ml/train_ml_models.py` (training), `ml/predict_ml.py` (inference)
**Artifacts:** `ml/models/margin_model.json`, `ml/models/total_model.json`

### Algorithm

Ridge regression (L2 regularization) trained separately for margin and total prediction. The intercept is not regularized. Uses a numerical solver (`np.linalg.solve`) for stability. Validation uses a forward-looking time-series split.

### Features

- Automatically extracted from `ml/model_features.csv`
- Auto-excludes: event IDs, team IDs, actual outcomes, suspicious "actual_*" columns
- Drops constant features (std = 0)
- Imputes NaN values with per-feature medians
- Engineered features from `ml/features_v2.py`:

| Feature | Calculation |
|---------|-------------|
| `tempo_gap_l7` | pace_home − pace_away |
| `shot_quality_gap_l7` | eFG%_home − eFG%_away |
| `turnover_gap_l7` | TOV%_away − TOV%_home |
| `rebound_gap_l7` | (ORB_h − DRB_a) − (ORB_a − DRB_h) |
| `ftr_gap_l7` | FT rate home − away |
| `three_rate_gap_l7` | 3PA rate home − away |
| `netrtg_gap_l7` | netrtg_home − netrtg_away |
| `rest_gap_3d` | games_played_away − games_played_home (last 3 days) |
| `rest_gap_7d` | same for 7 days |
| `style_distance_l7` | (style_dist_home + style_dist_away) / 2 |

### Outputs

- **margin_model.json** → Predicts `actual_margin_home`
- **total_model.json** → Predicts `actual_total`
- **predictions_latest.csv** → Row-level predictions with `pred_margin_home`, `pred_total`, metadata, and `row_hash` (SHA-256)

### Key Parameters

| Parameter | Default | Environment Variable |
|-----------|---------|---------------------|
| Ridge λ | 0.01 | `ML_RIDGE_LAMBDA` |
| Validation split | 10% | `ML_VAL_SPLIT` |
| Min training rows | 25 | `ML_MIN_TRAIN_ROWS` |
| Max NaN threshold | 80% | `ML_MAX_NAN_PCT` |
| Min feature variance | 0.01 | — |

### Performance Metrics

Stored in each model JSON: train RMSE, validation RMSE, rows cleaned/dropped, features used/dropped counts.

---

## 5. Ensemble Stacker

**File:** `ml/ensemble.py`

### Algorithm

Two combination methods for merging multiple model predictions:

**Weighted Average:**
- Combines prediction vectors via normalized weights
- Validates shape consistency, weight finiteness, normalization

**Ridge Stacker:**
- Fits a secondary ridge regression over base model predictions
- Intercept not regularized
- Sanitizes non-finite values (inf/NaN → 0) to prevent solver crashes
- Default L2 penalty: 0.001

### Key Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| Ridge L2 | 0.001 | Regularization for stacker |
| `fit_intercept` | True | Whether to include bias term |

### Optimization

**File:** `ml/optimize_ensemble.py`

Brute-force grid search over all weight combinations summing to 1.0 with a configurable step size. Evaluates via MAE (minimize), MSE (minimize), or hit rate (maximize). Outputs `ensemble_weights.json`.

---

## 6. Formula Model Lab

**Files:** `pages/2_Formula_Model_Lab.py`, `pages/3_Model_Lab.py`
**Docs:** `docs/FORMULA_MODEL_FEATURES.md`

### Algorithm

User-configurable weighted linear combination of 8 features. Weights are auto-normalized to sum to 1.0. Supports both static and dynamic home court advantage and optional pace adjustment.

### Features

| Feature | Default Weight | Calculation |
|---------|---------------|-------------|
| Torvik AdjEM | 40% | `home_torvik_adj_em − away_torvik_adj_em` |
| Recent (L7) | 20% | `home_netrtg_l7_pre − away_netrtg_l7_pre` |
| Four Factors | 12% | `(eFG − TOV% + ORB% + FTR) × 10` per team |
| SOS Weighted | 8% | `(home_sos − away_sos) / 10` |
| Defensive Efficiency | 8% | `away_drtg_l7_pre − home_drtg_l7_pre` |
| Offensive Efficiency | 6% | `home_ortg_l7_pre − away_ortg_l7_pre` |
| Tempo Advantage | 4% | `(home_pace − away_pace) × 0.15` |
| Three-Point Rate | 2% | `(home_3par − away_3par) × 20` |

### Key Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| HCA mode | static | `static` or `dynamic` |
| HCA static value | 2.7 | Points added for home team |
| Pace adjustment | True | Apply tempo correction |
| Confidence method | sample_size_boost | How confidence is calculated |

### Model Registry

**Files:** `core/model_registry.py`, `data/model_registry_local.json`

Dual-backend registry (Supabase primary, local JSON fallback). Supports CRUD operations, model activation/deactivation, and filtering by type (spread / total / blowout / multihead / ensemble).

### Backtesting

**File:** `backtesting/backtest_engine.py`

Applies formula model parameters to historical pre-game snapshots and compares against actual margins and market spreads. Reports MAE, win%, ROI, edge distribution, and value scan (ROI by minimum edge threshold).

---

## 7. Totals Model

**File:** `ml/totals_model.py`

### Algorithm

Hybrid analytical model for tournament game totals. Projects full game, first half, and second half totals using Points Per Possession (PPP) and possession estimates with round-specific and conference-tier deflators.

**Core Calculation:**
```
Possessions = base_pace − elimination_suppression(−1.5) − fatigue − foul_rate(−0.5)
PPP = (off_ppp + def_ppp_allowed) / 2 − deflators
Expected Points = PPP × Possessions
```

**Half Splits:** Full game × 0.485 (first half), remainder for second half.

### Deflators

| Factor | Range |
|--------|-------|
| Round deflator | −1.5% to −4% by round |
| Conference tier deflator | −1% to −2% by tier |

### Outputs

| Component | Description |
|-----------|-------------|
| Q2 | Possession projections and pace battle |
| Q3 | Efficiency metrics, foul mismatches |
| Q4 | Half projections and edges |
| Q5 | Full game projection with 80% confidence interval (±6 points) |

### Bet Rules

| Market | Min Edge | Max Bet |
|--------|----------|---------|
| Full game | 1.5 pts | 100% |
| First half | 1.0 pts | 60% |
| Second half | 1.5 pts | 75% |

---

## 8. Tournament Quant Pipeline

**File:** `ml/tournament_quant_pipeline.py`

### Algorithm

Composite five-step scoring framework (Q1–Q5) designed specifically for March Madness and conference tournament play. Combines archetype analysis, upset probability, fragility indexing, situational factors, and mathematical integration.

| Step | Module | Purpose |
|------|--------|---------|
| Q1 | Archetype | Team style profiling (e.g., ELITE_DEFENSE_WINS) |
| Q2 | Upset Hunter | Underdog DNA score + upset probability |
| Q3 | Fragility | Favorite weakness detection (load mgmt, foul trouble, dependency) |
| Q4 | Situationist | Situational edges (rest, motivation, crowd, travel) |
| Q5 | Mathematician | Composite edge score (weighted average of Q1–Q4) |

### Contextual Features

- Bye rest advantage, conference tournament bye rounds
- NIT demoralization, First Four short-rest flags
- Defense undervalued detection, seed vs efficiency gap
- Geographic proximity, weather region mismatch
- Style clash detection (slow vs fast, Princeton offense, pack line)

### Outputs

- Recommended bet + confidence tier (A / B / C)
- Kelly-recommended unit size (`composite_edge / 70`)
- Timing windows, correlation groups

---

## 9. Conference Tournament Model

**File:** `ml/conf_tournament_model.py`

### Algorithm

Specialized composite edge model for conference tournaments with team profiling, upset detection adjusted for third-meeting dynamics, and round-specific bet sizing.

**Upset View Formula:**
```
score = 0.25 + 0.35 × dog_dna + 0.08 × third_meeting_adj + 0.04 × fatigue
```

**Fragility Weights:** Load management (45%), game 3 fatigue wall (25%), key player dependency (20%), motivation (10%).

### Edge Thresholds

| Conference Tier | Min Edge |
|-----------------|----------|
| Power 6 | 2.0 pts |
| Mid-Major | 1.5 pts |
| Low-Major | 1.0 pts |

### Bet Sizing Rules

| Round | Max Bankroll % | Open Window |
|-------|---------------|-------------|
| Early rounds | 50% | 12 hrs before |
| Semifinals | 75% | 6 hrs before |
| Finals | 100% | 4 hrs before |

---

## 10. Quant 1 – Archeologist (Team Archetype Profiler)

**File:** `ml/quant1_archeologist.py`

### Algorithm

Rules-based heuristic analysis with historical base rates. Classifies teams into winning and failure archetypes to assess tournament viability.

### Features

- Defensive rank, tournament experience, free throw %, 3-point rate, guard scoring share
- Strength of schedule, road games vs top-50, win streak entering tournament
- Center foul trouble minutes, transfer portal count, coach tournament history
- Tempo, conference tournament games, seed inflation score

### Winning Archetypes

| Archetype | Signal |
|-----------|--------|
| Elite Defense Wins | Top-tier defensive rank with historical Final Four/championship base rates |
| Experienced March | Roster with deep tournament minutes history |
| Free Throw Clutch | Elite FT shooting for late-game resilience |
| Low Variance Offense | Consistent, non-volatile offensive system |
| Guard Dominant | Backcourt-driven scoring in tournament settings |
| Battle-Tested Schedule | Strong road/neutral record against elite opponents |
| Hot Entry | Extended win streak entering the tournament |

### Failure Archetypes

| Archetype | Risk |
|-----------|------|
| 3-Point Dependent | Over-reliance on three-point shooting |
| One-Big Dependent | Single dominant player creating depth risk |
| Soft Schedule Fraud | Inflated record against weak competition |
| Transfer Chaos | Roster instability from heavy portal use |
| Style Mismatch Vulnerable | Susceptible to specific opponent styles |
| Coach Inexperience | Limited tournament coaching history |
| Tournament Exhaustion | Fatigue from deep conference tournament run |

### Validation

CLV delta and ATS ROI against closing lines (2015–2025 historical data).

---

## 11. Quant 2 – Upset Hunter

**File:** `ml/quant2_upset_hunter.py`

### Algorithm

Weighted composite scoring of underdog "DNA" factors with round-specific adjustments and market spread multipliers.

**Core Formula:**
```
dog_dna_score = weighted_mean(10 factors)           → 0 to 1
base_upset     = 0.15 + min(spread, 14) × 0.015
upset_prob     = base_upset × (0.75 + dog_dna_score) × round_adjustment
```

### Features (10 Factors)

| Factor | Weight | Description |
|--------|--------|-------------|
| KenPom Underrated | 1.20 | Seed vs efficiency gap |
| Slow-It-Down Capacity | 1.35 | Ability to control tempo |
| Free Throw Disparity | 1.15 | FT advantage over opponent |
| Defense vs Offense Gap | 1.0 | Defensive efficiency edge |
| Tempo Differential | 1.0 | Pace mismatch |
| 3PT Defensive Mismatch | 1.0 | Perimeter defense vs opponent's 3PT offense |
| Rebounding Parity | 1.0 | Board battle competitiveness |
| Turnover Forcing | 1.0 | Steals and forced turnover rates |
| Tournament Experience | 1.0 | Players with 10+ tournament minutes |
| 3PT Shooting Volume | 1.0 | Three-point attempts and efficiency |

### Round Adjustments

| Round | Multiplier |
|-------|-----------|
| Round of 32 | 0.94 |
| First Four | 0.97 |
| Conference QF | 1.03 |
| NIT R1 | 1.04 |

### Outputs

- Upset probability (0–1), archetype label (LIVE_DOG / VOLATILE_DOG / LONGSHOT_DOG)
- Key upset drivers (factors ≥ 0.6)
- Market edge vs spread
- Probability cap flag for auto-bid vs power program mismatches

### Validation

Brier score and CLV delta metrics.

---

## 12. Quant 3 – Executioner (Favorite Fragility)

**File:** `ml/quant3_executioner.py`

### Algorithm

Identifies structural weaknesses in favored teams that could lead to early tournament exits. Produces a fragility index from 0 (solid) to 1 (fragile).

### Fragility Drivers

| Driver | Signal |
|--------|--------|
| EFFICIENCY_CLIFF | Point differential rank diverges from KenPom rank |
| TURNOVER_TIMEBOMB | High turnover rate vs underdog's steal rate |
| PERIMETER_EXPOSURE | Weak perimeter defense vs opponent's 3PT volume |
| DEPTH_ILLUSION | Top-6 players carry excessive minute share |
| SLOW_START_TENDENCY | Second half net rating > first half net rating |
| ROAD_WARRIOR_FALSE | Home efficiency far exceeds road/neutral efficiency |
| RECENCY_TRAP | Recent 8-game win streak masks season-long issues |
| INTERIOR_DOMINANCE_FALLACY | 2-point strength negated by opponent's zone usage |

### Modifiers

- **Brand Premium:** Duke, Kentucky, Kansas, North Carolina, Gonzaga receive 1.25× fragility multiplier (market overvalues brand)
- **Fatigue Multiplier:** 5+ games in 9 days increases fragility
- **Tournament Hangover:** Recent conference tournament final loss

### Outputs

- Fragility index (0–1)
- Early / first / second round exit probabilities
- Recommended action: fade / monitor / avoid
- Fragility drivers list

---

## 13. Quant 4 – Situationist

**File:** `ml/quant4_situationist.py`

### Algorithm

Quantifies psychological, physical, and environmental factors that don't appear in traditional box-score stats. Produces a situational edge score from −1 to +1.

### Positive Factors (5)

| Factor | Description |
|--------|-------------|
| Selection Criticism | Team playing with a chip on their shoulder |
| Senior Farewell | 3+ seniors in final tournament |
| Auto-Bid Relief | Low-seed team relieved to be in the tournament |
| Revenge Matchup | Previous loss to this opponent |
| Crowd Support | Home-region crowd advantage percentage |

### Negative Factors (10)

| Factor | Description |
|--------|-------------|
| NIT Demoralization | Scaled by program tier disappointment multiplier |
| Travel Burden | `miles / 2200 + timezone_changes × 0.15` |
| Altitude Adjustment | `(site_altitude − campus_altitude) / 5000` |
| Back-to-Back Fatigue | Consecutive game days |
| Tip-Off Time Mismatch | ±9 hours from usual game time |
| Officiating Crew Mismatch | Unfamiliar referee tendencies |
| Media Overload | High-profile distraction level |
| First Four Short Rest | Compressed recovery time |
| Campus Site Hostile | Playing at opponent's home arena |
| Weather Region Mismatch | Climate adjustment factor |

### Outputs

| Field | Description |
|-------|-------------|
| `edge_score` | −1 to +1 situational edge |
| `adjustment_points` | `edge_score × 2.5` (points to add to spread) |
| `motivation_flag` | HIGH_DOG / LOW_MOTIVATION / NEUTRAL |
| `physical_state` | Physical readiness rating |
| `risk_flags` | List of contextual risk factors |

---

## 14. Quant 5 – Mathematician (Integration Engine)

**File:** `ml/quant5_mathematician.py`

### Algorithm

Ridge regression ensemble that combines all four quant modules into a single composite edge score. Applies Platt scaling for calibration and Kelly criterion for bet sizing.

**Composite Edge Formula:**
```
edge = w1 × archetype + w2 × upset_dna + w3 × fragility + w4 × situational − market_edge
```

Weights are learned via ridge regression (α = 1.0) on historical ATS outcomes, normalized to sum to 1.

**Confidence Decay:**
```
confidence × (0.6 + 0.4 × 0.5^(hours_to_tipoff / 24))
```

### Confidence Tiers

| Tier | Requirement |
|------|------------|
| A | Edge ≥ 2.0 |
| B | Edge ≥ 1.0 |
| C | Edge < 1.0 |

### Bet Card Outputs

- Edge, Kelly fraction (full and recommended), max bet sizing (capped at 5%)
- Correlation group, hedge opportunities (|edge| ≥ 3.0)
- Timing recommendation:
  - **"Bet now"** if edge ≥ 1.5 AND line shop value ≥ 0.25
  - **"Wait for sharp action"** if edge ≥ 1.0
  - **"Avoid late"** otherwise

### Risk Controls

- Max 15% exposure per correlation group (same region/round)
- Bracket correlation matrix: 1.0 same game, 0.6 same group, 0.05 different region
- Contrarian value finder: public > 65% AND |model_edge| > 3.0
- Platt scaling calibration by round

---

## 15. Variant Models (Lookback Window Ridge)

**File:** `ml/train_variants.py`

### Algorithm

Trains and evaluates multiple ridge linear models using different lookback windows, plus 50/50 ensemble combinations of window pairs.

### Windows Tested

| Window (Games) |
|:---:|
| 4 |
| 5 |
| 6 |
| 7 |
| 10 |
| 12 |

### Outputs

- Per-variant model JSON files in `ml/models/`
- `ml/variant_results.csv` with train RMSE, validation RMSE, row counts, and ensemble MAE/RMSE for each combination

---

## 16. Supporting Components

### Betting Engine

**File:** `core/betting_engine.py`

Computes expected value, Kelly stake, and gating thresholds for spread bet recommendations. Gates on minimum edge and minimum confidence before recommending a bet.

### Edge Computation

**File:** `ml/edge.py`

- `edge_prob(model_prob, market_prob)` — raw probability edge
- `american_to_prob(odds)` / `prob_to_american(p)` — odds conversion
- `expected_value(prob, odds, stake)` — per-unit EV
- `expected_roi(prob, odds)` — return on investment

### Kelly Criterion & Staking

**File:** `ml/staking.py`

- `kelly_fraction(prob, odds)` — optimal bankroll fraction: `f = (p×d − 1) / (d − 1)`
- `unit_stake(kelly, fraction=0.25, max_units=3.0)` — quarter-Kelly with 3-unit cap

### Calibration

**File:** `ml/calibration.py`

Platt scaling (logistic regression in log-odds space) with L2 regularization and early stopping. Metrics: log loss, Brier score, Expected Calibration Error (ECE).

| Parameter | Default |
|-----------|---------|
| Learning rate | 0.1 |
| Steps | 500 |
| L2 strength | 0.001 |
| Patience | 20 |

### Backtesting

**Files:** `ml/backtest.py`, `ml/backtest_full_season.py`

| Mode | Description |
|------|-------------|
| Line-Free | Accuracy only (MAE, RMSE, directional accuracy) |
| With-Lines | Edge-based signals, hit rate, ROI |
| Walk-Forward | Expanding-window cross-validation (6 folds) with ridge regression, feature importance, and drop-1 ablation |

### Feature Schema

**Files:** `ml/feature_schema.py`, `ml/schema.py`

Canonical schema for raw team-level pregame features with stable hashing for drift detection, type checking, min/max constraints, and versioning (currently v2).

### Strategy Presets

| Preset | Kelly Fraction | Max Units | Min Edge | Min Confidence |
|--------|---------------|-----------|----------|----------------|
| Conservative | 15% | 1.5 | 5% | 60% |
| Balanced | 25% | 3.0 | 3% | 60% |
| Aggressive | 40% | 4.0 | 2% | 60% |

---

## Model Integration Flow

```
  ┌──────────────────────────────────────────────────────────┐
  │                     DATA SOURCES                         │
  │  ESPN (CBBpy) · NCAA Casablanca · Henry API · Barttorvik │
  └────────────────────────┬─────────────────────────────────┘
                           │
                   ┌───────▼───────┐
                   │ Feature Matrix │
                   │ (features_v2)  │
                   └───────┬───────┘
                           │
          ┌────────────────┼────────────────┐
          │                │                │
   ┌──────▼──────┐  ┌─────▼──────┐  ┌──────▼──────┐
   │  Primary v2  │  │  Ridge ML  │  │  Formula    │
   │  (Normalized │  │  (Margin + │  │  (8 Weighted│
   │  Bidirection)│  │   Total)   │  │   Features) │
   └──────┬──────┘  └─────┬──────┘  └──────┬──────┘
          │                │                │
          └────────┬───────┘────────────────┘
                   │
            ┌──────▼──────┐
            │   Ensemble   │
            │   Stacker    │
            └──────┬──────┘
                   │
      ┌────────────┼────────────────────────────┐
      │            │                             │
┌─────▼─────┐ ┌───▼───┐  ┌─────────────────────▼──┐
│  Betting  │ │Totals │  │ Tournament Quant (Q1-Q5)│
│  Engine   │ │Model  │  │ Archeologist → Upset    │
│           │ │       │  │ Hunter → Executioner →  │
│           │ │       │  │ Situationist →          │
│           │ │       │  │ Mathematician           │
└─────┬─────┘ └───┬───┘  └────────────┬───────────┘
      │            │                   │
      └────────────┼───────────────────┘
                   │
            ┌──────▼──────┐
            │ Bet Card /  │
            │ Dashboard   │
            └─────────────┘
```
