"""
Tests for multi-source data integration and integrity merge.
"""

import sys
import os

# Add core directory to path
_CORE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core")
if _CORE_DIR not in sys.path:
    sys.path.insert(0, _CORE_DIR)

from data_sources import GameData, SourceResult, SourceType, DataQuality
from integrity_merger import IntegrityMerger, GameConflict, MergedGame
from datetime import datetime


def test_game_data_completeness():
    """Test GameData completeness scoring"""
    # Full game
    full_game = GameData(
        game_id="401829197",
        date="2024-01-15",
        home_team="Duke",
        away_team="UNC",
        home_score=80,
        away_score=75,
        status="final",
        venue="Cameron Indoor Stadium",
        game_datetime="2024-01-15T19:00:00Z",
        market_spread=-5.5,
        market_total=155.0,
        market_home_ml=-220,
        market_away_ml=180,
        source="espn"
    )
    
    assert full_game.completeness_score() == 1.0, "Full game should have 100% completeness"
    assert full_game.is_complete_basic(), "Full game should pass basic check"
    
    # Minimal game
    minimal_game = GameData(
        game_id="401829198",
        date="2024-01-15",
        home_team="Kentucky",
        away_team="Tennessee"
    )
    
    assert minimal_game.completeness_score() < 0.5, "Minimal game should have low completeness"
    assert minimal_game.is_complete_basic(), "Minimal game should still pass basic check"
    
    # Incomplete game (missing required fields)
    incomplete_game = GameData(
        game_id="401829199",
        date="2024-01-15",
        home_team="",  # Missing
        away_team=""   # Missing
    )
    
    assert not incomplete_game.is_complete_basic(), "Incomplete game should fail basic check"
    
    print("✓ test_game_data_completeness passed")


def test_source_result_quality():
    """Test SourceResult quality assessment"""
    # High quality result (need all fields for 80%+ completeness)
    high_quality_games = [
        GameData(
            game_id=f"40182{i}",
            date="2024-01-15",
            home_team=f"Team{i}A",
            away_team=f"Team{i}B",
            home_score=80,
            away_score=75,
            status="final",
            venue="Arena",
            game_datetime="2024-01-15T19:00:00Z",
            market_spread=-5.5,
            market_total=155.0,
            market_home_ml=-220,
            market_away_ml=180,
            source="espn"
        )
        for i in range(5)
    ]
    
    high_result = SourceResult(
        source=SourceType.ESPN,
        success=True,
        games=high_quality_games,
        fetch_time=datetime.now(datetime.UTC) if hasattr(datetime, 'UTC') else datetime.utcnow()
    )
    
    assert high_result.quality == DataQuality.HIGH, "Complete games should be high quality"
    
    # Low quality result (minimal data)
    low_quality_games = [
        GameData(
            game_id=f"40182{i}",
            date="2024-01-15",
            home_team=f"Team{i}A",
            away_team=f"Team{i}B"
        )
        for i in range(5)
    ]
    
    low_result = SourceResult(
        source=SourceType.NCAA_CASABLANCA,
        success=True,
        games=low_quality_games,
        fetch_time=datetime.now(datetime.UTC) if hasattr(datetime, 'UTC') else datetime.utcnow()
    )
    
    assert low_result.quality in [DataQuality.LOW, DataQuality.MEDIUM], "Minimal games should be low/medium quality"
    
    # Failed result
    failed_result = SourceResult(
        source=SourceType.HENRY_API,
        success=False,
        error="Connection timeout",
        fetch_time=datetime.now(datetime.UTC) if hasattr(datetime, 'UTC') else datetime.utcnow()
    )
    
    assert failed_result.quality == DataQuality.FAILED, "Failed fetch should be FAILED quality"
    
    print("✓ test_source_result_quality passed")


def test_integrity_merger_no_conflicts():
    """Test merger with consistent data from multiple sources"""
    # Create identical game data from three sources
    espn_game = GameData(
        game_id="401829197",
        date="2024-01-15",
        home_team="Duke Blue Devils",
        away_team="UNC Tar Heels",
        home_score=80,
        away_score=75,
        status="final",
        venue="Cameron Indoor Stadium",
        source="espn"
    )
    
    ncaa_game = GameData(
        game_id="401829197",
        date="2024-01-15",
        home_team="Duke Blue Devils",
        away_team="UNC Tar Heels",
        home_score=80,
        away_score=75,
        status="final",
        venue="Cameron Indoor Stadium",
        source="ncaa_casablanca"
    )
    
    henry_game = GameData(
        game_id="401829197",
        date="2024-01-15",
        home_team="Duke Blue Devils",
        away_team="UNC Tar Heels",
        home_score=80,
        away_score=75,
        status="final",
        venue="Cameron Indoor Stadium",
        source="henry_api"
    )
    
    results = [
        SourceResult(SourceType.ESPN, True, [espn_game]),
        SourceResult(SourceType.NCAA_CASABLANCA, True, [ncaa_game]),
        SourceResult(SourceType.HENRY_API, True, [henry_game]),
    ]
    
    merger = IntegrityMerger()
    merged_games, report = merger.merge(results)
    
    assert len(merged_games) == 1, "Should merge into single game"
    assert merged_games[0].source_count == 3, "Should have 3 sources"
    assert not merged_games[0].has_conflicts, "Should have no conflicts"
    assert report.total_conflicts == 0, "Report should show no conflicts"
    assert merged_games[0].game.home_score == 80, "Score should be preserved"
    
    print("✓ test_integrity_merger_no_conflicts passed")


def test_integrity_merger_score_conflicts():
    """Test merger with conflicting scores"""
    # Create conflicting game data
    espn_game = GameData(
        game_id="401829197",
        date="2024-01-15",
        home_team="Duke",
        away_team="UNC",
        home_score=80,
        away_score=75,
        status="final",
        source="espn"
    )
    
    ncaa_game = GameData(
        game_id="401829197",
        date="2024-01-15",
        home_team="Duke",
        away_team="UNC",
        home_score=80,  # Same
        away_score=76,  # Different!
        status="final",
        source="ncaa_casablanca"
    )
    
    henry_game = GameData(
        game_id="401829197",
        date="2024-01-15",
        home_team="Duke",
        away_team="UNC",
        home_score=80,
        away_score=75,  # Agrees with ESPN
        status="final",
        source="henry_api"
    )
    
    results = [
        SourceResult(SourceType.ESPN, True, [espn_game]),
        SourceResult(SourceType.NCAA_CASABLANCA, True, [ncaa_game]),
        SourceResult(SourceType.HENRY_API, True, [henry_game]),
    ]
    
    merger = IntegrityMerger()
    merged_games, report = merger.merge(results)
    
    assert len(merged_games) == 1, "Should merge into single game"
    assert merged_games[0].has_conflicts, "Should detect conflict"
    assert report.total_conflicts > 0, "Report should show conflicts"
    
    # Majority vote should pick 75 (ESPN + Henry agree)
    assert merged_games[0].game.away_score == 75, "Should use majority vote (75)"
    
    # Check conflict details
    conflicts = merged_games[0].conflicts
    assert len(conflicts) > 0, "Should have conflict records"
    
    score_conflict = [c for c in conflicts if c.field_name == 'away_score']
    assert len(score_conflict) == 1, "Should have away_score conflict"
    assert score_conflict[0].resolved_value == 75, "Resolved value should be 75"
    
    print("✓ test_integrity_merger_score_conflicts passed")


def test_integrity_merger_source_priority():
    """Test merger respects source priority for tie-breaking"""
    # Create conflicting game data with no majority
    espn_game = GameData(
        game_id="401829197",
        date="2024-01-15",
        home_team="Duke",
        away_team="UNC",
        home_score=80,
        status="final",
        source="espn"
    )
    
    ncaa_game = GameData(
        game_id="401829197",
        date="2024-01-15",
        home_team="Duke",
        away_team="UNC",
        home_score=81,  # Different
        status="final",
        source="ncaa_casablanca"
    )
    
    results = [
        SourceResult(SourceType.ESPN, True, [espn_game]),
        SourceResult(SourceType.NCAA_CASABLANCA, True, [ncaa_game]),
    ]
    
    # ESPN has priority
    merger = IntegrityMerger(source_priority=[SourceType.ESPN, SourceType.NCAA_CASABLANCA])
    merged_games, report = merger.merge(results)
    
    assert merged_games[0].game.home_score == 80, "Should use ESPN (higher priority)"
    
    # Reverse priority
    merger2 = IntegrityMerger(source_priority=[SourceType.NCAA_CASABLANCA, SourceType.ESPN])
    merged_games2, report2 = merger2.merge(results)
    
    assert merged_games2[0].game.home_score == 81, "Should use NCAA (higher priority)"
    
    print("✓ test_integrity_merger_source_priority passed")


def test_integrity_merger_single_source():
    """Test merger handles single source gracefully"""
    espn_game = GameData(
        game_id="401829197",
        date="2024-01-15",
        home_team="Duke",
        away_team="UNC",
        home_score=80,
        away_score=75,
        source="espn"
    )
    
    results = [
        SourceResult(SourceType.ESPN, True, [espn_game]),
    ]
    
    merger = IntegrityMerger()
    merged_games, report = merger.merge(results)
    
    assert len(merged_games) == 1, "Should handle single source"
    assert merged_games[0].source_count == 1, "Should have 1 source"
    assert not merged_games[0].has_conflicts, "Single source should have no conflicts"
    assert report.games_from_single_source == 1, "Report should show single source game"
    
    print("✓ test_integrity_merger_single_source passed")


def test_integrity_merger_failed_sources():
    """Test merger handles failed sources"""
    espn_game = GameData(
        game_id="401829197",
        date="2024-01-15",
        home_team="Duke",
        away_team="UNC",
        home_score=80,
        away_score=75,
        source="espn"
    )
    
    results = [
        SourceResult(SourceType.ESPN, True, [espn_game]),
        SourceResult(SourceType.NCAA_CASABLANCA, False, error="Timeout"),
        SourceResult(SourceType.HENRY_API, False, error="404 Not Found"),
    ]
    
    merger = IntegrityMerger()
    merged_games, report = merger.merge(results)
    
    assert len(merged_games) == 1, "Should still get data from ESPN"
    assert len(report.failed_sources) == 2, "Should track 2 failed sources"
    assert "ncaa_casablanca" in report.failed_sources, "Should list ncaa_casablanca as failed"
    assert "henry_api" in report.failed_sources, "Should list henry_api as failed"
    assert len(report.sources_used) == 1, "Should only use ESPN"
    
    print("✓ test_integrity_merger_failed_sources passed")


def test_integrity_report_summary():
    """Test IntegrityReport summary generation"""
    from integrity_merger import IntegrityReport
    
    report = IntegrityReport(
        total_games=10,
        sources_used=["espn", "ncaa_casablanca"],
        games_from_single_source=3,
        games_from_multiple_sources=7,
        total_conflicts=5,
        conflicts_by_field={"away_score": 3, "status": 2},
        failed_sources=["henry_api"]
    )
    
    summary = report.summary()
    
    assert "Total games: 10" in summary, "Should include total games"
    assert "espn" in summary, "Should list sources used"
    assert "henry_api" in summary, "Should list failed sources"
    assert "away_score: 3" in summary, "Should show conflict breakdown"
    
    print("✓ test_integrity_report_summary passed")


def run_all_tests():
    """Run all tests"""
    print("\nRunning Multi-Source Integration Tests")
    print("=" * 50)
    
    test_game_data_completeness()
    test_source_result_quality()
    test_integrity_merger_no_conflicts()
    test_integrity_merger_score_conflicts()
    test_integrity_merger_source_priority()
    test_integrity_merger_single_source()
    test_integrity_merger_failed_sources()
    test_integrity_report_summary()
    
    print("=" * 50)
    print("All tests passed! ✓")


if __name__ == "__main__":
    run_all_tests()
