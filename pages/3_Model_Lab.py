import json
import os
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from supabase import create_client


st.set_page_config(page_title="Model Lab", page_icon="🧪", layout="wide")


def _get_supabase_client():
    url = (os.getenv("SUPABASE_URL") or "").strip()
    key = (os.getenv("SUPABASE_ANON_KEY") or "").strip()
    if not url or not key:
        return None
    return create_client(url, key)


def _load_registry() -> pd.DataFrame:
    client = _get_supabase_client()
    if client is None:
        return pd.DataFrame()
    try:
        resp = client.table("model_registry").select("*").order("created_at", desc=True).execute()
        return pd.DataFrame(resp.data or [])
    except Exception:
        return pd.DataFrame()


def _upsert_registry(payload: dict) -> str | None:
    client = _get_supabase_client()
    if client is None:
        return "Supabase credentials missing."
    try:
        client.table("model_registry").upsert(payload, on_conflict="model_id").execute()
        return None
    except Exception as exc:
        return str(exc)


st.title("🧪 Model Lab")
st.caption("Register multiple models and track which are active.")

registry = _load_registry()
if registry.empty:
    st.info("No models registered yet (or Supabase credentials missing).")

with st.form("register_model", clear_on_submit=True):
    st.subheader("Register model")
    model_id = st.text_input("Model ID (unique)", placeholder="base-efficiency-v1")
    model_name = st.text_input("Model name", placeholder="Base Efficiency")
    model_type = st.selectbox("Model type", ["spread", "total", "blowout", "multihead", "ensemble"])
    feature_set = st.text_input("Feature set", placeholder="l7+v2")
    model_version = st.text_input("Model version", placeholder="2025-01-15")
    params = st.text_area("Params (JSON)", value="{}")
    is_active = st.checkbox("Set active", value=False)
    submitted = st.form_submit_button("Save")

if submitted:
    if not model_id or not model_name:
        st.error("Model ID and Model name are required.")
    else:
        try:
            params_json = {} if not params.strip() else json.loads(params)
        except Exception:
            params_json = None
        if params_json is None:
            st.error("Params must be valid JSON.")
        else:
            payload = {
                "model_id": model_id.strip(),
                "model_name": model_name.strip(),
                "model_type": model_type,
                "feature_set": feature_set.strip() or None,
                "model_version": model_version.strip() or None,
                "params": params_json,
                "is_active": bool(is_active),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            err = _upsert_registry(payload)
            if err:
                st.error(f"Failed to save: {err}")
            else:
                st.success("Model saved.")
                registry = _load_registry()

st.subheader("Registered models")
if registry.empty:
    st.info("No models to display.")
else:
    display_cols = [
        "model_id",
        "model_name",
        "model_type",
        "feature_set",
        "model_version",
        "is_active",
        "created_at",
    ]
    st.dataframe(registry[[c for c in display_cols if c in registry.columns]], use_container_width=True)
