#!/usr/bin/env python3
"""
ESPN CBB Boxscore + Feature Builder (Season-to-date, append forever)
Modular architecture - orchestration layer only.

Outputs:
- espn_games.csv                     (scoreboard snapshot, one row per game, append+dedupe)
- espn_team_game_logs.csv            (team-game rows + per-game metrics + audit, append+dedupe)
- espn_team_game_features.csv        (pregame rolling features + opponent joins + rest/volatility/style, append+dedupe)
- espn_matchups_model_ready.csv      (one row per game, home/away pregame features + labels, rebuild each run)
- espn_feature_diagnostics.csv       (row-level diagnostics for sparse/NaN fields)
- espn_dq_audit.csv                  (Data Quality Repair Gate audit, per-row reasons + actions)
- espn_player_boxscores.csv          (player box score rows, one row per player per game, append+dedupe)
"""

import time
from datetime import datetime, timedelta

import pandas as pd
import numpy as np

# ---- Configuration ----
from espn_config import (
    # URLs and endpoints
    ESPN_SUMMARY_URL,
    ESPN_SCOREBOARD_URL,
    DEFAULT_HEADERS,

    # Timeouts and retries
    REQUEST_TIMEOUT,
    MAX_RETRIES,
    RETRY_INITIAL_DELAY,
    RETRY_BACKOFF,

    # Pipeline settings
    PARSE_VERSION,
    SOURCE_NAME,
    TZ_PST,
    DEFAULT_DAYS_BACK,
    DRY_RUN,

    # Checkpointing
    CHECKPOINT_FILE,
    CHECKPOINT_EVERY_N_GAMES,
    ERROR_LOG_PATH,

    # Output paths
    OUT_GAMES,
    OUT_TEAM_LOGS,
    OUT_TEAM_FEATURES,
    OUT_MATCHUPS,
    OUT_DIAGNOSTICS,
    OUT_DQ_AUDIT,
    OUT_PLAYER_BOX,

    # Feature flags
    WRITE_DIAGNOSTICS,
    WRITE_DQ_AUDIT,

    # Gates
    GATE_MIN_OPP_JOIN_RATE_FINAL,
    GATE_MIN_POSS_PRESENT_FINAL,
    GATE_MIN_EXPECTED_PRESENT_FINAL,

    # DQRG
    DQRG_ENABLE,
    DQRG_MAX_EVENTS,
    DQRG_REFETCH_ON_FAIL,

    # Validation
    VALID_HOME_AWAY,
)

# ---- Data Utilities ----
from data_utils import (
    _to_int,
    _to_float,
    _safe_div,
    _parse_made_attempt,
    _normalize_home_away_series,
    _normalize_id_series,
    _stable_row_hash,
    _utc_now_iso,
    _completeness_score_row,
    _estimate_possessions,
    _flip_home_away,
)

# ---- HTTP Client ----
from espn_http_client import (
    fetch_with_retry,
    fetch_scoreboard,
    fetch_summary,
)

# ---- Parsers ----
from espn_parsers import (
    parse_scoreboard_event,
    parse_summary_json,
    summary_to_team_rows,
    _extract_players,
    _extract_odds_from_comp,
    _iso_to_game_dates,
)

# ---- File I/O ----
from file_io import (
    log_error,
    write_error_summary,
    load_checkpoint,
    save_checkpoint,
    clear_checkpoint,
    _atomic_csv_write,
    _read_csv_if_exists,
    _ensure_csv_exists,
    _append_dedupe_write,
    verify_dataframe_integrity,
    ensure_all_output_files_exist,
)

# ---- Opponent Merge ----
from opponent_merge import (
    _merge_opponent_rows,
    _drop_bad_event_ids_keep_good,
    validate_opponent_merge,
)

# ---- Metrics Calculator ----
from metrics_calculator import (
    _compute_per_game_advanced_metrics,
    _add_rolling_pack,
    _add_noblow_rollups,
    _add_allowed_forced_pack,
    _time_window_counts_per_team,
)

# ---- Data Quality ----
from data_quality import (
    _dedupe_by_completeness,
    _dqrg_find_issues,
    _dqrg_repair_in_place,
)

# ---- Matchup Builder ----
from matchup_builder import (
    build_matchups_model_ready,
)

# ---- External feature modules ----
from weights import WeightConfig, add_all_base_weights
from plus_and_fit import PlusConfig, CompositeConfig, add_all_plus_and_composites
from cbb_advanced_metrics import add_all_advanced_metrics
from rolling_features import RollingConfig, add_unweighted_rollups

# ---- NEW: SOS + Home/Away dynamic HCA modules ----
from strength_of_schedule import add_sos_features
from home_away_analyzer import add_home_away_features, add_matchup_net_hca


# ============================================================================
# Scoreboard Functions
# ============================================================================

def fetch_scoreboard_games_for_date(date_yyyymmdd: str):
    """Fetch and parse scoreboard for a single date."""
    try:
        data = fetch_scoreboard(date_yyyymmdd)
    except Exception as e:
        log_error("fetch_scoreboard", e, extra={"date": date_yyyymmdd})
        return []

    events = data.get("events") or []
    rows = []
    for e in events:
        parsed = parse_scoreboard_event(e)
        if parsed:
            parsed["date"] = date_yyyymmdd
            parsed["pulled_at_utc"] = _utc_now_iso()
            parsed["source"] = SOURCE_NAME
            rows.append(parsed)
    return rows


def build_espn_games_csv(days_back=DEFAULT_DAYS_BACK, out_csv=OUT_GAMES, verbose=True):
    """
    Build games CSV from scoreboard data.
    Always include today + tomorrow to avoid PST/UTC boundary misses.
    """
    now_pst = datetime.now(TZ_PST)

    # Build date set: past window + today + tomorrow
    date_set = set()
    for i in range(days_back):
        date_set.add((now_pst - timedelta(days=i)).strftime("%Y%m%d"))
    date_set.add(now_pst.strftime("%Y%m%d"))
    date_set.add((now_pst + timedelta(days=1)).strftime("%Y%m%d"))

    all_rows = []
    for d in sorted(date_set, reverse=True):
        rows = fetch_scoreboard_games_for_date(d)
        all_rows.extend(rows)
        if verbose:
            total = len(rows)
            finals = sum(1 for r in rows if r.get("completed"))
            print(f"{d}: {total} games, {finals} final")

    df_new = pd.DataFrame(all_rows)
    if df_new.empty:
        if verbose:
            print("No games returned from scoreboard.")
        return df_new

    df_all = _append_dedupe_write(
        out_csv,
        df_new,
        subset_keys=["game_id"],
        sort_cols=["date", "game_id"],
    )
    if verbose:
        print(f"{out_csv} total rows: {len(df_all)}")

    return df_all


# ============================================================================
# Main Pipeline Orchestrator
# ============================================================================

def run_pipeline(days_back: int = DEFAULT_DAYS_BACK):
    """
    Main pipeline orchestrator.
    All business logic is in dedicated modules - this is just coordination.
    """
    pulled_at = _utc_now_iso()
    print(f"Run started: {pulled_at} | DAYS_BACK={days_back} | PARSE_VERSION={PARSE_VERSION}")

    # Initialize all output files
    ensure_all_output_files_exist()

    # ========================================================================
    # PASS 0: Fetch Scoreboard
    # ========================================================================
    print("\n=== PASS 0: Fetch Scoreboard ===")
    games_df = build_espn_games_csv(days_back=days_back, out_csv=OUT_GAMES, verbose=True)
    if games_df.empty:
        print("No games from scoreboard. Exiting.")
        write_error_summary()
        return

    # Determine run window
    now_pst = datetime.now(TZ_PST)
    window_dates = {(now_pst - timedelta(days=i)).strftime("%Y%m%d") for i in range(days_back)}
    run_window = games_df[games_df["date"].astype(str).isin(window_dates)].copy()
    game_ids = run_window["game_id"].astype(str).unique().tolist()
    print(f"Scoreboard game_ids in run window: {len(game_ids)}")

    # Load checkpoint
    checkpoint = load_checkpoint()
    processed = set(map(str, checkpoint.get("processed_game_ids", [])))

    # ========================================================================
    # PASS 1: Fetch Summaries & Compute Metrics
    # ========================================================================
    print("\n=== PASS 1: Fetch Summaries & Compute Metrics ===")
    team_rows = []
    errors = 0

    for i, gid in enumerate(game_ids, 1):
        if str(gid) in processed:
            continue

        try:
            raw = fetch_summary(gid)
            parsed = parse_summary_json(raw, gid)
            hrow, arow = summary_to_team_rows(parsed)
            team_rows.append(hrow)
            team_rows.append(arow)
            processed.add(str(gid))
        except Exception as e:
            errors += 1
            log_error("summary_parse", e, event_id=str(gid))
            if errors <= 10:
                print(f"[WARN] summary parse failed for event {gid}: {e}")

        # Checkpoint progress
        if i % CHECKPOINT_EVERY_N_GAMES == 0:
            print(f"Parsed {i}/{len(game_ids)} summaries...")
            save_checkpoint({
                "processed_game_ids": list(processed),
                "last_updated_utc": _utc_now_iso(),
                "errors_so_far": errors,
            })

        # Rate limiting for large historical pulls
        if days_back >= 30:
            time.sleep(0.15)

    if not team_rows:
        print("No team rows parsed. Exiting.")
        write_error_summary()
        return

    clear_checkpoint()

    # Compute metrics and apply DQRG
    df_logs_new = pd.DataFrame(team_rows)
    df_logs_new = _compute_per_game_advanced_metrics(df_logs_new)
    df_logs_new, dq_audit_new = _dqrg_repair_in_place(df_logs_new)
    df_logs_new = _dedupe_by_completeness(df_logs_new, keys=["event_id", "team_id"], label="PASS1 logs_new")
    df_logs_new = _drop_bad_event_ids_keep_good(df_logs_new, label="PASS1 logs_new symmetry")

    # Write team logs
    df_logs_all = _append_dedupe_write(
        OUT_TEAM_LOGS,
        df_logs_new.drop(columns=["game_dt"], errors="ignore"),
        subset_keys=["event_id", "team_id"],
        sort_cols=["game_datetime_utc", "event_id", "team_id", "home_away"],
    )
    print(f"{OUT_TEAM_LOGS} total rows: {len(df_logs_all)}")

    # Write DQ audit
    if WRITE_DQ_AUDIT and dq_audit_new is not None and not dq_audit_new.empty:
        _append_dedupe_write(
            OUT_DQ_AUDIT,
            dq_audit_new,
            subset_keys=["event_id", "team_id"],
            sort_cols=["pulled_at_utc", "event_id", "team_id"],
        )
        print(f"{OUT_DQ_AUDIT} appended: {len(dq_audit_new)} rows")

    # ========================================================================
    # PASS 2: Load Historical Logs
    # ========================================================================
    print("\n=== PASS 2: Load Historical Logs ===")
    df = df_logs_all.copy()
    df["event_id"] = _normalize_id_series(df["event_id"])
    df["team_id"] = _normalize_id_series(df["team_id"])
    if "home_away" in df.columns:
        df["home_away"] = _normalize_home_away_series(df["home_away"])
    df["game_dt"] = pd.to_datetime(df["game_datetime_utc"], utc=True, errors="coerce")

    # NEW: game_date string for leak-free feature modules (SOS/HCA)
    df["game_date"] = df["game_dt"].dt.date.astype(str)

    df = df.sort_values(["team_id", "game_dt", "event_id", "home_away"])

    df_clean = df[df["data_ok"] == True].copy()
    print(f"PASS2: {len(df_clean)}/{len(df)} rows with data_ok=True")

    # ========================================================================
    # PASS 3: Rolling Features
    # ========================================================================
    print("\n=== PASS 3: Rolling Features ===")
    df_clean = _add_rolling_pack(df_clean, group_cols=("team_id",), prefix="")
    df_clean = _add_rolling_pack(df_clean, group_cols=("team_id", "home_away"), prefix="ha_")
    df_clean = _add_noblow_rollups(df_clean, group_cols=("team_id",), prefix="")
    df_clean = _add_noblow_rollups(df_clean, group_cols=("team_id", "home_away"), prefix="ha_")
    print(f"PASS3: Rolling features computed on {len(df_clean)} rows")

    # ========================================================================
    # PASS 4: Time-Based Features
    # ========================================================================
    print("\n=== PASS 4: Time-Based Features ===")
    df_clean = _time_window_counts_per_team(df_clean)
    print("PASS4: Time window features added")

    # ========================================================================
    # PASS 5: Opponent Merge & Advanced Features
    # ========================================================================
    print("\n=== PASS 5: Opponent Merge & Advanced Features ===")

    # Opponent merge (first pass)
    df_clean = _merge_opponent_rows(df_clean)

    # PPP columns (for plus metrics)
    df_clean["off_ppp"] = df_clean.apply(
        lambda r: _safe_div(r.get("points_for", np.nan), r.get("poss", np.nan), np.nan),
        axis=1
    )
    df_clean["def_ppp"] = df_clean.apply(
        lambda r: _safe_div(r.get("points_against", np.nan), r.get("poss", np.nan), np.nan),
        axis=1
    )

    # Defensive allowed/forced signals
    df_clean["efg_allowed_game"] = df_clean.get("opp_efg", np.nan)
    df_clean["ftr_allowed_game"] = df_clean.get("opp_ftr", np.nan)
    df_clean["orb_allowed_game"] = df_clean.get("opp_orb_pct", np.nan)
    df_clean["tov_forced_game"] = df_clean.get("opp_tov_pct", np.nan)
    df_clean["def_ppp_allowed_game"] = df_clean.get("opp_off_ppp", np.nan)

    # Defensive rolling features
    df_clean = _add_allowed_forced_pack(df_clean, group_cols=("team_id",), prefix="")
    df_clean = _add_allowed_forced_pack(df_clean, group_cols=("team_id", "home_away"), prefix="ha_")

    # Second opponent merge (to get opponent defensive baselines)
    df_clean = _merge_opponent_rows(df_clean)

    # Aliases for plus metrics
    df_clean["opp_efg_allowed_pre"] = df_clean.get("opp_efg_allowed_l7_pre", np.nan)
    df_clean["opp_ftr_allowed_pre"] = df_clean.get("opp_ftr_allowed_l7_pre", np.nan)
    df_clean["opp_orb_allowed_pre"] = df_clean.get("opp_orb_allowed_l7_pre", np.nan)
    df_clean["opp_tov_forced_pre"] = df_clean.get("opp_tov_forced_l7_pre", np.nan)
    df_clean["opp_def_ppp_allowed_pre"] = df_clean.get("opp_def_ppp_allowed_l7_pre", np.nan)

    # Weights + plus/composites
    wcfg = WeightConfig(
        group_cols=("team_id",),
        order_col="game_datetime_utc",
        opp_rating_col="opp_netrtg_l7_pre",
        site_col="home_away",
        ot_flag_col="is_ot",
    )
    df_clean = add_all_base_weights(df_clean, wcfg)
    df_clean = add_all_plus_and_composites(df_clean, PlusConfig(), CompositeConfig())

    # Advanced matchup metrics
    df_clean = add_all_advanced_metrics(df_clean, n_last=10)

    # Extra rolling signals
    rolling_cfg = RollingConfig(
        group_cols=("team_id",),
        order_col="game_datetime_utc",
        window=10,
        prefix="rf10_",
    )
    df_clean = add_unweighted_rollups(
        df_clean,
        metrics=[
            "netrtg", "ortg", "drtg", "pace", "efg", "tov_pct",
            "orb_pct", "drb_pct", "ftr", "3par", "gps", "net_over_exp",
        ],
        cfg=rolling_cfg,
    )

    # NEW: SOS + Home/Away dynamic HCA features (leak-free, uses prior games only)
    # Requirements: game_date, margin, ortg, drtg, home_away, neutral_site, opp_netrtg_l7_pre
    if "game_date" not in df_clean.columns:
        df_clean["game_date"] = pd.to_datetime(df_clean["game_datetime_utc"], utc=True, errors="coerce").dt.date.astype(str)

    df_clean = add_home_away_features(df_clean, lookback_windows=[5, 10, 15])
    df_clean = add_sos_features(df_clean, lookback_windows=[5, 10, 15])

    # Validate opponent merge
    opp_diagnostics = validate_opponent_merge(df_clean)
    print(f"PASS5: Opponent merge complete. Join rate: {opp_diagnostics['join_rate']*100:.2f}%")

    # Gate checks
    if opp_diagnostics['join_rate'] < GATE_MIN_OPP_JOIN_RATE_FINAL:
        print(
            f"[WARN] Opponent join rate {opp_diagnostics['join_rate']*100:.2f}% "
            f"below gate {GATE_MIN_OPP_JOIN_RATE_FINAL*100:.2f}%"
        )

    poss_present = df_clean["poss"].notna().mean() if len(df_clean) else 0.0
    if poss_present < GATE_MIN_POSS_PRESENT_FINAL:
        print(
            f"[WARN] Poss present rate {poss_present*100:.2f}% "
            f"below gate {GATE_MIN_POSS_PRESENT_FINAL*100:.2f}%"
        )

    expected_cols = [
        c for c in ["ortg", "drtg", "netrtg", "ortg_l7_pre", "drtg_l7_pre", "netrtg_l7_pre"]
        if c in df_clean.columns
    ]
    expected_present = df_clean[expected_cols].notna().all(axis=1).mean() if expected_cols and len(df_clean) else 0.0
    if expected_cols and expected_present < GATE_MIN_EXPECTED_PRESENT_FINAL:
        print(
            f"[WARN] Expected present rate {expected_present*100:.2f}% "
            f"below gate {GATE_MIN_EXPECTED_PRESENT_FINAL*100:.2f}%"
        )

    # Write features CSV
    df_features = _append_dedupe_write(
        OUT_TEAM_FEATURES,
        df_clean.drop(columns=["game_dt"], errors="ignore"),
        subset_keys=["event_id", "team_id"],
        sort_cols=["game_datetime_utc", "event_id", "team_id", "home_away"],
    )
    print(f"{OUT_TEAM_FEATURES} total rows: {len(df_features)}")

    # ========================================================================
    # Building Matchups
    # ========================================================================
    print("\n=== Building Matchups ===")
    df_matchups = build_matchups_model_ready(df_features)

    # OPTIONAL: add matchup-level net HCA (single scalar per matchup)
    # This will only appear in OUT_MATCHUPS if matchup_builder passes it through.
    df_matchups = add_matchup_net_hca(df_matchups, df_features, lookback=15)

    if DRY_RUN:
        print(f"[DRY RUN] Would write {len(df_matchups)} rows -> {OUT_MATCHUPS}")
    else:
        _atomic_csv_write(df_matchups, OUT_MATCHUPS)
        print(f"{OUT_MATCHUPS} written: {len(df_matchups)} rows")

    # ========================================================================
    # Diagnostics
    # ========================================================================
    if WRITE_DIAGNOSTICS:
        print("\n=== Writing Diagnostics ===")
        diagnostics = []
        for _, row in df_features.iterrows():
            issues = []
            if pd.isna(row.get("poss")):
                issues.append("missing_poss")
            if pd.isna(row.get("ortg")):
                issues.append("missing_ortg")
            if not bool(row.get("opp_join_ok", True)):
                issues.append("opp_join_failed")

            if issues:
                diagnostics.append({
                    "event_id": row.get("event_id"),
                    "team_id": row.get("team_id"),
                    "team": row.get("team"),
                    "diagnostic_reason": "|".join(issues),
                })

        if diagnostics:
            df_diag = pd.DataFrame(diagnostics)
            if DRY_RUN:
                print(f"[DRY RUN] Would write {len(df_diag)} diagnostic rows")
            else:
                _atomic_csv_write(df_diag, OUT_DIAGNOSTICS)
                print(f"{OUT_DIAGNOSTICS} written: {len(df_diag)} rows")

    print("\n=== Run Complete ===")
    print(f"Summary parse errors: {errors}")
    write_error_summary()


def main():
    run_pipeline(days_back=DEFAULT_DAYS_BACK)


if __name__ == "__main__":
    main()
