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


def _clean_col_name(c: str) -> str:
    s = str(c).strip().lower()
    s = s.replace("%", "pct")
    s = s.replace(".", "")
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^\w]", "", s)
    return s


def _clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [_clean_col_name(c) for c in df.columns]
    return df


def _debug(msg: str) -> None:
    print(f"[refresh_sources][debug] {msg}", file=sys.stderr)


def fetch_bytes(url: str, session: requests.Session) -> bytes:
    r = session.get(url, headers=UA_HEADERS, timeout=TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    return r.content


def _read_csv_bytes(content: bytes) -> pd.DataFrame:
    try:
        return pd.read_csv(io.BytesIO(content))
    except Exception:
        text = content.decode("utf-8", errors="ignore")
        return pd.read_csv(io.StringIO(text), sep=None, engine="python")


def _team_key(s: str) -> str:
    if s is None:
        return ""
    s = str(s).strip().lower()
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def _parse_games_from_record(rec: str) -> int | None:
    if rec is None:
        return None
    m = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", str(rec))
    if not m:
        return None
    return int(m.group(1)) + int(m.group(2))


# -------------------------
# BartTorvik: Players (keep as barttorvik.csv)
# -------------------------
def refresh_barttorvik_players(out_path: str, year: int, session: requests.Session) -> int:
    url = f"https://barttorvik.com/getadvstats.php?year={year}&csv=1"
    content = fetch_bytes(url, session)

    df = pd.read_csv(io.BytesIO(content))
    df = _clean_columns(df)

    if "adjoe" in df.columns and "adjde" in df.columns and "adjem" not in df.columns:
        df["adjem"] = df["adjoe"] - df["adjde"]

    df.to_csv(out_path, index=False)
    return len(df)


# -------------------------
# BartTorvik: Team Results (rank/proj/sos/wab/adjt etc)
# -------------------------
def fetch_barttorvik_team_results_df(year: int, session: requests.Session) -> pd.DataFrame:
    url = f"https://barttorvik.com/{year}_team_results.csv"
    content = fetch_bytes(url, session)

    df = _read_csv_bytes(content)
    df = _clean_columns(df)

    df = df.rename(
        columns={
            "rank": "rk",
            "record": "rec",
        }
    )

    if "rk" not in df.columns or "team" not in df.columns:
        _debug(f"Torvik team_results columns (cleaned): {list(df.columns)}")
        raise ValueError("Torvik team_results missing required columns (rk/team).")

    df["team_key"] = df["team"].map(_team_key)

    if "g" not in df.columns:
        df["g"] = df["rec"].map(_parse_games_from_record)

    return df


def refresh_barttorvik_team_results(out_path: str, year: int, session: requests.Session) -> int:
    df = fetch_barttorvik_team_results_df(year, session)

    n = len(df)
    if n < 330 or n > 390:
        raise ValueError(f"Unexpected Torvik team_results row count: {n}")

    df.drop(columns=["team_key"], errors="ignore").to_csv(out_path, index=False)
    return n


# -------------------------
# BartTorvik: Four Factors (YEAR_fffinal.csv)
# -------------------------
def fetch_barttorvik_fffinal_df(year: int, session: requests.Session) -> pd.DataFrame:
    url = f"https://barttorvik.com/{year}_fffinal.csv"
    content = fetch_bytes(url, session)

    df = _read_csv_bytes(content)
    df = _clean_columns(df)

    # fffinal uses teamname
    if "team" not in df.columns and "teamname" in df.columns:
        df = df.rename(columns={"teamname": "team"})

    if "team" not in df.columns:
        _debug(f"Torvik fffinal columns (cleaned): {list(df.columns)}")
        raise ValueError("Torvik fffinal missing team column (team/teamname).")

    df["team_key"] = df["team"].map(_team_key)

    rename_map = {
        "efgpct_def": "efgdpct",
        "topct": "tor",           # key fix
        "topct_def": "tord",
        "ftr_def": "ftrd",
        "orpct": "orb",
        "drpct": "drb",
        "3pdpct": "3ppctd",
        "3p_rate": "3pr",
        "3p_rate_d": "3prd",
    }
    df = df.rename(columns=rename_map)

    keep = [
        "team",
        "team_key",
        "efgpct",
        "efgdpct",
        "tor",
        "tord",
        "orb",
        "drb",
        "ftr",
        "ftrd",
        "2ppct",
        "2ppctd",
        "3ppct",
        "3ppctd",
        "3pr",
        "3prd",
    ]
    missing = [c for c in keep if c not in df.columns]
    if missing:
        _debug(f"Torvik fffinal columns (cleaned): {list(df.columns)}")
        raise ValueError(f"Torvik fffinal missing expected columns: {missing}")

    return df[keep].copy()


def _build_fffinal_teamkey_fallback(fffinal: pd.DataFrame, results: pd.DataFrame) -> pd.DataFrame:
    """
    If fffinal team_key values don't overlap results team_key at all,
    fall back to a deterministic best prefix match.

    Strategy:
    - Sort results team keys by length DESC (prefer longer, more specific)
    - For each fffinal key, find first results key where:
        fffinal_key.startswith(results_key) OR results_key.startswith(fffinal_key)
    - This maps "michiganwolverines" -> "michigan"
      and "northcarolinastatewolfpack" -> "northcarolinastate"
      and avoids "utah" stealing "utahstate" (because utahstate is longer)
    """
    results_keys = results["team_key"].dropna().astype(str).unique().tolist()
    results_keys.sort(key=len, reverse=True)

    def guess(ffk: str) -> str | None:
        if not ffk:
            return None
        for rk in results_keys:
            if ffk.startswith(rk) or rk.startswith(ffk):
                return rk
        return None

    fffinal = fffinal.copy()
    fffinal["team_key_guess"] = fffinal["team_key"].map(guess)
    matched = fffinal["team_key_guess"].notna().sum()
    _debug(f"Fallback team_key matching: matched {matched} / {len(fffinal)}")

    # overwrite with guess where present
    fffinal["team_key"] = fffinal["team_key_guess"].fillna(fffinal["team_key"])
    fffinal = fffinal.drop(columns=["team_key_guess"], errors="ignore")
    return fffinal


def refresh_barttorvik_teams_merged(out_path: str, year: int, session: requests.Session) -> int:
    results = fetch_barttorvik_team_results_df(year, session)
    fffinal = fetch_barttorvik_fffinal_df(year, session)

    # If there is zero overlap, apply fallback mapping
    overlap = len(set(results["team_key"]) & set(fffinal["team_key"]))
    if overlap == 0:
        _debug("No overlap between team_results team_key and fffinal team_key. Applying fallback resolver.")
        fffinal = _build_fffinal_teamkey_fallback(fffinal, results)

    merged = results.merge(fffinal.drop(columns=["team"], errors="ignore"), on="team_key", how="left")

    missing_ff = merged["efgpct"].isna().sum()
    if missing_ff > 0:
        sample = merged.loc[merged["efgpct"].isna(), ["team"]].head(15)["team"].tolist()
        raise ValueError(
            f"Torvik teams merge: {missing_ff} teams missing fffinal match. Examples: {sample}"
        )

    required = [
        "rk",
        "team",
        "conf",
        "g",
        "rec",
        "adjoe",
        "adjde",
        "barthag",
        "efgpct",
        "efgdpct",
        "tor",
        "tord",
        "orb",
        "drb",
        "ftr",
        "ftrd",
        "2ppct",
        "2ppctd",
        "3ppct",
        "3ppctd",
        "3pr",
        "3prd",
        "adjt",
        "wab",
    ]
    missing_cols = [c for c in required if c not in merged.columns]
    if missing_cols:
        _debug(f"Merged columns: {list(merged.columns)}")
        raise ValueError(f"Torvik merged team file missing columns: {missing_cols}")

    merged["adjem"] = merged["adjoe"] - merged["adjde"]

    out_df = merged[required + ["adjem"]].copy()

    n = len(out_df)
    if n < 330 or n > 390:
        raise ValueError(f"Unexpected merged Torvik teams row count: {n}")

    out_df.to_csv(out_path, index=False)
    return n


# -------------------------
# Haslametrics
# -------------------------
def refresh_haslametrics(out_path: str, session: requests.Session, table_index: int = 0) -> int:
    url = "https://haslametrics.com/ratings.php"
    html_bytes = fetch_bytes(url, session)
    html_text = html_bytes.decode("utf-8", errors="ignore")

    tables = pd.read_html(io.StringIO(html_text))
    if not tables:
        raise ValueError("No tables found on Haslametrics ratings page.")

    df = None

    if 0 <= table_index < len(tables):
        t = _clean_columns(tables[table_index])
        if len(t) >= 300:
            df = t

    if df is None:
        for t in tables:
            t2 = _clean_columns(t)
            if len(t2) >= 300:
                df = t2
                break

    if df is None:
        sizes = [len(t) for t in tables]
        raise ValueError(f"Could not find a Haslametrics team table with ~365 rows. Table sizes: {sizes}")

    df.to_csv(out_path, index=False)
    return len(df)


def main() -> int:
    torvik_players_out = os.environ.get("TORVIK_PLAYERS_OUT", "barttorvik.csv")
    torvik_teams_out = os.environ.get("TORVIK_TEAMS_OUT", "barttorvik_teams.csv")
    torvik_team_results_out = os.environ.get("TORVIK_TEAM_RESULTS_OUT", "barttorvik_team_results.csv")
    hasla_out = os.environ.get("HASLA_OUT", "haslametrics.csv")

    torvik_year_env = os.environ.get("TORVIK_YEAR", "").strip()
    torvik_year = int(torvik_year_env) if torvik_year_env else cbb_season_year()

    hasla_table_index_env = os.environ.get("HASLA_TABLE_INDEX", "").strip()
    hasla_table_index = int(hasla_table_index_env) if hasla_table_index_env else 0

    session = requests.Session()
    ok_any = False

    print(f"[refresh_sources] Using TORVIK_YEAR={torvik_year}")
    print(
        f"[refresh_sources] Output: {torvik_players_out}, {torvik_teams_out}, {torvik_team_results_out}, {hasla_out}"
    )

    try:
        n = refresh_barttorvik_players(torvik_players_out, torvik_year, session)
        print(f"[refresh_sources] BartTorvik Players OK: wrote {n} rows -> {torvik_players_out}")
        ok_any = True
    except Exception as e:
        print(f"[refresh_sources] BartTorvik Players FAILED: {e}", file=sys.stderr)

    time.sleep(1.0)

    try:
        n = refresh_barttorvik_team_results(torvik_team_results_out, torvik_year, session)
        print(f"[refresh_sources] BartTorvik Team Results OK: wrote {n} rows -> {torvik_team_results_out}")
        ok_any = True
    except Exception as e:
        print(f"[refresh_sources] BartTorvik Team Results FAILED: {e}", file=sys.stderr)

    time.sleep(1.0)

    try:
        n = refresh_barttorvik_teams_merged(torvik_teams_out, torvik_year, session)
        print(f"[refresh_sources] BartTorvik Teams (Merged) OK: wrote {n} rows -> {torvik_teams_out}")
        ok_any = True
    except Exception as e:
        print(f"[refresh_sources] BartTorvik Teams (Merged) FAILED: {e}", file=sys.stderr)

    time.sleep(1.0)

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
