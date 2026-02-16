"""
NCAA Casablanca JSON Feed Configuration
Configuration for NCAA's unofficial but widely used Casablanca JSON API.
"""

import os

# ---------------- API Endpoints ----------------
# NCAA Casablanca JSON feeds
# Scoreboard: https://data.ncaa.com/casablanca/scoreboard/basketball-men/d1/YYYY/MM/DD/scoreboard.json
# Box score: https://data.ncaa.com/casablanca/game/<gameId>/boxscore.json

NCAA_SCOREBOARD_URL = (
    "https://data.ncaa.com/casablanca/scoreboard/basketball-men/d1/{year}/{month}/{day}/scoreboard.json"
)

NCAA_BOXSCORE_URL = (
    "https://data.ncaa.com/casablanca/game/{game_id}/boxscore.json"
)

# ---------------- HTTP Configuration ----------------
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

REQUEST_TIMEOUT = int(os.getenv("NCAA_TIMEOUT", "30"))

# Retry configuration
MAX_RETRIES = int(os.getenv("NCAA_MAX_RETRIES", "3"))
RETRY_INITIAL_DELAY = float(os.getenv("NCAA_RETRY_INITIAL_DELAY", "1.0"))
RETRY_BACKOFF = float(os.getenv("NCAA_RETRY_BACKOFF", "2.0"))

# ---------------- Pipeline Metadata ----------------
PARSE_VERSION = "v1.0.0"
SOURCE_NAME = "ncaa_casablanca"

# ---------------- Output File Paths ----------------
# Store CSVs in the ESPN folder as requested
OUT_NCAA_GAMES = "CSV/ncaa_games.csv"
OUT_NCAA_TEAM_LOGS = "CSV/ncaa_team_game_logs.csv"
OUT_NCAA_PLAYER_BOX = "CSV/ncaa_player_boxscores.csv"

# ---------------- CSV Column Definitions ----------------
CSV_SCHEMAS = {
    "games": [
        "game_id", "date", "game_datetime", "home_team", "away_team",
        "home_score", "away_score", "status", "venue",
        "pulled_at_utc", "source", "parse_version",
    ],
    "team_logs": [
        "game_id", "team", "opponent", "home_away",
        "game_date", "game_datetime", "venue",
        "points_for", "points_against", "margin",
        "fgm", "fga", "fg_pct", "tpm", "tpa", "tp_pct", "ftm", "fta", "ft_pct",
        "reb", "orb", "drb", "ast", "stl", "blk", "tov", "pf",
        "pulled_at_utc", "source", "parse_version",
    ],
    "player_box": [
        "game_id", "team", "player_name", "player_id",
        "starter", "minutes", "points",
        "fgm", "fga", "fg_pct", "tpm", "tpa", "tp_pct", "ftm", "fta", "ft_pct",
        "reb", "orb", "drb", "ast", "stl", "blk", "tov", "pf",
        "pulled_at_utc", "source", "parse_version",
    ],
}
