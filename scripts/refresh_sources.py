import io
import os
import re
import time
import sys
import requests
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo

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
    
    # Check if CSV appears to have a proper header row
    # If first row looks like data (e.g., player names, team names), it's missing headers
    lines = content.decode("utf-8", errors="replace").split('\n')
    if lines:
        first_line = lines[0].strip()
        # Check first few fields - player/team names typically have underscores or spaces
        # while clean column headers don't
        HEADER_CHECK_COLUMNS = 3  # Check first 3 columns for data patterns
        parts = first_line.split(',')[:HEADER_CHECK_COLUMNS]
        if len(parts) >= 2:
            # Player name + team name pattern: contains underscores/spaces
            col0_looks_like_data = '_' in parts[0] or ' ' in parts[0]
            col1_looks_like_data = '_' in parts[1] or ' ' in parts[1]
            # If both first columns look like identifiers (not clean column names), 
            # assume headers are missing
            if col0_looks_like_data and col1_looks_like_data:
                raise ValueError(
                    "CSV appears to be missing header row (first row looks like data). "
                    "Check the upstream API response."
                )
    
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
    
    # Validate that we got meaningful columns (not data values)
    # If columns look like numeric IDs or player names, something is wrong
    COLUMNS_TO_CHECK = 5  # Check first 5 columns
    SUSPICIOUS_THRESHOLD = 3  # If 3+ columns are suspicious, likely malformed
    suspicious_columns = [c for c in df.columns[:COLUMNS_TO_CHECK] if c.isdigit() or '_' in c]
    if len(suspicious_columns) >= SUSPICIOUS_THRESHOLD:
        raise ValueError(
            f"Suspicious column names detected: {suspicious_columns}. "
            "API may have returned malformed data."
        )
    
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

def main():
    session = requests.Session()
    year = int(os.environ.get("TORVIK_YEAR", cbb_season_year()))
    players_out = os.environ.get("TORVIK_PLAYERS_OUT", "barttorvik.csv")
    teams_out = os.environ.get("TORVIK_TEAMS_OUT", "barttorvik_teams.csv")
    team_results_out = os.environ.get("TORVIK_TEAM_RESULTS_OUT", "barttorvik_team_results.csv")
    haslam_out = os.environ.get("HASLA_OUT", "haslametrics.csv")
    table_idx = int(os.environ.get("HASLA_TABLE_INDEX", "0"))

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
    print("Done")

if __name__ == "__main__":
    main()
