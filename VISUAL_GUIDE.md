# Visual Guide - Sports Betting Quant Features

## Page Overview

### 🏀 Main Dashboard (app.py)
```
┌─────────────────────────────────────────────────────────────┐
│ Sidebar                    │ Main Content                   │
├────────────────────────────┼────────────────────────────────┤
│ ⚙️ Configuration            │ 🏀 CBB Betting Model v2.1      │
│   Strategy: [Dropdown]     │                                │
│   Model: [Dropdown]        │ 📊 Performance Analytics ▼     │
│                            │   Total Bets: 15               │
│ ─────────────────────────  │   Record: 9-6                  │
│ 💰 Bankroll               │   Win Rate: 60.0%              │
│   Current: $10,000         │   Total Wagered: $1,500        │
│   Unit Size: $100          │   Net P&L: +$245               │
│   Total P&L: +$245 (+2.5%) │                                │
│                            │ ─────────────────────────────  │
│ ─────────────────────────  │ Game 1: Duke vs UNC            │
│ ☑️ Show Analytics          │   Prediction: Duke -6.5        │
│   Win Rate: 60.0%          │   Market: -5.0                 │
│   Record: 9-6              │   Edge: 1.5 pts               │
│                            │   💰 Bet: Duke -5.0            │
│                            │   Stake: [$100] [📝 Track Bet] │
│                            │ ─────────────────────────────  │
└────────────────────────────┴────────────────────────────────┘
```

### 📊 4. Sports Betting Quant Page

```
┌─────────────────────────────────────────────────────────────┐
│                  📊 Sports Betting Quant                    │
│      Advanced quantitative analysis and risk management     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│ 1️⃣ Kelly Criterion Calculator                               │
│ ┌──────────────────────┬──────────────────────┐            │
│ │ Input Parameters     │ Results              │            │
│ │ Win Prob: [◄═══►] 55%│ Full Kelly: 5.00%    │            │
│ │ Odds: [-110_____]    │ Quarter Kelly: 1.25% │            │
│ │ Kelly Frac: [◄►] 0.25│ Expected EV: $0.91   │            │
│ └──────────────────────┴──────────────────────┘            │
│                                                             │
│ 2️⃣ Bankroll Simulator                                       │
│ ┌─────────────────────────────────────────┐                │
│ │ Starting: $10,000  │ Bets: 100           │                │
│ │ Win Prob: 55%      │ Method: Kelly       │                │
│ │ Simulations: [═══►] 10                  │                │
│ │ [🎲 Run Simulation]                     │                │
│ └─────────────────────────────────────────┘                │
│ Results:                                                    │
│ ┌─────┬─────┬─────┬─────┬─────┐                           │
│ │ Avg │ Med │ Ret │ DD  │ SR  │                           │
│ │$11.5│$11.2│+15% │-8%  │1.23 │                           │
│ └─────┴─────┴─────┴─────┴─────┘                           │
│ [Bankroll Trajectory Chart ───────────]                    │
│                                                             │
│ 3️⃣ Portfolio Optimizer                                      │
│ ┌─────────────────────────────────────────┐                │
│ │ Opportunity 1: Duke vs UNC ▼            │                │
│ │   Win Prob: 55%  │ Odds: -110           │                │
│ ├─────────────────────────────────────────┤                │
│ │ Opportunity 2: Kansas vs Kentucky ▼     │                │
│ │   Win Prob: 52%  │ Odds: +120           │                │
│ └─────────────────────────────────────────┘                │
│ [⚡ Optimize Portfolio]                                     │
│                                                             │
│ 4️⃣ Risk Metrics & Analysis                                  │
│ ┌─────────────────────────────────────────┐                │
│ │ Results: +100,-100,+91,-100,+91...      │                │
│ │ Total P&L: $182  │ Win Rate: 57.1%     │                │
│ │ Sharpe: 1.23     │ Max DD: -12.5%      │                │
│ └─────────────────────────────────────────┘                │
│ [Equity Curve Chart ──────────────────]                    │
│                                                             │
│ 📚 Education & Best Practices ▼                            │
│   - Kelly Criterion explained                               │
│   - Fractional Kelly rationale                              │
│   - Risk management guidelines                              │
│   - Responsible gambling principles                         │
└─────────────────────────────────────────────────────────────┘
```

### 🧪 3. Model Lab (Enhanced)

```
┌─────────────────────────────────────────────────────────────┐
│                        🧪 Model Lab                         │
│      Register multiple models and track which are active    │
├─────────────────────────────────────────────────────────────┤
│ [📋 Registry] [🔬 Live Compare] [📊 Charts] [⚡ Batch Test] │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│ TAB 1: Model Registry (Original)                           │
│ ┌─────────────────────────────────────────┐                │
│ │ Model ID     │ Name      │ Type  │Active│                │
│ │ primary-v2   │ Primary   │ spread│  ✓   │                │
│ │ recursive-v1 │ Recursive │ spread│      │                │
│ └─────────────────────────────────────────┘                │
│ [Activate] [Deactivate] [Delete]                           │
│                                                             │
│ TAB 2: Live Comparison                                     │
│ ┌─────────────────────────────────────────┐                │
│ │ Select: [x] primary-v2 [x] recursive-v1 │                │
│ │ Metric: [Prediction ▼]                  │                │
│ ├─────────────────────────────────────────┤                │
│ │ Matchup      │ primary-v2│ recursive-v1 │                │
│ │ Duke vs UNC  │ -6.5      │ -7.2         │                │
│ │ Kansas vs UK │ +3.2      │ +2.8         │                │
│ └─────────────────────────────────────────┘                │
│                                                             │
│ TAB 3: Performance Charts                                  │
│ ┌─────────────────────────────────────────┐                │
│ │ Model: [primary-v2 ▼]                   │                │
│ │ Accuracy: [Line chart ─────────]        │                │
│ │ ROI:      [Line chart ─────────]        │                │
│ │ Volume:   [Bar chart  █ █ █ █ ]        │                │
│ └─────────────────────────────────────────┘                │
│                                                             │
│ TAB 4: Batch Testing                                       │
│ ┌─────────────────────────────────────────┐                │
│ │ Parameter: [confidence_threshold ▼]     │                │
│ │ Range: [0.1] to [0.5] │ Steps: [5]     │                │
│ │ [🚀 Run Batch Test]                     │                │
│ ├─────────────────────────────────────────┤                │
│ │ Value  │ Accuracy│ ROI   │ Sharpe      │                │
│ │ 0.100  │ 52.3%   │ +8.2% │ 1.23        │                │
│ │ 0.200  │ 53.1%   │ +9.5% │ 1.45 ← Best │                │
│ └─────────────────────────────────────────┘                │
└─────────────────────────────────────────────────────────────┘
```

## Feature Flow Diagrams

### Bet Tracking Flow
```
User views game prediction
       ↓
System shows recommendation with edge
       ↓
User adjusts stake amount
       ↓
User clicks "Track Bet"
       ↓
Bet added to session history
       ↓
Performance analytics updated
       ↓
Sidebar shows updated P&L and ROI
```

### Kelly Criterion Flow
```
User enters win probability
       ↓
User enters American odds
       ↓
System converts to decimal odds
       ↓
Kelly formula: (bp - q) / b
       ↓
Apply fractional Kelly (0.25x)
       ↓
Calculate EV and ROI
       ↓
Display recommendation with thresholds
```

### Bankroll Simulation Flow
```
User sets starting bankroll and parameters
       ↓
User selects staking method (Kelly/Fixed/%)
       ↓
User clicks "Run Simulation"
       ↓
System runs N Monte Carlo simulations
       ↓
For each simulation:
  - Generate random outcomes based on win prob
  - Apply staking method
  - Track bankroll changes
       ↓
Aggregate results across simulations
       ↓
Display: Avg, Median, Return, Drawdown, Sharpe
       ↓
Plot bankroll trajectories
```

### Model Comparison Flow
```
User navigates to Model Lab → Live Comparison
       ↓
User selects 2+ models to compare
       ↓
System loads model parameters and metadata
       ↓
User selects comparison metric
       ↓
System displays side-by-side comparison:
  - Model details matrix
  - Parameter comparison (JSON)
  - Sample predictions (mock data)
       ↓
User analyzes differences
```

## Key Metrics Explained

### Kelly Criterion
```
f* = (bp - q) / b

Where:
- f* = Fraction of bankroll to wager
- b = Net odds received (profit per unit stake)
- p = Probability of winning
- q = Probability of losing (1 - p)

Fractional Kelly = Full Kelly × Fraction
Example: Quarter Kelly = Full Kelly × 0.25
```

### Expected Value (EV)
```
EV = (p × profit) - (q × loss)

Where:
- p = Win probability
- profit = Amount won if bet wins
- q = Loss probability (1 - p)
- loss = Amount lost if bet loses
```

### Sharpe Ratio
```
SR = (E[R] - Rf) / σ(R)

Where:
- E[R] = Expected return
- Rf = Risk-free rate (typically 0 for betting)
- σ(R) = Standard deviation of returns

Higher is better (>1.0 is good, >2.0 is excellent)
```

### Maximum Drawdown
```
DD = (Peak - Trough) / Peak

Largest percentage decline from peak to trough
Lower is better (closer to 0%)
```

## Usage Tips

### For Beginners
1. Start with Sports Betting Quant Kelly Calculator
2. Use Quarter Kelly (0.25) - never full Kelly
3. Enable Performance Analytics in main dashboard
4. Track all your bets to learn actual edge
5. Review Risk Metrics regularly

### For Advanced Users
1. Run bankroll simulations before implementing strategy
2. Use Portfolio Optimizer for multiple opportunities
3. Compare models in Model Lab Live Comparison
4. Run batch tests to optimize parameters
5. Monitor Sharpe ratio and adjust accordingly

### Best Practices
- Never bet more than 5% of bankroll on single bet
- Track everything - data is your friend
- Use Quarter Kelly or less
- Respect your bankroll limits
- Take breaks and review performance regularly
- Remember: No system guarantees profits

## Integration Points

### Session State Variables
```python
st.session_state.bet_history = []      # List of tracked bets
st.session_state.bankroll = 10000.0    # Current bankroll
st.session_state.show_analytics = False # Analytics toggle
```

### Key Functions
```python
kelly_criterion(prob, odds, fraction)  # Optimal bet size
expected_value(prob, odds, stake)      # EV calculation
sharpe_ratio(returns)                  # Risk-adjusted return
max_drawdown(bankroll_history)         # Largest decline
simulate_betting_strategy(...)         # Monte Carlo sim
```

## Deployment Checklist

✅ All features implemented
✅ All code reviewed
✅ Security scan passed
✅ Documentation complete
✅ README updated
✅ Backward compatible
✅ Error handling in place
✅ User disclaimers added

Ready for production deployment! 🚀
