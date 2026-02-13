"""Shared Supabase client helpers for Streamlit pages and core loaders."""

from __future__ import annotations

import os
from typing import Optional, Tuple

from supabase import Client, create_client


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


def get_public_supabase_client() -> Optional[Client]:
    """Return client when SUPABASE_URL + anon key are available, else None."""
    url, key = read_public_supabase_creds()
    if not url or not key:
        return None
    try:
        return create_client(url, key)
    except Exception:
        return None
