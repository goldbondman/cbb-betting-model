#!/usr/bin/env python3
import gzip
import os
import re
import sys
import tempfile
import time
from pathlib import Path

import requests

SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
SERVICE_ROLE_KEY = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
BUCKET = (os.getenv("SUPABASE_BUCKET") or "cbb-data").strip()

# Controls behavior:
# - UPLOAD_GROUP: "espn" | "torvik" | "ml" | "all"  (default: "all")
# - SKIP_MISSING: if set (1/true/yes), missing local files are skipped instead of failing the run.
UPLOAD_GROUP = (os.getenv("UPLOAD_GROUP") or "all").strip().lower()
SKIP_MISSING = (os.getenv("SKIP_MISSING") or "0").strip().lower() in ("1", "true", "yes")

# Optional safety guard for object size (bytes). If 0/empty, no pre-check is enforced.
MAX_OBJECT_BYTES = int(os.getenv("SUPABASE_MAX_OBJECT_BYTES", "0"))
GZIP_FALLBACK = (os.getenv("SUPABASE_GZIP_FALLBACK") or "1").strip().lower() in (
    "1",
    "true",
    "yes",
)

# Basic retry (helps with transient 429/5xx)
MAX_RETRIES = int(os.getenv("SUPABASE_UPLOAD_MAX_RETRIES", "3"))
RETRY_INITIAL_DELAY = float(os.getenv("SUPABASE_UPLOAD_RETRY_INITIAL_DELAY", "1.0"))
RETRY_BACKOFF = float(os.getenv("SUPABASE_UPLOAD_RETRY_BACKOFF", "2.0"))

# File groups
FILES_ESPN = [
    ("ESPN/CSV/espn_games.csv", "espn/latest/espn_games.csv"),
    ("ESPN/CSV/espn_team_game_logs.csv", "espn/latest/espn_team_game_logs.csv"),
    ("ESPN/CSV/espn_team_game_features.csv", "espn/latest/espn_team_game_features.csv"),
    ("ESPN/CSV/espn_matchups_model_ready.csv", "espn/latest/espn_matchups_model_ready.csv"),
    ("ESPN/CSV/espn_feature_diagnostics.csv", "espn/latest/espn_feature_diagnostics.csv"),
    ("ESPN/CSV/espn_dq_audit.csv", "espn/latest/espn_dq_audit.csv"),
    ("ESPN/CSV/ncaa_games.csv", "espn/latest/ncaa_games.csv"),
    ("ESPN/CSV/ncaa_team_game_logs.csv", "espn/latest/ncaa_team_game_logs.csv"),
    ("ESPN/CSV/ncaa_player_boxscores.csv", "espn/latest/ncaa_player_boxscores.csv"),
    ("ESPN/espn_pipeline_errors.json", "espn/latest/espn_pipeline_errors.json"),
]

FILES_TORVIK = [
    ("barttorvik.csv", "torvik/latest/barttorvik.csv"),
    ("barttorvik_team_results.csv", "torvik/latest/barttorvik_team_results.csv"),
]

FILES_ML = [
    ("ml/model_features.csv", "ml/latest/model_features.csv"),
    ("ml/dq_audit_ml.csv", "ml/latest/dq_audit_ml.csv"),
    ("ml/feature_schema_hash.txt", "ml/latest/feature_schema_hash.txt"),
    ("ml/run_log.json", "ml/latest/run_log.json"),
    ("ml/predictions_latest.csv", "ml/latest/predictions_latest.csv"),
    ("ml/models/margin_model.json", "ml/latest/margin_model.json"),
    ("ml/models/total_model.json", "ml/latest/total_model.json"),
]


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
    if not re.fullmatch(r"[a-z0-9-]+", BUCKET):
        _die(
            f"SUPABASE_BUCKET looks invalid: '{BUCKET}'. "
            "Use lowercase letters, digits, hyphen only. Example: cbb-data"
        )
    if UPLOAD_GROUP not in ("espn", "torvik", "ml", "all"):
        _die("UPLOAD_GROUP must be one of: espn, torvik, ml, all")


def _guess_content_type(local_path: str) -> str:
    p = str(local_path).lower()
    if p.endswith(".json"):
        return "application/json"
    if p.endswith(".csv"):
        return "text/csv"
    if p.endswith(".gz"):
        return "application/gzip"
    return "application/octet-stream"


def _headers(content_type: str):
    return {
        "Authorization": f"Bearer {SERVICE_ROLE_KEY}",
        "apikey": SERVICE_ROLE_KEY,
        "Content-Type": content_type,
        "x-upsert": "true",
    }


def _files_for_group():
    if UPLOAD_GROUP == "espn":
        return FILES_ESPN
    if UPLOAD_GROUP == "torvik":
        return FILES_TORVIK
    if UPLOAD_GROUP == "ml":
        return FILES_ML
    return FILES_ESPN + FILES_TORVIK + FILES_ML


def _format_bytes(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024**2:
        return f"{num_bytes / 1024:.1f} KB"
    if num_bytes < 1024**3:
        return f"{num_bytes / 1024**2:.1f} MB"
    return f"{num_bytes / 1024**3:.1f} GB"


def _gzip_to_temp(local_path: Path) -> Path:
    with local_path.open("rb") as src:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".gz") as tmp:
            with gzip.GzipFile(fileobj=tmp, mode="wb") as gz:
                for chunk in iter(lambda: src.read(1024 * 1024), b""):
                    gz.write(chunk)
            return Path(tmp.name)


def _upload_file(local_path: Path, remote_path: str) -> requests.Response:
    url = f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{remote_path}"
    content_type = _guess_content_type(local_path)
    with local_path.open("rb") as f:
        return requests.put(
            url,
            headers=_headers(content_type),
            data=f,
            timeout=(15, 180),
        )


def upload(local_path: str, remote_path: str):
    lp = Path(local_path)
    if not lp.exists() and not lp.is_absolute():
        fallback = Path(local_path.replace("ESPN/CSV/", "", 1))
        if fallback.exists():
            lp = fallback

    if not lp.exists():
        if SKIP_MISSING:
            _warn(f"Skipping missing local file: {local_path}")
            return
        _die(f"Local file missing: {local_path}")

    size_bytes = lp.stat().st_size
    if MAX_OBJECT_BYTES and size_bytes > MAX_OBJECT_BYTES:
        msg = (
            f"Local file size {_format_bytes(size_bytes)} exceeds "
            f"SUPABASE_MAX_OBJECT_BYTES={_format_bytes(MAX_OBJECT_BYTES)}: {local_path}"
        )
        if not GZIP_FALLBACK:
            _die(msg)
        _warn(msg)

    last_status = None
    last_text = None

    for attempt in range(MAX_RETRIES):
        if attempt > 0:
            delay = RETRY_INITIAL_DELAY * (RETRY_BACKOFF ** (attempt - 1))
            time.sleep(delay)

        try:
            r = _upload_file(lp, remote_path)
            last_status = r.status_code
            last_text = r.text

            if r.status_code in (200, 201):
                print(f"[OK] Uploaded {local_path} -> {remote_path}")
                return

            if r.status_code == 413 and GZIP_FALLBACK and not str(lp).endswith(".gz"):
                _warn(
                    "Upload exceeded storage size limit; attempting gzip fallback for "
                    f"{local_path}."
                )
                gz_path = _gzip_to_temp(lp)
                gz_remote = f"{remote_path}.gz"
                try:
                    gz_response = _upload_file(gz_path, gz_remote)
                    if gz_response.status_code in (200, 201):
                        print(
                            "[OK] Uploaded gzip fallback "
                            f"{gz_path} -> {gz_remote}"
                        )
                        _warn(
                            "Original file was not uploaded; update any downstream "
                            "consumers to read the .gz artifact."
                        )
                        return
                    raise RuntimeError(
                        f"Gzip upload failed {local_path} -> {gz_remote}\n"
                        f"HTTP {gz_response.status_code}: {gz_response.text}"
                    )
                finally:
                    gz_path.unlink(missing_ok=True)

            if r.status_code == 429 or (500 <= r.status_code <= 599):
                continue

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
    files = _files_for_group()
    print(f"[INFO] Uploading group='{UPLOAD_GROUP}' files={len(files)} skip_missing={SKIP_MISSING}")
    for local, remote in files:
        upload(local, remote)


if __name__ == "__main__":
    main()
