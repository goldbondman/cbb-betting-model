# Migration Guide: Modular ESPN Pipeline

This guide shows how to update `espn_boxscore_builder.py` to use the new modular architecture.

---

## Quick Start: Import Statements

Replace the old helper functions with these imports at the top of `espn_boxscore_builder.py`:

```python
#!/usr/bin/env python3
"""
ESPN CBB Boxscore + Feature Builder (Season-to-date, append forever)
Modular architecture - orchestration layer only.
"""

import os
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
```

---

## Function Mapping: Old → New

### Configuration & Constants
| Old Location | New Module | Notes |
|--------------|------------|-------|
| `ESPN_SUMMARY_URL` | `espn_config` | Import constant |
| `DEFAULT_HEADERS` | `espn_config` | Import constant |
| `OUT_GAMES`, `OUT_TEAM_LOGS`, etc. | `espn_config` | Import constants |
| All `os.getenv()` calls | `espn_config` | Already handled |

### Utilities
| Old Function | New Module | New Function |
|--------------|------------|--------------|
| `_to_int()` | `data_utils` | `_to_int()` |
| `_to_float()` | `data_utils` | `_to_float()` |
| `_safe_div()` | `data_utils` | `_safe_div()` |
| `_parse_made_attempt()` | `data_utils` | `_parse_made_attempt()` |
| `_normalize_home_away_series()` | `data_utils` | `_normalize_home_away_series()` |
| `_normalize_id_series()` | `data_utils` | `_normalize_id_series()` |
| `_stable_row_hash()` | `data_utils` | `_stable_row_hash()` |
| `_utc_now_iso()` | `data_utils` | `_utc_now_iso()` |
| `_completeness_score_row()` | `data_utils` | `_completeness_score_row()` |
| `_estimate_possessions()` | `data_utils` | `_estimate_possessions()` |
| `_flip_home_away()` | `data_utils` | `_flip_home_away()` |

### HTTP & Parsing
| Old Function | New Module | New Function |
|--------------|------------|--------------|
| `fetch_with_retry()` | `espn_http_client` | `fetch_with_retry()` |
| `fetch_scoreboard_games()` | Combined approach | See "Scoreboard Changes" below |
| `fetch_and_parse_espn_summary()` | Combined approach | See "Summary Changes" below |
| `_extract_odds_from_comp()` | `espn_parsers` | `_extract_odds_from_comp()` |
| `_extract_players()` | `espn_parsers` | `_extract_players()` |
| `_sum_player_totals()` | `espn_parsers` | Internal (not exported) |
| `_stat_map()` | `espn_parsers` | Internal (not exported) |
| `summary_to_team_rows()` | `espn_parsers` | `summary_to_team_rows()` |

### File I/O
| Old Function | New Module | New Function |
|--------------|------------|--------------|
| `log_error()` | `file_io` | `log_error()` |
| `write_error_summary()` | `file_io` | `write_error_summary()` |
| `load_checkpoint()` | `file_io` | `load_checkpoint()` |
| `save_checkpoint()` | `file_io` | `save_checkpoint()` |
| `clear_checkpoint()` | `file_io` | `clear_checkpoint()` |
| `_atomic_csv_write()` | `file_io` | `_atomic_csv_write()` |
| `_read_csv_if_exists()` | `file_io` | `_read_csv_if_exists()` |
| `_ensure_csv_exists()` | `file_io` | `_ensure_csv_exists()` |
| `_append_dedupe_write()` | `file_io` | `_append_dedupe_write()` |
| `verify_dataframe_integrity()` | `file_io` | `verify_dataframe_integrity()` |

### Metrics & Features
| Old Function | New Module | New Function |
|--------------|------------|--------------|
| `_compute_per_game_advanced_metrics()` | `metrics_calculator` | `_compute_per_game_advanced_metrics()` |
| `_add_rolling_pack()` | `metrics_calculator` | `_add_rolling_pack()` |
| `_add_noblow_rollups()` | `metrics_calculator` | `_add_noblow_rollups()` |
| `_add_allowed_forced_pack()` | `metrics_calculator` | `_add_allowed_forced_pack()` |
| `_time_window_counts_per_team()` | `metrics_calculator` | `_time_window_counts_per_team()` |
| `_group_shift_rolling()` | `metrics_calculator` | Internal (not exported) |
| `_group_shift_expanding_mean()` | `metrics_calculator` | Internal (not exported) |
| `_add_coverage_counts()` | `metrics_calculator` | Internal (not exported) |

### Opponent Merge
| Old Function | New Module | New Function |
|--------------|------------|--------------|
| `_merge_opponent_rows()` | `opponent_merge` | `_merge_opponent_rows()` |
| `_drop_bad_event_ids_keep_good()` | `opponent_merge` | `_drop_bad_event_ids_keep_good()` |
| New: validation diagnostics | `opponent_merge` | `validate_opponent_merge()` |

### Data Quality
| Old Function | New Module | New Function |
|--------------|------------|--------------|
| `_dedupe_by_completeness()` | `data_quality` | `_dedupe_by_completeness()` |
| `_dqrg_find_issues()` | `data_quality` | `_dqrg_find_issues()` |
| `_dqrg_repair_in_place()` | `data_quality` | `_dqrg_repair_in_place()` |

### Matchup Builder
| Old Function | New Module | New Function |
|--------------|------------|--------------|
| `build_matchups_model_ready()` | `matchup_builder` | `build_matchups_model_ready()` |

---

## Key Code Changes

### 1. Scoreboard Fetching (PASS 0)

**Old approach:**
```python
def fetch_scoreboard_games(date_yyyymmdd: str, timeout: int = REQUEST_TIMEOUT):
    url = ESPN_SCOREBOARD_URL.format(date=date_yyyymmdd)
    try:
        data = fetch_with_retry(url, headers=DEFAULT_HEADERS, timeout=timeout)
    except Exception as e:
        log_error("fetch_scoreboard", e, extra={"url": url, "date": date_yyyymmdd})
        return []
    
    events = data.get("events") or []
    rows = []
    for e in events:
        # ... parsing logic ...
        rows.append({...})
    return rows
```

**New approach:**
```python
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
            parsed["date"] = date_yyyymmdd  # Add date field
            rows.append(parsed)
    return rows
```

### 2. Summary Fetching (PASS 1)

**Old approach:**
```python
s = fetch_and_parse_espn_summary(event_id)
hrow, arow = summary_to_team_rows(s)
```

**New approach:**
```python
try:
    raw = fetch_summary(event_id)
    parsed = parse_summary_json(raw, event_id)
    hrow, arow = summary_to_team_rows(parsed)
except Exception as e:
    log_error("summary_parse", e, event_id=event_id)
    continue
```

### 3. File Initialization (Start of run_pipeline)

**Old approach:**
```python
_ensure_csv_exists(OUT_GAMES, columns=[...])
_ensure_csv_exists(OUT_TEAM_LOGS, columns=[...])
# ... repeat for each file ...
```

**New approach:**
```python
ensure_all_output_files_exist()  # Single call, uses OUTPUT_FILE_SCHEMAS from config
```

---

## Updated run_pipeline() Function

Here's a complete refactored `run_pipeline()` function:

```python
def run_pipeline(days_back: int = DEFAULT_DAYS_BACK):
    """
    Main pipeline orchestrator.
    All business logic is in dedicated modules - this is just coordination.
    """
    pulled_at = _utc_now_iso()
    print(f"Run started: {pulled_at} | DAYS_BACK={days_back} | PARSE_VERSION={PARSE_VERSION}")

    # Initialize all output files
    ensure_all_output_files_exist()
    
    # PASS 0: Build games CSV
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

    # PASS 1: Fetch summaries and compute metrics
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

    # PASS 2: Load historical logs
    print("\n=== PASS 2: Load Historical Logs ===")
    df = df_logs_all.copy()
    df["event_id"] = _normalize_id_series(df["event_id"])
    df["team_id"] = _normalize_id_series(df["team_id"])
    if "home_away" in df.columns:
        df["home_away"] = _normalize_home_away_series(df["home_away"])
    df["game_dt"] = pd.to_datetime(df["game_datetime_utc"], utc=True, errors="coerce")
    df = df.sort_values(["team_id", "game_dt", "event_id", "home_away"])

    df_clean = df[df["data_ok"] == True].copy()
    print(f"PASS2: {len(df_clean)}/{len(df)} rows with data_ok=True")

    # PASS 3: Rolling features
    print("\n=== PASS 3: Rolling Features ===")
    df_clean = _add_rolling_pack(df_clean, group_cols=("team_id",), prefix="")
    df_clean = _add_rolling_pack(df_clean, group_cols=("team_id", "home_away"), prefix="ha_")
    df_clean = _add_noblow_rollups(df_clean, group_cols=("team_id",), prefix="")
    df_clean = _add_noblow_rollups(df_clean, group_cols=("team_id", "home_away"), prefix="ha_")
    print(f"PASS3: Rolling features computed on {len(df_clean)} rows")

    # PASS 4: Time-based features
    print("\n=== PASS 4: Time-Based Features ===")
    df_clean = _time_window_counts_per_team(df_clean)
    print("PASS4: Time window features added")

    # PASS 5: Opponent merge and advanced features
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

    # Validate opponent merge
    opp_diagnostics = validate_opponent_merge(df_clean)
    print(f"PASS5: Opponent merge complete. Join rate: {opp_diagnostics['join_rate']*100:.2f}%")
    
    # Gate checks
    if opp_diagnostics['join_rate'] < GATE_MIN_OPP_JOIN_RATE_FINAL:
        print(f"[WARN] Opponent join rate {opp_diagnostics['join_rate']*100:.2f}% "
              f"below gate {GATE_MIN_OPP_JOIN_RATE_FINAL*100:.2f}%")

    poss_present = df_clean["poss"].notna().mean() if len(df_clean) else 0.0
    if poss_present < GATE_MIN_POSS_PRESENT_FINAL:
        print(f"[WARN] Poss present rate {poss_present*100:.2f}% "
              f"below gate {GATE_MIN_POSS_PRESENT_FINAL*100:.2f}%")

    expected_cols = [c for c in ["ortg", "drtg", "netrtg", "ortg_l7_pre", "drtg_l7_pre", "netrtg_l7_pre"] 
                     if c in df_clean.columns]
    expected_present = df_clean[expected_cols].notna().all(axis=1).mean() if expected_cols and len(df_clean) else 0.0
    if expected_cols and expected_present < GATE_MIN_EXPECTED_PRESENT_FINAL:
        print(f"[WARN] Expected present rate {expected_present*100:.2f}% "
              f"below gate {GATE_MIN_EXPECTED_PRESENT_FINAL*100:.2f}%")

    # Write features CSV
    df_features = _append_dedupe_write(
        OUT_TEAM_FEATURES,
        df_clean.drop(columns=["game_dt"], errors="ignore"),
        subset_keys=["event_id", "team_id"],
        sort_cols=["game_datetime_utc", "event_id", "team_id", "home_away"],
    )
    print(f"{OUT_TEAM_FEATURES} total rows: {len(df_features)}")

    # Build matchups table
    print("\n=== Building Matchups ===")
    df_matchups = build_matchups_model_ready(df_features)
    if DRY_RUN:
        print(f"[DRY RUN] Would write {len(df_matchups)} rows -> {OUT_MATCHUPS}")
    else:
        _atomic_csv_write(df_matchups, OUT_MATCHUPS)
        print(f"{OUT_MATCHUPS} written: {len(df_matchups)} rows")

    # Diagnostics
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

    print(f"\n=== Run Complete ===")
    print(f"Summary parse errors: {errors}")
    write_error_summary()
```

---

## Testing Strategy

### 1. Verify Imports
```bash
# Test all imports work
python3 -c "
from espn_config import *
from data_utils import *
from espn_http_client import *
from espn_parsers import *
from file_io import *
from opponent_merge import *
from metrics_calculator import *
from data_quality import *
from matchup_builder import *
print('✓ All imports successful')
"
```

### 2. Run Small Test
```bash
# Test with 1 day of data
DAYS_BACK=1 python3 espn_boxscore_builder.py
```

### 3. Compare Outputs
```bash
# Before migration: save outputs
cp espn_team_game_logs.csv espn_team_game_logs.csv.before
cp espn_matchups_model_ready.csv espn_matchups_model_ready.csv.before

# After migration: compare
python3 -c "
import pandas as pd
before = pd.read_csv('espn_team_game_logs.csv.before').sort_values(['event_id', 'team_id'])
after = pd.read_csv('espn_team_game_logs.csv').sort_values(['event_id', 'team_id'])

# Compare key metrics
cols = ['event_id', 'team_id', 'ortg', 'drtg', 'poss', 'efg']
diff = before[cols].compare(after[cols])
if diff.empty:
    print('✓ Outputs are identical')
else:
    print('Differences found:')
    print(diff.head(20))
"
```

---

## Rollback Plan

If issues arise, you can quickly rollback:

1. **Keep original file**: Copy `espn_boxscore_builder.py` to `espn_boxscore_builder.py.original`
2. **Modular version**: Save refactored version as `espn_boxscore_builder.py.modular`
3. **Switch**: `cp espn_boxscore_builder.py.original espn_boxscore_builder.py`

---

## Benefits After Migration

✅ **Easier debugging** - Stack traces show exact module  
✅ **Faster iteration** - Edit one small file vs. scrolling through 1350 lines  
✅ **Better testing** - Unit test individual modules  
✅ **Code reuse** - Import `metrics_calculator` in other scrapers  
✅ **Clearer ownership** - "HTTP issues? Check `espn_http_client.py`"  
✅ **Onboarding** - New devs read one module at a time  

---

## Common Issues & Solutions

### Issue: ImportError
```
ImportError: cannot import name 'fetch_summary' from 'espn_http_client'
```
**Solution**: Ensure all new module files are in the same directory as `espn_boxscore_builder.py`

### Issue: Circular import
```
ImportError: cannot import name 'fetch_summary' from partially initialized module 'espn_http_client'
```
**Solution**: This shouldn't happen with current design, but if it does, move the import inside the function

### Issue: Missing constant
```
NameError: name 'OUT_TEAM_LOGS' is not defined
```
**Solution**: Add `from espn_config import OUT_TEAM_LOGS` to imports

---

## Next Steps

1. ✅ Create all 9 module files in your repo
2. ✅ Update imports in `espn_boxscore_builder.py`
3. ✅ Replace function calls (use mapping table above)
4. ✅ Test with `DAYS_BACK=1`
5. ✅ Compare outputs with original
6. ✅ Run full historical backfill
7. ✅ Update documentation/README

Good luck with the migration! 🚀
