"""Tests for run_game_api_probe script."""

from pathlib import Path
from types import SimpleNamespace
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts import run_game_api_probe
from core.data_sources import GameData, SourceResult, SourceType


class _FakeSource:
    def __init__(self, result):
        self._result = result

    def fetch_games(self, _date):
        return self._result


def test_run_probe_and_write_artifacts(tmp_path, monkeypatch):
    game = GameData(
        game_id="401999999",
        date="2026-02-18",
        home_team="Duke",
        away_team="UNC",
        home_score=80,
        away_score=75,
        status="final",
        game_datetime="2026-02-18T20:00:00Z",
        source="cbbpy",
    )
    result = SourceResult(source=SourceType.CBBPY, success=True, games=[game])

    monkeypatch.setattr(run_game_api_probe, "_source_from_name", lambda _n: _FakeSource(result))

    config = {
        "dates": ["2026-02-18"],
        "sources": ["cbbpy"],
        "options": {"continue_on_error": True},
        "output": {"artifact_dir": str(tmp_path), "basename": "probe"},
    }

    report = run_game_api_probe.run_probe(config)
    assert report["summary"]["total_games_pulled"] == 1
    assert report["summary"]["total_verified"] == 1

    artifacts = run_game_api_probe._write_artifacts(report, config)
    assert Path(artifacts["json"]).exists()
    assert Path(artifacts["csv"]).exists()


def test_run_probe_handles_source_error(monkeypatch):
    failed = SourceResult(source=SourceType.CBBPY, success=False, error="boom")
    monkeypatch.setattr(run_game_api_probe, "_source_from_name", lambda _n: _FakeSource(failed))

    config = {
        "dates": ["2026-02-18"],
        "sources": ["cbbpy"],
        "options": {"continue_on_error": True},
        "output": {"artifact_dir": "artifacts", "basename": "probe"},
    }

    report = run_game_api_probe.run_probe(config)
    assert len(report["runs"]) == 1
    assert report["runs"][0]["success"] is False
    assert report["runs"][0]["error"] == "boom"
