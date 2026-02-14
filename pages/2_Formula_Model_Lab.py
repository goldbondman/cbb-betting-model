"""Model Lab - Create, edit, backtest, and activate formula models.

This page allows you to create and test formula-based prediction models with weighted components.

Available Features:
  Core Metrics (Legacy):
    - Torvik AdjEM: Adjusted efficiency margin from Barttorvik ratings
    - Recent (L7): Net rating over last 7 games
    - Four Factors: Composite of eFG%, TOV%, ORB%, FTR (Dean Oliver's four factors)
    - SOS Weighted: Strength of schedule adjusted margin (L10)
    
  Advanced Metrics (Enhanced):
    - Defensive Efficiency: DRTG gap (home vs away defense)
    - Offensive Efficiency: ORTG gap (home vs away offense)
    - Tempo Advantage: Pace differential impact on spread
    - Three-Point Rate: 3-point attempt rate differential
    
All features use pre-game stats to prevent data leakage.
Models are backwards compatible - legacy 4-feature models work alongside enhanced 8-feature models.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from backtesting.backtest_engine import BacktestEngine
from core.model_registry import activate_model, create_model, delete_model, get_active_model, list_all_models

st.set_page_config(page_title="Model Lab", page_icon="🧪", layout="wide")
st.title("🧪 Model Lab")

st.header("🟢 Active Model")
active = get_active_model("spread")
if active:
    st.success(f"**{active['model_name']}** ({active['model_id']})")
    with st.expander("View Configuration"):
        st.json(active["params"])
else:
    st.info("No active model (using fallback)")

st.divider()

st.header("➕ Create New Model")
with st.form("create_model"):
    col1, col2 = st.columns(2)
    with col1:
        new_id = st.text_input("Model ID", placeholder="duke-killer-v1")
        new_name = st.text_input("Model Name", placeholder="Duke Killer v1")
    with col2:
        model_type = st.selectbox("Model Type", ["spread", "total"])

    st.subheader("Component Weights")
    st.caption("Configure weights for each feature component. Total will auto-normalize to 1.0.")
    
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Core Metrics**")
        w_torvik = st.slider("Torvik AdjEM", 0.0, 1.0, 0.40, 0.05, help="Adjusted efficiency margin rating")
        w_recent = st.slider("Recent (L7)", 0.0, 1.0, 0.20, 0.05, help="Last 7 games net rating")
        w_ff = st.slider("Four Factors", 0.0, 1.0, 0.12, 0.05, help="Composite: eFG%, TOV%, ORB%, FTR")
        w_sos = st.slider("SOS Weighted", 0.0, 1.0, 0.08, 0.05, help="Strength of schedule (L10)")
        
    with col2:
        st.markdown("**Advanced Metrics**")
        w_def_eff = st.slider("Defensive Efficiency", 0.0, 1.0, 0.08, 0.05, help="DRTG vs opponent's ORTG")
        w_off_eff = st.slider("Offensive Efficiency", 0.0, 1.0, 0.06, 0.05, help="ORTG differential")
        w_tempo = st.slider("Tempo Advantage", 0.0, 1.0, 0.04, 0.05, help="Pace impact on spread")
        w_three_rate = st.slider("Three-Point Rate", 0.0, 1.0, 0.02, 0.05, help="3-point attempt rate differential")

    total_weight = w_torvik + w_recent + w_ff + w_sos + w_def_eff + w_off_eff + w_tempo + w_three_rate
    if total_weight > 0:
        st.caption(f"Total weight: {total_weight:.2f} (will auto-normalize to 1.0)")
        w_torvik = w_torvik / total_weight
        w_recent = w_recent / total_weight
        w_ff = w_ff / total_weight
        w_sos = w_sos / total_weight
        w_def_eff = w_def_eff / total_weight
        w_off_eff = w_off_eff / total_weight
        w_tempo = w_tempo / total_weight
        w_three_rate = w_three_rate / total_weight

    st.subheader("Home Court Advantage")
    hca_mode = st.radio("HCA Mode", ["dynamic", "static"])
    hca_static = st.number_input("Static HCA Value", 0.0, 10.0, 2.7, 0.1) if hca_mode == "static" else 2.7

    st.subheader("Options")
    pace_adj = st.checkbox("Pace Adjustment", value=True)

    submitted = st.form_submit_button("Create Model")
    if submitted:
        if not new_id or not new_name:
            st.error("Model ID and Name are required")
        else:
            params = {
                "formula_type": "weighted_components",
                "weights": {
                    "torvik_adjem": float(w_torvik),
                    "recent_netrtg": float(w_recent),
                    "four_factors": float(w_ff),
                    "sos_weighted": float(w_sos),
                    "def_efficiency": float(w_def_eff),
                    "off_efficiency": float(w_off_eff),
                    "tempo_advantage": float(w_tempo),
                    "three_rate": float(w_three_rate),
                },
                "hca_mode": hca_mode,
                "hca_static_value": float(hca_static),
                "pace_adjustment": pace_adj,
                "confidence_method": "sample_size_boost",
            }
            if create_model(new_id, new_name, model_type, params):
                st.success(f"Created model: {new_name}")
                st.rerun()
            else:
                st.error("Failed to create model")

st.divider()

st.header("📋 All Models")
models = list_all_models("spread")
if models:
    df = pd.DataFrame(models)
    show_cols = [col for col in ["model_id", "model_name", "is_active", "created_at"] if col in df.columns]
    st.dataframe(df[show_cols], use_container_width=True)

    st.subheader("Model Actions")
    selected_id = st.selectbox("Select model", df["model_id"].tolist())
    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("✅ Activate") and activate_model(selected_id):
            st.success(f"Activated {selected_id}")
            st.rerun()

    with col2:
        if st.button("📊 Backtest"):
            with st.spinner("Running backtest..."):
                model = next(m for m in models if m["model_id"] == selected_id)
                bt = BacktestEngine()
                results = bt.backtest_model(model, days_back=30)

                st.subheader(f"Backtest Results: {model['model_name']}")
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("MAE", f"{results['mae']:.2f} pts")
                m2.metric("Win %", f"{results['win_pct']:.1%}")
                m3.metric("ROI", f"{results['roi']:.1%}")
                m4.metric("Games", results["total_games"])

                st.subheader("Edge Distribution")
                st.bar_chart(results["edge_distribution"])

                with st.expander("Game-by-game Results"):
                    st.dataframe(results["details"], use_container_width=True)

    with col3:
        if st.button("🗑️ Delete") and delete_model(selected_id):
            st.success(f"Deleted {selected_id}")
            st.rerun()
else:
    st.info("No models created yet")
