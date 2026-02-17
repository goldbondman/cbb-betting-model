"""
Example usage of the multi-source data integration system.
Demonstrates basic usage and key features.
"""

import sys
import os

# Add core directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'core'))

from multi_source_fetcher import MultiSourceFetcher
from data_sources import SourceType


def example_basic_usage():
    """Example 1: Basic usage - fetch games for a date"""
    print("\n=== Example 1: Basic Usage ===\n")
    
    # Initialize fetcher with all sources
    fetcher = MultiSourceFetcher()
    
    # Fetch games for a specific date
    # NOTE: This will make real API calls - use a recent date with actual games
    date = "2024-01-15"
    
    try:
        print(f"Fetching games for {date}...")
        games, report = fetcher.fetch_date(date, allow_partial=True)
        
        # Display report summary
        print("\n" + report.summary())
        
        # Show some games
        if games:
            print(f"\nFirst {min(3, len(games))} games:")
            for i, merged_game in enumerate(games[:3]):
                game = merged_game.game
                print(f"\n{i+1}. {game.home_team} vs {game.away_team}")
                print(f"   Score: {game.home_score}-{game.away_score}")
                print(f"   Sources: {merged_game.sources}")
                print(f"   Quality: {merged_game.quality_score:.2f}")
                
                if merged_game.has_conflicts:
                    print(f"   ⚠️  Conflicts detected: {len(merged_game.conflicts)}")
                    for conflict in merged_game.conflicts:
                        print(f"      - {conflict.field_name}: {conflict.values}")
        else:
            print("No games found for this date")
            
    except Exception as e:
        print(f"Error: {e}")


def example_custom_sources():
    """Example 2: Custom source configuration"""
    print("\n=== Example 2: Custom Source Configuration ===\n")
    
    # Only use ESPN and NCAA (disable Henry API)
    fetcher = MultiSourceFetcher(
        enable_espn=True,
        enable_ncaa=True,
        enable_henry=False
    )
    
    print(f"Enabled sources: {[s.get_source_type().value for s in fetcher.sources]}")
    
    # Custom source priority (NCAA first, then ESPN)
    fetcher_custom_priority = MultiSourceFetcher(
        source_priority=[
            SourceType.NCAA_CASABLANCA,
            SourceType.ESPN,
        ]
    )
    
    print(f"Source priority: {[s.value for s in fetcher_custom_priority.merger.source_priority]}")


def example_save_to_csv():
    """Example 3: Save results to CSV"""
    print("\n=== Example 3: Save to CSV ===\n")
    
    fetcher = MultiSourceFetcher()
    
    # This would fetch real data - using mock for example
    print("Would fetch games and save to CSV:")
    print("  fetcher.save_to_csv(games, 'output/games.csv', include_metadata=True)")
    print("\nCSV would include:")
    print("  - All game data fields")
    print("  - source_count: number of sources")
    print("  - has_conflicts: conflict indicator")
    print("  - quality_score: data completeness score")


def example_error_handling():
    """Example 4: Error handling and partial failures"""
    print("\n=== Example 4: Error Handling ===\n")
    
    fetcher = MultiSourceFetcher()
    
    print("The system handles partial failures gracefully:")
    print("")
    print("Scenario: ESPN works, NCAA fails, Henry fails")
    print("Result: Returns games from ESPN with warning")
    print("")
    print("Check report.failed_sources to see which sources failed:")
    print("  if report.failed_sources:")
    print("      print(f'Warning: {report.failed_sources} failed')")
    print("")
    print("Use allow_partial=False to require all sources:")
    print("  games, report = fetcher.fetch_date(date, allow_partial=False)")


def example_conflict_detection():
    """Example 5: Understanding conflict detection"""
    print("\n=== Example 5: Conflict Detection ===\n")
    
    print("The system detects conflicts in critical fields:")
    print("  - Scores (home_score, away_score)")
    print("  - Game status")
    print("  - Team names")
    print("  - Venue")
    print("")
    print("Example conflict:")
    print("  ESPN:  away_score = 75")
    print("  NCAA:  away_score = 76")
    print("  Henry: away_score = 75")
    print("")
    print("Resolution (majority vote):")
    print("  Result: away_score = 75 (ESPN + Henry agree)")
    print("")
    print("Access conflicts:")
    print("  for game in games:")
    print("      if game.has_conflicts:")
    print("          for conflict in game.conflicts:")
    print("              print(f'{conflict.field_name}: {conflict.values}')")


def main():
    """Run all examples"""
    print("=" * 60)
    print("Multi-Source Data Integration - Usage Examples")
    print("=" * 60)
    
    example_basic_usage()
    example_custom_sources()
    example_save_to_csv()
    example_error_handling()
    example_conflict_detection()
    
    print("\n" + "=" * 60)
    print("For more details, see MULTI_SOURCE_INTEGRATION.md")
    print("=" * 60)


if __name__ == "__main__":
    main()
