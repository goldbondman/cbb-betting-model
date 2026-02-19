# Primary Model Logic

This document describes the end-to-end model logic used by the CBB Betting Model to generate predictions and betting recommendations for college basketball games.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Data Pipeline & Feature Engineering](#data-pipeline--feature-engineering)
3. [Prediction Engines](#prediction-engines)
   - [Formula-Based Engine](#1-formula-based-engine)
   - [ML Linear Engine (Ridge Regression)](#2-ml-linear-engine-ridge-regression)
   - [Primary v2.0 Engine (Normalized Bidirectional)](#3-primary-v20-engine-normalized-bidirectional)
4. [Betting Engine](#betting-engine)
5. [Backtesting](#backtesting)
6. [Key Files Reference](#key-files-reference)

---

## Architecture Overview

```
Data Sources (ESPN / NCAA / CBBpy)
        │
        ▼
┌──────────────────────┐
│  Data Ingestion      │   ESPN/espn_boxscore_builder_modular.py
│  (5-pass pipeline)   │   ESPN/espn_http_client.py
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  Feature Engineering │   ESPN/metrics_calculator.py
│  (leak-free rolling) │   ESPN/opponent_merge.py
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  Prediction Engines  │   core/prediction_engine.py        (formula)
│  (3 engines)         │   ml/train_ml_models.py + predict  (ML ridge)
│                      │   primary_prediction_model.py      (v2.0 bidirectional)
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  Betting Engine      │   core/betting_engine.py
│  (edge + Kelly)      │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  Backtesting         │   backtesting/backtest_engine.py
│  (leak-free eval)    │
└──────────────────────┘
```

---

## Data Pipeline & Feature Engineering

### Per-Game Advanced Metrics

For each team-game row the system computes (**`ESPN/metrics_calculator.py`**):

| Metric | Formula |
|--------|---------|
| **Pace** | Possessions estimate |
| **ORtg** | `points_for × 100 / possessions` |
| **DRtg** | `points_against × 100 / possessions` |
| **NetRtg** | `ORtg − DRtg` |
| **Blowout flag** | Margin ≥ 18 points |
| **OT flag** | Detected from status strings |
| **Noise flag** | OT or extreme pace (< 55 or > 85) |

### Rolling Features (Leak-Free)

All rolling calculations **exclude the current game** (`shift(1)` before rolling) to prevent data leakage:

| Window | Description |
|--------|-------------|
| **L3** | Last 3 games rolling mean |
| **L7** | Last 7 games rolling mean |
| **L7 std** | Last 7 games standard deviation (volatility) |
| **Season** | Expanding mean (season-to-date) |

Metrics tracked across all windows:

- **Offensive:** ORtg, eFG%, FTR, 3PAr
- **Defensive:** DRtg, opponent eFG% allowed, TOV forced
- **Rebounding:** ORB%, DRB%
- **Overall:** NetRtg, Pace

### Non-Blowout Rollups

Separate L7 averages computed after masking blowout games (18+ point margin), providing more stable baselines.

### Time-Based Features

- `days_rest` — calendar days between games excluding game day itself (e.g., a game yesterday = 0 rest days)
- `back_to_back` — flag if ≤ 1.5 days since last game
- `games_last_N_days` — count of games in trailing 3–12 day windows
- `three_in_six` — flag if 3+ games in last 6 days

---

## Prediction Engines

### 1. Formula-Based Engine

**File:** `core/prediction_engine.py`

Registry-driven weighted-component model loaded from `model_registry`. Falls back to default weights when the registry is unavailable.

#### Spread Calculation

```
spread_points = w_torvik   × (home_adjEM − away_adjEM)
              + w_recent   × (home_netrtg_L7 − away_netrtg_L7)
              + w_ff       × four_factors_edge
              + w_sos      × sos_edge
```

**Default weights:**

| Component | Weight |
|-----------|--------|
| `torvik_adjem` | 0.55 |
| `recent_netrtg` | 0.25 |
| `four_factors` | 0.20 |
| `sos_weighted` | 0.00 |

#### Four Factors Edge

```
FF_home = eFG_L7 − TOV%_L7 + ORB%_L7 + FTR_L7
FF_away = eFG_L7 − TOV%_L7 + ORB%_L7 + FTR_L7
four_factors_edge = (FF_home − FF_away) × 10
```

#### Home Court Advantage (HCA)

| Mode | Value |
|------|-------|
| **Static** (default) | +2.7 points |
| **Dynamic** | `home_margin_lift_L20 − away_margin_penalty_L20` |

#### Pace Adjustment

When enabled (default), the raw spread is scaled by expected tempo, then HCA is added as an unscaled constant (intentionally not affected by pace):

```
pace = (home_pace_L7 + away_pace_L7) / 2
predicted_spread = spread_points × (pace / 100) + HCA
```

#### Confidence

Based on sample size (games played by each team):

```
sample_boost = min(0.15, (min(games_A, games_B) / 20) × 0.15)
confidence = clamp(0.60 + sample_boost, 0.50, 0.95)
```

---

### 2. ML Linear Engine (Ridge Regression)

**Training:** `ml/train_ml_models.py` → **Prediction:** `ml/predict_ml.py`

#### Two Models

| Model | Target | Output File |
|-------|--------|-------------|
| Margin model | `actual_margin_home` | `ml/models/margin_model.json` |
| Total model | `actual_total` | `ml/models/total_model.json` |

#### Training Pipeline

1. **Load features** from `ml/model_features.csv`
2. **Sort** by `game_datetime_utc` (stable mergesort)
3. **Time-series split** — chronological train/validation (default 90/10)
4. **Feature selection** — all numeric columns excluding IDs, timestamps, and target columns; leakage guard drops any column starting with `actual_`
5. **Sanitization** — rows with > 50% NaN features dropped; remaining NaNs imputed with per-feature medians
6. **Constant feature removal** — features with zero standard deviation are dropped
7. **Ridge regression** — closed-form solution where the penalty matrix applies `λ` to all feature coefficients but sets the intercept penalty to zero so it remains unregularized:

```
A = Xᵀ X + λI  (diagonal element for intercept set to 0)
β = A⁻¹ Xᵀ y
```

Default `λ = 0.01`.

8. **Validation RMSE** computed on held-out temporal split
9. **Artifacts saved** — coefficients, intercept, feature order, feature medians, dropped features

#### Prediction (Scoring)

1. Load model JSON files (coefficients, intercept, feature order, medians)
2. Build feature matrix aligned to `feature_order`
3. Fill missing features with saved training-time medians, then 0.0
4. Score: `prediction = intercept + X · coefficients`
5. Emit diagnostic checks (constant-prediction guard)

---

### 3. Primary v2.0 Engine (Normalized Bidirectional)

**File:** `primary_prediction_model.py` — Adapter: `core/primary_prediction_engine.py`

This is the most advanced engine. It evaluates team performance **relative to what opponents typically allow**, using recursive opponent context.

#### Core Concept — "vs Expectation"

> When UNC scores 78 against Virginia and Virginia typically allows 72, UNC performed **+6 vs expectation**. The model aggregates these deltas across recent games to capture true team strength after adjusting for schedule difficulty.

#### Game Weighting (Decay)

Most recent games receive higher weight using smooth exponential decay:

| Game # (1 = most recent) | Weight |
|---------------------------|--------|
| 1 | 1.00 |
| 2 | 0.98 |
| 3 | 0.96 |
| 4 | 0.94 |
| 5 | 0.92 |
| 6 | 0.75 |
| 7 | 0.70 |
| 8 | 0.65 |
| 9 | 0.60 |
| 10 | 0.55 |

#### Three-Layer Normalized Baseline

For each opponent, the system builds a defensive baseline:

1. **Layer 1 — Raw:** Simple average of points allowed (as efficiency per 100 possessions)
2. **Layer 2 — Opponent-Quality Weighted:** Games against elite opponents receive higher weight (up to 2×); games against weak opponents down-weighted (minimum 0.5×)
3. **Layer 3 — Schedule-Adjusted:** Adjusts for the average offensive efficiency of opponents faced:
   ```
   adjustment = (avg_opp_off_eff − 105.0) × 0.5
   adjusted_baseline = weighted_baseline − adjustment
   ```

#### Possession Estimation

Uses Dean Oliver's formula averaged between both teams to ensure consistency:

```
team_poss = FGA + 0.475 × FTA − ORB + TOV + 0.33 × OPP_ORB
opp_poss  = OPP_FGA + 0.475 × OPP_FTA − OPP_ORB + OPP_TOV + 0.33 × ORB
possessions = (team_poss + opp_poss) / 2
```

#### Four Factors (per game)

| Factor | Formula |
|--------|---------|
| eFG% | `(FGM + 0.5 × 3PM) / FGA` |
| TOV% | `(TOV / possessions) × 100` |
| ORB% | `ORB / (ORB + OPP_DRB)` |
| DRB% | `DRB / (DRB + OPP_ORB)` |
| FTR | `FTA / FGA` |

#### Matchup Calculation

For each factor, raw performance and vs-expectation are blended (default 30/70):

```
delta = 0.30 × raw_delta + 0.70 × vs_expectation_delta
```

Weighted composite using Dean Oliver's factor importance:

| Factor | Weight |
|--------|--------|
| eFG% | 0.28 |
| TOV% | 0.22 |
| ORB% | 0.18 |
| FTR | 0.16 |
| DRB% | 0.16 |

Final spread (sign convention: **negative = home favored**, matching the market convention where a −5 spread means the home team is favored by 5):

```
raw_edge = 0.60 × efficiency_edge + 0.40 × composite_edge
predicted_spread = −(raw_edge + HCA)
```

- **HCA** = 3.2 points (0 for neutral-site games)
- Pace does **not** scale the spread (only the total)

#### Total Prediction

```
home_points = home_off_eff × (expected_pace / 100)
away_points = away_off_eff × (expected_pace / 100)
predicted_total = home_points + away_points
```

#### Confidence

Combines sample size and consistency:

```
sample_conf = avg(min(home_games / 5, 1), min(away_games / 5, 1))
consistency_conf = avg(max(0, 1 − |home_L5_net − home_L10_net| / 20),
                       max(0, 1 − |away_L5_net − away_L10_net| / 20))
confidence = clamp(0.60 × sample_conf + 0.40 × consistency_conf, 0.05, 0.95)
```

---

## Betting Engine

**File:** `core/betting_engine.py`

### Edge Calculation

```
edge_pts = predicted_spread − market_spread
side     = "home" if edge_pts > 0 else "away"
edge     = |edge_pts| / 10.0
```

### Gating Thresholds

A bet is only recommended when **both** gates pass:

| Gate | Default |
|------|---------|
| Minimum edge | 0.03 (3%) |
| Minimum confidence | 0.60 |

### Expected Value

Accounts for standard -110 vigorish:

```
win_prob = clamp(0.50 + edge / 2.0, 0.05, 0.95)
EV = win_prob × 0.91 − (1 − win_prob) × 1.0
```

### Kelly Criterion Sizing

Fractional Kelly with configurable fraction and cap:

```
raw_kelly = (0.91 × win_prob − (1 − win_prob)) / 0.91
kelly_units = clamp(raw_kelly × fraction × 10.0, 0.0, max_units)
```

| Parameter | Default |
|-----------|---------|
| `kelly_fraction` | 0.25 (quarter Kelly) |
| `max_units` | 3.0 |

---

## Backtesting

**File:** `backtesting/backtest_engine.py`

### Leak-Free Evaluation

For each historical game:

1. Retrieve team snapshots using **only data before the game date**
2. Generate prediction with the model under test
3. Compare to actual margin and market spread

### Metrics

| Metric | Description |
|--------|-------------|
| **MAE** | Mean absolute error of spread predictions |
| **Win %** | Percentage of correct side predictions |
| **ROI** | `(wins × 0.91 − losses) / total_bets` (accounting for -110 juice) |
| **ATS** | Against-the-spread win rate |
| **Edge Distribution** | Game counts by edge bucket (0-3, 3-6, 6-9, 9+) |
| **Value Scan** | Win % and ROI at edge thresholds (0+, 1+, 2+, 3+, 4+, 5+) |

---

## Key Files Reference

| Purpose | File |
|---------|------|
| Per-game metrics & rolling features | `ESPN/metrics_calculator.py` |
| Formula-based prediction | `core/prediction_engine.py` |
| ML model training (Ridge) | `ml/train_ml_models.py` |
| ML model scoring | `ml/predict_ml.py` |
| Primary v2.0 engine | `primary_prediction_model.py` |
| Primary engine adapter | `core/primary_prediction_engine.py` |
| Recursive bidirectional engine | `core/recursive_prediction_engine.py` |
| Betting recommendations | `core/betting_engine.py` |
| Backtesting | `backtesting/backtest_engine.py` |
| Model registry | `core/model_registry.py` |
| Data loader | `core/data_loader.py` |
