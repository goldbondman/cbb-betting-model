"""
JSON Storage Module
Handles saving raw ESPN API responses to disk for archival and debugging.
"""

import os
import json
from typing import Any, Dict, Optional
from datetime import datetime

from espn_config import JSON_OUTPUT_DIR, SAVE_RAW_JSON, TZ_PST
from data_utils import _utc_now_iso


def save_scoreboard_json(date_yyyymmdd: str, data: Dict[str, Any]) -> Optional[str]:
    """
    Save scoreboard JSON response to disk.
    
    Args:
        date_yyyymmdd: Date in YYYYMMDD format (e.g., "20240115")
        data: Raw ESPN scoreboard JSON response
        
    Returns:
        Path to saved file, or None if saving is disabled or fails
    """
    if not SAVE_RAW_JSON:
        return None
    
    try:
        # Create directory structure: ESPN/raw_json/scoreboard/YYYY/MM/
        year = date_yyyymmdd[:4]
        month = date_yyyymmdd[4:6]
        dir_path = os.path.join(JSON_OUTPUT_DIR, "scoreboard", year, month)
        os.makedirs(dir_path, exist_ok=True)
        
        # Filename: scoreboard_YYYYMMDD_timestamp.json
        timestamp = datetime.now(TZ_PST).strftime("%Y%m%d_%H%M%S")
        filename = f"scoreboard_{date_yyyymmdd}_{timestamp}.json"
        filepath = os.path.join(dir_path, filename)
        
        # Add metadata
        payload = {
            "metadata": {
                "date": date_yyyymmdd,
                "fetched_at_utc": _utc_now_iso(),
                "api_endpoint": "scoreboard",
            },
            "data": data,
        }
        
        # Write with pretty formatting
        with open(filepath, "w") as f:
            json.dump(payload, f, indent=2, default=str)
        
        return filepath
    except Exception as e:
        # Don't crash pipeline on storage failures
        print(f"[WARN] Failed to save scoreboard JSON for {date_yyyymmdd}: {e}")
        return None


def save_summary_json(event_id: str, data: Dict[str, Any]) -> Optional[str]:
    """
    Save summary/boxscore JSON response to disk.
    
    Args:
        event_id: ESPN event ID
        data: Raw ESPN summary JSON response
        
    Returns:
        Path to saved file, or None if saving is disabled or fails
    """
    if not SAVE_RAW_JSON:
        return None
    
    try:
        # Extract game date from response for better organization
        game_date = None
        try:
            header = data.get("header", {})
            competitions = header.get("competitions", [])
            if competitions:
                game_date_str = competitions[0].get("date", "")
                if game_date_str:
                    dt = datetime.fromisoformat(game_date_str.replace("Z", "+00:00"))
                    game_date = dt.strftime("%Y%m%d")
        except Exception:
            pass
        
        # Create directory structure: ESPN/raw_json/summary/YYYY/MM/ or ESPN/raw_json/summary/unknown/
        if game_date:
            year = game_date[:4]
            month = game_date[4:6]
            dir_path = os.path.join(JSON_OUTPUT_DIR, "summary", year, month)
        else:
            dir_path = os.path.join(JSON_OUTPUT_DIR, "summary", "unknown")
        
        os.makedirs(dir_path, exist_ok=True)
        
        # Filename: summary_eventid_timestamp.json
        timestamp = datetime.now(TZ_PST).strftime("%Y%m%d_%H%M%S")
        filename = f"summary_{event_id}_{timestamp}.json"
        filepath = os.path.join(dir_path, filename)
        
        # Add metadata
        payload = {
            "metadata": {
                "event_id": event_id,
                "game_date": game_date,
                "fetched_at_utc": _utc_now_iso(),
                "api_endpoint": "summary",
            },
            "data": data,
        }
        
        # Write with pretty formatting
        with open(filepath, "w") as f:
            json.dump(payload, f, indent=2, default=str)
        
        return filepath
    except Exception as e:
        # Don't crash pipeline on storage failures
        print(f"[WARN] Failed to save summary JSON for event {event_id}: {e}")
        return None


def get_json_storage_stats() -> Dict[str, Any]:
    """
    Get statistics about stored JSON files.
    
    Returns:
        Dictionary with file counts and total size
    """
    if not os.path.exists(JSON_OUTPUT_DIR):
        return {
            "enabled": SAVE_RAW_JSON,
            "directory": JSON_OUTPUT_DIR,
            "scoreboard_files": 0,
            "summary_files": 0,
            "total_files": 0,
            "total_size_mb": 0.0,
        }
    
    scoreboard_count = 0
    summary_count = 0
    total_size = 0
    
    try:
        for root, dirs, files in os.walk(JSON_OUTPUT_DIR):
            for file in files:
                if file.endswith(".json"):
                    filepath = os.path.join(root, file)
                    total_size += os.path.getsize(filepath)
                    
                    if "scoreboard" in root:
                        scoreboard_count += 1
                    elif "summary" in root:
                        summary_count += 1
    except Exception:
        pass
    
    return {
        "enabled": SAVE_RAW_JSON,
        "directory": JSON_OUTPUT_DIR,
        "scoreboard_files": scoreboard_count,
        "summary_files": summary_count,
        "total_files": scoreboard_count + summary_count,
        "total_size_mb": round(total_size / (1024 * 1024), 2),
    }
