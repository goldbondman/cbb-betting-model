import json
import os
from datetime import datetime, timezone

import pandas as pd
import streamlit as st
from supabase import create_client

st.set_page_config(page_title="Model Lab", page_icon="🧪", layout="wide")

REGISTRY_TABLE = os.getenv("MODEL_REGISTRY_TABLE", "model_registry")
REGISTRY_SCHEMA = os.getenv("MODEL_REGISTRY_SCHEMA", "public")


def _get_supabase_client():
    url = (os.getenv("SUPABASE_URL") or "").strip()
    key = (os.getenv("SUPABASE_ANON_KEY") or "").strip()
    if not url or not key:
        return None
    return create_client(url, key)


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

client_ok = _get_supabase_client() is not None
if not client_ok:
    st.warning("Supabase credentials missing (SUPABASE_URL, SUPABASE_ANON_KEY). UI will be read-only.")

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
