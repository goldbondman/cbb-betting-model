"""
Multi-Source Data Integration Layer
Fetches data from ESPN, NCAA Casablanca, and Henry API with integrity checks.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class SourceType(Enum):
    """Data source types"""
    ESPN = "espn"
    NCAA_CASABLANCA = "ncaa_casablanca"
    HENRY_API = "henry_api"
    CBBPY = "cbbpy"


class DataQuality(Enum):
    """Data quality levels"""
    HIGH = "high"      # Complete data, all fields present
    MEDIUM = "medium"  # Some fields missing but usable
    LOW = "low"        # Significant fields missing
    FAILED = "failed"  # Source failed or unusable


@dataclass
class GameData:
    """Standardized game data structure across all sources"""
    game_id: str
    date: str
    home_team: str
    away_team: str
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    status: Optional[str] = None
    venue: Optional[str] = None
    game_datetime: Optional[str] = None
    
    # Market data
    market_spread: Optional[float] = None
    market_total: Optional[float] = None
    market_home_ml: Optional[int] = None
    market_away_ml: Optional[int] = None
    
    # Metadata
    source: Optional[str] = None
    quality: DataQuality = DataQuality.MEDIUM
    pulled_at: Optional[str] = None
    raw_data: Optional[Dict] = field(default=None, repr=False)
    
    def completeness_score(self) -> float:
        """Calculate completeness score (0-1) based on available fields"""
        total_fields = 14  # Core fields
        present = sum([
            self.game_id is not None,
            self.date is not None,
            self.home_team is not None,
            self.away_team is not None,
            self.home_score is not None,
            self.away_score is not None,
            self.status is not None,
            self.venue is not None,
            self.game_datetime is not None,
            self.market_spread is not None,
            self.market_total is not None,
            self.market_home_ml is not None,
            self.market_away_ml is not None,
            self.source is not None,
        ])
        return present / total_fields
    
    def is_complete_basic(self) -> bool:
        """Check if basic required fields are present"""
        return all([
            self.game_id,
            self.date,
            self.home_team,
            self.away_team,
        ])


@dataclass
class SourceResult:
    """Result from a single data source fetch"""
    source: SourceType
    success: bool
    games: List[GameData] = field(default_factory=list)
    error: Optional[str] = None
    fetch_time: Optional[datetime] = None
    
    @property
    def quality(self) -> DataQuality:
        """Overall quality of this source's data"""
        if not self.success or not self.games:
            return DataQuality.FAILED
        
        avg_completeness = sum(g.completeness_score() for g in self.games) / len(self.games)
        if avg_completeness >= 0.8:
            return DataQuality.HIGH
        elif avg_completeness >= 0.5:
            return DataQuality.MEDIUM
        else:
            return DataQuality.LOW


class DataSource(ABC):
    """Abstract base class for data sources"""
    
    @abstractmethod
    def fetch_games(self, date: str) -> SourceResult:
        """
        Fetch games for a specific date.
        
        Args:
            date: Date in YYYY-MM-DD format
            
        Returns:
            SourceResult with fetched games and metadata
        """
        pass
    
    @abstractmethod
    def get_source_type(self) -> SourceType:
        """Return the source type identifier"""
        pass
    
    def _create_result(
        self, 
        success: bool, 
        games: List[GameData] = None, 
        error: str = None
    ) -> SourceResult:
        """Helper to create a SourceResult"""
        return SourceResult(
            source=self.get_source_type(),
            success=success,
            games=games or [],
            error=error,
            fetch_time=datetime.now(timezone.utc)
        )
