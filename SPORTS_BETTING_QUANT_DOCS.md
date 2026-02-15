# Sports Betting Quant Enhancement Documentation

## Overview
This enhancement brings professional-grade quantitative sports betting tools to the CBB Betting Model application, along with significant improvements to the Model Lab and main dashboard.

## New Features

### 1. Sports Betting Quant Page (4_Sports_Betting_Quant.py)

A comprehensive quantitative analysis toolkit for sports betting, featuring:

#### Kelly Criterion Calculator
- Calculate optimal bet sizing using the Kelly Criterion formula
- Support for fractional Kelly (Quarter Kelly, Half Kelly, etc.)
- Real-time Expected Value (EV) and ROI calculations
- Visual recommendations with thresholds

#### Bankroll Simulator
- Monte Carlo simulation of betting strategies
- Multiple staking methods:
  - Fixed unit betting
  - Kelly Criterion-based
  - Percentage of bankroll
- Simulate up to 100 parallel scenarios
- Visualize bankroll trajectories
- Track key metrics:
  - Final bankroll
  - Total return %
  - Maximum drawdown
  - Sharpe ratio

#### Portfolio Optimizer
- Analyze multiple betting opportunities simultaneously
- Calculate optimal allocation across correlated bets
- Risk-adjusted position sizing
- Portfolio exposure management
- EV and ROI comparison matrix

#### Risk Metrics & Analysis
- Calculate Sharpe ratio from past results
- Maximum drawdown analysis
- Win rate and P&L tracking
- Equity curve visualization
- Historical performance analysis

#### Educational Content
- Kelly Criterion formula and explanation
- Best practices for bet sizing
- Risk management guidelines
- Common pitfalls and how to avoid them
- Professional betting standards

### 2. Enhanced Model Lab (3_Model_Lab.py)

The Model Lab now features a tabbed interface with four distinct sections:

#### Tab 1: Model Registry (Original)
- Register and manage models
- Set active models
- CRUD operations on model configurations
- Backward compatible with existing functionality

#### Tab 2: Live Comparison
- Compare multiple models side-by-side
- Parameter comparison matrix
- Live prediction comparison (with mock data)
- Model metadata visualization

#### Tab 3: Performance Charts
- Historical performance tracking
- Accuracy over time visualization
- ROI trajectory charts
- Bet volume analysis
- Summary statistics dashboard

#### Tab 4: Batch Testing
- Test multiple parameter configurations simultaneously
- Parameter optimization tools
- Performance comparison across settings
- Quick preset tests (Conservative, Aggressive, Balanced)
- Visual performance correlation

### 3. Enhanced Main Dashboard (app.py)

The main dashboard now includes:

#### Sidebar Enhancements
- **Bankroll Management:**
  - Track current bankroll
  - Set unit size
  - Display total P&L and ROI
  - Real-time performance metrics

- **Quick Stats:**
  - Win/loss record
  - Win rate percentage
  - Toggle performance analytics view

#### Performance Analytics Dashboard
- Expandable section showing:
  - Total bets placed
  - W-L record
  - Win rate
  - Total wagered
  - Net profit/loss
  - Recent bet history (last 10 bets)
  - Clear history option

#### Bet Tracking
- **Track Bet Button** on each recommended bet
- Customizable stake amount
- Automatic tracking of:
  - Game details
  - Bet side and spread
  - Stake amount
  - Edge and confidence
  - Expected value
- Historical bet tracking in session state

## Technical Implementation

### Session State Management
```python
st.session_state.bet_history = []  # Track all bets
st.session_state.bankroll = 10000.0  # Current bankroll
st.session_state.show_analytics = False  # Analytics toggle
```

### Key Functions

#### Kelly Criterion
```python
kelly_criterion(win_prob: float, odds: float, fraction: float = 1.0) -> float
```
Calculates optimal bet size as fraction of bankroll.

#### Expected Value
```python
expected_value(win_prob: float, odds: float, stake: float = 1.0) -> float
```
Calculates expected value of a bet.

#### Sharpe Ratio
```python
sharpe_ratio(returns: list[float], risk_free_rate: float = 0.0) -> float
```
Measures risk-adjusted returns.

#### Max Drawdown
```python
max_drawdown(bankroll_history: list[float]) -> tuple[float, int, int]
```
Calculates maximum drawdown from peak.

#### Betting Simulator
```python
simulate_betting_strategy(
    starting_bankroll: float,
    num_bets: int,
    win_prob: float,
    odds: float,
    stake_method: str = "fixed",
    ...
) -> dict
```
Runs Monte Carlo simulation of betting strategy.

## Usage Examples

### Using the Kelly Calculator
1. Navigate to "Sports Betting Quant" page
2. Enter your estimated win probability (e.g., 0.55 for 55%)
3. Enter the American odds (e.g., -110)
4. Adjust Kelly fraction (default 0.25 for Quarter Kelly)
5. View recommended bet size and expected value

### Running Bankroll Simulations
1. Set starting bankroll and number of bets
2. Choose staking method (Kelly, fixed, or percentage)
3. Select number of simulations (1-100)
4. Click "Run Simulation"
5. Analyze bankroll trajectories and metrics

### Tracking Bets on Main Dashboard
1. Enable "Show Performance Analytics" in sidebar
2. Set your current bankroll and unit size
3. Review game predictions and recommendations
4. For recommended bets, adjust stake and click "Track Bet"
5. View tracked bets in the Performance Analytics section

### Comparing Models
1. Go to Model Lab → Live Comparison tab
2. Select 2-3 models to compare
3. Choose comparison metric (Prediction, Confidence, Parameters)
4. Review side-by-side comparison matrix

### Batch Testing Parameters
1. Go to Model Lab → Batch Testing tab
2. Select parameter to vary (e.g., confidence_threshold)
3. Set test range (min, max, steps)
4. Choose test period and model type
5. Click "Run Batch Test"
6. Analyze optimal configuration from results

## Integration with Existing Code

### Backward Compatibility
- All existing functionality preserved
- Original Model Lab features available in Tab 1
- Main dashboard retains all original predictions
- No breaking changes to data loaders or engines

### Data Flow
```
User Input → Kelly Calculator → Optimal Stake Size
Bankroll State → Bet Tracking → Performance Metrics
Model Registry → Live Comparison → Visual Analytics
```

## Best Practices

### Bankroll Management
1. Never bet more than 5% of bankroll on single bet
2. Use Quarter Kelly or less for most situations
3. Track all bets to calculate actual vs. theoretical edge
4. Respect maximum exposure limits

### Model Testing
1. Compare multiple models before going live
2. Run batch tests to optimize parameters
3. Monitor performance metrics continuously
4. Adjust based on real results, not just backtests

### Risk Management
1. Calculate Sharpe ratio regularly
2. Monitor maximum drawdown
3. Set stop-loss limits
4. Diversify across uncorrelated opportunities

## Future Enhancements

Potential additions for future versions:
- Real-time connection to live prediction engine for model comparison
- Historical backtest integration for batch testing
- Export bet history to CSV
- Advanced portfolio correlation analysis
- Machine learning-based parameter optimization
- Integration with sportsbook APIs
- Automated bet placement (with appropriate safeguards)

## Security & Compliance

### Disclaimer
The Sports Betting Quant tools are for educational and analytical purposes only:
- No guarantee of profits
- Past performance doesn't indicate future results
- Users responsible for legal compliance
- Always gamble responsibly

### Data Privacy
- All bet tracking stored in session state (not persisted)
- No sensitive data sent to external services
- User bankroll information stays local

## Testing

Run the validation script to verify all features:
```bash
python validate_enhancements.py
```

Expected output: All syntax and feature validations should pass.

## Files Modified

1. **pages/4_Sports_Betting_Quant.py** (NEW)
   - Complete quantitative betting toolkit
   - ~500 lines of code
   - 5 major sections with educational content

2. **pages/3_Model_Lab.py** (ENHANCED)
   - Added tabbed interface
   - 4 tabs with distinct functionality
   - ~400 lines of new code
   - Original features preserved in Tab 1

3. **app.py** (ENHANCED)
   - Added bankroll management sidebar
   - Performance analytics dashboard
   - Bet tracking functionality
   - ~50 lines of new code
   - Fully backward compatible

## Support

For issues or questions:
1. Review this documentation
2. Check the in-app educational content
3. Run the validation script
4. Review code comments for implementation details
