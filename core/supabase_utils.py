"""Shared Supabase client helpers for Streamlit pages and core loaders.

Provides both *public/anon-key* clients (for Streamlit UI) and
*service-role* clients (for backend pipelines), plus a reusable
``upsert_rows`` helper so that every script doesn't need to carry its
own upsert implementation.
"""

from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional, Tuple

from supabase import Client, create_client

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Credential helpers
# ---------------------------------------------------------------------------

def _read_streamlit_secret(name: str) -> Optional[str]:
    """Best-effort read from Streamlit secrets without hard dependency."""
    try:
        import streamlit as st

        value = st.secrets.get(name, None)
    except Exception:
        value = None

    if value is None:
        return None
    text = str(value).strip()
    return text or None


def read_public_supabase_creds() -> Tuple[Optional[str], Optional[str]]:
    """
    Read public Supabase creds from Streamlit secrets first, then env.

    Intentionally uses anon/public keys only (never service-role) for UI callers.
    Also supports SUPABASE_KEY as a compatibility alias for anon key.
    """
    url = _read_streamlit_secret("SUPABASE_URL") or (os.getenv("SUPABASE_URL") or "").strip() or None

    anon_key = (
        _read_streamlit_secret("SUPABASE_ANON_KEY")
        or _read_streamlit_secret("SUPABASE_KEY")
        or (os.getenv("SUPABASE_ANON_KEY") or "").strip()
        or (os.getenv("SUPABASE_KEY") or "").strip()
        or None
    )

    return url, anon_key


# ---------------------------------------------------------------------------
# Client factories
# ---------------------------------------------------------------------------

def get_public_supabase_client() -> Optional[Client]:
    """Return client when SUPABASE_URL + anon key are available, else None."""
    url, key = read_public_supabase_creds()
    if not url or not key:
        return None
    try:
        return create_client(url, key)
    except Exception:
        return None


def get_service_role_client() -> Client:
    """Return a Supabase client using the service-role key.

    Raises ``RuntimeError`` when the required environment variables
    (``SUPABASE_URL``, ``SUPABASE_SERVICE_ROLE_KEY``) are missing so
    callers get a clear, early failure.
    """
    url = (os.getenv("SUPABASE_URL") or "").strip()
    key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        raise RuntimeError("SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY missing.")
    return create_client(url, key)


# ---------------------------------------------------------------------------
# Reusable upsert helper
# ---------------------------------------------------------------------------

def upsert_rows(
    client: Client,
    schema: str,
    table: str,
    rows: List[Dict[str, object]],
    on_conflict: Optional[str] = None,
) -> int:
    """Upsert *rows* into *schema*.*table* via the Supabase REST API.

    Returns the number of rows acknowledged by the server.
    """
    if not rows:
        return 0

    payload = rows if len(rows) > 1 else rows[0]
    req = client.schema(schema).table(table)

    if on_conflict:
        resp = req.upsert(payload, on_conflict=on_conflict).execute()
    else:
        resp = req.upsert(payload).execute()

    data = resp.data or []
    return len(data) if isinstance(data, list) else 1
