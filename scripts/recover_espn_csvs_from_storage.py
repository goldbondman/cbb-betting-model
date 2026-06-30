#!/usr/bin/env python3
"""
Recover missing ESPN CSV files from Supabase Storage.

For each expected CSV that is missing or empty on disk, attempt to download the
latest version from the Supabase Storage bucket.

Usage:
    python scripts/recover_espn_csvs_from_storage.py [--csv-dir ESPN/CSV]

Environment variables (required):
    SUPABASE_URL              - Supabase project URL
    SUPABASE_SERVICE_ROLE_KEY - Service role key for authentication
    SUPABASE_BUCKET           - Storage bucket name (default: cbb-data)
"""

import argparse
import os
import sys

import requests

SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
SERVICE_ROLE_KEY = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
BUCKET = (os.getenv("SUPABASE_BUCKET") or "cbb-data").strip()

# Map of local filename -> remote storage path inside the bucket
RECOVERY_MAP = {
    "espn_games.csv": "espn/latest/espn_games.csv",
    "espn_teams.csv": "espn/latest/espn_teams.csv",
    "espn_team_game_logs.csv": "espn/latest/espn_team_game_logs.csv",
    "espn_team_game_features.csv": "espn/latest/espn_team_game_features.csv",
    "espn_matchups_model_ready.csv": "espn/latest/espn_matchups_model_ready.csv",
    "espn_player_boxscores.csv": "espn/latest/espn_player_boxscores.csv",
    "espn_injuries.csv": "espn/latest/espn_injuries.csv",
    "espn_dq_audit.csv": "espn/latest/espn_dq_audit.csv",
    "espn_feature_diagnostics.csv": "espn/latest/espn_feature_diagnostics.csv",
}


def _needs_recovery(local_path: str) -> bool:
    """Return True if the file is missing or empty."""
    if not os.path.exists(local_path):
        return True
    return os.path.getsize(local_path) == 0


def download_from_storage(remote_path: str, local_path: str) -> bool:
    """Download a file from Supabase Storage. Returns True on success."""
    url = f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{remote_path}"
    headers = {
        "Authorization": f"Bearer {SERVICE_ROLE_KEY}",
        "apikey": SERVICE_ROLE_KEY,
    }
    try:
        resp = requests.get(url, headers=headers, timeout=(15, 120))
        if resp.status_code == 200 and len(resp.content) > 0:
            os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
            with open(local_path, "wb") as fh:
                fh.write(resp.content)
            return True
        print(f"  [WARN] HTTP {resp.status_code} for {remote_path}")
    except Exception as exc:
        print(f"  [WARN] download error for {remote_path}: {exc}")
    return False


def run_recovery(csv_dir: str) -> int:
    """Attempt to recover missing CSVs. Returns count of recovered files."""
    if not SUPABASE_URL or not SERVICE_ROLE_KEY:
        print("[WARN] SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set; skipping storage recovery.")
        return 0

    recovered = 0
    for filename, remote_path in RECOVERY_MAP.items():
        local_path = os.path.join(csv_dir, filename)
        if not _needs_recovery(local_path):
            continue

        print(f"[RECOVER] {filename} missing/empty — downloading from storage...")
        if download_from_storage(remote_path, local_path):
            size = os.path.getsize(local_path)
            print(f"  [OK] Recovered {filename} ({size} bytes)")
            recovered += 1
        else:
            print(f"  [FAIL] Could not recover {filename}")

    return recovered


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recover ESPN CSVs from Supabase Storage")
    parser.add_argument(
        "--csv-dir",
        default=os.path.join("ESPN", "CSV"),
        help="Path to the CSV directory (default: ESPN/CSV)",
    )
    args = parser.parse_args(argv)

    recovered = run_recovery(args.csv_dir)
    if recovered:
        print(f"\n[INFO] Recovered {recovered} file(s) from Supabase Storage.")
    else:
        print("\n[INFO] No files recovered (all present or storage unavailable).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
