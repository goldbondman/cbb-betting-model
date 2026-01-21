#!/usr/bin/env python3
import os
import sys
import requests

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SERVICE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
BUCKET = os.getenv("SUPABASE_BUCKET", "cbb-data")

FILES = [
    ("espn_games.csv", "espn/latest/espn_games.csv"),
    ("espn_team_game_logs.csv", "espn/latest/espn_team_game_logs.csv"),
    ("espn_team_game_features.csv", "espn/latest/espn_team_game_features.csv"),
    ("espn_matchups_model_ready.csv", "espn/latest/espn_matchups_model_ready.csv"),
]

def upload(local_path: str, remote_path: str) -> None:
    if not os.path.exists(local_path) or os.path.getsize(local_path) == 0:
        print(f"[SKIP] Missing/empty: {local_path}")
        return

    url = f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{remote_path}"
    headers = {
        "Authorization": f"Bearer {SERVICE_KEY}",
        "apikey": SERVICE_KEY,
        "Content-Type": "text/csv",
        # Overwrite if exists:
        "x-upsert": "true",
    }

    with open(local_path, "rb") as f:
        r = requests.post(url, headers=headers, data=f)

    if r.status_code not in (200, 201):
        raise RuntimeError(f"Upload failed {local_path} -> {remote_path}: {r.status_code} {r.text}")

    print(f"[OK] Uploaded: {local_path} -> {remote_path}")

def main():
    if not SUPABASE_URL or not SERVICE_KEY:
        print("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY env vars.")
        sys.exit(1)

    for lp, rp in FILES:
        upload(lp, rp)

    print("\nPublic URLs (copy/paste into Streamlit):")
    for _, rp in FILES:
        print(f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{rp}")

if __name__ == "__main__":
    main()
