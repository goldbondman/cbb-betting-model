#!/usr/bin/env python3
import io
import os
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
    """
    BartTorvik 'year' is the season-ending year.
    Examples:
      - Nov/Dec 2025 season is year=2026
      - Jan/Feb/Mar 2026 season is year=2026
    Rule of thumb: if month >= July, use next year.
    """
    now = now or datetime.now(ZoneInfo("America/Los_Angeles"))
    return now.year + 1 if now.month >= 7 else now.year


def _clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize column names:
    - lowercase
    - trim
    - spaces -> underscores
    - drop periods
    - % -> pct
    """
    df = df.copy()
    df.columns = [
        str(c)
        .strip()
        .lower()
        .replace(" ", "_")
        .replace(".", "")
        .replace("%", "pct")
        for c in df.columns
    ]
    return df


def fetch_bytes(url: str, session: requests.Session) -> bytes:
    r = session.get(url, headers=UA_HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.content


def refresh_barttorvik_players(out_path: str, year: int, session: requests.Session) -> int:
    """
    Player-level advanced stats from BartTorvik.
    Output: barttorvik.csv (one row per player)
    """
    url = f"https://barttorvik.com/getadvstats.php?year={year}&csv=1"
    content = fetch_bytes(url, session)
    df = pd.read_csv(io.BytesIO(content))
    df = _clean_columns(df)

    # Convenience metric
    if "adjoe" in df.columns and "adjde" in df.columns and "adjem" not in df.columns:
        df["adjem"] = df["adjoe"] - df["adjde"]

    df.to_csv(out_path, index=False)
    return len(df)


def refresh_barttorvik_teams(out_path: str, year: int, session: requests.Session) -> int:
    """
    Team-level results from BartTorvik.
    Output: barttorvik_teams.csv (~365 rows, one per D1 team)
    """
    url = f"https://barttorvik.com/{year}_team_results.csv"
    content = fetch_bytes(url, session)
    df = pd.read_csv(io.BytesIO(content))
    df = _clean_columns(df)

    # Guardrail 1: sanity check team count
    n = len(df)
    if n < 330 or n > 390:
        raise ValueError(f"Unexpected Torvik team row count: {n}")

    # Guardrail 2: required columns (fail fast if schema changes)
    required = {
        "rk",
        "team",
        "conf",
        "adjoe",
        "adjde",
        "barthag",
        "adjt",
        "wab",
    }
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"Torvik team file missing required columns: {missing}")

    # Convenience metric
    if "adjoe" in df.columns and "adjde" in df.columns and "adjem" not in df.columns:
        df["adjem"] = df["adjoe"] - df["adjde"]

    df.to_csv(out_path, index=False)
    return n


def refresh_haslametrics(out_path: str, session: requests.Session, table_index: int = 0) -> int:
    """
    Team-level ratings from Haslametrics.
    """
    url = "https://haslametrics.com/ratings.php"
    html_bytes = fetch_bytes(url, session)

    html_text = html_bytes.decode("utf-8", errors="ignore")
    tables = pd.read_html(html_text)
    if not tables:
        raise ValueError("No tables found on Haslametrics ratings page.")

    if table_index < 0 or table_index >= len(tables):
        raise ValueError(
            f"Invalid HASLA_TABLE_INDEX={table_index}. Found {len(tables)} tables."
        )

    df = tables[table_index]
    df = _clean_columns(df)
    df.to_csv(out_path, index=False)
    return len(df)


def main() -> int:
    # Outputs (player-level Torvik stays as barttorvik.csv)
    torvik_players_out = os.environ.get("TORVIK_PLAYERS_OUT", "barttorvik.csv")
    torvik_teams_out = os.environ.get("TORVIK_TEAMS_OUT", "barttorvik_teams.csv")
    hasla_out = os.environ.get("HASLA_OUT", "haslametrics.csv")

    # Optional overrides
    torvik_year_env = os.environ.get("TORVIK_YEAR", "").strip()
    torvik_year = int(torvik_year_env) if torvik_year_env else cbb_season_year()

    hasla_table_index_env = os.environ.get("HASLA_TABLE_INDEX", "").strip()
    hasla_table_index = int(hasla_table_index_env) if hasla_table_index_env else 0

    session = requests.Session()
    ok_any = False

    print(f"[refresh_sources] Using TORVIK_YEAR={torvik_year}")
    print(
        f"[refresh_sources] Output: "
        f"{torvik_players_out}, {torvik_teams_out}, {hasla_out}"
    )

    # 1) BartTorvik players
    try:
        n = refresh_barttorvik_players(torvik_players_out, torvik_year, session)
        print(
            f"[refresh_sources] BartTorvik Players OK: "
            f"wrote {n} rows -> {torvik_players_out}"
        )
        ok_any = True
    except Exception as e:
        print(f"[refresh_sources] BartTorvik Players FAILED: {e}", file=sys.stderr)

    time.sleep(1.5)

    # 2) BartTorvik teams
    try:
        n = refresh_barttorvik_teams(torvik_teams_out, torvik_year, session)
        print(
            f"[refresh_sources] BartTorvik Teams OK: "
            f"wrote {n} rows -> {torvik_teams_out}"
        )
        ok_any = True
    except Exception as e:
        print(f"[refresh_sources] BartTorvik Teams FAILED: {e}", file=sys.stderr)

    time.sleep(1.5)

    # 3) Haslametrics
    try:
        n = refresh_haslametrics(hasla_out, session, table_index=hasla_table_index)
        print(
            f"[refresh_sources] Haslametrics OK: "
            f"wrote {n} rows -> {hasla_out}"
        )
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
