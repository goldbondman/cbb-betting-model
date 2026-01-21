#!/usr/bin/env python3
import io
import os
import re
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests


UA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 30


def cbb_season_year(now=None) -> int:
    now = now or datetime.now(ZoneInfo("America/Los_Angeles"))
    return now.year + 1 if now.month >= 7 else now.year


def _clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    cleaned = []
    for c in df.columns:
        s = str(c).strip().lower().replace("%", "pct")
        s = re.sub(r"\s+", "_", s)
        s = re.sub(r"[^\w]", "", s)
        cleaned.append(s)
    df.columns = cleaned
    return df


def fetch_bytes(url: str, session: requests.Session) -> bytes:
    r = session.get(url, headers=UA_HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.content


def refresh_barttorvik_players(out_path: str, year: int, session: requests.Session) -> int:
    url = f"https://barttorvik.com/getadvstats.php?year={year}&csv=1"
    content = fetch_bytes(url, session)
    df = pd.read_csv(io.BytesIO(content))
    df = _clean_columns(df)

    if "adjoe" in df.columns and "adjde" in df.columns and "adjem" not in df.columns:
        df["adjem"] = df["adjoe"] - df["adjde"]

    df.to_csv(out_path, index=False)
    return len(df)


def refresh_barttorvik_teams(out_path: str, year: int, session: requests.Session) -> int:
    """
    Team-level data from barttorvik.com/{year}_team_results.csv
    """
    url = f"https://barttorvik.com/{year}_team_results.csv"
    content = fetch_bytes(url, session)

    df = pd.read_csv(io.BytesIO(content))
    df = _clean_columns(df)

    # Enforce that we got data for ~365 teams
    n = len(df)
    if n < 330 or n > 390:
        raise ValueError(f"Unexpected Torvik team row count from {url}: {n}")

    # Map Torvik cleaned columns to your canonical names where needed:
    # (site columns likely already mostly match, but unify variants)
    rename_map = {
        "adjoteff": "adjoe",
        "adjdeeff": "adjde",
        "efgpcto": "efgpct",
        "efgpctd": "efgdpct",
        "torpcto": "tor",
        "torpctd": "tord",
        "orbpcto": "orb",
        "drbpctd": "drb",
        "ftrto": "ftr",
        "ftrtd": "ftrd",
        "2ppcto": "2ppct",
        "2ppctd": "2ppctd",
        "3ppcto": "3ppct",
        "3ppctd": "3ppctd",
        "3prto": "3pr",
        "3prtd": "3prd",
        "adjtempo": "adjt",
    }
    for src, dst in rename_map.items():
        if src in df.columns and dst not in df.columns:
            df = df.rename(columns={src: dst})

    # Required set (based on the fields you listed)
    required = {
        "rk", "team", "conf", "g", "rec",
        "adjoe", "adjde", "barthag",
        "efgpct", "efgdpct",
        "tor", "tord",
        "orb", "drb",
        "ftr", "ftrd",
        "2ppct", "2ppctd",
        "3ppct", "3ppctd",
        "3pr", "3prd",
        "adjt", "wab",
    }
    missing = sorted(required - set(df.columns))
    if missing:
        print(f"[refresh_sources][debug] Torvik team columns (cleaned): {list(df.columns)}", file=sys.stderr)
        raise ValueError(f"Torvik team file missing required columns: {missing}")

    if "adjem" not in df.columns:
        df["adjem"] = df["adjoe"] - df["adjde"]

    df.to_csv(out_path, index=False)
    return n


def refresh_haslametrics(out_path: str, session: requests.Session, table_index: int = 0) -> int:
    url = "https://haslametrics.com/ratings.php"
    html_bytes = fetch_bytes(url, session)
    html_text = html_bytes.decode("utf-8", errors="ignore")

    tables = pd.read_html(html_text)
    if not tables:
        raise ValueError("No tables found on Haslametrics ratings page.")

    # Pick the first table that looks like ~365 rows
    df = None
    for t in tables:
        t2 = _clean_columns(t)
        if len(t2) >= 300:
            df = t2
            break

    if df is None:
        sizes = [len(t) for t in tables]
        raise ValueError(f"Could not find Haslametrics team table in ratings page. Table sizes: {sizes}")

    df.to_csv(out_path, index=False)
    return len(df)


def main() -> int:
    torvik_players_out = os.environ.get("TORVIK_PLAYERS_OUT", "barttorvik.csv")
    torvik_teams_out = os.environ.get("TORVIK_TEAMS_OUT", "barttorvik_teams.csv")
    hasla_out = os.environ.get("HASLA_OUT", "haslametrics.csv")

    torvik_year_env = os.environ.get("TORVIK_YEAR", "").strip()
    torvik_year = int(torvik_year_env) if torvik_year_env else cbb_season_year()

    hasla_table_index_env = os.environ.get("HASLA_TABLE_INDEX", "").strip()
    hasla_table_index = int(hasla_table_index_env) if hasla_table_index_env else 0

    session = requests.Session()
    ok_any = False

    print(f"[refresh_sources] Using TORVIK_YEAR={torvik_year}")
    print(f"[refresh_sources] Output: {torvik_players_out}, {torvik_teams_out}, {hasla_out}")

    # 1) Torvik players
    try:
        n = refresh_barttorvik_players(torvik_players_out, torvik_year, session)
        print(f"[refresh_sources] BartTorvik Players OK: wrote {n} rows -> {torvik_players_out}")
        ok_any = True
    except Exception as e:
        print(f"[refresh_sources] BartTorvik Players FAILED: {e}", file=sys.stderr)

    time.sleep(1.5)

    # 2) Torvik teams (YEAR_team_results.csv)
    try:
        n = refresh_barttorvik_teams(torvik_teams_out, torvik_year, session)
        print(f"[refresh_sources] BartTorvik Teams OK: wrote {n} rows -> {torvik_teams_out}")
        ok_any = True
    except Exception as e:
        print(f"[refresh_sources] BartTorvik Teams FAILED: {e}", file=sys.stderr)

    time.sleep(1.5)

    # 3) Haslametrics
    try:
        n = refresh_haslametrics(hasla_out, session, table_index=hasla_table_index)
        print(f"[refresh_sources] Haslametrics OK: wrote {n} rows -> {hasla_out}")
        ok_any = True
    except Exception as e:
        print(f"[refresh_sources] Haslametrics FAILED: {e}", file=sys.stderr)

    if not ok_any:
        print("[refresh_sources] All sources failed. Failing job.", file=sys.stderr)
        return 1

    print("[refresh_sources] Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
