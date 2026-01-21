#!/usr/bin/env python3
import os
import re
import sys
from pathlib import Path

import requests

SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
SERVICE_ROLE_KEY = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
BUCKET = (os.getenv("SUPABASE_BUCKET") or "cbb-data").strip()

FILES = [
    ("espn_games.csv", "espn/latest/espn_games.csv"),
    ("espn_team_game_logs.csv", "espn/latest/espn_team_game_logs.csv"),
    ("espn_team_game_features.csv", "espn/latest/espn_team_game_features.csv"),
    ("espn_matchups_model_ready.csv", "espn/latest/espn_matchups_model_ready.csv"),
]

def _die(msg: str, code: int = 1):
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(code)

def _validate_env():
    if not SUPABASE_URL:
        _die("SUPABASE_URL is missing/empty.")
    if not SERVICE_ROLE_KEY:
        _die("SUPABASE_SERVICE_ROLE_KEY is missing/empty.")
    # Supabase bucket naming: keep it simple: lowercase letters, digits, hyphen
    if not re.fullmatch(r"[a-z0-9-]+", BUCKET):
        _die(
            f"SUPABASE_BUCKET looks invalid: '{BUCKET}'. "
            "Use lowercase letters, digits, hyphen only. Example: cbb-data"
        )

def _headers(content_type: str):
    return {
        "Authorization": f"Bearer {SERVICE_ROLE_KEY}",
        "apikey": SERVICE_ROLE_KEY,
        "Content-Type": content_type,
        # overwrite if exists
        "x-upsert": "true",
    }
def upload(local_path: str, remote_path: str):
    lp = Path(local_path)
    if not lp.exists():
        _die(f"Local file missing: {local_path}")

    # Storage upload endpoint
    # PUT /storage/v1/object/<bucket>/<path>
    url = f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{remote_path}"

    # Guess a reasonable content type
    content_type = "text/csv"

    with lp.open("rb") as f:
        r = requests.put(url, headers=_headers(content_type), data=f, timeout=120)

    if r.status_code not in (200, 201):
        # Print extra debugging context
        raise RuntimeError(
            f"Upload failed {local_path} -> {remote_path}\n"
            f"SUPABASE_URL={SUPABASE_URL}\n"
            f"BUCKET='{BUCKET}'\n"
            f"HTTP {r.status_code}: {r.text}"
        )

    print(f"[OK] Uploaded {local_path} -> {remote_path}")

def main():
    _validate_env()
    for local, remote in FILES:
        upload(local, remote)

if __name__ == "__main__":
    main()
