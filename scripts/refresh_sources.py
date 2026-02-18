import io
import os
import re
import time
import sys
import requests
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Allow importing ESPN modules when run from repo root
_ESPN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ESPN")
if _ESPN_DIR not in sys.path:
    sys.path.insert(0, _ESPN_DIR)

# Allow importing core modules
_CORE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core")
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

UA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 30
MAX_RETRIES = int(os.environ.get("REFRESH_MAX_RETRIES", "3"))
RETRY_INITIAL_DELAY = float(os.environ.get("REFRESH_RETRY_INITIAL_DELAY", "1.0"))
RETRY_BACKOFF = float(os.environ.get("REFRESH_RETRY_BACKOFF", "2.0"))

TEAM_ALIASES = {
    "uconn": "connecticut",
    "nc state": "north carolina st",
    "iowa st": "iowa state",
    "michigan st": "michigan state",
    "ole miss": "mississippi",
    # add more as needed
}

def cbb_season_year(now=None):
    now = now or datetime.now(ZoneInfo("America/Los_Angeles"))
    return now.year + 1 if now.month >= 7 else now.year

def _clean_columns(df):
    df = df.copy()
    df.columns = [
        str(c).strip().lower().replace(" ", "_").replace(".", "").replace("%", "pct")
        for c in df.columns
    ]
    return df

def fetch_bytes(url, session):
    last_exc = None
    for attempt in range(MAX_RETRIES):
        if attempt > 0:
            delay = RETRY_INITIAL_DELAY * (RETRY_BACKOFF ** (attempt - 1))
            time.sleep(delay)
        try:
            r = session.get(url, headers=UA_HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            return r.content
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            continue
    raise RuntimeError(f"Failed to fetch {url} after {MAX_RETRIES} attempts: {last_exc}")

def read_csv_bytes(content):
    head = content[:300].decode("utf-8", errors="ignore").lower()
    if "<html" in head or "verifying your browser" in head:
        raise ValueError("Received HTML (likely Cloudflare bot check) instead of CSV")
    return pd.read_csv(io.BytesIO(content))

def canon_team(name):
    if not name:
        return ""
    name = name.lower()
    name = re.sub(r"[^\w\s]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    # map abbreviations
    return TEAM_ALIASES.get(name, name)

def try_teamstats_endpoint(year, session):
    """
    Try to fetch four-factor stats from teamstats.php.
    Returns a DataFrame or None if blocked/unusable.
    """
    url = f"https://barttorvik.com/teamstats.php?year={year}&conlimit=Sum&csv=1"
    try:
        content = fetch_bytes(url, session)
        df = read_csv_bytes(content)
        df = _clean_columns(df)
        # identify team column
        if "team" not in df.columns:
            # sometimes it’s "team_name"
            for cand in ("team_name", "teamname"):
                if cand in df.columns:
                    df = df.rename(columns={cand: "team"})
                    break
        if "team" not in df.columns:
            return None
        # check numeric team names
        if df["team"].astype(str).str.isnumeric().all():
            return None  # unreachable: team names are IDs
        df["team_key"] = df["team"].map(canon_team)
        return df
    except Exception:
        return None

def try_fffinal_csv(year, session):
    """
    Try to fetch four-factor stats from {year}_fffinal.csv.
    Returns a DataFrame or None if numeric or missing.
    """
    url = f"https://barttorvik.com/{year}_fffinal.csv"
    try:
        content = fetch_bytes(url, session)
        df = read_csv_bytes(content)
        df = _clean_columns(df)
        if "team" not in df.columns and "teamname" in df.columns:
            df = df.rename(columns={"teamname": "team"})
        if "team" not in df.columns:
            return None
        # bail if numeric codes
        if df["team"].astype(str).str.isnumeric().all():
            return None
        df["team_key"] = df["team"].map(canon_team)
        # rename columns to our desired names
        rename = {
            "efgpct_def": "efgdpct",
            "topct": "tor",
            "topct_def": "tord",
            "orpct": "orb",
            "drpct": "drb",
            "ftr_def": "ftrd",
            "3pdpct": "3ppctd",
            "3p_rate": "3pr",
            "3p_rate_d": "3prd",
        }
        df = df.rename(columns=rename)
        return df
    except Exception:
        return None

def refresh_barttorvik_players(out_path, year, session):
    url = f"https://barttorvik.com/getadvstats.php?year={year}&csv=1"
    content = fetch_bytes(url, session)
    df = read_csv_bytes(content)
    df = _clean_columns(df)
    if {"adjoe", "adjde"} <= set(df.columns) and "adjem" not in df.columns:
        df["adjem"] = df["adjoe"] - df["adjde"]
    df.to_csv(out_path, index=False)
    return len(df)

def refresh_barttorvik_team_results(out_path, year, session):
    url = f"https://barttorvik.com/{year}_team_results.csv"
    content = fetch_bytes(url, session)
    df = read_csv_bytes(content)
    df = _clean_columns(df)
    df = df.rename(columns={"rank": "rk", "record": "rec"})
    if "team" not in df.columns:
        raise ValueError("team_results.csv missing 'team' column")
    df["team_key"] = df["team"].map(canon_team)
    df.to_csv(out_path, index=False)
    return len(df), df

def refresh_barttorvik_team_factors(out_path, year, session, team_results_df):
    """
    Fetches four factors data via teamstats or fffinal and merges with results.
    If neither source works, logs a warning and returns False.
    """
    factors_df = try_teamstats_endpoint(year, session)
    if factors_df is None:
        # fallback to fffinal
        factors_df = try_fffinal_csv(year, session)
    if factors_df is None:
        print(f"[WARN] Could not fetch four-factor stats for {year}. Leaving factors out.")
        return False  # no file written

    merged = team_results_df.merge(
        factors_df.drop(columns=["team"], errors="ignore"),
        on="team_key",
        how="inner",
        suffixes=("", "_ff"),
    )
    if "efgpct" not in merged.columns:
        print(f"[WARN] Four-factor columns missing after merge. Check Torvik format.")
        return False
    merged = merged.drop(columns=["team_key"])
    merged.to_csv(out_path, index=False)
    return True

def refresh_haslametrics(out_path, session, table_index=0):
    url = "https://haslametrics.com/ratings.php"
    html = fetch_bytes(url, session).decode("utf-8", errors="ignore")
    tables = pd.read_html(io.StringIO(html))
    if table_index >= len(tables) or len(tables[table_index]) < 300:
        raise ValueError("Haslametrics table index appears wrong")
    df = _clean_columns(tables[table_index])
    df.to_csv(out_path, index=False)
    return len(df)

def refresh_espn_injuries(out_path, team_ids):
    """
    Fetch injury reports from ESPN for the given team IDs and write to CSV.
    Uses the ESPN team API endpoint to extract injury data.

    Args:
        out_path: Path to write the injuries CSV
        team_ids: List of ESPN team ID strings

    Returns:
        Number of injury rows written
    """
    from espn_injuries import fetch_injuries_for_teams

    df = fetch_injuries_for_teams(team_ids)
    df.to_csv(out_path, index=False)
    return len(df)


def _collect_team_ids_from_csv(games_csv_path):
    """
    Collect unique team IDs from an ESPN games or team game logs CSV.
    Looks for a 'team_id' column first, then falls back to player boxscores.

    Args:
        games_csv_path: Path to a CSV containing team_id column

    Returns:
        Sorted list of unique team ID strings
    """
    def _read_team_ids(path):
        """Read unique team IDs from a CSV if the file has a team_id column."""
        if not os.path.exists(path):
            return []
        try:
            df = pd.read_csv(path, usecols=["team_id"])
            return sorted(df["team_id"].dropna().astype(str).unique().tolist())
        except Exception:
            return []

    team_ids = _read_team_ids(games_csv_path)
    if team_ids:
        return team_ids

    parent = os.path.dirname(games_csv_path) or "."
    for fallback_name in ("espn_player_boxscores.csv", "espn_teams.csv"):
        fallback_path = os.path.join(parent, fallback_name)
        team_ids = _read_team_ids(fallback_path)
        if team_ids:
            return team_ids
    return []


def refresh_multi_source_games(date_str=None, days_back=7, output_path="ESPN/CSV/espn_games_merged.csv"):
    """
    Fetch games using multi-source integration (ESPN + NCAA + Henry API).
    
    Args:
        date_str: Date string in YYYY-MM-DD format (None = today)
        days_back: Number of days to fetch back from date
        output_path: Path to save merged games CSV
        
    Returns:
        Number of games fetched, or 0 if failed
    """
    try:
        # Import multi-source fetcher (core already in sys.path)
        from multi_source_fetcher import MultiSourceFetcher
        
        # Initialize fetcher
        fetcher = MultiSourceFetcher(
            enable_espn=True,
            enable_ncaa=True,
            enable_henry=True,
            enable_cbbd=os.environ.get("ENABLE_CBBD", "false").lower() == "true"
        )
        
        # Determine date
        if date_str:
            date = datetime.strptime(date_str, "%Y-%m-%d")
        else:
            date = datetime.now(ZoneInfo("America/Los_Angeles"))
        
        # Fetch date range
        dates = []
        for i in range(days_back):
            d = (date - timedelta(days=i)).strftime("%Y-%m-%d")
            dates.append(d)
        
        # Fetch all dates
        all_games = []
        total_conflicts = 0
        
        for d in dates:
            try:
                games, report = fetcher.fetch_date(d, allow_partial=True)
                all_games.extend(games)
                total_conflicts += report.total_conflicts
                
                if report.failed_sources:
                    print(f"[WARN] {d}: Failed sources: {', '.join(report.failed_sources)}")
            except Exception as e:
                print(f"[ERROR] Failed to fetch {d}: {e}")
                continue
        
        if all_games:
            # Save to CSV
            fetcher.save_to_csv(all_games, output_path, include_metadata=True)
            print(f"Multi-source games: {len(all_games)} games ({total_conflicts} conflicts)")
            return len(all_games)
        else:
            print("[WARN] No games fetched from multi-source system")
            return 0
            
    except Exception as e:
        print(f"[ERROR] Multi-source refresh failed: {e}")
        return 0


def main():
    session = requests.Session()
    year = int(os.environ.get("TORVIK_YEAR", cbb_season_year()))
    players_out = os.environ.get("TORVIK_PLAYERS_OUT", "barttorvik.csv")
    teams_out = os.environ.get("TORVIK_TEAMS_OUT", "barttorvik_teams.csv")
    team_results_out = os.environ.get("TORVIK_TEAM_RESULTS_OUT", "barttorvik_team_results.csv")
    haslam_out = os.environ.get("HASLA_OUT", "haslametrics.csv")
    table_idx = int(os.environ.get("HASLA_TABLE_INDEX", "0"))
    injuries_out = os.environ.get("INJURIES_OUT", "ESPN/CSV/espn_injuries.csv")

    try:
        n = refresh_barttorvik_players(players_out, year, session)
        print(f"Players: {n} rows")
    except Exception as e:
        print(f"[ERROR] Players refresh failed: {e}")
    try:
        n_team, df_team = refresh_barttorvik_team_results(team_results_out, year, session)
        print(f"Team results: {n_team} rows")
    except Exception as e:
        print(f"[ERROR] Team results refresh failed: {e}")
        df_team = None
    if df_team is not None:
        ok = refresh_barttorvik_team_factors(teams_out, year, session, df_team)
        if ok:
            print("Team factors: refresh OK")
        else:
            print("Team factors: no data; see logs.")
    try:
        h = refresh_haslametrics(haslam_out, session, table_idx)
        print(f"Haslametrics: {h} rows")
    except Exception as e:
        print(f"[ERROR] Haslametrics refresh failed: {e}")

    # --- ESPN Injury Reports ---
    team_logs_csv = os.environ.get(
        "ESPN_TEAM_LOGS_CSV", "ESPN/CSV/espn_team_game_logs.csv"
    )
    team_ids = _collect_team_ids_from_csv(team_logs_csv)
    if team_ids:
        try:
            n = refresh_espn_injuries(injuries_out, team_ids)
            print(f"Injuries: {n} rows")
        except Exception as e:
            print(f"[ERROR] Injury refresh failed: {e}")
    else:
        print("[WARN] No team IDs found; skipping injury refresh.")

    # --- Multi-Source Game Data Integration (NEW) ---
    enable_multi_source = os.environ.get("ENABLE_MULTI_SOURCE", "false").lower() == "true"
    if enable_multi_source:
        print("\n--- Multi-Source Game Data Integration ---")
        multi_source_days = int(os.environ.get("MULTI_SOURCE_DAYS_BACK", "7"))
        multi_source_out = os.environ.get("MULTI_SOURCE_OUT", "ESPN/CSV/espn_games_merged.csv")
        try:
            n_games = refresh_multi_source_games(
                date_str=None,  # Use today
                days_back=multi_source_days,
                output_path=multi_source_out
            )
            if n_games > 0:
                print(f"Multi-source integration: {n_games} games saved to {multi_source_out}")
            else:
                print("Multi-source integration: No games fetched")
        except Exception as e:
            print(f"[ERROR] Multi-source integration failed: {e}")
    else:
        print("\n[INFO] Multi-source integration disabled (set ENABLE_MULTI_SOURCE=true to enable)")

    print("Done")

if __name__ == "__main__":
    main()
