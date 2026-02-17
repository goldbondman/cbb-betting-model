"""
Integrity Merge Logic
Combines data from multiple sources with conflict detection and resolution.
"""

from typing import List, Dict, Optional, Set, Tuple
from dataclasses import dataclass, field
from collections import defaultdict
import logging

from data_sources import GameData, SourceResult, SourceType, DataQuality

logger = logging.getLogger(__name__)


@dataclass
class GameConflict:
    """Represents a conflict between sources for a specific field"""
    game_id: str
    field_name: str
    values: Dict[str, any]  # source -> value mapping
    resolved_value: Optional[any] = None
    resolution_method: Optional[str] = None


@dataclass
class MergedGame:
    """A game with data merged from multiple sources"""
    game: GameData
    sources: List[str] = field(default_factory=list)
    conflicts: List[GameConflict] = field(default_factory=list)
    quality_score: float = 0.0
    
    @property
    def source_count(self) -> int:
        return len(self.sources)
    
    @property
    def has_conflicts(self) -> bool:
        return len(self.conflicts) > 0


@dataclass
class IntegrityReport:
    """Report of the integrity merge operation"""
    total_games: int
    sources_used: List[str]
    games_from_single_source: int
    games_from_multiple_sources: int
    total_conflicts: int
    conflicts_by_field: Dict[str, int] = field(default_factory=dict)
    failed_sources: List[str] = field(default_factory=list)
    
    def summary(self) -> str:
        """Generate human-readable summary"""
        lines = [
            f"Integrity Merge Report",
            f"  Total games: {self.total_games}",
            f"  Sources used: {', '.join(self.sources_used)}",
            f"  Failed sources: {', '.join(self.failed_sources) if self.failed_sources else 'None'}",
            f"  Games from single source: {self.games_from_single_source}",
            f"  Games from multiple sources: {self.games_from_multiple_sources}",
            f"  Total conflicts detected: {self.total_conflicts}",
        ]
        if self.conflicts_by_field:
            lines.append("  Conflicts by field:")
            for field, count in sorted(self.conflicts_by_field.items(), key=lambda x: -x[1]):
                lines.append(f"    {field}: {count}")
        return "\n".join(lines)


class IntegrityMerger:
    """
    Merges data from multiple sources with integrity checks.
    
    Strategy:
    1. Match games across sources by game_id or team matchup
    2. Detect conflicts in scores, status, and metadata
    3. Resolve conflicts using voting, source priority, or data quality
    4. Generate merged dataset with conflict reports
    """
    
    def __init__(self, source_priority: List[SourceType] = None):
        """
        Initialize merger with optional source priority.
        
        Args:
            source_priority: Ordered list of sources by priority (first = highest)
                           Defaults to [ESPN, NCAA, HENRY]
        """
        self.source_priority = source_priority or [
            SourceType.ESPN,
            SourceType.NCAA_CASABLANCA,
            SourceType.HENRY_API
        ]
    
    def merge(self, results: List[SourceResult]) -> Tuple[List[MergedGame], IntegrityReport]:
        """
        Merge results from multiple sources.
        
        Args:
            results: List of SourceResult from different sources
            
        Returns:
            Tuple of (merged games, integrity report)
        """
        # Filter successful results
        successful = [r for r in results if r.success and r.games]
        failed = [r.source.value for r in results if not r.success]
        
        if not successful:
            logger.error("No successful sources to merge")
            return [], IntegrityReport(
                total_games=0,
                sources_used=[],
                games_from_single_source=0,
                games_from_multiple_sources=0,
                total_conflicts=0,
                failed_sources=failed
            )
        
        # Group games by identifier
        game_groups = self._group_games(successful)
        
        # Merge each group
        merged_games = []
        all_conflicts = []
        
        for game_id, games_by_source in game_groups.items():
            merged = self._merge_game_group(game_id, games_by_source)
            merged_games.append(merged)
            all_conflicts.extend(merged.conflicts)
        
        # Generate report
        sources_used = [r.source.value for r in successful]
        single_source_count = sum(1 for g in merged_games if g.source_count == 1)
        multi_source_count = sum(1 for g in merged_games if g.source_count > 1)
        
        conflicts_by_field = defaultdict(int)
        for conflict in all_conflicts:
            conflicts_by_field[conflict.field_name] += 1
        
        report = IntegrityReport(
            total_games=len(merged_games),
            sources_used=sources_used,
            games_from_single_source=single_source_count,
            games_from_multiple_sources=multi_source_count,
            total_conflicts=len(all_conflicts),
            conflicts_by_field=dict(conflicts_by_field),
            failed_sources=failed
        )
        
        logger.info(f"Integrity merge complete: {report.summary()}")
        
        return merged_games, report
    
    def _group_games(self, results: List[SourceResult]) -> Dict[str, Dict[str, GameData]]:
        """
        Group games from different sources by unique identifier.
        
        Returns:
            Dict mapping game_id -> {source_name: GameData}
        """
        game_groups = defaultdict(dict)
        
        for result in results:
            source_name = result.source.value
            for game in result.games:
                # Primary key: game_id
                key = game.game_id
                
                # If game_id is not unique, create composite key
                if key in game_groups and source_name not in game_groups[key]:
                    # Check if it's actually the same game (by teams)
                    existing_games = list(game_groups[key].values())
                    if existing_games:
                        existing = existing_games[0]
                        if not self._same_teams(existing, game):
                            # Different game, use composite key
                            key = f"{game.game_id}_{game.home_team}_{game.away_team}"
                
                game_groups[key][source_name] = game
        
        return game_groups
    
    def _same_teams(self, game1: GameData, game2: GameData) -> bool:
        """Check if two games involve the same teams"""
        teams1 = {game1.home_team.lower(), game1.away_team.lower()}
        teams2 = {game2.home_team.lower(), game2.away_team.lower()}
        return teams1 == teams2
    
    def _merge_game_group(self, game_id: str, games_by_source: Dict[str, GameData]) -> MergedGame:
        """
        Merge a group of games from different sources.
        
        Args:
            game_id: Game identifier
            games_by_source: Dict of source_name -> GameData
            
        Returns:
            MergedGame with resolved data and conflicts
        """
        if len(games_by_source) == 1:
            # Single source, no conflicts
            source, game = list(games_by_source.items())[0]
            return MergedGame(
                game=game,
                sources=[source],
                conflicts=[],
                quality_score=game.completeness_score()
            )
        
        # Multiple sources - detect and resolve conflicts
        conflicts = self._detect_conflicts(game_id, games_by_source)
        merged_game = self._resolve_conflicts(games_by_source, conflicts)
        
        quality_score = merged_game.completeness_score()
        
        return MergedGame(
            game=merged_game,
            sources=list(games_by_source.keys()),
            conflicts=conflicts,
            quality_score=quality_score
        )
    
    def _detect_conflicts(self, game_id: str, games_by_source: Dict[str, GameData]) -> List[GameConflict]:
        """
        Detect conflicts across sources for critical fields.
        
        Args:
            game_id: Game identifier
            games_by_source: Dict of source_name -> GameData
            
        Returns:
            List of detected conflicts
        """
        conflicts = []
        
        # Fields to check for conflicts
        critical_fields = [
            'home_score', 'away_score', 'status',
            'home_team', 'away_team', 'venue'
        ]
        
        for field in critical_fields:
            values = {}
            for source, game in games_by_source.items():
                value = getattr(game, field, None)
                if value is not None:
                    values[source] = value
            
            # Check if values differ
            if len(set(str(v) for v in values.values())) > 1:
                conflicts.append(GameConflict(
                    game_id=game_id,
                    field_name=field,
                    values=values
                ))
        
        return conflicts
    
    def _resolve_conflicts(
        self, 
        games_by_source: Dict[str, GameData], 
        conflicts: List[GameConflict]
    ) -> GameData:
        """
        Resolve conflicts and create merged game data.
        
        Resolution strategy:
        1. For scores: Use majority vote, fallback to highest priority source
        2. For status: Use most reliable source (ESPN preferred)
        3. For metadata: Use most complete data
        
        Args:
            games_by_source: Dict of source_name -> GameData
            conflicts: List of conflicts to resolve
            
        Returns:
            Merged GameData with resolved fields
        """
        # Start with highest priority source as base
        base_game = None
        for source_type in self.source_priority:
            source_name = source_type.value
            if source_name in games_by_source:
                base_game = games_by_source[source_name]
                break
        
        if not base_game:
            # Fallback to any source
            base_game = list(games_by_source.values())[0]
        
        # Create merged game (copy of base)
        merged = GameData(
            game_id=base_game.game_id,
            date=base_game.date,
            home_team=base_game.home_team,
            away_team=base_game.away_team,
            home_score=base_game.home_score,
            away_score=base_game.away_score,
            status=base_game.status,
            venue=base_game.venue,
            game_datetime=base_game.game_datetime,
            market_spread=base_game.market_spread,
            market_total=base_game.market_total,
            market_home_ml=base_game.market_home_ml,
            market_away_ml=base_game.market_away_ml,
            source=f"merged:{'+'.join(games_by_source.keys())}",
            pulled_at=base_game.pulled_at
        )
        
        # Resolve each conflict
        for conflict in conflicts:
            resolved_value = self._resolve_field_conflict(conflict, games_by_source)
            conflict.resolved_value = resolved_value
            conflict.resolution_method = "source_priority"
            
            # Update merged game with resolved value
            setattr(merged, conflict.field_name, resolved_value)
        
        # Fill in missing fields from other sources
        for source, game in games_by_source.items():
            if merged.market_spread is None and game.market_spread is not None:
                merged.market_spread = game.market_spread
            if merged.market_total is None and game.market_total is not None:
                merged.market_total = game.market_total
            if merged.venue is None and game.venue is not None:
                merged.venue = game.venue
        
        return merged
    
    def _resolve_field_conflict(
        self, 
        conflict: GameConflict, 
        games_by_source: Dict[str, GameData]
    ) -> any:
        """
        Resolve a single field conflict.
        
        Strategy:
        1. Count values (majority vote)
        2. If tie, use source priority
        3. Return resolved value
        
        Args:
            conflict: The conflict to resolve
            games_by_source: All available game data by source
            
        Returns:
            Resolved value
        """
        # Count occurrences of each value
        value_counts = defaultdict(list)
        for source, value in conflict.values.items():
            value_counts[str(value)].append(source)
        
        # If one value appears more than others, use it (majority vote)
        if value_counts:
            max_count = max(len(sources) for sources in value_counts.values())
            majority_values = [v for v, sources in value_counts.items() if len(sources) == max_count]
            
            if len(majority_values) == 1:
                # Clear majority
                majority_value_str = majority_values[0]
                # Get actual value from first source that has it
                for source, value in conflict.values.items():
                    if str(value) == majority_value_str:
                        return value
        
        # No clear majority, use source priority
        for source_type in self.source_priority:
            source_name = source_type.value
            if source_name in conflict.values:
                return conflict.values[source_name]
        
        # Fallback to any value
        return list(conflict.values.values())[0]
