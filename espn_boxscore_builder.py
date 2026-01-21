import os
import requests
import pandas as pd
import numpy as np
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta

# -------------------------------------------------------------------
# ESPN endpoints
# NOTE: groups=50 is the key to "all Division I" scoreboard results.
# -------------------------------------------------------------------
ESPN_SUMMARY_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/summary"
    "?event={event_id}"
)

ESPN_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
    "?groups=50&limit=300&dates={date}"
)

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json,text/plain,*/*",
}

PST_TZ = ZoneInfo("America/Los_Angeles")


# ---------------- helpers ----------------
def _to_int(x, default=0):
    try:
        if x is None:
            return default
        if isinstance(x, str):
            x = x.strip()
            if x == "":
                return default
            return int(float(x))
        return int(x)
    except Exception:
        return default


def _to_float(x, default=np.nan):
    try:
        if x is None:
            return default
        if isinstance(x, str):
            x = x.strip()
            if x == "":
                return default
            return float(x)
        return float(x)
    except Exception:
        return default


def _parse_made_attempt(display: str):
    if not display or not isinstance(display, str) or "-" not in display:
        return (0, 0)
    a, b = display.split("-", 1)
    return (_to_int(a, 0), _to_int(b, 0))


def _stat_map(team_stats_list):
    out = {}
    if not isinstance(team_stats_list, list):
        return out
    for item in team_stats_list:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        dv = item.get("displayValue")
        if name:
            out[str(name)] = dv
    return out


def _estimate_possessions(fga, fta, tov, orb):
    # poss ≈ FGA + 0.44*FTA - ORB + TOV
    return fga + 0.44 * fta - orb + tov


def _safe_div(num, den, default=np.nan):
    return default if den in (0, 0.0, None) else num / den


def _extract_status_from_comp(comp: dict) -> dict:
    """
    Normalized status fields from the scoreboard/competition object.
    """
    status = (comp or {}).get("status") or {}
    stype = status.get("type") or {}

    # ESPN commonly provides these:
    # - type.name: "STATUS_FINAL", "STATUS_IN_PROGRESS", "STATUS_SCHEDULED", etc.
    # - type.state: "pre", "in", "post"
    # - type.completed: boolean
    # - displayClock, period
    return {
        "status_name": stype.get("name"),
        "status_state": stype.get("state"),
        "status_completed": bool(stype.get("completed", False)),
        "status_detail": stype.get("detail"),
        "status_short": stype.get("shortDetail"),
        "period": _to_int(status.get("period"), 0),
        "clock": status.get("displayClock"),
    }


def _is_final_status(status_row: dict) -> bool:
    # Most reliable: completed==True OR state == "post" OR name == STATUS_FINAL
    if not isinstance(status_row, dict):
        return False
    if status_row.get("status_completed") is True:
        return True
    if str(status_row.get("status_state") or "").lower() == "post":
        return True
    if str(status_row.get("status_name") or "").upper() == "STATUS_FINAL":
        return True
    return False


def _append_forever(df_new: pd.DataFrame, out_csv: str, dedupe_keys: list[str], sort_cols: list[str] | None = None):
    """
    Append new rows into an ever-growing season-to-date CSV.
    Dedupe on dedupe_keys, keep the latest occurrence.
    """
    if df_new is None or df_new.empty:
        # still ensure file exists if it doesn't
        if not os.path.exists(out_csv):
            pd.DataFrame().to_csv(out_csv, index=False)
        return pd.DataFrame()

    if os.path.exists(out_csv):
        try:
            df_old = pd.read_csv(out_csv)
        except Exception:
            df_old = pd.DataFrame()
    else:
        df_old = pd.DataFrame()

    df_all = pd.concat([df_old, df_new], ignore_index=True)

    # Drop exact dupes first
    df_all = df_all.drop_duplicates()

    # Dedupe by keys
    if dedupe_keys and all(k in df_all.columns for k in dedupe_keys):
        df_all = df_all.drop_duplicates(subset=dedupe_keys, keep="last")

    if sort_cols and all(c in df_all.columns for c in sort_cols):
        df_all = df_all.sort_values(sort_cols)

    df_all.to_csv(out_csv, index=False)
    return df_all


# ---------------- summary parsing ----------------
def _extract_players(summary_json, team_id: str):
    """
    Lightweight player minutes + usage proxy (optional for later).
    """
    players = []
    box = summary_json.get("boxscore", {}) if isinstance(summary_json, dict) else {}
    team_groups = box.get("players", [])
    if not isinstance(team_groups, list):
        return players

    for tg in team_groups:
        t = tg.get("team", {})
        if not isinstance(t, dict):
            continue
        if str(t.get("id", "")) != str(team_id):
            continue

        stat_tables = tg.get("statistics", [])
        if not isinstance(stat_tables, list):
            continue

        for table in stat_tables:
            athletes = table.get("athletes", [])
            if not isinstance(athletes, list):
                continue

            labels = table.get("labels", [])
            if not isinstance(labels, list):
                labels = []

            for a in athletes:
                athlete = a.get("athlete", {}) or {}
                name = athlete.get("displayName") or athlete.get("shortName") or athlete.get("fullName") or "Unknown"

                stats = a.get("stats", [])
                if not isinstance(stats, list) or (labels and len(labels) != len(stats)):
                    continue

                lmap = {str(labels[i]).lower(): stats[i] for i in range(len(labels))} if labels else {}

                def pick(*keys):
                    for k in keys:
                        if k in lmap:
                            return lmap[k]
                    return None

                row = {"player": name}
                row["minutes"] = _to_float(pick("min", "minutes"), np.nan)
                row["points"] = _to_int(pick("pts", "points"), 0)

                fg = pick("fg", "field goals")
                ft = pick("ft", "free throws")
                to = pick("to", "tov", "turnovers")
                reb = pick("reb", "rebs", "rebounds")
                ast = pick("ast", "assists")

                fgm, fga = _parse_made_attempt(fg) if isinstance(fg, str) else (0, 0)
                ftm, fta = _parse_made_attempt(ft) if isinstance(ft, str) else (0, 0)

                row["fgm"] = fgm
                row["fga"] = fga
                row["ftm"] = ftm
                row["fta"] = fta
                row["tov"] = _to_int(to, 0)
                row["reb"] = _to_int(reb, 0)
                row["ast"] = _to_int(ast, 0)
                row["usage_proxy"] = row["fga"] + 0.44 * row["fta"] + row["tov"]

                players.append(row)

    return players


def fetch_and_parse_espn_summary(event_id: str, timeout: int = 25):
    """
    Fetch ESPN summary JSON for a game, parse team box score metrics for both teams.
    """
    url = ESPN_SUMMARY_URL.format(event_id=event_id)
    r = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
    r.raise_for_status()
    data = r.json()

    header = data.get("header", {}) if isinstance(data, dict) else {}
    competitions = header.get("competitions", []) if isinstance(header, dict) else []
    comp0 = competitions[0] if isinstance(competitions, list) and competitions else {}

    game_date = comp0.get("date")  # ISO datetime string
    venue = None
    try:
        venue = (comp0.get("venue", {}) or {}).get("fullName")
    except Exception:
        venue = None

    box = data.get("boxscore", {}) if isinstance(data, dict) else {}
    teams = box.get("teams", [])
    if not isinstance(teams, list) or len(teams) < 2:
        raise ValueError("Unexpected ESPN summary format: boxscore.teams missing or too short")

    # Determine home/away by team id via header competitors (most reliable)
    home_team_id = None
    away_team_id = None
    competitors = comp0.get("competitors", []) if isinstance(comp0, dict) else []
    if isinstance(competitors, list) and len(competitors) >= 2:
        for c in competitors:
            if c.get("homeAway") == "home":
                home_team_id = str((c.get("team") or {}).get("id", ""))
            elif c.get("homeAway") == "away":
                away_team_id = str((c.get("team") or {}).get("id", ""))

    def parse_team(team_entry):
        t = team_entry.get("team", {}) or {}
        tid = str(t.get("id", ""))
        name = t.get("displayName") or t.get("shortDisplayName") or t.get("name") or "Unknown"

        stats_list = team_entry.get("teamStats", [])
        smap = _stat_map(stats_list)

        # Shooting lines
        fgm, fga = _parse_made_attempt(smap.get("fieldGoals", ""))
        if fga == 0:
            fga = _to_int(smap.get("fieldGoalsAttempted"), 0)
            fgm = _to_int(smap.get("fieldGoalsMade"), 0)

        tpm, tpa = _parse_made_attempt(smap.get("threePointFieldGoals", ""))
        if tpa == 0:
            tpa = _to_int(smap.get("threePointFieldGoalsAttempted"), 0)
            tpm = _to_int(smap.get("threePointFieldGoalsMade"), 0)

        ftm, fta = _parse_made_attempt(smap.get("freeThrows", ""))
        if fta == 0:
            fta = _to_int(smap.get("freeThrowsAttempted"), 0)
            ftm = _to_int(smap.get("freeThrowsMade"), 0)

        # Other stats
        tov = _to_int(smap.get("turnovers"), 0)
        orb = _to_int(smap.get("reboundsOffensive"), 0)
        drb = _to_int(smap.get("reboundsDefensive"), 0)
        reb = _to_int(smap.get("rebounds"), orb + drb)

        # Derived metrics
        efg = _safe_div((fgm + 0.5 * tpm), fga, np.nan)
        ftr = _safe_div(fta, fga, np.nan)
        threepar = _safe_div(tpa, fga, np.nan)
        poss = _estimate_possessions(fga, fta, tov, orb)

        return {
            "team_id": tid,
            "team": name,
            "fgm": fgm, "fga": fga,
            "tpm": tpm, "tpa": tpa,
            "ftm": ftm, "fta": fta,
            "tov": tov,
            "orb": orb, "drb": drb, "reb": reb,
            "efg": float(efg) if pd.notna(efg) else np.nan,
            "ftr": float(ftr) if pd.notna(ftr) else np.nan,
            "3par": float(threepar) if pd.notna(threepar) else np.nan,
            "poss": float(poss),
        }

    parsed = [parse_team(te) for te in teams]

    # Map to home/away
    if home_team_id and away_team_id:
        home_row = next((x for x in parsed if x["team_id"] == home_team_id), None)
        away_row = next((x for x in parsed if x["team_id"] == away_team_id), None)
        if home_row is None or away_row is None:
            home_row, away_row = parsed[0], parsed[1]
    else:
        home_row, away_row = parsed[0], parsed[1]

    # Opponent-dependent metrics
    home_row["orb_pct"] = _safe_div(home_row["orb"], (home_row["orb"] + away_row["drb"]), np.nan)
    away_row["orb_pct"] = _safe_div(away_row["orb"], (away_row["orb"] + home_row["drb"]), np.nan)

    home_row["drb_pct"] = _safe_div(home_row["drb"], (home_row["drb"] + away_row["orb"]), np.nan)
    away_row["drb_pct"] = _safe_div(away_row["drb"], (away_row["drb"] + home_row["orb"]), np.nan)

    home_row["tov_pct"] = _safe_div(home_row["tov"], home_row["poss"], np.nan)
    away_row["tov_pct"] = _safe_div(away_row["tov"], away_row["poss"], np.nan)

    # Optional players
    players_home = _extract_players(data, home_row["team_id"])
    players_away = _extract_players(data, away_row["team_id"])

    return {
        "event_id": str(event_id),
        "game_date": game_date,
        "venue": venue,
        "home": home_row,
        "away": away_row,
        "players_home": players_home,
        "players_away": players_away,
    }


def summary_to_team_rows(parsed_summary: dict):
    """
    Flatten parsed_summary into 2 per-team rows for CSV storage.
    """
    event_id = parsed_summary.get("event_id")
    game_date = parsed_summary.get("game_date")
    venue = parsed_summary.get("venue")

    home = parsed_summary["home"].copy()
    away = parsed_summary["away"].copy()

    for row in (home, away):
        row["event_id"] = event_id
        row["game_date"] = game_date
        row["venue"] = venue

    home["opponent"] = away["team"]
    away["opponent"] = home["team"]

    home["home_away"] = "home"
    away["home_away"] = "away"

    return home, away


# ---------------- scoreboard ----------------
def fetch_scoreboard_json(date_yyyymmdd: str, timeout: int = 25):
    url = ESPN_SCOREBOARD_URL.format(date=date_yyyymmdd)
    r = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_scoreboard_games(date_yyyymmdd: str, timeout: int = 25):
    data = fetch_scoreboard_json(date_yyyymmdd, timeout=timeout)

    rows = []
    for e in data.get("events", []) or []:
        game_id = e.get("id")
        competitions = e.get("competitions") or []
        comp = competitions[0] if competitions else {}
        competitors = comp.get("competitors") or []

        if not game_id or len(competitors) < 2:
            continue

        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        if not home or not away:
            continue

        status_row = _extract_status_from_comp(comp)

        home_team = ((home.get("team") or {}).get("displayName")) or None
        away_team = ((away.get("team") or {}).get("displayName")) or None

        home_score = _to_int(home.get("score"), 0)
        away_score = _to_int(away.get("score"), 0)
        home_win = home.get("winner")

        rows.append({
            "date": date_yyyymmdd,
            "game_id": str(game_id),
            "home_team": home_team,
            "away_team": away_team,
            "home_score": home_score,
            "away_score": away_score,
            "home_win": home_win,
            **status_row,
        })

    return rows


def fetch_scoreboard_event_rows(date_yyyymmdd: str, timeout: int = 25):
    """
    Return event rows with status info (used to decide which games to summary-fetch).
    """
    data = fetch_scoreboard_json(date_yyyymmdd, timeout=timeout)
    out = []

    for e in data.get("events", []) or []:
        eid = e.get("id")
        comp = (e.get("competitions") or [{}])[0]
        competitors = comp.get("competitors") or []
        if len(competitors) < 2 or not eid:
            continue

        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        if not home or not away:
            continue

        status_row = _extract_status_from_comp(comp)

        out.append({
            "event_id": str(eid),
            "scoreboard_date": date_yyyymmdd,
            "home_team": (home.get("team") or {}).get("displayName"),
            "away_team": (away.get("team") or {}).get("displayName"),
            **status_row,
        })

    return out


# ---------------- builders ----------------
def build_espn_games_csv(days_back=7, out_csv="espn_games.csv", append_forever=True, verbose=True):
    now_pst = datetime.now(PST_TZ)
    all_rows = []

    for i in range(days_back):
        d = (now_pst - timedelta(days=i)).strftime("%Y%m%d")
        rows = fetch_scoreboard_games(d)
        all_rows.extend(rows)
        if verbose:
            print(f"{d}: {len(rows)} games")

    df_new = pd.DataFrame(all_rows)
    if not df_new.empty:
        df_new = df_new.drop_duplicates(subset=["game_id"]).sort_values(["date", "game_id"])

    if append_forever:
        df_all = _append_forever(
            df_new=df_new,
            out_csv=out_csv,
            dedupe_keys=["game_id"],
            sort_cols=["date", "game_id"],
        )
        if verbose:
            print(f"{out_csv} written (append): {len(df_all)} rows")
        return df_all

    df_new.to_csv(out_csv, index=False)
    if verbose:
        print(f"{out_csv} written: {len(df_new)} rows")
    return df_new


def build_team_game_logs_csv(days_back=7, out_csv="espn_team_game_logs.csv", append_forever=True, verbose=True):
    """
    Builds espn_team_game_logs.csv as a season-to-date append-only file.
    One row per TEAM per GAME, for FINAL games only.
    Includes status columns so you can debug schedule/in-progress.
    """
    now_pst = datetime.now(PST_TZ)

    rows = []
    games_checked = 0
    final_games = 0
    games_processed = 0
    skipped_empty_box = 0
    failures = 0

    for i in range(days_back):
        d = (now_pst - timedelta(days=i)).strftime("%Y%m%d")
        events = fetch_scoreboard_event_rows(d)
        games_checked += len(events)

        finals = [ev for ev in events if _is_final_status(ev)]
        final_games += len(finals)

        if verbose:
            print(f"📅 {d}: {len(events)} games, {len(finals)} final")

        for ev in finals:
            try:
                parsed = fetch_and_parse_espn_summary(ev["event_id"])

                # Extra guard: sometimes summary exists but boxscore stats still empty.
                # If we detect that, skip.
                home_row, away_row = summary_to_team_rows(parsed)

                # If both teams have 0 FGA and 0 FTA and 0 TOV, this is almost certainly empty.
                def looks_empty(r):
                    return (_to_int(r.get("fga"), 0) == 0 and _to_int(r.get("fta"), 0) == 0 and _to_int(r.get("tov"), 0) == 0)

                if looks_empty(home_row) and looks_empty(away_row):
                    skipped_empty_box += 1
                    continue

                # Attach status columns to each team row
                for r in (home_row, away_row):
                    r["status_name"] = ev.get("status_name")
                    r["status_state"] = ev.get("status_state")
                    r["status_completed"] = ev.get("status_completed")
                    r["status_detail"] = ev.get("status_detail")
                    r["status_short"] = ev.get("status_short")

                rows.append(home_row)
                rows.append(away_row)
                games_processed += 1

            except Exception as e:
                failures += 1
                if verbose:
                    print("FAILED", ev["event_id"], str(e)[:180])

    df_new = pd.DataFrame(rows)

    keep = [
        "team_id","team","opponent","home_away","event_id","game_date","venue",
        "status_name","status_state","status_completed","status_detail","status_short",
        "fgm","fga","tpm","tpa","ftm","fta","tov","orb","drb","reb",
        "efg","3par","ftr","orb_pct","drb_pct","tov_pct","poss"
    ]
    if not df_new.empty:
        df_new = df_new[[c for c in keep if c in df_new.columns]]

    if append_forever:
        # Unique team-game row key: (event_id, team_id) is stable.
        df_all = _append_forever(
            df_new=df_new,
            out_csv=out_csv,
            dedupe_keys=["event_id", "team_id"],
            sort_cols=["game_date", "event_id", "home_away"],
        )
    else:
        df_new.to_csv(out_csv, index=False)
        df_all = df_new

    if verbose:
        print("\n✅ DONE")
        print("Games checked:", games_checked)
        print("Final games:", final_games)
        print("Games processed:", games_processed)
        print("Skipped empty boxscores:", skipped_empty_box)
        print("Rows written:", len(df_all))
        print("Failures:", failures)
        print("Output:", out_csv)

    return df_all


# ---------------- main ----------------
if __name__ == "__main__":
    # One-time backfill: set DAYS_BACK=80 in your GitHub Action env (recommended),
    # or just edit this number to 80 for a single run.
    DAYS_BACK = int(os.getenv("DAYS_BACK", "7"))

    build_espn_games_csv(days_back=DAYS_BACK, append_forever=True, verbose=True)
    build_team_game_logs_csv(days_back=DAYS_BACK, append_forever=True, verbose=True)
