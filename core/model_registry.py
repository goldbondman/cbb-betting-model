"""Supabase model registry CRUD for formula-based model definitions."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from supabase import Client, create_client


def _client() -> Client | None:
    url = (os.getenv("SUPABASE_URL") or "").strip()
    key = (os.getenv("SUPABASE_ANON_KEY") or "").strip()
    if not url or not key:
        return None
    try:
        return create_client(url, key)
    except Exception:
        return None


def get_active_model(model_type: str = "spread") -> dict[str, Any]:
    """Return active model row for given type, or empty dict."""
    client = _client()
    if client is None:
        return {}
    try:
        resp = (
            client.table("model_registry")
            .select("*")
            .eq("model_type", model_type)
            .eq("is_active", True)
            .order("updated_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        return rows[0] if rows else {}
    except Exception:
        return {}


def list_all_models(model_type: str | None = None) -> list[dict[str, Any]]:
    """List all model rows, optionally filtered by model_type."""
    client = _client()
    if client is None:
        return []
    try:
        query = client.table("model_registry").select("*").order("created_at", desc=True)
        if model_type:
            query = query.eq("model_type", model_type)
        resp = query.execute()
        return list(resp.data or [])
    except Exception:
        return []


def create_model(model_id: str, model_name: str, model_type: str, params: dict[str, Any]) -> bool:
    """Insert a new formula model."""
    client = _client()
    if client is None:
        return False
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "model_id": model_id,
        "model_name": model_name,
        "model_type": model_type,
        "params": params,
        "is_active": False,
        "created_at": now,
        "updated_at": now,
    }
    try:
        client.table("model_registry").insert(payload).execute()
        return True
    except Exception:
        return False


def update_model(model_id: str, params: dict[str, Any]) -> bool:
    """Update params JSON for a model."""
    client = _client()
    if client is None:
        return False
    try:
        client.table("model_registry").update(
            {"params": params, "updated_at": datetime.now(timezone.utc).isoformat()}
        ).eq("model_id", model_id).execute()
        return True
    except Exception:
        return False


def activate_model(model_id: str) -> bool:
    """Activate one model and deactivate others in same model_type."""
    client = _client()
    if client is None:
        return False
    try:
        current = client.table("model_registry").select("model_type").eq("model_id", model_id).limit(1).execute()
        rows = current.data or []
        if not rows:
            return False
        model_type = rows[0]["model_type"]
        now = datetime.now(timezone.utc).isoformat()
        client.table("model_registry").update({"is_active": False, "updated_at": now}).eq("model_type", model_type).execute()
        client.table("model_registry").update({"is_active": True, "updated_at": now}).eq("model_id", model_id).execute()
        return True
    except Exception:
        return False


def delete_model(model_id: str) -> bool:
    """Delete a model by model_id."""
    client = _client()
    if client is None:
        return False
    try:
        client.table("model_registry").delete().eq("model_id", model_id).execute()
        return True
    except Exception:
        return False
