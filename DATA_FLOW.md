# CBB Betting Model Data Flow Documentation

This document explains how data flows through the CBB betting model system, from ingestion to predictions to app display.

---

## Table of Contents
1. [Overview](#overview)
2. [Data Ingestion](#data-ingestion)
3. [Feature Engineering](#feature-engineering)
4. [Vegas Lines & Market Data](#vegas-lines--market-data)
5. [Model Predictions](#model-predictions)
6. [Data Flow to app.py](#data-flow-to-apppy)
7. [Backtesting System](#backtesting-system)
8. [Database Architecture](#database-architecture)

---

## Overview

The CBB betting model uses a multi-stage data pipeline:

```
ESPN API → CSV Files → Feature Engineering → Predictions → Supabase → app.py
                 ↓                                ↓
           Raw Games                        Backtesting
```

**Key Data Sources:**
- **ESPN API**: Game results, scores, box scores, and market lines
- **Barttorvik**: Advanced team efficiency metrics (pre-computed)
- **Supabase Database**: Centralized storage for games, predictions, and market lines

---

## Data Ingestion

### 1. ESPN Scoreboard & Game Data

**Entry Point:** `ESPN/espn_boxscore_builder_modular.py`

The pipeline fetches game data from ESPN's API in multiple passes:

#### PASS 0: Scoreboard Ingestion
- **Function:** `fetch_scoreboard_games_for_date()`
- **Source:** ESPN Scoreboard API
- **Output:** `ESPN/CSV/espn_games.csv`
- **Data Includes:**
  - Game IDs, dates, teams
  - Scores (when completed)
  - Market lines (spread, total, moneylines)
  - Game status (scheduled/final)

```python
# From espn_http_client.py
def fetch_scoreboard_games(date: str) -> List[Dict]:
    url = ESPN_SCOREBOARD_URL.format(date=date)
    json_data = fetch_with_retry(url)
    # Parses scoreboard events into game dictionaries
```

#### PASS 1: Game Summary & Box Scores
- **Function:** `fetch_summary()`
- **Source:** ESPN Game Summary API (per game)
- **Output:** `ESPN/CSV/espn_team_game_logs.csv`
- **Data Includes:**
  - Detailed box scores (FGM/FGA, 3PM/3PA, FTM/FTA)
  - Team statistics (rebounds, turnovers, assists)
  - Per-game metrics (ORtg, DRtg, pace, eFG%, TOV%)
  - Player box scores → `ESPN/CSV/espn_player_boxscores.csv`

**Key Processing:**
```python
# From espn_parsers.py
def summary_to_team_rows(summary_json: Dict) -> List[Dict]:
    # Extracts home and away team rows
    # Computes possessions: FGA - ORB + TOV + 0.44*FTA
    # Calculates per-100 possession metrics
```

### 2. Data Quality & Repair

**Module:** `ESPN/data_quality.py`

The **Data Quality Repair Gate (DQRG)** validates and repairs incomplete data:

1. **Detection:** Identifies games with missing critical fields (possessions, base totals)
2. **Repair:** Attempts to refetch game summary from ESPN API
3. **Validation:** Verifies repaired data meets quality thresholds
4. **Audit:** Logs all repairs to `ESPN/CSV/espn_dq_audit.csv`

```python
# From data_quality.py
def verify_dataframe_integrity(df: pd.DataFrame) -> Tuple[bool, List[str]]:
    # Checks for required columns
    # Validates data types and value ranges
    # Returns pass/fail with detailed reasons
```

### 3. File Storage & Persistence

**Module:** `ESPN/file_io.py`

All writes use **atomic operations** to prevent data corruption:

```python
def _atomic_csv_write(df: pd.DataFrame, path: Path):
    # 1. Write to temporary file
    # 2. Create backup of existing file
    # 3. Replace old with new
    # 4. Rollback on failure
```

**Output Files:**
- `ESPN/CSV/espn_games.csv` - Game-level data with market lines
- `ESPN/CSV/espn_team_game_logs.csv` - Team box scores and per-game metrics
- `ESPN/CSV/espn_team_game_features.csv` - Pregame rolling features
- `ESPN/CSV/espn_matchups_model_ready.csv` - Model-ready matchups (home/away features)
- `ESPN/CSV/espn_player_boxscores.csv` - Player-level statistics

---

## Feature Engineering

### PASS 2-5: Rolling Features & Opponent Context

**Module:** `ESPN/metrics_calculator.py`

The pipeline builds sophisticated pregame features through multiple passes:

#### PASS 2: Historical Data Loading
- Loads all historical game logs
- Normalizes team names and IDs
- Filters to completed games only

#### PASS 3: Rolling Window Features
Computes leak-free rolling averages (using only past games):

```python
# From metrics_calculator.py
def _group_shift_rolling(df, group_cols, sort_col, metric, windows):
    # L3 (last 3 games)
    # L7 (last 7 games)  
    # Season-to-date averages
    # Uses shift(1) to prevent data leakage
```

**Feature Categories:**
- **Offensive Metrics:** ORtg (offensive rating), eFG%, FTR (free throw rate)
- **Defensive Metrics:** DRtg (defensive rating), opponent eFG%
- **Four Factors:** eFG%, TOV%, ORB%, FTR
- **Pace:** Possessions per game
- **Recent Form:** NetRtg (ORtg - DRtg) over L3, L7 windows

#### PASS 4: Time-Based Features
Adds situational context:

```python
def _time_window_counts_per_team(df: pd.DataFrame) -> pd.DataFrame:
    # rest_days: Days since last game
    # back_to_back: Played yesterday (0/1)
    # three_in_six: 3+ games in last 6 days (0/1)
    # games_in_7d: Count of games in past week
```

#### PASS 5: Opponent Merge & Context
The most sophisticated pass - joins opponent pregame stats to each game:

```python
# From opponent_merge.py
def _merge_opponent_rows(df: pd.DataFrame) -> pd.DataFrame:
    # For each game, joins opponent's pregame features
    # Example: UNC vs Duke
    #   - UNC gets Duke's L7 DRtg (how good is Duke's defense?)
    #   - Duke gets UNC's L7 ORtg (how good is UNC's offense?)
    # Enables "vs expectation" analysis
```

**Opponent Features Added:**
- `opp_drtg_l7_pre` - How good is opponent's defense?
- `opp_ortg_l7_pre` - How good is opponent's offense?
- `opp_pace_l7_pre` - How fast does opponent play?
- `allowed_*` metrics - What opponent typically allows on defense

### Advanced Features

**Module:** `ESPN/plus_and_fit.py`

Computes relative performance metrics:
- **PLUS metrics:** Performance vs league average
- **FIT scores:** Style compatibility between teams

**Module:** `ESPN/cbb_advanced_metrics.py`

Adds predictive metrics:
- **GPS (Game Prediction Score):** Composite power rating
- **Expected Margin:** Statistical expected outcome

**Module:** `ESPN/strength_of_schedule.py`

- **SOS weighted margin:** Margin vs strength of schedule
- **Opponent quality adjustments**

---

## Vegas Lines & Market Data

### Market Line Ingestion

Market lines are embedded in ESPN's scoreboard data:

```python
# From espn_parsers.py
def _extract_odds_from_comp(comp: Dict) -> Dict:
    # Extracts from ESPN API response:
    #   - market_spread (home team spread)
    #   - market_total (over/under)
    #   - market_home_ml (home moneyline)
    #   - market_away_ml (away moneyline)
    #   - market_provider (typically "consensus")
```

**Storage Locations:**
1. **CSV:** `espn_games.csv` - Column `market_spread`, `market_total`
2. **Supabase:** `public.market_lines` table (via `scripts/daily_auto_predict.py`)

### Market Line Flow to Database

**Script:** `scripts/daily_auto_predict.py`

This daily automation script:

1. **Fetches** ESPN scoreboard for today ± configurable window
2. **Upserts** to Supabase:
   ```python
   # Upserts to public.market_lines
   {
       "game_id": external_game_id,
       "book": "espn",  # or "consensus"
       "pulled_at": timestamp,
       "spread_home": market_spread,
       "total": market_total,
       "ml_home": home_moneyline,
       "ml_away": away_moneyline
   }
   ```

---

## Model Predictions

The system uses **two prediction engines**:

### 1. Formula-Based Predictions (Default)

**Module:** `core/prediction_engine.py`

**How it works:**
1. **Load Active Model** from `core/model_registry.py`
   - Models stored in Supabase `model_registry` table or local JSON
   - Each model defines weights, HCA (home court advantage), and parameters

2. **Compute Weighted Edges:**
   ```python
   def predict_spread(home: Dict, away: Dict) -> Dict:
       # Component edges (home advantage)
       torvik_edge = home["torvik_adj_em"] - away["torvik_adj_em"]
       recent_edge = home["netrtg_l7_pre"] - away["netrtg_l7_pre"]
       ff_edge = _compute_ff_edge(home, away)  # Four factors
       sos_edge = home["sos_weighted_margin"] - away["sos_weighted_margin"]
       
       # Weighted combination
       spread = (
           weights["torvik_adjem"] * torvik_edge +
           weights["recent_netrtg"] * recent_edge +
           weights["four_factors"] * ff_edge +
           weights["sos_weighted"] * sos_edge
       )
       
       # Home court advantage
       hca = 2.7  # Static or dynamic based on model config
       
       # Pace adjustment
       avg_pace = (home["pace_l7"] + away["pace_l7"]) / 2
       predicted_spread = (spread * pace / 100.0) + hca
       
       return {
           "predicted_spread": predicted_spread,
           "confidence": _compute_confidence(home, away),
           "model_id": "formula-v1",
           "breakdown": {...}
       }
   ```

3. **Confidence Calculation:**
   ```python
   def _compute_confidence(home, away, params):
       # Based on sample size (games played)
       games_min = min(home["games_played"], away["games_played"])
       sample_boost = min(0.15, (games_min / 20) * 0.15)
       return 0.60 + sample_boost  # Base 60%, up to 75%
   ```

**Model Registry:**
- **Location:** Supabase `model_registry` table or `data/model_registry_local.json`
- **Fields:** `model_id`, `model_name`, `model_type`, `params`, `is_active`
- **Management:** CRUD functions in `core/model_registry.py`

### 2. Recursive Bidirectional Predictions (Advanced)

**Module:** `core/recursive_prediction_engine.py`

**Philosophy:**
> "When UNC beats Virginia by 8 with +5 ORB, we need context. What does Virginia typically allow in ORB? UNC's +5 ORB is only impressive if Virginia normally allows +2."

**How it works:**
1. **Loads game logs** from `ESPN/CSV/espn_team_game_logs.csv`
2. **For each team's recent games:**
   - Gets opponent's typical performance (from opponent's history)
   - Computes "vs expectation" metrics
   - Example: UNC scored 78 vs Virginia, but Virginia allows 72 on avg → +6 vs expectation
3. **Aggregates** vs-expectation performance across L5 and L10 games
4. **Predicts spread** using recursive context-aware edges

```python
def predict_spread(home_team: str, away_team: str) -> Dict:
    # Get each team's recent context-aware performance
    home_edge = _compute_bidirectional_edge(home_team)
    away_edge = _compute_bidirectional_edge(away_team)
    
    # Combine edges
    spread = home_edge - away_edge + hca
    
    return {
        "predicted_spread": spread,
        "confidence": confidence,
        "model_id": "recursive-bidirectional-v1"
    }
```

### 3. Machine Learning Predictions

**Scripts:**
- `ml/train_ml_models.py` - Trains linear models on historical features
- `ml/predict_ml.py` - Scores games using trained models

**Training Flow:**
1. **Load** `ESPN/CSV/espn_matchups_model_ready.csv`
2. **Split** into train/test by date
3. **Train** linear regression models:
   - `ml/models/margin_model.json` - Predicts point margin
   - `ml/models/total_model.json` - Predicts total points
4. **Save** feature order and coefficients

**Prediction Flow:**
```python
# From ml/predict_ml.py
def predict():
    # 1. Load model features from ml/model_features.csv
    model = json.load(open("ml/models/margin_model.json"))
    
    # 2. Vectorized scoring (no loops)
    coefficients = np.array(model["coefficients"])
    intercept = model["intercept"]
    X = df[model["feature_order"]].fillna(0).values
    predictions = X @ coefficients + intercept
    
    # 3. Write to ml/predictions_latest.csv
    df["pred_margin_home"] = predictions
    df.to_csv("ml/predictions_latest.csv")
```

**Output:** `ml/predictions_latest.csv`

### Prediction Upload to Database

**Script:** `scripts/daily_auto_predict.py`

Daily automation that combines predictions with market lines:

```python
# 1. Pull raw predictions from raw.predictions_latest table
preds = supabase.table("predictions_latest").select("*").execute()

# 2. Merge with scoreboard (for market lines)
merged = preds.merge(scoreboard, on="event_id")

# 3. Compute edges
for row in merged:
    pred_spread = row["pred_margin_home"]
    market_spread = row["market_spread"]
    vegas_edge = pred_spread - market_spread  # Our edge vs market
    
    confidence = 1.0 - exp(-abs(vegas_edge) / 6.0)  # Exponential confidence

# 4. Upsert to public.predictions table
supabase.table("predictions").upsert({
    "prediction_key": f"{model_version}:{event_id}",
    "game_id": event_id,
    "ensemble_prediction": pred_spread,
    "confidence": confidence,
    "vegas_edge": vegas_edge,
    "model_predictions": {...},
    ...
}, on_conflict="prediction_key")
```

**Key Tables:**
- **Input:** `raw.predictions_latest` (ML predictions)
- **Output:** `public.predictions` (merged with market lines)

---

## Data Flow to app.py

### Main Application Entry Point

**File:** `app.py`

The Streamlit app orchestrates prediction display:

```python
def main():
    # 1. Initialize data loader
    data = DataLoader()
    
    # 2. Load today's games with market lines
    games = data.load_vegas_lines(date="today")
    # Source: ESPN/CSV/espn_games.csv
    # Contains: game_id, home_team, away_team, market_spread, market_total
    
    # 3. Load precomputed predictions
    daily_preds = data.load_todays_predictions()
    # Priority 1: Supabase public.predictions table
    # Priority 2: CSV fallback (ml/predictions_latest.csv, data/predictions.csv)
    
    # 4. For each game, match prediction to game
    for game in games:
        # Try to find existing prediction
        pred = daily_preds[daily_preds["event_id"] == game["game_id"]]
        
        if pred.exists():
            # Use precomputed prediction
            predicted_spread = pred["predicted_spread"]
        else:
            # Generate live prediction
            home_snapshot = data.get_team_snapshot(game["home_team"])
            away_snapshot = data.get_team_snapshot(game["away_team"])
            pred = pred_engine.predict_spread(home_snapshot, away_snapshot)
        
        # 5. Display prediction card
        ui.render_prediction_card(
            home_team=game["home_team"],
            away_team=game["away_team"],
            prediction=pred,
            vegas_spread=game["market_spread"]
        )
        
        # 6. Generate bet recommendation
        bet = bet_engine.recommend_spread(
            predicted_spread=pred["predicted_spread"],
            market_spread=game["market_spread"],
            confidence=pred["confidence"]
        )
        ui.render_bet_recommendation(bet)
```

### DataLoader Details

**Module:** `core/data_loader.py`

**Key Methods:**

#### 1. Load Vegas Lines
```python
def load_vegas_lines(date="today") -> pd.DataFrame:
    # 1. Read ESPN/CSV/espn_games.csv
    games = pd.read_csv("ESPN/CSV/espn_games.csv")
    
    # 2. Normalize market_spread column (handles multiple naming conventions)
    if "market_spread" not in games:
        for alias in ["vegas_spread", "spread", "closing_spread_home"]:
            if alias in games:
                games["market_spread"] = games[alias]
    
    # 3. Filter to today's date
    if date == "today":
        today = datetime.utcnow().date()
        games = games[games["game_date"].dt.date == today]
    
    return games
```

#### 2. Load Predictions
```python
def load_todays_predictions() -> pd.DataFrame:
    # Priority 1: Supabase
    if supabase_client:
        resp = supabase.table("predictions").select("*").execute()
        return pd.DataFrame(resp.data)
    
    # Priority 2: CSV fallback
    for path in ["data/predictions.csv", "ml/predictions_latest.csv"]:
        if os.path.exists(path):
            return pd.read_csv(path)
    
    return pd.DataFrame()  # Empty if none found
```

#### 3. Get Team Snapshot
```python
def get_team_snapshot(team_name: str) -> Dict:
    # 1. Load feature store
    df = pd.read_csv("ESPN/CSV/espn_team_game_features.csv")
    
    # 2. Filter to team and sort by date (latest first)
    team_df = df[df["team"] == team_name].sort_values("game_date", ascending=False)
    
    # 3. Return most recent row as dictionary
    return team_df.iloc[0].to_dict()
    # Contains: torvik_adj_em, netrtg_l7_pre, pace_l7_pre, 
    #           efg_l7_pre, sos_weighted_margin, etc.
```

### Betting Engine

**Module:** `core/betting_engine.py`

```python
def recommend_spread(predicted_spread, market_spread, confidence, ...):
    # 1. Calculate edge
    edge = abs(predicted_spread - market_spread)
    
    # 2. Check minimum edge threshold
    if edge < config["edge_min"]:
        return {"recommendation": "PASS"}
    
    # 3. Check confidence threshold
    if confidence < config["confidence_min"]:
        return {"recommendation": "PASS"}
    
    # 4. Determine side
    side = "home" if predicted_spread > market_spread else "away"
    
    # 5. Calculate bet size (Kelly criterion)
    kelly_fraction = (confidence * edge) / config["kelly_divisor"]
    
    return {
        "recommendation": "BET",
        "side": side,
        "edge": edge,
        "confidence": confidence,
        "size": kelly_fraction
    }
```

---

## Backtesting System

**Module:** `backtesting/backtest_engine.py`

### How Backtesting Works

The backtesting engine validates model performance on historical data:

```python
class BacktestEngine:
    def backtest_model(model: Dict, days_back: int = 30) -> Dict:
        # 1. Load historical completed games
        start_date = datetime.now() - timedelta(days=days_back)
        games = data_loader.load_historical_games(start_date, end_date)
        
        # 2. For each completed game
        results = []
        for game in games:
            # 3. Get leak-free pregame snapshots (only past data)
            home_snap = _get_snapshot_at_date(
                game["home_team"], 
                game["game_date"]  # Only data BEFORE this date
            )
            away_snap = _get_snapshot_at_date(game["away_team"], game["game_date"])
            
            # 4. Generate prediction using model
            pred = _predict_with_params(home_snap, away_snap, model["params"])
            
            # 5. Compare to actual result
            actual_margin = game["home_score"] - game["away_score"]
            error = abs(pred["predicted_spread"] - actual_margin)
            correct_side = (pred["predicted_spread"] > 0) == (actual_margin > 0)
            
            # 6. Evaluate ATS (Against The Spread) if market line available
            if game["market_spread"]:
                bet_side = "home" if pred > game["market_spread"] else "away"
                ats_won = (actual_margin > game["market_spread"]) if bet_side == "home" else (actual_margin < game["market_spread"])
            
            results.append({
                "predicted_spread": pred,
                "actual_margin": actual_margin,
                "error": error,
                "correct_side": correct_side,
                "ats_result": ats_won
            })
        
        # 7. Calculate aggregate metrics
        return {
            "mae": mean_absolute_error,      # Average prediction error
            "win_pct": correct_side_pct,     # % correct side
            "roi": (wins * 0.91 - losses) / total_bets,  # Return on investment
            "total_games": len(results),
            "edge_distribution": {...}       # Performance by edge size
        }
```

### Key Backtesting Features

#### 1. Leak-Free Snapshots
```python
def _get_snapshot_at_date(team_name: str, game_date: datetime) -> Dict:
    # CRITICAL: Only use data from BEFORE the game date
    df = load_feature_store()
    team_df = df[
        (df["team"] == team_name) & 
        (df["game_date"] < game_date)  # Exclude game and future data
    ]
    return team_df.sort_values("game_date", ascending=False).iloc[0].to_dict()
```

This ensures we only use data that would have been available at prediction time.

#### 2. ATS (Against The Spread) Performance

```python
# For each bet recommendation:
if predicted_spread > market_spread:
    # Bet on home team covering
    bet_won = (actual_margin > market_spread)
else:
    # Bet on away team covering  
    bet_won = (actual_margin < market_spread)

# Calculate ROI (accounting for -110 juice)
roi = (wins * 0.91 - losses) / total_bets
```

#### 3. Edge Distribution Analysis

```python
edge_distribution = {
    "0-3": games_with_edge_0_to_3,
    "3-6": games_with_edge_3_to_6,
    "6-9": games_with_edge_6_to_9,
    "9+": games_with_edge_9_plus
}
```

Shows which edge sizes are most profitable.

### Backtest Report

**Module:** `backtesting/backtest_report.py`

Generates detailed performance reports:
- Win rate by edge tier
- Calibration plots (predicted vs actual)
- ROI over time
- Best/worst predictions

---

## Database Architecture

### Supabase Schema

The system uses Supabase PostgreSQL with two schemas:

#### Raw Schema (`raw`)
Staging area for external data:

**`raw.raw_games`**
- Raw ESPN JSON payloads
- Verification status (verified/partial/rejected)
- Audit trail for data quality issues

**`raw.predictions_latest`**
- ML model outputs (uploaded from `ml/predictions_latest.csv`)
- Source of truth for latest predictions
- Updated daily by prediction pipeline

#### Public Schema (`public`)
Production-ready data:

**`public.teams`**
```sql
CREATE TABLE teams (
    team_id TEXT PRIMARY KEY,
    team_name TEXT NOT NULL,
    conference TEXT
);
```

**`public.games`**
```sql
CREATE TABLE games (
    game_id TEXT PRIMARY KEY,
    game_date TEXT,
    home_team TEXT,
    away_team TEXT,
    home_score INT,
    away_score INT,
    status TEXT,
    season INT,
    verification_status TEXT
);
```

**`public.market_lines`**
```sql
CREATE TABLE market_lines (
    game_id TEXT,
    book TEXT,
    pulled_at TIMESTAMP,
    spread_home FLOAT,
    total FLOAT,
    ml_home INT,
    ml_away INT,
    PRIMARY KEY (game_id, book, pulled_at)
);
```

**`public.predictions`**
```sql
CREATE TABLE predictions (
    id UUID PRIMARY KEY,
    prediction_key TEXT UNIQUE,  -- "{model_version}:{event_id}"
    model_version_id TEXT,
    game_id TEXT,
    event_id TEXT,
    game_date TEXT,
    team_a TEXT,
    team_b TEXT,
    home_team TEXT,
    away_team TEXT,
    ensemble_prediction FLOAT,    -- Predicted spread
    confidence FLOAT,
    vegas_line FLOAT,             -- Market spread
    vegas_edge FLOAT,             -- ensemble_prediction - vegas_line
    pred_total FLOAT,
    market_total FLOAT,
    model_predictions JSONB,      -- Full prediction details
    updated_at TIMESTAMP
);
```

**`public.model_registry`**
```sql
CREATE TABLE model_registry (
    model_id TEXT PRIMARY KEY,
    model_name TEXT,
    model_type TEXT,              -- "spread", "total", etc.
    params JSONB,                 -- Model weights and configuration
    is_active BOOLEAN,            -- Only one active model per type
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);
```

**`public.dq_audit`**
```sql
CREATE TABLE dq_audit (
    id UUID PRIMARY KEY,
    entity_type TEXT,
    entity_id TEXT,
    severity TEXT,                -- "warning" or "error"
    reason_codes TEXT[],
    details JSONB,
    created_at TIMESTAMP
);
```

### Data Upload Scripts

**`load_csv_to_db.py`**
- Uploads CSV files to Supabase tables
- Handles schema validation and type conversion
- Used for bulk historical data loads

**`ESPN/upload_predictions_to_supabase.py`**
- Uploads ML predictions to `raw.predictions_latest`
- Called after `ml/predict_ml.py` runs

**`scripts/daily_auto_predict.py`**
- Daily orchestration script
- Fetches ESPN data → Upserts to Supabase
- Pulls predictions → Merges with market lines → Upserts to `public.predictions`

---

## Summary: Complete Data Flow

### Daily Automation Workflow

```
1. ESPN API
   ↓ (scripts/refresh_sources.py or espn_boxscore_builder_modular.py)
2. CSV Files (ESPN/CSV/*.csv)
   ↓ (Feature engineering passes)
3. Feature Store (espn_team_game_features.csv)
   ↓ (ml/train_ml_models.py - periodic)
4. Trained Models (ml/models/*.json)
   ↓ (ml/predict_ml.py)
5. Raw Predictions (ml/predictions_latest.csv)
   ↓ (upload_predictions_to_supabase.py)
6. Supabase raw.predictions_latest
   ↓ (scripts/daily_auto_predict.py)
7. Supabase public.predictions (with market lines merged)
   ↓ (app.py reads via DataLoader)
8. Streamlit UI Display
```

### App.py Data Sources

When a user opens `app.py`:

1. **Load Vegas Lines:**
   - **Source:** `ESPN/CSV/espn_games.csv`
   - **Contains:** Today's games with market spreads

2. **Load Predictions:**
   - **Priority 1:** Supabase `public.predictions` table
   - **Priority 2:** `ml/predictions_latest.csv`
   - **Priority 3:** `data/predictions.csv`
   - **Fallback:** Live prediction using formula engine

3. **Get Team Features:**
   - **Source:** `ESPN/CSV/espn_team_game_features.csv`
   - **Used for:** Live predictions when precomputed unavailable

4. **Generate Recommendations:**
   - Compares predicted spread to market spread
   - Calculates edge and confidence
   - Returns bet/pass recommendation

---

## Key Configuration Files

- **`core/config.py`** - App configuration, strategy presets
- **`ESPN/espn_config.py`** - Pipeline configuration, feature flags
- **`ml/feature_schema.py`** - Feature definitions for ML models
- **`requirements.txt`** - Python dependencies

---

## Useful Commands

```bash
# Fetch latest ESPN data
python ESPN/espn_boxscore_builder_modular.py

# Train ML models
python ml/train_ml_models.py

# Generate predictions
python ml/predict_ml.py

# Upload to Supabase
python scripts/daily_auto_predict.py

# Run app locally
streamlit run app.py

# Run backtests
python ml/backtest_full_season.py
```

---

**Last Updated:** 2026-02-14
**Version:** 1.0
