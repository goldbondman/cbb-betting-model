#!/usr/bin/env python3
import os
import re
import sys
import time
from pathlib import Path

import requests

SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
SERVICE_ROLE_KEY = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
BUCKET = (os.getenv("SUPABASE_BUCKET") or "cbb-data").strip()

# If set (1/true/yes), missing local files are skipped instead of failing the run.
SKIP_MISSING = (os.getenv("SKIP_MISSING") or "0").strip().lower() in ("1", "true", "yes")

FILES = [
    # ESPN pipeline outputs
    ("espn_games.csv", "espn/latest/espn_games.csv"),
    ("espn_team_game_logs.csv", "espn/latest/espn_team_game_logs.csv"),
    ("espn_team_game_features.csv", "espn/latest/espn_team_game_features.csv"),
    ("espn_matchups_model_ready.csv", "espn/latest/espn_matchups_model_ready.csv"),
    ("espn_feature_diagnostics.csv", "espn/latest/espn_feature_diagnostics.csv"),
    ("espn_dq_audit.csv", "espn/latest/espn_dq_audit.csv"),
    ("espn_pipeline_errors.json", "espn/latest/espn_pipeline_errors.json"),

    # Torvik refresh outputs (from scripts/refresh_sources.py)
    ("barttorvik.csv", "torvik/latest/barttorvik.csv"),
    ("barttorvik_team_results.csv", "torvik/latest/barttorvik_team_results.csv"),
]

# Basic retry (helps with transient 429/5xx)
MAX_RETRIES = int(os.getenv("SUPABASE_UPLOAD_MAX_RETRIES", "3"))
RETRY_INITIAL_DELAY = float(os.getenv("SUPABASE_UPLOAD_RETRY_INITIAL_DELAY", "1.0"))
RETRY_BACKOFF = float(os.getenv("SUPABASE_UPLOAD_RETRY_BACKOFF", "2.0"))


def _die(msg: str, code: int = 1):
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(code)


def _warn(msg: str):
    print(f"[WARN] {msg}")


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


def _guess_content_type(local_path: str) -> str:
    p = str(local_path).lower()
    if p.endswith(".json"):
        return "application/json"
    if p.endswith(".csv"):
        return "text/csv"
    return "application/octet-stream"


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
        if SKIP_MISSING:
            _warn(f"Skipping missing local file: {local_path}")
            return
        _die(f"Local file missing: {local_path}")

    # PUT /storage/v1/object/<bucket>/<path>
    url = f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{remote_path}"
    content_type = _guess_content_type(local_path)

    last_status = None
    last_text = None

    for attempt in range(MAX_RETRIES):
        if attempt > 0:
            delay = RETRY_INITIAL_DELAY * (RETRY_BACKOFF ** (attempt - 1))
            time.sleep(delay)

        try:
            with lp.open("rb") as f:
                r = requests.put(
                    url,
                    headers=_headers(content_type),
                    data=f,  # streamed upload
                    timeout=(15, 180),  # (connect, read)
                )

            last_status = r.status_code
            last_text = r.text

            if r.status_code in (200, 201):
                print(f"[OK] Uploaded {local_path} -> {remote_path}")
                return

            # Retry on rate limit / transient server errors
            if r.status_code == 429 or (500 <= r.status_code <= 599):
                continue

            # Non-retryable
            raise RuntimeError(
                f"Upload failed {local_path} -> {remote_path}\n"
                f"HTTP {r.status_code}: {r.text}"
            )

        except requests.exceptions.Timeout as e:
            last_text = f"Timeout: {e}"
            continue
        except requests.exceptions.RequestException as e:
            last_text = f"RequestException: {e}"
            continue

    raise RuntimeError(
        f"Upload failed after {MAX_RETRIES} attempts: {local_path} -> {remote_path}\n"
        f"SUPABASE_URL={SUPABASE_URL}\n"
        f"BUCKET='{BUCKET}'\n"
        f"Last HTTP={last_status} Last response={last_text}"
    )


def main():
    _validate_env()
    for local, remote in FILES:
        upload(local, remote)


if __name__ == "__main__":
    main()
