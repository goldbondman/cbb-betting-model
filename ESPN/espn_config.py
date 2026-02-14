"""
ESPN CBB Pipeline Configuration
All constants, environment variables, and configuration settings for the ESPN pipeline.
"""

import os
from zoneinfo import ZoneInfo

# ---------------- API Endpoints ----------------
ESPN_SUMMARY_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/summary?event={event_id}"
)

ESPN_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
    "?dates={date}&groups=50&limit=1000"
)

# ---------------- HTTP Configuration ----------------
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json,text/plain,*/*",
}

REQUEST_TIMEOUT = int(os.getenv("ESPN_TIMEOUT", "25"))

# Retry hardening
MAX_RETRIES = int(os.getenv("ESPN_MAX_RETRIES", "3"))
RETRY_INITIAL_DELAY = float(os.getenv("ESPN_RETRY_INITIAL_DELAY", "1.0"))
RETRY_BACKOFF = float(os.getenv("ESPN_RETRY_BACKOFF", "2.0"))

# ---------------- Pipeline Metadata ----------------
PARSE_VERSION = "v1.4.2"
SOURCE_NAME = "espn"

# ---------------- Timezone Configuration ----------------
TZ_PST = ZoneInfo("America/Los_Angeles")

# ---------------- Run Configuration ----------------
DEFAULT_DAYS_BACK = int(os.getenv("DAYS_BACK", "3"))
DRY_RUN = os.getenv("DRY_RUN", "0").strip().lower() in ("1", "true", "yes")

# ---------------- Checkpointing & Logging ----------------
CHECKPOINT_FILE = os.getenv("CHECKPOINT_FILE", "espn_pipeline_checkpoint.json")
CHECKPOINT_EVERY_N_GAMES = int(os.getenv("CHECKPOINT_EVERY_N_GAMES", "50"))
ERROR_LOG_PATH = os.getenv("ERROR_LOG_PATH", "espn_pipeline_errors.json")

# ---------------- Output File Paths ----------------
OUT_GAMES = "espn_games.csv"
OUT_TEAM_LOGS = "espn_team_game_logs.csv"
OUT_TEAM_FEATURES = "espn_team_game_features.csv"
OUT_MATCHUPS = "espn_matchups_model_ready.csv"
OUT_DIAGNOSTICS = "espn_feature_diagnostics.csv"
OUT_DQ_AUDIT = "espn_dq_audit.csv"
OUT_PLAYER_BOX = "espn_player_boxscores.csv"

# ---------------- Raw JSON Storage ----------------
JSON_OUTPUT_DIR = os.getenv("ESPN_JSON_DIR", "ESPN/raw_json")
SAVE_RAW_JSON = os.getenv("SAVE_RAW_JSON", "1").strip().lower() in ("1", "true", "yes")

# ---------------- Feature Flags ----------------
WRITE_DIAGNOSTICS = os.getenv("WRITE_DIAGNOSTICS", "1").strip() not in ("0", "false", "False", "no", "NO")
WRITE_DQ_AUDIT = os.getenv("WRITE_DQ_AUDIT", "1").strip() not in ("0", "false", "False", "no", "NO")

# ---------------- Data Quality Gates ----------------
# Gates for daily automation (final pass validation)
GATE_MIN_OPP_JOIN_RATE_FINAL = float(os.getenv("GATE_MIN_OPP_JOIN_RATE_FINAL", "0.985"))
GATE_MIN_POSS_PRESENT_FINAL = float(os.getenv("GATE_MIN_POSS_PRESENT_FINAL", "0.985"))
GATE_MIN_EXPECTED_PRESENT_FINAL = float(os.getenv("GATE_MIN_EXPECTED_PRESENT_FINAL", "0.970"))

# ---------------- Summary Retry Configuration ----------------
# Repair attempts after initial fetch (ESPN-specific recovery)
RETRY_SUMMARY_ON_BASE_MISS = int(os.getenv("RETRY_SUMMARY_ON_BASE_MISS", "1"))
MAX_SUMMARY_RETRIES = int(os.getenv("MAX_SUMMARY_RETRIES", "1"))
SUMMARY_RETRY_SLEEP_SEC = float(os.getenv("SUMMARY_RETRY_SLEEP_SEC", "0.35"))

# ---------------- Data Quality Repair Gate (DQRG) Configuration ----------------
DQRG_ENABLE = os.getenv("DQRG_ENABLE", "1").strip().lower() in ("1", "true", "yes")
DQRG_MAX_EVENTS = int(os.getenv("DQRG_MAX_EVENTS", "300"))
DQRG_REFETCH_ON_FAIL = os.getenv("DQRG_REFETCH_ON_FAIL", "1").strip().lower() in ("1", "true", "yes")

# ---------------- Validation Sets ----------------
VALID_HOME_AWAY = {"home", "away"}

# ---------------- CSV Column Definitions ----------------
# Used by _ensure_csv_exists() in file_io module
CSV_SCHEMAS = {
    "games": [
        "date", "game_id", "game_datetime_utc", "venue", "home_team", "away_team",
        "home_score", "away_score", "home_win", "away_win",
        "completed", "state", "status_desc", "status_detail",
        "pulled_at_utc", "source",
        # Market / Vegas (from ESPN scoreboard, best-effort)
        "market_provider", "market_details", "market_spread", "market_total",
        "market_home_ml", "market_away_ml",
    ],
    "team_logs": [
        # Primary identifiers
        "event_id", "team_id", "team", "opponent", "home_away",
        # Game date/time - game_date moved to far left per user requirement
        "game_date", "game_date_utc", "game_datetime_utc", "venue",
        # Score data
        "points_for", "points_against", "margin",
        # Box score raw stats
        "fgm", "fga", "tpm", "tpa", "ftm", "fta", "tov", "orb", "drb", "reb",
        # Derived metrics
        "poss", "efg", "ftr", "3par", "3p_pct", "ft_pct", "tov_pct", "orb_pct", "drb_pct",
        "ortg", "drtg", "netrtg", "pace",
        # Game metadata
        "neutral_site", "is_ot", "num_ot", "noise_flag",
        "data_ok", "completed", "state", "status_desc", "status_detail",
        # Technical metadata
        "pulled_at_utc", "source", "parse_version",
        # Additional fields
        "home_team", "away_team", "blowout", "row_hash"
    ],
    "team_features": [
        "event_id", "team_id", "team", "home_away", "game_datetime_utc"
    ],
    "matchups": [
        "event_id"
    ],
    "diagnostics": [
        "event_id", "team_id", "team", "diagnostic_reason"
    ],
    "dq_audit": [
        "event_id", "team_id", "team", "home_away", "dq_missing_fields", "dq_reason_codes", "dq_action_plan",
        "dq_repair_success", "dq_repair_actions_taken", "pulled_at_utc", "parse_version"
    ],
    "player_box": [
        "event_id", "game_datetime_utc", "team_id", "team", "home_away",
        "athlete_id", "player", "starter",
        "min", "pts",
        "fgm", "fga", "tpm", "tpa", "ftm", "fta",
        "reb", "orb", "drb", "ast", "stl", "blk", "tov", "pf",
        "pulled_at_utc", "source", "parse_version"
    ],
}

# Map output file paths to their schemas
OUTPUT_FILE_SCHEMAS = {
    OUT_GAMES: CSV_SCHEMAS["games"],
    OUT_TEAM_LOGS: CSV_SCHEMAS["team_logs"],
    OUT_TEAM_FEATURES: CSV_SCHEMAS["team_features"],
    OUT_MATCHUPS: CSV_SCHEMAS["matchups"],
    OUT_DIAGNOSTICS: CSV_SCHEMAS["diagnostics"],
    OUT_DQ_AUDIT: CSV_SCHEMAS["dq_audit"],
    OUT_PLAYER_BOX: CSV_SCHEMAS["player_box"],
}
