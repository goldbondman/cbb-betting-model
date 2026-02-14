"""Supabase model registry CRUD for formula-based model definitions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from supabase import Client

from core.supabase_utils import get_public_supabase_client

_LOCAL_REGISTRY_PATH = Path("data/model_registry_local.json")


def _client() -> Client | None:
    return get_public_supabase_client()


def _read_local_models() -> list[dict[str, Any]]:
    if not _LOCAL_REGISTRY_PATH.exists():
        return []
    try:
        payload = json.loads(_LOCAL_REGISTRY_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            return []
        return [row for row in payload if isinstance(row, dict)]
    except Exception:
        return []


def _write_local_models(models: list[dict[str, Any]]) -> bool:
    try:
        _LOCAL_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        _LOCAL_REGISTRY_PATH.write_text(json.dumps(models, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def _get_local_active_model(model_type: str) -> dict[str, Any]:
    rows = [m for m in _read_local_models() if m.get("model_type") == model_type and bool(m.get("is_active"))]
    if not rows:
        return {}
    rows.sort(key=lambda r: str(r.get("updated_at", "")), reverse=True)
    return rows[0]


def get_active_model(model_type: str = "spread") -> dict[str, Any]:
    """Return active model row for given type, or empty dict."""
    client = _client()
    if client is None:
        return _get_local_active_model(model_type)
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
        return rows[0] if rows else _get_local_active_model(model_type)
    except Exception:
        return _get_local_active_model(model_type)


def list_all_models(model_type: str | None = None) -> list[dict[str, Any]]:
    """List all model rows, optionally filtered by model_type."""
    client = _client()
    if client is None:
        rows = _read_local_models()
        return [m for m in rows if m.get("model_type") == model_type] if model_type else rows
    try:
        query = client.table("model_registry").select("*").order("created_at", desc=True)
        if model_type:
            query = query.eq("model_type", model_type)
        resp = query.execute()
        return list(resp.data or [])
    except Exception:
        rows = _read_local_models()
        return [m for m in rows if m.get("model_type") == model_type] if model_type else rows


def create_model(model_id: str, model_name: str, model_type: str, params: dict[str, Any]) -> bool:
    """Insert a new formula model."""
    client = _client()
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
    if client is None:
        models = _read_local_models()
        if any(m.get("model_id") == model_id for m in models):
            return False
        models.append(payload)
        return _write_local_models(models)
    try:
        client.table("model_registry").insert(payload).execute()
        return True
    except Exception:
        models = _read_local_models()
        if any(m.get("model_id") == model_id for m in models):
            return False
        models.append(payload)
        return _write_local_models(models)


def update_model(model_id: str, params: dict[str, Any]) -> bool:
    """Update params JSON for a model."""
    client = _client()
    now = datetime.now(timezone.utc).isoformat()
    if client is None:
        models = _read_local_models()
        for model in models:
            if model.get("model_id") == model_id:
                model["params"] = params
                model["updated_at"] = now
                return _write_local_models(models)
        return False
    try:
        client.table("model_registry").update(
            {"params": params, "updated_at": now}
        ).eq("model_id", model_id).execute()
        return True
    except Exception:
        models = _read_local_models()
        for model in models:
            if model.get("model_id") == model_id:
                model["params"] = params
                model["updated_at"] = now
                return _write_local_models(models)
        return False


def activate_model(model_id: str) -> bool:
    """Activate one model and deactivate others in same model_type."""
    client = _client()
    now = datetime.now(timezone.utc).isoformat()
    if client is None:
        models = _read_local_models()
        selected = next((m for m in models if m.get("model_id") == model_id), None)
        if not selected:
            return False
        model_type = selected.get("model_type")
        for model in models:
            if model.get("model_type") == model_type:
                model["is_active"] = model.get("model_id") == model_id
                model["updated_at"] = now
        return _write_local_models(models)
    try:
        current = client.table("model_registry").select("model_type").eq("model_id", model_id).limit(1).execute()
        rows = current.data or []
        if not rows:
            return False
        model_type = rows[0]["model_type"]
        client.table("model_registry").update({"is_active": False, "updated_at": now}).eq("model_type", model_type).execute()
        client.table("model_registry").update({"is_active": True, "updated_at": now}).eq("model_id", model_id).execute()
        return True
    except Exception:
        models = _read_local_models()
        selected = next((m for m in models if m.get("model_id") == model_id), None)
        if not selected:
            return False
        model_type = selected.get("model_type")
        for model in models:
            if model.get("model_type") == model_type:
                model["is_active"] = model.get("model_id") == model_id
                model["updated_at"] = now
        return _write_local_models(models)


def delete_model(model_id: str) -> bool:
    """Delete a model by model_id."""
    client = _client()
    if client is None:
        models = _read_local_models()
        filtered = [m for m in models if m.get("model_id") != model_id]
        if len(filtered) == len(models):
            return False
        return _write_local_models(filtered)
    try:
        client.table("model_registry").delete().eq("model_id", model_id).execute()
        return True
    except Exception:
        models = _read_local_models()
        filtered = [m for m in models if m.get("model_id") != model_id]
        if len(filtered) == len(models):
            return False
        return _write_local_models(filtered)
