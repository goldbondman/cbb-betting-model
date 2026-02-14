#!/usr/bin/env python3
"""
Test ESPN API and diagnose box score parsing issues.

Usage:
    python test_espn_api.py <event_id>
    
Example:
    python test_espn_api.py 401820577

This will:
1. Fetch the game summary from ESPN API
2. Parse the box score data
3. Show what data was found
4. Identify any missing fields
"""

import sys
import json
from typing import Dict, Any

# Import ESPN modules
from espn_http_client import fetch_summary
from espn_parsers import parse_summary_json, summary_to_team_rows
from data_utils import _to_int


def analyze_team_stats(team_entry: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze what stats are present in a team entry"""
    t = team_entry.get("team", {}) or {}
    name = t.get("displayName", "Unknown")
    tid = t.get("id", "Unknown")
    
    # Check for different stat field names
    teamStats = team_entry.get("teamStats")
    statistics = team_entry.get("statistics")
    
    stats_found = {
        "team_name": name,
        "team_id": tid,
        "has_teamStats": teamStats is not None,
        "has_statistics": statistics is not None,
        "teamStats_type": type(teamStats).__name__ if teamStats is not None else None,
        "statistics_type": type(statistics).__name__ if statistics is not None else None,
        "available_keys": list(team_entry.keys())
    }
    
    # If stats list exists, show what's in it
    stats_list = teamStats or statistics or []
    if isinstance(stats_list, list):
        stats_found["num_stats"] = len(stats_list)
        if stats_list:
            stats_found["sample_stat_names"] = [s.get("name") for s in stats_list[:10] if isinstance(s, dict)]
    
    return stats_found


def test_game(event_id: str):
    """Test fetching and parsing a specific game"""
    print("="*70)
    print(f"Testing ESPN API for Event ID: {event_id}")
    print("="*70)
    
    # Step 1: Fetch summary
    print("\n1. Fetching summary from ESPN API...")
    try:
        raw = fetch_summary(event_id)
        print("   ✓ Successfully fetched summary")
    except Exception as e:
        print(f"   ✗ Error fetching summary: {e}")
        return
    
    # Step 2: Save raw JSON for inspection
    json_file = f"test_game_{event_id}.json"
    with open(json_file, "w") as f:
        json.dump(raw, f, indent=2)
    print(f"   ✓ Saved raw JSON to {json_file}")
    
    # Step 3: Check boxscore structure
    print("\n2. Analyzing boxscore structure...")
    box = raw.get("boxscore", {})
    teams = box.get("teams", [])
    
    print(f"   - Boxscore found: {box is not None}")
    print(f"   - Number of teams: {len(teams)}")
    
    if not teams:
        print("   ✗ No teams found in boxscore!")
        print("   Available keys in boxscore:", list(box.keys()) if box else "None")
        return
    
    # Step 4: Analyze each team's stats
    print("\n3. Analyzing team stats...")
    for i, team_entry in enumerate(teams):
        print(f"\n   Team {i+1}:")
        analysis = analyze_team_stats(team_entry)
        for key, value in analysis.items():
            print(f"      {key}: {value}")
    
    # Step 5: Parse and show results
    print("\n4. Parsing with espn_parsers.py...")
    try:
        parsed = parse_summary_json(raw, event_id)
        home, away = summary_to_team_rows(parsed)
        
        print("   ✓ Successfully parsed")
        print(f"\n   Home Team: {home.get('team')}")
        print(f"      Points: {home.get('points_for')}")
        print(f"      FGM/FGA: {home.get('fgm')}/{home.get('fga')}")
        print(f"      TPM/TPA: {home.get('tpm')}/{home.get('tpa')}")
        print(f"      FTM/FTA: {home.get('ftm')}/{home.get('fta')}")
        print(f"      Rebounds: {home.get('reb')} (ORB={home.get('orb')}, DRB={home.get('drb')})")
        print(f"      TOV: {home.get('tov')}, POSS: {home.get('poss')}")
        
        print(f"\n   Away Team: {away.get('team')}")
        print(f"      Points: {away.get('points_for')}")
        print(f"      FGM/FGA: {away.get('fgm')}/{away.get('fga')}")
        print(f"      TPM/TPA: {away.get('tpm')}/{away.get('tpa')}")
        print(f"      FTM/FTA: {away.get('ftm')}/{away.get('fta')}")
        print(f"      Rebounds: {away.get('reb')} (ORB={away.get('orb')}, DRB={away.get('drb')})")
        print(f"      TOV: {away.get('tov')}, POSS: {away.get('poss')}")
        
        # Check for zeros
        home_fga = _to_int(home.get('fga'), 0)
        away_fga = _to_int(away.get('fga'), 0)
        
        if home_fga == 0 or away_fga == 0:
            print("\n   ⚠️  WARNING: Box scores are zero or missing!")
            print("      This indicates ESPN API is not returning team statistics.")
            print("      Check the saved JSON file for the raw API response structure.")
        else:
            print("\n   ✓ Box scores look good!")
            
    except Exception as e:
        print(f"   ✗ Error parsing: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "="*70)
    print("Test complete. Review the output and JSON file for details.")
    print("="*70)


def main():
    if len(sys.argv) < 2:
        print("Usage: python test_espn_api.py <event_id>")
        print("Example: python test_espn_api.py 401820577")
        sys.exit(1)
    
    event_id = sys.argv[1]
    test_game(event_id)


if __name__ == "__main__":
    main()
