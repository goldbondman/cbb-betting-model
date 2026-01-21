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
    """
    BartTorvik 'year' is the season-ending year.
    Rule of thumb: if month >= July, use next year.
    """
    now = now or datetime.now(ZoneInfo("America/Los_Angeles"))
    return now.year + 1 if now.month >= 7 else now.year


def _clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize column names.
    - lowercase, trim
    - % -> pct
    - whitespace -> underscore
    - remove punctuation (keep underscores/alphanumerics)
    """
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
    """
    Player-level advanced stats.
    Output: barttorvik.csv (one row per player)
    """
    url = f"https://barttorvik.com/getadvstats.php?year={year}&csv=1"
    content = fetch_bytes(url, session)
    df = pd.read_csv(io.BytesIO(content))
    df = _clean_columns(df)

    if "adjoe" in df.columns and "adjde" in df.columns and "adjem" not in df.columns:
        df["adjem"] = df["adjoe"] - df["adjde"]

    df.to_csv(out_path, index=False)
    return len(df)


def _rename_if_present(df: pd.DataFrame, candidates: list[str], target: str) -> pd.DataFrame:
    """
    If any candidate column exists and target does not, rename the first match to target.
    """
    if target in df.columns:
        return df
    for c in candidates:
        if c in df.columns:
            return df.rename(columns={c: target})
    return df


def _normalize_torvik_trank_schema(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize BartTorvik TRank (team analytics) CSV headers into a stable schema.

    Expected site headers (display):
      RK, Team, Conf, G, Rec, AdjOE, AdjDE, Barthag,
      EFG%, EFGD%, TOR, TORD, ORB, DRB, FTR, FTRD,
      2P%, 2P%D, 3P%, 3P%D, 3PR, 3PRD, Adj T., WAB

    Canonical cleaned names we want:
      rk, team, conf, g, rec, adjoe, adjde, barthag,
      efgpct, efgdpct, tor, tord, orb, drb, ftr, ftrd,
      2ppct, 2ppctd, 3ppct, 3ppctd, 3pr, 3prd, adjt, wab
    """
    df = df.copy()

    # Rank column: sometimes becomes "unnamed0" or similar
    if "rk" not in df.columns:
        first = df.columns[0]
        s = df.iloc[:, 0].astype(str).str.strip()
        if s.str.fullmatch(r"\d+").mean() >= 0.90:
            df = df.rename(columns={first: "rk"})

    # Normalize obvious alternates (defensive, if any variants appear)
    df = _rename_if_present(df, ["conference"], "conf")
    df = _rename_if_present(df, ["games", "gp"], "g")
    df = _rename_if_present(df, ["record"], "rec")
    df = _rename_if_present(df, ["adj_oe", "adj_o"], "adjoe")
    df = _rename_if_present(df, ["adj_de", "adj_d"], "adjde")
    df = _rename_if_present(df, ["adj_t", "adjtempo", "adj_tempo"], "adjt")

    # EFG off/def
    df = _rename_if_present(df, ["efg", "efgo", "efgpct"], "efgpct")
    df = _rename_if_present(df, ["efgd", "efg_d", "efgdef", "efgdpct"], "efgdpct")

    # Turnovers
    df = _rename_if_present(df, ["to"], "tor")
    df = _rename_if_present(df, ["tod"], "tord")

    # Rebounding
    df = _rename_if_present(df, ["orbpct", "orb_pct"], "orb")
    df = _rename_if_present(df, ["drbpct", "drb_pct"], "drb")

    # Free throw rate
    df = _rename_if_present(df, ["ftrate", "ftr_rate"], "ftr")
    df = _rename_if_present(df, ["ftrated", "ftr_rated", "ftrdef"], "ftrd")

    # 2P / 3P
    df = _rename_if_present(df, ["2p", "2po", "2ppct"], "2ppct")
    df = _rename_if_present(df, ["2pd", "2pdef", "2ppctd"], "2ppctd")

    df = _rename_if_present(df, ["3pt", "3p", "3po", "3ppct"], "3ppct")
    df = _rename_if_present(df, ["3ptd", "3pd", "3pdef", "3ppctd"], "3ppctd")

    # 3P rate
    df = _rename_if_present(df, ["3prate", "3pr"], "3pr")
    df = _rename_if_present(df, ["3prated", "3prd", "3prdef"], "3prd")

    return df


def refresh_barttorvik_teams(out_path: str, year: int, session: requests.Session) -> int:
    """
    Team-level analytics table from BartTorvik (TRank export).
    Output: barttorvik_teams.csv (~365 rows)
    """
    # IMPORTANT: This is the TRank table (contains EFG%, TOR, ORB, FTR, 2P%, 3P%, 3PR, etc.)
    url = f"https://barttorvik.com/trank.php?year={year}&csv=1"
    content = fetch_bytes(url, session)

    df = pd.read_csv(io.BytesIO(content))
    df = _clean_columns(df)
    df = _normalize_torvik_trank_schema(df)

    if df.empty:
        raise ValueError("Torvik TRank team CSV parsed but contains zero rows")

    n = len(df)
    if n < 330 or n > 390:
        raise ValueError(f"Unexpected Torvik TRank team row count: {n}")

    # Strict required set based on your header list
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
        print(f"[refresh_sources][debug] Torvik TRank columns (cleaned): {list(df.columns)}", file=sys.stderr)
        raise ValueError(f"Torvik TRank team file missing required columns: {missing}")

    # Convenience metric
    if "adjem" not in df.columns:
        df["adjem"] = df["adjoe"] - df["adjde"]

    df.to_csv(out_path, index=False)
    return n


def refresh_haslametrics(out_path: str, session: requests.Session, table_index: int = 0) -> int:
    """
    Team-level ratings from Haslametrics.
    Auto-picks the ~365-row table if the provided index is wrong.
    """
    url = "https://haslametrics.com/ratings.php"
    html_bytes = fetch_bytes(url, session)
    html_text = html_bytes.decode("utf-8", errors="ignore")

    tables = pd.read_html(io.StringIO(html_text))
    if not tables:
        raise ValueError("No tables found on Haslametrics ratings page.")

    chosen_idx = table_index if 0 <= table_index < len(tables) else None

    def _clean_table(i: int) -> pd.DataFrame:
        t = tables[i]
        return _clean_columns(t)

    df = None
    if chosen_idx is not None:
        df_try = _clean_table(chosen_idx)
        if len(df_try) >= 300:
            df = df_try

    if df is None:
        for i, t in enumerate(tables):
            if len(t) >= 300:
                df = _clean_table(i)
                chosen_idx = i
                break

    if df is None:
        sizes = [len(t) for t in tables]
        raise ValueError(f"Could not find a Haslametrics table with ~365 teams. Table sizes: {sizes}")

    n = len(df)
    if n < 300:
        sizes = [len(t) for t in tables]
        raise ValueError(
            f"Haslametrics table_index={table_index} returned only {n} rows (wrong table). "
            f"Available table sizes: {sizes}"
        )

    df.to_csv(out_path, index=False)
    return n


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

    # 2) Torvik teams (TRank)
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
