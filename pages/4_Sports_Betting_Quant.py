"""Sports Betting Quant - Advanced quantitative betting analytics and risk management.

This page provides professional sports betting tools including:
- Kelly Criterion calculator and optimizer
- Bankroll management and position sizing
- Portfolio optimization and risk metrics
- Expected value and ROI analysis
- Drawdown analysis and recovery simulations
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st
from datetime import datetime, timedelta

st.set_page_config(page_title="Sports Betting Quant", page_icon="📊", layout="wide")

# ============================================================
# HELPER FUNCTIONS
# ============================================================

def kelly_criterion(win_prob: float, odds: float, fraction: float = 1.0) -> float:
    """
    Calculate Kelly Criterion bet size.
    
    Args:
        win_prob: Probability of winning (0-1)
        odds: American odds (e.g., -110, +150)
        fraction: Fraction of Kelly to use (e.g., 0.25 for quarter Kelly)
    
    Returns:
        Optimal bet size as fraction of bankroll
    """
    # Convert American odds to decimal
    if odds > 0:
        b = odds / 100.0  # profit per unit stake
    else:
        b = 100.0 / abs(odds)
    
    q = 1 - win_prob
    kelly = (b * win_prob - q) / b
    return max(0, kelly * fraction)


def expected_value(win_prob: float, odds: float, stake: float = 1.0) -> float:
    """Calculate expected value of a bet."""
    if odds > 0:
        profit = stake * (odds / 100.0)
    else:
        profit = stake * (100.0 / abs(odds))
    
    ev = (win_prob * profit) - ((1 - win_prob) * stake)
    return ev


def expected_roi(win_prob: float, odds: float) -> float:
    """Calculate expected ROI as percentage."""
    ev = expected_value(win_prob, odds, 1.0)
    return ev * 100


def sharpe_ratio(returns: list[float], risk_free_rate: float = 0.0) -> float:
    """Calculate Sharpe ratio of returns."""
    if not returns or len(returns) < 2:
        return 0.0
    
    returns_array = np.array(returns)
    excess_returns = returns_array - risk_free_rate
    
    if np.std(excess_returns) == 0:
        return 0.0
    
    return np.mean(excess_returns) / np.std(excess_returns)


def max_drawdown(bankroll_history: list[float]) -> tuple[float, int, int]:
    """
    Calculate maximum drawdown.
    
    Returns:
        (max_drawdown_pct, start_idx, end_idx)
    """
    if not bankroll_history or len(bankroll_history) < 2:
        return 0.0, 0, 0
    
    peak = bankroll_history[0]
    max_dd = 0.0
    peak_idx = 0
    trough_idx = 0
    
    for i, value in enumerate(bankroll_history):
        if value > peak:
            peak = value
            peak_idx = i
        
        drawdown = (peak - value) / peak if peak > 0 else 0.0
        if drawdown > max_dd:
            max_dd = drawdown
            trough_idx = i
    
    return max_dd, peak_idx, trough_idx


def simulate_betting_strategy(
    starting_bankroll: float,
    num_bets: int,
    win_prob: float,
    odds: float,
    stake_method: str = "fixed",
    fixed_units: float = 1.0,
    kelly_fraction: float = 0.25,
    unit_size: float = 100.0
) -> dict:
    """
    Simulate a betting strategy over multiple bets.
    
    Args:
        starting_bankroll: Initial bankroll
        num_bets: Number of bets to simulate
        win_prob: Win probability per bet
        odds: American odds
        stake_method: "fixed", "kelly", or "percentage"
        fixed_units: Number of units for fixed betting
        kelly_fraction: Fraction of Kelly to use
        unit_size: Dollar value per unit
    
    Returns:
        Dictionary with simulation results
    """
    bankroll_history = [starting_bankroll]
    bet_history = []
    
    current_bankroll = starting_bankroll
    
    for i in range(num_bets):
        # Determine stake size
        if stake_method == "fixed":
            stake = fixed_units * unit_size
        elif stake_method == "kelly":
            kelly_pct = kelly_criterion(win_prob, odds, kelly_fraction)
            stake = current_bankroll * kelly_pct
        else:  # percentage
            stake = current_bankroll * (fixed_units / 100)
        
        # Ensure stake doesn't exceed bankroll
        stake = min(stake, current_bankroll)
        
        # Simulate bet outcome
        won = np.random.random() < win_prob
        
        if won:
            if odds > 0:
                profit = stake * (odds / 100.0)
            else:
                profit = stake * (100.0 / abs(odds))
            current_bankroll += profit
        else:
            current_bankroll -= stake
        
        bankroll_history.append(current_bankroll)
        bet_history.append({
            "bet_num": i + 1,
            "stake": stake,
            "won": won,
            "bankroll": current_bankroll,
            "profit": profit if won else -stake
        })
        
        # Stop if bankroll depleted
        if current_bankroll <= 0:
            break
    
    # Calculate metrics
    final_bankroll = bankroll_history[-1]
    total_return = ((final_bankroll - starting_bankroll) / starting_bankroll) * 100
    max_dd, peak_idx, trough_idx = max_drawdown(bankroll_history)
    
    returns = [bet_history[i]["profit"] / starting_bankroll for i in range(len(bet_history))]
    sharpe = sharpe_ratio(returns)
    
    return {
        "bankroll_history": bankroll_history,
        "bet_history": bet_history,
        "final_bankroll": final_bankroll,
        "total_return": total_return,
        "max_drawdown": max_dd,
        "sharpe_ratio": sharpe,
        "num_wins": sum(1 for b in bet_history if b["won"]),
        "num_losses": sum(1 for b in bet_history if not b["won"]),
    }


# ============================================================
# MAIN APP
# ============================================================

st.title("📊 Sports Betting Quant")
st.caption("Advanced quantitative analysis and risk management tools for sports betting")

# ============================================================
# 1. KELLY CRITERION CALCULATOR
# ============================================================

st.header("1️⃣ Kelly Criterion Calculator")
st.caption("Calculate optimal bet sizing using the Kelly Criterion formula")

col1, col2 = st.columns(2)

with col1:
    st.subheader("Input Parameters")
    win_probability = st.slider("Win Probability", 0.0, 1.0, 0.55, 0.01, 
                                 help="Your estimated probability of winning")
    american_odds = st.number_input("American Odds", -1000, 1000, -110, 10,
                                     help="Market odds (e.g., -110, +150)")
    kelly_frac = st.slider("Kelly Fraction", 0.0, 1.0, 0.25, 0.05,
                           help="Fraction of full Kelly to use (0.25 = Quarter Kelly)")

with col2:
    st.subheader("Results")
    
    # Calculate Kelly
    full_kelly = kelly_criterion(win_probability, american_odds, 1.0) * 100
    fractional_kelly = kelly_criterion(win_probability, american_odds, kelly_frac) * 100
    ev = expected_value(win_probability, american_odds, 1.0)
    roi = expected_roi(win_probability, american_odds)
    
    m1, m2 = st.columns(2)
    m1.metric("Full Kelly", f"{full_kelly:.2f}%", help="Full Kelly bet size")
    m2.metric(f"{kelly_frac:.0%} Kelly", f"{fractional_kelly:.2f}%", 
             help=f"Recommended bet size using {kelly_frac:.0%} Kelly")
    
    m3, m4 = st.columns(2)
    m3.metric("Expected Value", f"${ev:.2f}", help="EV per $1 wagered")
    m4.metric("Expected ROI", f"{roi:.2f}%", help="Expected return on investment")
    
    # Show recommendation
    if full_kelly <= 0:
        st.error("❌ No bet recommended - Negative or zero edge")
    elif full_kelly < 2:
        st.warning("⚠️ Small edge - Consider passing")
    else:
        st.success(f"✅ Bet {fractional_kelly:.2f}% of bankroll")

st.divider()

# ============================================================
# 2. BANKROLL SIMULATOR
# ============================================================

st.header("2️⃣ Bankroll Simulator")
st.caption("Simulate betting strategy performance over multiple bets")

col1, col2, col3 = st.columns(3)

with col1:
    starting_bank = st.number_input("Starting Bankroll ($)", 100, 100000, 10000, 100)
    sim_win_prob = st.slider("Win Probability", 0.0, 1.0, 0.55, 0.01, key="sim_win_prob")
    sim_odds = st.number_input("Odds", -1000, 1000, -110, 10, key="sim_odds")

with col2:
    num_bets = st.number_input("Number of Bets", 10, 1000, 100, 10)
    stake_method = st.selectbox("Staking Method", ["kelly", "fixed", "percentage"])
    
with col3:
    if stake_method == "fixed":
        unit_size = st.number_input("Unit Size ($)", 1, 10000, 100, 10)
        num_units = st.number_input("Units per Bet", 0.1, 10.0, 1.0, 0.1)
    elif stake_method == "kelly":
        num_units = st.slider("Kelly Fraction", 0.0, 1.0, 0.25, 0.05)
        unit_size = 1.0  # Not used for Kelly
    else:
        num_units = st.slider("Bankroll %", 0.1, 10.0, 2.0, 0.1)
        unit_size = 1.0  # Not used for percentage

num_simulations = st.slider("Number of Simulations", 1, 100, 10, 1,
                            help="Run multiple simulations to see variance")

if st.button("🎲 Run Simulation", type="primary"):
    with st.spinner("Running simulations..."):
        all_results = []
        
        for _ in range(num_simulations):
            result = simulate_betting_strategy(
                starting_bankroll=starting_bank,
                num_bets=num_bets,
                win_prob=sim_win_prob,
                odds=sim_odds,
                stake_method=stake_method,
                fixed_units=num_units,
                kelly_fraction=num_units if stake_method == "kelly" else 0.25,
                unit_size=unit_size if stake_method == "fixed" else 100
            )
            all_results.append(result)
        
        # Display aggregate metrics
        st.subheader("Simulation Results")
        
        avg_final = np.mean([r["final_bankroll"] for r in all_results])
        med_final = np.median([r["final_bankroll"] for r in all_results])
        avg_return = np.mean([r["total_return"] for r in all_results])
        avg_drawdown = np.mean([r["max_drawdown"] for r in all_results])
        avg_sharpe = np.mean([r["sharpe_ratio"] for r in all_results])
        
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Avg Final", f"${avg_final:,.0f}")
        m2.metric("Median Final", f"${med_final:,.0f}")
        m3.metric("Avg Return", f"{avg_return:.1f}%")
        m4.metric("Avg Max DD", f"{avg_drawdown:.1%}")
        m5.metric("Avg Sharpe", f"{avg_sharpe:.2f}")
        
        # Plot bankroll trajectories
        st.subheader("Bankroll Trajectories")
        chart_data = pd.DataFrame({
            f"Sim {i+1}": result["bankroll_history"] 
            for i, result in enumerate(all_results)
        })
        st.line_chart(chart_data)
        
        # Show individual simulation details
        with st.expander("📋 Individual Simulation Details"):
            for i, result in enumerate(all_results[:5]):  # Show up to 5
                st.write(f"**Simulation {i+1}:**")
                col1, col2, col3 = st.columns(3)
                col1.write(f"Final: ${result['final_bankroll']:,.2f}")
                col2.write(f"Return: {result['total_return']:.1f}%")
                col3.write(f"W-L: {result['num_wins']}-{result['num_losses']}")

st.divider()

# ============================================================
# 3. PORTFOLIO OPTIMIZER
# ============================================================

st.header("3️⃣ Portfolio Optimizer")
st.caption("Optimize bet allocation across multiple opportunities")

st.info("💡 Enter multiple betting opportunities to find optimal portfolio allocation")

num_bets_portfolio = st.number_input("Number of Opportunities", 2, 10, 3, 1)

portfolio_data = []
for i in range(num_bets_portfolio):
    with st.expander(f"Opportunity {i+1}", expanded=(i == 0)):
        col1, col2, col3 = st.columns(3)
        with col1:
            name = st.text_input("Name", f"Bet {i+1}", key=f"name_{i}")
        with col2:
            prob = st.slider("Win Prob", 0.0, 1.0, 0.55, 0.01, key=f"prob_{i}")
        with col3:
            odds = st.number_input("Odds", -1000, 1000, -110, 10, key=f"odds_{i}")
        
        portfolio_data.append({"name": name, "prob": prob, "odds": odds})

total_bankroll = st.number_input("Total Bankroll ($)", 100, 1000000, 10000, 100)
max_exposure = st.slider("Max Total Exposure (%)", 1, 100, 25, 1,
                         help="Maximum % of bankroll to risk across all bets")

if st.button("⚡ Optimize Portfolio", type="primary"):
    st.subheader("Optimal Allocation")
    
    # Calculate Kelly for each opportunity
    allocations = []
    for bet in portfolio_data:
        kelly = kelly_criterion(bet["prob"], bet["odds"], 0.25)  # Quarter Kelly
        ev = expected_value(bet["prob"], bet["odds"], 1.0)
        roi = expected_roi(bet["prob"], bet["odds"])
        
        allocations.append({
            "Opportunity": bet["name"],
            "Win Prob": f"{bet['prob']:.1%}",
            "Odds": bet["odds"],
            "Kelly %": f"{kelly*100:.2f}%",
            "Stake ($)": f"${total_bankroll * kelly:,.2f}",
            "EV": f"${ev:.2f}",
            "ROI": f"{roi:.2f}%"
        })
    
    df = pd.DataFrame(allocations)
    st.dataframe(df, use_container_width=True)
    
    # Calculate portfolio metrics
    total_stake = sum([total_bankroll * kelly_criterion(b["prob"], b["odds"], 0.25) 
                      for b in portfolio_data])
    exposure_pct = (total_stake / total_bankroll) * 100
    
    col1, col2 = st.columns(2)
    col1.metric("Total Stake", f"${total_stake:,.2f}")
    col2.metric("Portfolio Exposure", f"{exposure_pct:.1f}%")
    
    if exposure_pct > max_exposure:
        st.warning(f"⚠️ Total exposure ({exposure_pct:.1f}%) exceeds max ({max_exposure}%). Consider reducing stakes proportionally.")
        
        # Show scaled allocation
        scale_factor = max_exposure / exposure_pct
        st.info(f"**Scaled Allocation** (multiply each stake by {scale_factor:.2f})")

st.divider()

# ============================================================
# 4. RISK METRICS
# ============================================================

st.header("4️⃣ Risk Metrics & Analysis")
st.caption("Calculate key risk metrics for your betting strategy")

col1, col2 = st.columns(2)

with col1:
    st.subheader("Input Past Results")
    results_input = st.text_area(
        "Enter past bet results (one per line)",
        value="+100\n-100\n+91\n-100\n+91\n+91\n-100",
        help="Enter profit/loss for each bet (e.g., +91 for win at -110, -100 for loss)",
        height=150
    )

with col2:
    st.subheader("Risk Metrics")
    
    try:
        # Parse results
        results = [float(x.strip()) for x in results_input.split('\n') if x.strip()]
        
        if results:
            # Calculate metrics
            total_return = sum(results)
            avg_return = np.mean(results)
            std_return = np.std(results)
            # Annualization factor: assumes each result represents an equal time period (e.g., daily)
            # and returns are independent. This is a common approximation for Sharpe ratio scaling.
            sharpe = sharpe_ratio(results) * np.sqrt(len(results)) if len(results) > 1 else 0
            
            # Cumulative bankroll
            cumulative = [1000 + sum(results[:i+1]) for i in range(len(results))]
            max_dd, _, _ = max_drawdown(cumulative)
            
            win_rate = sum(1 for r in results if r > 0) / len(results)
            
            st.metric("Total P&L", f"${total_return:,.2f}")
            st.metric("Win Rate", f"{win_rate:.1%}")
            st.metric("Avg Return", f"${avg_return:.2f}")
            st.metric("Std Dev", f"${std_return:.2f}")
            st.metric("Sharpe Ratio", f"{sharpe:.2f}")
            st.metric("Max Drawdown", f"{max_dd:.1%}")
            
            # Plot equity curve
            st.subheader("Equity Curve")
            equity_df = pd.DataFrame({"Bankroll": cumulative})
            st.line_chart(equity_df)
    except Exception as e:
        st.error(f"Error parsing results: {e}")

st.divider()

# ============================================================
# 5. EDUCATION & RESOURCES
# ============================================================

with st.expander("📚 Education & Best Practices"):
    st.markdown("""
    ### Kelly Criterion
    The Kelly Criterion is a formula for optimal bet sizing that maximizes long-term growth rate.
    
    **Formula:** `f* = (bp - q) / b`
    - f* = fraction of bankroll to wager
    - b = net odds received (decimal odds - 1)
    - p = probability of winning
    - q = probability of losing (1 - p)
    
    ### Fractional Kelly
    Most professional bettors use **Quarter Kelly (0.25)** or **Half Kelly (0.5)** to:
    - Reduce variance
    - Account for model uncertainty
    - Protect against estimation errors
    
    ### Best Practices
    1. **Never bet more than 5% on a single bet** (even with full Kelly)
    2. **Use Quarter Kelly or less** for most situations
    3. **Track all bets** to calculate actual edge vs estimated
    4. **Respect bankroll limits** - never risk more than you can afford to lose
    5. **Consider correlation** - don't over-expose to correlated bets
    
    ### Risk Metrics
    - **Sharpe Ratio:** Return per unit of risk (higher is better, >1.0 is good)
    - **Max Drawdown:** Largest peak-to-trough decline (lower is better)
    - **Win Rate:** Percentage of winning bets (needs to be calibrated with odds)
    - **ROI:** Return on investment as percentage
    
    ### Portfolio Management
    - Diversify across uncorrelated opportunities
    - Set maximum portfolio exposure (e.g., 25% of bankroll)
    - Rebalance regularly based on bankroll changes
    - Don't chase losses with increased stakes
    """)

with st.expander("⚠️ Disclaimer"):
    st.warning("""
    **DISCLAIMER:** This tool is for educational and analytical purposes only.
    
    - Sports betting involves risk and can result in financial loss
    - Past performance does not guarantee future results
    - No betting system can guarantee profits
    - Only bet what you can afford to lose
    - Kelly Criterion assumes accurate probability estimates
    - Consult local laws regarding sports betting legality
    - This is not financial advice
    
    Always gamble responsibly.
    """)
