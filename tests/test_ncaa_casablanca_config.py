import os
import sys
import importlib


_ESPN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ESPN"
)
if _ESPN_DIR not in sys.path:
    sys.path.insert(0, _ESPN_DIR)


def test_ncaa_api_default_base_url(monkeypatch):
    monkeypatch.delenv("NCAA_API_BASE_URL", raising=False)
    sys.modules.pop("ncaa_casablanca_config", None)
    ncaa_casablanca_config = importlib.import_module("ncaa_casablanca_config")

    assert ncaa_casablanca_config.NCAA_SCOREBOARD_URL == (
        "https://ncaa-api.henrygd.me/casablanca/scoreboard/basketball-men/d1/{year}/{month}/{day}/scoreboard.json"
    )
    assert ncaa_casablanca_config.NCAA_BOXSCORE_URL == (
        "https://ncaa-api.henrygd.me/casablanca/game/{game_id}/boxscore.json"
    )


def test_ncaa_api_base_url_override(monkeypatch):
    monkeypatch.setenv("NCAA_API_BASE_URL", "https://example.com/")
    sys.modules.pop("ncaa_casablanca_config", None)
    ncaa_casablanca_config = importlib.import_module("ncaa_casablanca_config")

    assert ncaa_casablanca_config.NCAA_SCOREBOARD_URL == (
        "https://example.com/casablanca/scoreboard/basketball-men/d1/{year}/{month}/{day}/scoreboard.json"
    )
    assert ncaa_casablanca_config.NCAA_BOXSCORE_URL == (
        "https://example.com/casablanca/game/{game_id}/boxscore.json"
    )
