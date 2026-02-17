# CBB Betting Model

College basketball prediction system using 4 ensemble models with advanced quantitative betting tools.

## Features
- Real-time predictions
- 4 model ensemble (Schedule, Four Factors, Bidirectional, Situational)
- Alpha signal detection
- Home court adjustments
- **NEW:** Sports Betting Quant tools (Kelly Criterion, bankroll management, portfolio optimization)
- **NEW:** Enhanced Model Lab with live comparison and batch testing
- **NEW:** Bankroll tracking and performance analytics

## Pages

### Main Dashboard (app.py)
- Live game predictions and bet recommendations
- Bankroll management in sidebar
- Bet tracking with customizable stakes
- Performance analytics dashboard

### 1. Daily Dashboard
- Today's games with model edges
- Bet recommendations with filters
- Live and precomputed predictions

### 2. Model Reports
- Historical model performance
- Leaderboards (win%, ROI, hit rate)
- Rolling performance charts

### 3. Model Lab 🆕
- **Registry Tab:** Manage and activate models
- **Live Comparison:** Compare multiple models side-by-side
- **Performance Charts:** Visualize model metrics over time
- **Batch Testing:** Optimize parameters with automated tests

### 4. Sports Betting Quant 🆕
- **Kelly Criterion Calculator:** Optimal bet sizing
- **Bankroll Simulator:** Monte Carlo strategy testing
- **Portfolio Optimizer:** Multi-bet allocation
- **Risk Metrics:** Sharpe ratio, max drawdown, P&L tracking
- Educational content and best practices

## Documentation
- **[DATA_FLOW.md](DATA_FLOW.md)** - Complete documentation of data ingestion, feature engineering, predictions, and backtesting
- **[SPORTS_BETTING_QUANT_DOCS.md](SPORTS_BETTING_QUANT_DOCS.md)** - Comprehensive guide to new quantitative betting features

## Data Sources

**Multi-Source Integration** (NEW): The system fetches data from multiple sources with automatic integrity checks and conflict resolution:
- **ESPN API** (game results, scores, market lines)
- **NCAA Casablanca API** (official NCAA data)
- **Henry API** (NCAA data proxy)

The multi-source system provides redundancy and data quality assurance. See [MULTI_SOURCE_INTEGRATION.md](MULTI_SOURCE_INTEGRATION.md) for details.

**Additional Sources**:
- Barttorvik (efficiency metrics)

## Run Locally
```bash
pip install -r requirements.txt
streamlit run streamlit_app.py  # Or: streamlit run app.py
```

## What's New

### Version 2.1 - Sports Betting Quant Edition

#### Quantitative Betting Tools
- **Kelly Criterion Calculator** with fractional Kelly support
- **Bankroll Simulator** with multiple staking methods (Kelly, fixed, percentage)
- **Portfolio Optimizer** for multi-bet allocation
- **Risk Analytics** including Sharpe ratio and maximum drawdown
- Monte Carlo simulations with up to 100 parallel scenarios

#### Enhanced Model Lab
- Tabbed interface for better organization
- Live model comparison matrix
- Performance visualization charts
- Batch parameter testing
- Quick preset configurations

#### Dashboard Improvements
- Bankroll tracking in sidebar with real-time P&L
- Bet history tracking with "Track Bet" buttons
- Performance analytics dashboard
- Win rate and ROI metrics
- Session-based tracking

## Quick Start

1. **Set Your Bankroll:**
   - Open the app
   - Go to sidebar → Bankroll section
   - Set your current bankroll and unit size

2. **Review Predictions:**
   - Main dashboard shows today's games
   - Check model predictions vs. market lines
   - Review bet recommendations with edge calculations

3. **Track Your Bets:**
   - Enable "Show Performance Analytics"
   - For recommended bets, adjust stake and click "Track Bet"
   - View tracked bets in Performance Analytics section

4. **Use Quant Tools:**
   - Navigate to "Sports Betting Quant" page
   - Use Kelly Calculator for optimal bet sizing
   - Run simulations to test strategies
   - Analyze your betting history with risk metrics

5. **Compare Models:**
   - Go to Model Lab → Live Comparison
   - Select 2-3 models to compare
   - Review parameters and predictions side-by-side

## Deploy to Streamlit Cloud
1. Push to GitHub
2. Go to streamlit.io
3. Deploy from repo

Review/testing app URL (allowlisted): https://cbb-betting-model-ddjun7mby42wjaxbjltebj.streamlit.app/

## Disclaimer

⚠️ **Sports betting involves risk.** These tools are for educational and analytical purposes only. No system can guarantee profits. Always gamble responsibly and within your means. Check local laws regarding sports betting legality.
