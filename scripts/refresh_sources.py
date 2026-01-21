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


def _clean_col_name(c: str) -> str:
    """
    Normalize column names:
      - lowercase
      - % -> pct
      - remove dots
      - spaces -> underscores
      - strip non-alphanum/underscore
    """
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


def fetch_bytes(url: str, session: requests.Session) -> bytes:
    r = session.get(url, headers=UA_HEADERS, timeout=TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    return r.content


def _looks_like_html(text_lower: str) -> bool:
    if "<html" in text_lower or "<!doctype html" in text_lower:
        return True
    bot_phrases = [
        "verifying your browser",
        "attention required",
        "cloudflare",
        "captcha",
        "enable javascript",
        "please wait",
        "js_required",
    ]
    return any(p in text_lower for p in bot_phrases)


def _debug_preview(content: bytes, label: str) -> None:
    try:
        txt = content.decode("utf-8", errors="ignore")
    except Exception:
        txt = repr(content[:500])
    preview = txt[:900].replace("\n", "\\n")
    print(f"[refresh_sources][debug] {label} preview: {preview}", file=sys.stderr)


def _read_csv_loose(content: bytes) -> pd.DataFrame:
    """
    Robust CSV reader that also detects HTML bot-check pages.
    """
    text = content.decode("utf-8", errors="ignore")

    if _looks_like_html(text.lower()):
        _debug_preview(content, "CSV read got HTML (likely bot-check)")
        raise ValueError("Expected CSV but got HTML/bot-check response.")

    # Try standard first
    try:
        return pd.read_csv(io.StringIO(text))
    except Exception:
        pass

    # Try autodetect delimiter
    try:
        return pd.read_csv(io.StringIO(text), sep=None, engine="python")
    except Exception:
        pass

    # Try common delimiters
    last_err = None
    for sep in [",", "\t", ";", "|"]:
        try:
            df = pd.read_csv(io.StringIO(text), sep=sep, engine="python")
            if df.shape[1] >= 5:
                return df
        except Exception as e:
            last_err = e

    raise last_err or ValueError("Unable to parse CSV content")


def _rename_if_present(df: pd.DataFrame, candidates: list[str], target: str) -> pd.DataFrame:
    if target in df.columns:
        return df
    for c in candidates:
        if c in df.columns:
            return df.rename(columns={c: target})
    return df


# -------------------------
# BartTorvik: Players (keep as barttorvik.csv)
# -------------------------
def refresh_barttorvik_players(out_path: str, year: int, session: requests.Session) -> int:
    url = f"https://barttorvik.com/getadvstats.php?year={year}&csv=1"
    content = fetch_bytes(url, session)

    df = pd.read_csv(io.BytesIO(content))
    df = _clean_columns(df)

    # convenience
    if "adjoe" in df.columns and "adjde" in df.columns and "adjem" not in df.columns:
        df["adjem"] = df["adjoe"] - df["adjde"]

    df.to_csv(out_path, index=False)
    return len(df)


# -------------------------
# BartTorvik: Team Results (your working static CSV)
# -------------------------
def refresh_barttorvik_team_results(out_path: str, year: int, session: requests.Session) -> int:
    url = f"https://barttorvik.com/{year}_team_results.csv"
    content = fetch_bytes(url, session)

    df = pd.read_csv(io.BytesIO(content))
    df = _clean_columns(df)

    n = len(df)
    if n < 330 or n > 390:
        _debug_preview(content, f"Torvik team_results unexpected row count={n}")
        raise ValueError(f"Unexpected Torvik team_results row count from {url}: {n}")

    df.to_csv(out_path, index=False)
    return n


# -------------------------
# BartTorvik: Team Stats (Four Factors)
# Prefer static YEAR_fffinal.csv (no JS), fallback to teamstats.php?csv=1
# -------------------------
def _normalize_torvik_teamstats_schema(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # identity fields
    df = _rename_if_present(df, ["rank", "rk"], "rk")
    df = _rename_if_present(df, ["school", "teamname", "team"], "team")
    df = _rename_if_present(df, ["conference", "conf"], "conf")
    df = _rename_if_present(df, ["games", "gp", "g"], "g")
    df = _rename_if_present(df, ["record", "rec"], "rec")

    # efficiencies + tempo + power
    df = _rename_if_present(df, ["adj_oe", "adj_o", "adjoe", "adjo"], "adjoe")
    df = _rename_if_present(df, ["adj_de", "adj_d", "adjde", "adjd"], "adjde")
    df = _rename_if_present(df, ["barthag"], "barthag")
    df = _rename_if_present(df, ["adj_t", "adjtempo", "adj_tempo", "adjt"], "adjt")
    df = _rename_if_present(df, ["wab"], "wab")

    # four factors / shooting + def counterparts
    df = _rename_if_present(df, ["efg", "efgo", "efgpct"], "efgpct")
    df = _rename_if_present(df, ["efgd", "efgdef", "efgdpct"], "efgdpct")

    df = _rename_if_present(df, ["to", "tor", "topct"], "tor")
    df = _rename_if_present(df, ["tod", "tord", "topctd"], "tord")

    df = _rename_if_present(df, ["orb", "orbpct"], "orb")
    df = _rename_if_present(df, ["drb", "drbpct"], "drb")

    df = _rename_if_present(df, ["ftr", "ftrate"], "ftr")
    df = _rename_if_present(df, ["ftrd", "ftrated"], "ftrd")

    df = _rename_if_present(df, ["2p", "2ppct"], "2ppct")
    df = _rename_if_present(df, ["2pd", "2ppctd"], "2ppctd")

    df = _rename_if_present(df, ["3pt", "3p", "3ppct"], "3ppct")
    df = _rename_if_present(df, ["3ptd", "3pd", "3ppctd"], "3ppctd")

    df = _rename_if_present(df, ["3pr", "3prate"], "3pr")
    df = _rename_if_present(df, ["3prd", "3prated"], "3prd")

    return df


def _validate_torvik_teamstats_required(df: pd.DataFrame) -> None:
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
        print(f"[refresh_sources][debug] Torvik teamstats columns (cleaned): {list(df.columns)}", file=sys.stderr)
        raise ValueError(f"Torvik teamstats missing required columns: {missing}")


def refresh_barttorvik_teamstats(out_path: str, year: int, session: requests.Session) -> int:
    """
    Produces barttorvik_teams.csv (team-level 4-factor style table).
    We avoid JS-bot-check pages by preferring YEAR_fffinal.csv.

    Sources:
      - Preferred: https://barttorvik.com/{year}_fffinal.csv
      - Fallback:  https://barttorvik.com/teamstats.php?year={year}&conlimit=Sum&csv=1
    """
    preferred_url = os.environ.get("TORVIK_FFFINAL_URL", "").strip()
    if not preferred_url:
        preferred_url = f"https://barttorvik.com/{year}_fffinal.csv"

    fallback_url = os.environ.get("TORVIK_TEAMSTATS_URL", "").strip()
    if not fallback_url:
        fallback_url = f"https://barttorvik.com/teamstats.php?year={year}&conlimit=Sum&csv=1"

    last_err = None
    for url in [preferred_url, fallback_url]:
        try:
            content = fetch_bytes(url, session)
            df = _read_csv_loose(content)
            df = _clean_columns(df)
            df = _normalize_torvik_teamstats_schema(df)

            n = len(df)
            if n < 330 or n > 390:
                _debug_preview(content, f"Torvik teamstats unexpected row count={n} from {url}")
                raise ValueError(f"Unexpected Torvik teamstats row count from {url}: {n}")

            _validate_torvik_teamstats_required(df)

            # convenience
            if "adjem" not in df.columns and "adjoe" in df.columns and "adjde" in df.columns:
                df["adjem"] = df["adjoe"] - df["adjde"]

            df.to_csv(out_path, index=False)
            return n
        except Exception as e:
            last_err = e
            print(f"[refresh_sources] Torvik TeamStats attempt FAILED ({url}): {e}", file=sys.stderr)
            time.sleep(1.0)

    raise last_err or ValueError("Torvik TeamStats failed for unknown reasons")


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

    # Prefer explicit table_index if it yields ~365 teams
    if 0 <= table_index < len(tables):
        t = _clean_columns(tables[table_index])
        if len(t) >= 300:
            df = t

    # Else auto-pick first big table
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

    # 1) Torvik players
    try:
        n = refresh_barttorvik_players(torvik_players_out, torvik_year, session)
        print(f"[refresh_sources] BartTorvik Players OK: wrote {n} rows -> {torvik_players_out}")
        ok_any = True
    except Exception as e:
        print(f"[refresh_sources] BartTorvik Players FAILED: {e}", file=sys.stderr)

    time.sleep(1.5)

    # 2) Torvik teamstats (Four Factors) via YEAR_fffinal.csv
    try:
        n = refresh_barttorvik_teamstats(torvik_teams_out, torvik_year, session)
        print(f"[refresh_sources] BartTorvik TeamStats OK: wrote {n} rows -> {torvik_teams_out}")
        ok_any = True
    except Exception as e:
        print(f"[refresh_sources] BartTorvik TeamStats FAILED: {e}", file=sys.stderr)

    time.sleep(1.5)

    # 3) Torvik team results (your working static CSV link)
    try:
        n = refresh_barttorvik_team_results(torvik_team_results_out, torvik_year, session)
        print(f"[refresh_sources] BartTorvik Team Results OK: wrote {n} rows -> {torvik_team_results_out}")
        ok_any = True
    except Exception as e:
        print(f"[refresh_sources] BartTorvik Team Results FAILED: {e}", file=sys.stderr)

    time.sleep(1.5)

    # 4) Haslametrics
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
