"""
ESPN JSON Parsers
Transform ESPN API responses into structured data.
Pure transformation logic - no I/O or side effects.
"""

import re
import os
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

from espn_config import PARSE_VERSION, SOURCE_NAME, TZ_PST
from data_utils import (
    _to_int,
    _to_float,
    _parse_made_attempt,
    _safe_div,
    _estimate_possessions,
    _utc_now_iso,
)


def _stat_map(team_stats_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Convert ESPN team stats list to name->value dictionary.
    
    Args:
        team_stats_list: List of stat dictionaries from ESPN API
        
    Returns:
        Dictionary mapping stat name to displayValue
    """
    out = {}
    if not isinstance(team_stats_list, list):
        return out
    for item in team_stats_list:
        if not isinstance(item, dict):
            continue
        dv = item.get("displayValue")
        if dv is None:
            dv = item.get("value")

        for k in ("name", "abbreviation", "shortDisplayName", "displayName", "label"):
            name = item.get(k)
            if name:
                out[str(name)] = dv
    return out


def _extract_odds_from_comp(comp: dict) -> dict:
    """
    Best-effort extraction of market lines from ESPN scoreboard competition payload.
    ESPN schema varies by sport/event; expect missing fields often.
    
    Args:
        comp: Competition dictionary from scoreboard API
        
    Returns:
        Dictionary with keys: odds_provider, odds_details, spread, over_under,
        home_moneyline, away_moneyline (values may be None)
    """
    out = {
        "odds_provider": None,
        "odds_details": None,      # often like "UNC -3.5" or similar
        "spread": None,            # numeric if available
        "over_under": None,        # numeric if available
        "home_moneyline": None,    # int if available
        "away_moneyline": None,    # int if available
    }

    odds_list = comp.get("odds") or []
    if not isinstance(odds_list, list) or len(odds_list) == 0:
        return out

    o = odds_list[0] if isinstance(odds_list[0], dict) else {}
    if not o:
        return out

    provider = o.get("provider") or {}
    if isinstance(provider, dict):
        out["odds_provider"] = provider.get("name") or provider.get("id")

    out["odds_details"] = o.get("details") or o.get("displayValue")

    # Totals
    ou = o.get("overUnder")
    if ou is not None:
        try:
            out["over_under"] = float(ou)
        except Exception:
            pass

    # Spread
    sp = o.get("spread")
    if sp is not None:
        try:
            out["spread"] = float(sp)
        except Exception:
            pass

    # Moneylines (field names vary, so check a few common ones)
    for k in ["homeTeamOdds", "homeOdds", "homeMoneyLine", "homeMoneyline"]:
        v = o.get(k)
        if isinstance(v, dict):
            v = v.get("moneyLine") or v.get("moneyline") or v.get("american") or v.get("value")
        if v is not None:
            try:
                out["home_moneyline"] = int(float(v))
                break
            except Exception:
                pass

    for k in ["awayTeamOdds", "awayOdds", "awayMoneyLine", "awayMoneyline"]:
        v = o.get(k)
        if isinstance(v, dict):
            v = v.get("moneyLine") or v.get("moneyline") or v.get("american") or v.get("value")
        if v is not None:
            try:
                out["away_moneyline"] = int(float(v))
                break
            except Exception:
                pass

    return out


def _iso_to_game_dates(game_datetime_utc: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Convert ISO timestamp to game dates in local (PST) and UTC.
    
    Args:
        game_datetime_utc: ISO timestamp string
        
    Returns:
        Tuple of (game_date_local, game_date_utc) in YYYY-MM-DD format
        Both may be None if parsing fails
    """
    if not game_datetime_utc:
        return (None, None)
    dt = pd.to_datetime(game_datetime_utc, utc=True, errors="coerce")
    if pd.isna(dt):
        return (None, None)
    game_date_utc = dt.date().isoformat()
    try:
        dt_pst = dt.tz_convert(TZ_PST)
        game_date_local = dt_pst.date().isoformat()
    except Exception:
        game_date_local = None
    return (game_date_local, game_date_utc)


def parse_scoreboard_event(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Parse a single event from ESPN scoreboard API.
    
    Args:
        event: Event dictionary from scoreboard response
        
    Returns:
        Dictionary with game data, or None if event is invalid/incomplete
    """
    game_id = event.get("id")
    if not game_id:
        return None

    competitions = event.get("competitions") or []
    if not competitions:
        return None

    comp = competitions[0] if competitions else None
    if not isinstance(comp, dict):
        return None

    competitors = comp.get("competitors") or []
    if len(competitors) < 2:
        return None

    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    if not home or not away:
        return None

    status = comp.get("status") or {}
    stype = (status.get("type") or {})
    completed = bool(stype.get("completed"))
    state = stype.get("state") or ""
    detail = stype.get("detail") or ""
    short_detail = stype.get("shortDetail") or ""
    status_desc = stype.get("description") or ""

    game_dt = comp.get("date") or event.get("date")
    venue = (comp.get("venue") or {}).get("fullName") if isinstance(comp.get("venue"), dict) else None

    home_team = (home.get("team") or {}).get("displayName")
    away_team = (away.get("team") or {}).get("displayName")

    home_score = _to_int(home.get("score"), 0)
    away_score = _to_int(away.get("score"), 0)

    home_win = home.get("winner")
    away_win = away.get("winner")

    # Market / Vegas lines (best-effort from ESPN scoreboard)
    odds_list = comp.get("odds") or []
    odds0 = odds_list[0] if (isinstance(odds_list, list) and len(odds_list) > 0 and isinstance(odds_list[0], dict)) else {}

    provider = odds0.get("provider") or {}
    market_provider = provider.get("name") if isinstance(provider, dict) else None

    market_details = odds0.get("details")  # often like "DUKE -6.5"
    market_over_under = _to_float(odds0.get("overUnder"), np.nan)

    # Spread is not always a clean numeric field. ESPN often uses "details" as the reliable string.
    market_spread = _to_float(odds0.get("spread"), np.nan)

    home_odds = odds0.get("homeTeamOdds") or {}
    away_odds = odds0.get("awayTeamOdds") or {}

    market_home_ml = _to_int(home_odds.get("moneyLine"), np.nan) if isinstance(home_odds, dict) else np.nan
    market_away_ml = _to_int(away_odds.get("moneyLine"), np.nan) if isinstance(away_odds, dict) else np.nan

    return {
        "game_id": str(game_id),
        "game_datetime_utc": game_dt,
        "venue": venue,
        "home_team": home_team,
        "away_team": away_team,
        "home_score": home_score,
        "away_score": away_score,
        "home_win": home_win,
        "away_win": away_win,
        "completed": completed,
        "state": state,
        "status_desc": status_desc,
        "status_detail": detail or short_detail,
        "pulled_at_utc": _utc_now_iso(),
        "source": SOURCE_NAME,
        # Market fields
        "market_provider": market_provider,
        "market_details": market_details,
        "market_spread": market_spread,
        "market_total": market_over_under,
        "market_home_ml": market_home_ml,
        "market_away_ml": market_away_ml,
    }


def _extract_players(summary_json: Dict[str, Any], team_id: str) -> List[Dict[str, Any]]:
    """
    Extract player box score data for a specific team from summary JSON.
    
    Args:
        summary_json: ESPN summary API response
        team_id: Team ID to extract players for
        
    Returns:
        List of player dictionaries with box score stats
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
            keys = table.get("keys", [])
            if not isinstance(keys, list):
                keys = []

            for a in athletes:
                athlete = a.get("athlete", {}) or {}
                name = athlete.get("displayName") or athlete.get("shortName") or athlete.get("fullName") or "Unknown"
                athlete_id = athlete.get("id")
                starter = a.get("starter")

                stats = a.get("stats", [])
                if not isinstance(stats, list):
                    continue

                headers = labels if labels else keys
                n = min(len(headers), len(stats))
                lmap = {str(headers[i]).lower().strip(): stats[i] for i in range(n)} if n > 0 else {}

                def pick(*keys):
                    for k in keys:
                        kk = str(k).lower().strip()
                        if kk in lmap:
                            return lmap[kk]
                    return None

                row = {"player": name}
                if athlete_id:
                    row["athlete_id"] = str(athlete_id)
                if starter is not None:
                    row["starter"] = int(starter or 0)

                row["minutes"] = _to_float(pick("min", "minutes"), np.nan)
                row["points"] = _to_int(pick("pts", "points"), 0)

                fg = pick("fg", "field goals")
                three = pick("3pt", "3p", "3fg", "3-point fg", "3pt fg")
                ft = pick("ft", "free throws")
                to = pick("to", "tov", "turnovers")
                oreb = pick("oreb", "off reb", "offensive rebounds", "offensive reb")
                dreb = pick("dreb", "def reb", "defensive rebounds", "defensive reb")
                reb = pick("reb", "rebs", "rebounds", "total rebounds")
                ast = pick("ast", "assists")
                stl = pick("stl", "steals")
                blk = pick("blk", "blocks")
                pf = pick("pf", "fouls", "personal fouls", "personal")

                fgm, fga = _parse_made_attempt(fg) if isinstance(fg, str) else (0, 0)
                tpm, tpa = _parse_made_attempt(three) if isinstance(three, str) else (0, 0)
                ftm, fta = _parse_made_attempt(ft) if isinstance(ft, str) else (0, 0)

                row["fgm"] = fgm
                row["fga"] = fga
                row["tpm"] = tpm
                row["tpa"] = tpa
                row["ftm"] = ftm
                row["fta"] = fta
                row["tov"] = _to_int(to, 0)
                row["orb"] = _to_int(oreb, 0)
                row["drb"] = _to_int(dreb, 0)
                row["reb"] = _to_int(reb, row["orb"] + row["drb"])
                row["ast"] = _to_int(ast, 0)
                row["stl"] = _to_int(stl, 0)
                row["blk"] = _to_int(blk, 0)
                row["pf"] = _to_int(pf, 0)

                row["usage_proxy"] = row["fga"] + 0.44 * row["fta"] + row["tov"]
                players.append(row)

    return players


def _sum_player_totals(players: List[Dict[str, Any]]) -> Optional[Dict[str, int]]:
    """
    Sum player stats to team-level totals.
    Used as fallback when team stats are missing.
    
    Args:
        players: List of player dictionaries
        
    Returns:
        Dictionary with team totals, or None if no players
    """
    if not players:
        return None
    keys = ["fgm", "fga", "tpm", "tpa", "ftm", "fta", "tov", "orb", "drb", "reb"]
    totals = {k: 0 for k in keys}
    for p in players:
        for k in keys:
            totals[k] += _to_int(p.get(k), 0)
    return totals


def parse_team_from_summary(team_entry: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse team box score data from summary JSON team entry.
    
    Args:
        team_entry: Team dictionary from boxscore.teams array
        
    Returns:
        Dictionary with team box score stats
    """
    t = team_entry.get("team", {}) or {}
    tid = str(t.get("id", ""))
    name = t.get("displayName") or t.get("shortDisplayName") or t.get("name") or "Unknown"

    # Debug: Check what fields are actually present
    stats_list = team_entry.get("teamStats") or team_entry.get("statistics") or []
    
    # DEBUG: Log if stats are missing (can be enabled via env var)
    if os.getenv("ESPN_DEBUG_MISSING_STATS") == "1":
        if not stats_list:
            print(f"[DEBUG] No teamStats or statistics found for team {name} (id={tid})")
            print(f"[DEBUG] Available keys in team_entry: {list(team_entry.keys())}")
    
    smap = _stat_map(stats_list)
    normalized_smap = {
        str(k).lower().replace(" ", "").replace("_", "").replace("-", ""): v
        for k, v in smap.items()
    }

    def pick(*keys: str) -> Any:
        """Return first matching stat value for any provided key alias."""
        for k in keys:
            if k in smap:
                return smap.get(k)
            lk = str(k).lower().replace(" ", "").replace("_", "").replace("-", "")
            if lk in normalized_smap:
                return normalized_smap.get(lk)
        return None

    fgm, fga = _parse_made_attempt(pick("fieldGoals", "fg", "fieldgoals", "field goals", "fgm-a") or "")
    if fga == 0:
        fga = _to_int(pick("fieldGoalsAttempted", "fga"), 0)
    if fgm == 0:
        fgm = _to_int(pick("fieldGoalsMade", "fgm"), 0)

    tpm, tpa = _parse_made_attempt(
        pick("threePointFieldGoals", "3pt", "3ptfg", "threepointfieldgoals", "3fg", "3pm-a") or ""
    )
    if tpa == 0:
        tpa = _to_int(pick("threePointFieldGoalsAttempted", "3pta", "tpa"), 0)
    if tpm == 0:
        tpm = _to_int(pick("threePointFieldGoalsMade", "3ptm", "tpm"), 0)

    ftm, fta = _parse_made_attempt(pick("freeThrows", "ft", "freethrows", "ftm-a") or "")
    if fta == 0:
        fta = _to_int(pick("freeThrowsAttempted", "fta"), 0)
    if ftm == 0:
        ftm = _to_int(pick("freeThrowsMade", "ftm"), 0)

    tov = _to_int(pick("turnovers", "to", "tov"), 0)
    orb = _to_int(pick("reboundsOffensive", "offensiveRebounds", "oreb", "offreb"), 0)
    drb = _to_int(pick("reboundsDefensive", "defensiveRebounds", "dreb", "defreb"), 0)
    reb = _to_int(pick("rebounds", "reb", "totalRebounds"), orb + drb)

    return {
        "team_id": tid,
        "team": name,
        "fgm": fgm, "fga": fga,
        "tpm": tpm, "tpa": tpa,
        "ftm": ftm, "fta": fta,
        "tov": tov,
        "orb": orb, "drb": drb, "reb": reb,
    }


def add_independent_derivatives(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Add derived metrics that don't require opponent data.
    Includes: eFG%, FTr, 3PAr, shooting percentages, possessions.
    
    Args:
        row: Team box score dictionary
        
    Returns:
        Same dictionary with derived fields added
    """
    fgm = _to_int(row.get("fgm"), 0)
    fga = _to_int(row.get("fga"), 0)
    tpm = _to_int(row.get("tpm"), 0)
    tpa = _to_int(row.get("tpa"), 0)
    ftm = _to_int(row.get("ftm"), 0)
    fta = _to_int(row.get("fta"), 0)
    tov = _to_int(row.get("tov"), 0)
    orb = _to_int(row.get("orb"), 0)

    efg = _safe_div((fgm + 0.5 * tpm), fga, np.nan)
    ftr = _safe_div(fta, fga, np.nan)
    threepar = _safe_div(tpa, fga, np.nan)
    three_pct = _safe_div(tpm, tpa, np.nan)
    ft_pct = _safe_div(ftm, fta, np.nan)
    poss = _estimate_possessions(fga, fta, tov, orb)
    # True Shooting Percentage: TS% = PTS / (2 * (FGA + 0.44 * FTA))
    pts = _to_int(row.get("points_for"), 0)
    ts_pct = _safe_div(pts, (2 * (fga + 0.44 * fta)), np.nan)

    row["efg"] = float(efg) if pd.notna(efg) else np.nan
    row["ftr"] = float(ftr) if pd.notna(ftr) else np.nan
    row["3par"] = float(threepar) if pd.notna(threepar) else np.nan
    row["3p_pct"] = float(three_pct) if pd.notna(three_pct) else np.nan
    row["ft_pct"] = float(ft_pct) if pd.notna(ft_pct) else np.nan
    row["ts_pct"] = float(ts_pct) if pd.notna(ts_pct) else np.nan
    row["poss"] = float(poss) if pd.notna(poss) else np.nan
    
    # Mark source for audit trail
    row["poss_source"] = "derived"
    row["efg_source"] = "derived"
    
    return row


def parse_summary_json(summary_json: Dict[str, Any], event_id: str) -> Dict[str, Any]:
    """
    Parse ESPN summary JSON into structured game data.
    
    Args:
        summary_json: Raw ESPN summary API response
        event_id: Event ID for this game
        
    Returns:
        Dictionary with keys: event_id, game_datetime_utc, venue, completed, 
        home (team dict), away (team dict), players_home, players_away
        
    Raises:
        ValueError: If boxscore structure is unexpected
    """
    DEBUG_MISSING_STATS = os.getenv("ESPN_DEBUG_MISSING_STATS") == "1"
    
    header = summary_json.get("header", {}) if isinstance(summary_json, dict) else {}
    competitions = header.get("competitions", []) if isinstance(header, dict) else []
    comp0 = competitions[0] if isinstance(competitions, list) and competitions else {}

    game_datetime_utc = comp0.get("date")
    venue = None
    try:
        venue = (comp0.get("venue") or {}).get("fullName")
    except Exception:
        venue = None

    status = comp0.get("status") or {}
    stype = (status.get("type") or {})
    completed = bool(stype.get("completed"))
    state = stype.get("state") or ""
    status_desc = stype.get("description") or ""
    status_detail = stype.get("detail") or stype.get("shortDetail") or ""

    neutral_site = bool(comp0.get("neutralSite")) if isinstance(comp0, dict) else False

    # OT detection
    sd = str(status_detail or "").upper()
    is_ot = 1 if "OT" in sd else 0
    num_ot = 0
    if "OT" in sd:
        num_ot = 1
        m = re.search(r"(\d+)\s*OT", sd)
        if m:
            try:
                num_ot = int(m.group(1))
            except Exception:
                num_ot = 1

    # Extract team IDs and scores from competitors
    home_team_id = None
    away_team_id = None
    home_points = None
    away_points = None
    competitors = comp0.get("competitors", []) if isinstance(comp0, dict) else []
    if isinstance(competitors, list) and len(competitors) >= 2:
        for c in competitors:
            ha = c.get("homeAway")
            tid = str((c.get("team") or {}).get("id", ""))
            pts = _to_int(c.get("score"), 0)
            if ha == "home":
                home_team_id = tid
                home_points = pts
            elif ha == "away":
                away_team_id = tid
                away_points = pts

    # Parse team box scores
    box = summary_json.get("boxscore", {}) if isinstance(summary_json, dict) else {}
    teams = box.get("teams", [])
    if not isinstance(teams, list) or len(teams) < 2:
        raise ValueError("Unexpected ESPN summary format: boxscore.teams missing or too short")

    parsed = [parse_team_from_summary(te) for te in teams]

    # Match by team_id if available, otherwise use order
    if home_team_id and away_team_id:
        home_row = next((x for x in parsed if x["team_id"] == home_team_id), None)
        away_row = next((x for x in parsed if x["team_id"] == away_team_id), None)
        if home_row is None or away_row is None:
            home_row, away_row = parsed[0], parsed[1]
    else:
        home_row, away_row = parsed[0], parsed[1]

    # Extract player data
    players_home = _extract_players(summary_json, home_row["team_id"])
    players_away = _extract_players(summary_json, away_row["team_id"])

    # Player fallback: if team stats missing but player stats exist, sum them
    def apply_player_fallback(row, players):
        if not completed:
            return row, False
        if _to_int(row.get("fga"), 0) > 0:
            return row, False
        totals = _sum_player_totals(players)
        if not totals:
            # Log when no player totals available for completed game with missing team stats
            if DEBUG_MISSING_STATS:
                print(f"[DEBUG] No player totals for team {row.get('team')}, {len(players)} players")
            return row, False
        changed = False
        for k, v in totals.items():
            if _to_int(row.get(k), 0) == 0 and _to_int(v, 0) > 0:
                row[k] = _to_int(v, 0)
                changed = True
        if changed and DEBUG_MISSING_STATS:
            print(f"[DEBUG] Applied player fallback for team {row.get('team')}: fga={row.get('fga')}")
        return row, changed

    home_row, home_fallback_changed = apply_player_fallback(home_row, players_home)
    away_row, away_fallback_changed = apply_player_fallback(away_row, players_away)

    # Add independent derivatives
    home_row = add_independent_derivatives(home_row)
    away_row = add_independent_derivatives(away_row)

    # Add opponent-dependent metrics
    home_row["orb_pct"] = _safe_div(home_row["orb"], (home_row["orb"] + away_row["drb"]), np.nan)
    away_row["orb_pct"] = _safe_div(away_row["orb"], (away_row["orb"] + home_row["drb"]), np.nan)

    home_row["drb_pct"] = _safe_div(home_row["drb"], (home_row["drb"] + away_row["orb"]), np.nan)
    away_row["drb_pct"] = _safe_div(away_row["drb"], (away_row["drb"] + home_row["orb"]), np.nan)

    home_row["tov_pct"] = _safe_div(home_row["tov"], home_row["poss"], np.nan)
    away_row["tov_pct"] = _safe_div(away_row["tov"], away_row["poss"], np.nan)

    # Add points and margin
    home_row["points_for"] = _to_int(home_points, 0)
    away_row["points_for"] = _to_int(away_points, 0)
    home_row["points_against"] = away_row["points_for"]
    away_row["points_against"] = home_row["points_for"]
    home_row["margin"] = home_row["points_for"] - home_row["points_against"]
    away_row["margin"] = away_row["points_for"] - away_row["points_against"]

    # Mark base totals source
    home_row["base_totals_source"] = "player_sum" if (completed and home_fallback_changed) else "team_stats"
    away_row["base_totals_source"] = "player_sum" if (completed and away_fallback_changed) else "team_stats"

    # DEBUG: Warn if completed game has zero box scores
    if DEBUG_MISSING_STATS and completed:
        if _to_int(home_row.get("fga"), 0) == 0 or _to_int(away_row.get("fga"), 0) == 0:
            print(f"[WARN] Completed game {event_id} has zero box scores!")
            print(f"  Home FGA: {home_row.get('fga')}, Away FGA: {away_row.get('fga')}")
            print(f"  Home players: {len(players_home)}, Away players: {len(players_away)}")
            print(f"  Fallback used: Home={home_fallback_changed}, Away={away_fallback_changed}")

    return {
        "event_id": str(event_id),
        "game_datetime_utc": game_datetime_utc,
        "venue": venue,
        "completed": completed,
        "state": state,
        "status_desc": status_desc,
        "status_detail": status_detail,
        "neutral_site": int(neutral_site),
        "is_ot": int(is_ot),
        "num_ot": int(num_ot),
        "home": home_row,
        "away": away_row,
        "players_home": players_home,
        "players_away": players_away,
    }


def summary_to_team_rows(parsed_summary: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Convert parsed summary into two team-game rows (home and away).
    
    Args:
        parsed_summary: Output from parse_summary_json()
        
    Returns:
        Tuple of (home_row, away_row) dictionaries ready for DataFrame
    """
    event_id = str(parsed_summary.get("event_id"))
    game_dt = parsed_summary.get("game_datetime_utc")
    venue = parsed_summary.get("venue")

    game_date_local, game_date_utc = _iso_to_game_dates(game_dt)

    meta = {
        "event_id": event_id,
        "game_datetime_utc": game_dt,
        "game_date": game_date_local,
        "game_date_utc": game_date_utc,
        "venue": venue,
        "completed": parsed_summary.get("completed"),
        "state": parsed_summary.get("state"),
        "status_desc": parsed_summary.get("status_desc"),
        "status_detail": parsed_summary.get("status_detail"),
        "neutral_site": parsed_summary.get("neutral_site", 0),
        "is_ot": parsed_summary.get("is_ot", 0),
        "num_ot": parsed_summary.get("num_ot", 0),
        "pulled_at_utc": _utc_now_iso(),
        "source": SOURCE_NAME,
        "parse_version": PARSE_VERSION,
    }

    home = parsed_summary["home"].copy()
    away = parsed_summary["away"].copy()

    for row in (home, away):
        row.update(meta)
        row["event_id"] = str(row.get("event_id", event_id))
        row["team_id"] = str(row.get("team_id", ""))

    home["opponent"] = away["team"]
    away["opponent"] = home["team"]

    home["home_away"] = "home"
    away["home_away"] = "away"

    home["home_team"] = home["team"]
    home["away_team"] = away["team"]
    away["home_team"] = home["team"]
    away["away_team"] = away["team"]

    return home, away
