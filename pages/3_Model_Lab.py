import json
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import streamlit as st

from core.supabase_utils import get_public_supabase_client

st.set_page_config(page_title="Model Lab", page_icon="🧪", layout="wide")

# Session state for dynamic features
if "model_comparison_cache" not in st.session_state:
    st.session_state.model_comparison_cache = {}
if "live_tuning_params" not in st.session_state:
    st.session_state.live_tuning_params = {}

REGISTRY_TABLE = os.getenv("MODEL_REGISTRY_TABLE", "model_registry")
REGISTRY_SCHEMA = os.getenv("MODEL_REGISTRY_SCHEMA", "public")


def _get_supabase_client():
    return get_public_supabase_client()


def _tbl(client):
    # Support both "public.table(...)" and default schema, depending on supabase-py version
    try:
        return client.schema(REGISTRY_SCHEMA).table(REGISTRY_TABLE)
    except Exception:
        return client.table(REGISTRY_TABLE)


def _load_registry() -> pd.DataFrame:
    client = _get_supabase_client()
    if client is None:
        return pd.DataFrame()

    try:
        resp = _tbl(client).select("*").order("created_at", desc=True).execute()
        return pd.DataFrame(resp.data or [])
    except Exception:
        return pd.DataFrame()


def _upsert_registry(payload: dict) -> str | None:
    client = _get_supabase_client()
    if client is None:
        return "Supabase credentials missing."

    try:
        _tbl(client).upsert(payload, on_conflict="model_id").execute()
        return None
    except Exception as exc:
        return str(exc)


def _set_active(model_id: str, make_active: bool, deactivate_same_type: bool = True) -> str | None:
    client = _get_supabase_client()
    if client is None:
        return "Supabase credentials missing."

    try:
        # Find model_type for this model_id (used for optional deactivate)
        resp = _tbl(client).select("model_id,model_type").eq("model_id", model_id).limit(1).execute()
        rows = resp.data or []
        if not rows:
            return f"Model not found: {model_id}"
        model_type = rows[0].get("model_type")

        now_iso = datetime.now(timezone.utc).isoformat()

        # Optionally deactivate other models of same type
        if make_active and deactivate_same_type and model_type:
            _tbl(client).update({"is_active": False, "updated_at": now_iso}).eq("model_type", model_type).execute()

        # Set target model
        _tbl(client).update({"is_active": bool(make_active), "updated_at": now_iso}).eq("model_id", model_id).execute()
        return None
    except Exception as exc:
        return str(exc)


def _deactivate_all() -> str | None:
    client = _get_supabase_client()
    if client is None:
        return "Supabase credentials missing."
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        _tbl(client).update({"is_active": False, "updated_at": now_iso}).neq("model_id", "").execute()
        return None
    except Exception as exc:
        return str(exc)


def _delete_model(model_id: str) -> str | None:
    client = _get_supabase_client()
    if client is None:
        return "Supabase credentials missing."
    try:
        _tbl(client).delete().eq("model_id", model_id).execute()
        return None
    except Exception as exc:
        return str(exc)


st.title("🧪 Model Lab")
st.caption("Register multiple models and track which are active. Option 2 compatible.")

# Add tabs for different features
tab1, tab2, tab3, tab4 = st.tabs(["📋 Model Registry", "🔬 Live Comparison", "📊 Performance Charts", "⚡ Batch Testing"])

# ---------- TAB 1: MODEL REGISTRY (Original functionality) ----------
with tab1:

    client_ok = _get_supabase_client() is not None
    if not client_ok:
        st.warning("Supabase credentials missing (SUPABASE_URL + SUPABASE_ANON_KEY or SUPABASE_KEY). UI will be read-only.")

    registry = _load_registry()
    if registry.empty:
        st.info("No models registered yet (or Supabase credentials missing).")

    # ---------- Quick actions ----------
    st.subheader("Quick actions")
    col_a, col_b = st.columns([1, 2])

    with col_a:
        deactivate_same_type = st.checkbox(
            "When activating, deactivate other models of same type",
            value=True,
            help="Keeps one active model per model_type (spread/total/ensemble/etc).",
            disabled=not client_ok,
        )

    with col_b:
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Deactivate all models", disabled=not client_ok):
                err = _deactivate_all()
                if err:
                    st.error(f"Failed: {err}")
                else:
                    st.success("All models deactivated.")
                    registry = _load_registry()

        with c2:
            st.write("")

    st.divider()

    # ---------- Register / Update form ----------
    with st.form("register_model", clear_on_submit=True):
        st.subheader("Register model")
        model_id = st.text_input("Model ID (unique)", placeholder="base-efficiency-v1")
        model_name = st.text_input("Model name", placeholder="Base Efficiency")
        model_type = st.selectbox("Model type", ["spread", "total", "blowout", "multihead", "ensemble"])
        feature_set = st.text_input("Feature set", placeholder="l7+v2")
        model_version = st.text_input("Model version", placeholder="2026-02-06")
        params = st.text_area("Params (JSON)", value="{}")
        is_active = st.checkbox("Set active", value=False, disabled=not client_ok)
        submitted = st.form_submit_button("Save", disabled=not client_ok)

    if submitted:
        if not model_id.strip() or not model_name.strip():
            st.error("Model ID and Model name are required.")
        else:
            try:
                params_json = {} if not params.strip() else json.loads(params)
            except Exception:
                params_json = None

            if params_json is None:
                st.error("Params must be valid JSON.")
            else:
                now_iso = datetime.now(timezone.utc).isoformat()
                payload = {
                    "model_id": model_id.strip(),
                    "model_name": model_name.strip(),
                    "model_type": model_type,
                    "feature_set": feature_set.strip() or None,
                    "model_version": model_version.strip() or None,
                    "params": params_json,
                    "is_active": bool(is_active),
                    "updated_at": now_iso,
                }

                # If activating and we want one-active-per-type, deactivate others first
                if bool(is_active) and deactivate_same_type:
                    err = _set_active(model_id.strip(), True, deactivate_same_type=True)
                    if err:
                        st.error(f"Failed to activate: {err}")
                        st.stop()

                err = _upsert_registry(payload)
                if err:
                    st.error(f"Failed to save: {err}")
                else:
                    st.success("Model saved.")
                    registry = _load_registry()

    st.divider()

    # ---------- Registry table + per-row controls ----------
    st.subheader("Registered models")

    if registry.empty:
        st.info("No models to display.")
    else:
        # Normalize columns for display
        for col in ["created_at", "updated_at"]:
            if col in registry.columns:
                registry[col] = pd.to_datetime(registry[col], utc=True, errors="coerce")

        display_cols = [
            "model_id",
            "model_name",
            "model_type",
            "feature_set",
            "model_version",
            "is_active",
            "created_at",
            "updated_at",
        ]
        existing_cols = [c for c in display_cols if c in registry.columns]
        st.dataframe(registry[existing_cols], use_container_width=True)

        st.subheader("Manage a model")
        model_ids = registry["model_id"].astype(str).tolist() if "model_id" in registry.columns else []
        selected = st.selectbox("Select model_id", model_ids)

        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button("Activate", disabled=not client_ok or not selected):
                err = _set_active(selected, True, deactivate_same_type=deactivate_same_type)
                if err:
                    st.error(f"Failed: {err}")
                else:
                    st.success("Activated.")
                    registry = _load_registry()

        with c2:
            if st.button("Deactivate", disabled=not client_ok or not selected):
                err = _set_active(selected, False, deactivate_same_type=False)
                if err:
                    st.error(f"Failed: {err}")
                else:
                    st.success("Deactivated.")
                    registry = _load_registry()

        with c3:
            if st.button("Delete", disabled=not client_ok or not selected):
                err = _delete_model(selected)
                if err:
                    st.error(f"Failed: {err}")
                else:
                    st.success("Deleted.")
                    registry = _load_registry()

# ---------- TAB 2: LIVE COMPARISON ----------
with tab2:
    st.subheader("🔬 Live Model Comparison")
    st.caption("Compare predictions from multiple models side-by-side")
    
    registry = _load_registry()
    if registry.empty:
        st.info("No models available for comparison. Create models in the Registry tab first.")
    else:
        model_ids = registry["model_id"].tolist() if "model_id" in registry.columns else []
        
        col1, col2 = st.columns(2)
        with col1:
            models_to_compare = st.multiselect("Select models to compare", model_ids, default=model_ids[:min(3, len(model_ids))])
        
        with col2:
            comparison_metric = st.selectbox("Comparison metric", ["Prediction", "Confidence", "Parameters"])
        
        if models_to_compare:
            st.subheader("Model Comparison Matrix")
            
            # Create comparison dataframe
            comparison_data = []
            for model_id in models_to_compare:
                model_row = registry[registry["model_id"] == model_id].iloc[0]
                comparison_data.append({
                    "Model ID": model_id,
                    "Model Name": model_row.get("model_name", "N/A"),
                    "Type": model_row.get("model_type", "N/A"),
                    "Feature Set": model_row.get("feature_set", "N/A"),
                    "Active": "✓" if model_row.get("is_active") else "",
                    "Created": model_row.get("created_at", "N/A")
                })
            
            df_comparison = pd.DataFrame(comparison_data)
            st.dataframe(df_comparison, use_container_width=True)
            
            # Show parameter comparison
            if comparison_metric == "Parameters":
                st.subheader("Parameter Comparison")
                for model_id in models_to_compare:
                    model_row = registry[registry["model_id"] == model_id].iloc[0]
                    with st.expander(f"📋 {model_id} parameters"):
                        params = model_row.get("params", {})
                        if isinstance(params, dict):
                            st.json(params)
                        else:
                            st.text(str(params))
            
            # Simulated prediction comparison (mock data for demonstration)
            if comparison_metric == "Prediction":
                st.subheader("Sample Prediction Comparison")
                st.caption("Comparing model predictions on hypothetical matchups")
                
                # Mock matchup data
                matchups = ["Duke vs UNC", "Kansas vs Kentucky", "Gonzaga vs UCLA"]
                mock_predictions = []
                
                for matchup in matchups:
                    row = {"Matchup": matchup}
                    for model_id in models_to_compare:
                        # Generate mock prediction
                        base_pred = np.random.uniform(-5, 5)
                        row[f"{model_id} Spread"] = f"{base_pred:+.1f}"
                        row[f"{model_id} Conf"] = f"{np.random.uniform(0.55, 0.85):.2f}"
                    mock_predictions.append(row)
                
                df_mock = pd.DataFrame(mock_predictions)
                st.dataframe(df_mock, use_container_width=True)
                
                st.info("💡 This is demonstration data. Connect to live prediction engine for real comparisons.")

# ---------- TAB 3: PERFORMANCE CHARTS ----------
with tab3:
    st.subheader("📊 Model Performance Analytics")
    st.caption("Visualize model performance metrics over time")
    
    registry = _load_registry()
    if registry.empty:
        st.info("No models available. Create models in the Registry tab first.")
    else:
        model_ids = registry["model_id"].tolist() if "model_id" in registry.columns else []
        selected_model = st.selectbox("Select model for detailed analysis", model_ids, key="perf_model")
        
        if selected_model:
            model_row = registry[registry["model_id"] == selected_model].iloc[0]
            
            col1, col2, col3 = st.columns(3)
            col1.metric("Model Type", model_row.get("model_type", "N/A"))
            col2.metric("Feature Set", model_row.get("feature_set", "N/A"))
            col3.metric("Status", "Active ✓" if model_row.get("is_active") else "Inactive")
            
            # Generate mock performance data
            st.subheader("Performance Over Time")
            dates = pd.date_range(end=datetime.now(), periods=30, freq='D')
            
            # Mock metrics
            mock_accuracy = 0.55 + np.random.randn(30) * 0.03
            mock_accuracy = np.clip(mock_accuracy, 0.4, 0.7)
            mock_roi = np.cumsum(np.random.randn(30) * 2)
            mock_volume = np.random.poisson(10, 30)
            
            chart_data = pd.DataFrame({
                'Date': dates,
                'Accuracy': mock_accuracy,
                'Cumulative ROI': mock_roi,
                'Daily Bets': mock_volume
            }).set_index('Date')
            
            col1, col2 = st.columns(2)
            with col1:
                st.line_chart(chart_data['Accuracy'])
            with col2:
                st.line_chart(chart_data['Cumulative ROI'])
            
            st.bar_chart(chart_data['Daily Bets'])
            
            # Summary statistics
            st.subheader("Summary Statistics")
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Avg Accuracy", f"{mock_accuracy.mean():.1%}")
            col2.metric("Final ROI", f"{mock_roi[-1]:+.1f}%")
            col3.metric("Total Bets", int(mock_volume.sum()))
            col4.metric("Avg Bets/Day", f"{mock_volume.mean():.1f}")
            
            st.info("💡 This is demonstration data. Connect to prediction database for real performance tracking.")

# ---------- TAB 4: BATCH TESTING ----------
with tab4:
    st.subheader("⚡ Batch Model Testing")
    st.caption("Test multiple model configurations simultaneously")
    
    st.markdown("""
    Use this tool to test multiple parameter combinations and find optimal model configurations.
    """)
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.write("**Test Configuration**")
        param_to_test = st.selectbox("Parameter to vary", [
            "confidence_threshold",
            "min_edge",
            "kelly_fraction",
            "model_weight"
        ])
        
        test_range_min = st.number_input("Min value", 0.0, 1.0, 0.1, 0.05)
        test_range_max = st.number_input("Max value", 0.0, 1.0, 0.5, 0.05)
        test_steps = st.number_input("Number of steps", 3, 20, 5, 1)
    
    with col2:
        st.write("**Test Scope**")
        test_period = st.selectbox("Test period", ["Last 7 days", "Last 30 days", "Last 90 days"])
        test_model_type = st.selectbox("Model type", ["spread", "total", "all"])
        
        run_test = st.button("🚀 Run Batch Test", type="primary")
    
    if run_test:
        with st.spinner("Running batch tests..."):
            # Generate test values
            test_values = np.linspace(test_range_min, test_range_max, test_steps)
            
            # Mock results
            results = []
            for val in test_values:
                mock_sharpe = np.clip(0.8 + val + np.random.randn() * 0.1, -1.0, 3.0)
                results.append({
                    param_to_test: f"{val:.3f}",
                    "Accuracy": f"{0.52 + np.random.randn() * 0.02:.1%}",
                    "ROI": f"{(val * 10 + np.random.randn() * 2):+.1f}%",
                    "Bets": int(100 - val * 50 + np.random.randint(-10, 10)),
                    "Sharpe": f"{mock_sharpe:.2f}"
                })
            
            df_results = pd.DataFrame(results)
            
            st.success(f"✅ Completed {len(results)} test configurations")
            
            st.subheader("Batch Test Results")
            st.dataframe(df_results, use_container_width=True)
            
            # Find actual optimal based on ROI
            roi_values = [float(r["ROI"].rstrip('%')) for r in results]
            optimal_idx = roi_values.index(max(roi_values))
            optimal_value = test_values[optimal_idx]
            
            st.subheader("📈 Optimal Configuration")
            st.info(f"**Best ROI:** {param_to_test} = {optimal_value:.3f} (ROI: {results[optimal_idx]['ROI']})")
            
            # Plot results
            st.subheader("Performance by Parameter Value")
            chart_data = pd.DataFrame({
                'Parameter Value': test_values,
                'ROI Impact': [float(r["ROI"].rstrip('%')) for r in results]
            }).set_index('Parameter Value')
            st.line_chart(chart_data)
            
            st.info("💡 This is demonstration data. Connect to backtesting engine for real parameter optimization.")
    
    # Quick preset tests
    st.divider()
    st.subheader("🎯 Quick Preset Tests")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("Test Conservative"):
            st.info("Testing conservative parameters: Low stakes, high confidence threshold")
    with col2:
        if st.button("Test Aggressive"):
            st.info("Testing aggressive parameters: Higher stakes, lower threshold")
    with col3:
        if st.button("Test Balanced"):
            st.info("Testing balanced parameters: Moderate stakes and thresholds")
