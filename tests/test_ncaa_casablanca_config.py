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
    import ncaa_casablanca_config

    importlib.reload(ncaa_casablanca_config)

    assert ncaa_casablanca_config.NCAA_SCOREBOARD_URL.startswith("https://ncaa-api.henrygd.me/casablanca/")
    assert ncaa_casablanca_config.NCAA_BOXSCORE_URL.startswith("https://ncaa-api.henrygd.me/casablanca/")


def test_ncaa_api_base_url_override(monkeypatch):
    monkeypatch.setenv("NCAA_API_BASE_URL", "https://example.com/")
    import ncaa_casablanca_config

    importlib.reload(ncaa_casablanca_config)

    assert ncaa_casablanca_config.NCAA_SCOREBOARD_URL.startswith("https://example.com/casablanca/")
    assert ncaa_casablanca_config.NCAA_BOXSCORE_URL.startswith("https://example.com/casablanca/")
