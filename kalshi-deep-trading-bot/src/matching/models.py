"""
Matching engine result models.
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class MatchMethod(str, Enum):
    """How the match was established."""

    EXACT = "exact"           # Team codes matched lookup table exactly
    FUZZY = "fuzzy"           # Levenshtein / partial-string fallback
    TIME_ONLY = "time_only"   # Only time window matched (low confidence)
    UNMATCHED = "unmatched"   # No match found


class YesSide(str, Enum):
    """Which sportsbook team the Kalshi YES contract represents."""

    HOME = "home"
    AWAY = "away"
    UNKNOWN = "unknown"   # Could not determine (single-team market, UFC, etc.)


class MatchResult(BaseModel):
    """A confirmed match between one Kalshi market and one sportsbook game."""

    kalshi_ticker: str = Field(..., description="Kalshi market ticker")
    sportsbook_game_id: str = Field(..., description="Provider-assigned game ID")
    provider: str = Field(..., description="Sportsbook provider name")

    # Team resolution
    home_team: str = Field(..., description="Home team full name (sportsbook)")
    away_team: str = Field(..., description="Away team full name (sportsbook)")
    yes_side: YesSide = Field(
        ...,
        description="Which sportsbook team the Kalshi YES contract covers",
    )
    yes_team: str = Field(
        ...,
        description="Full name of the team the Kalshi YES contract covers",
    )

    # Timing
    sportsbook_commence_time: str = Field(
        ..., description="ISO 8601 sportsbook game start time"
    )
    time_delta_hours: float = Field(
        ..., description="Abs difference between Kalshi close and sportsbook start (hours)"
    )

    # Match quality
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Match confidence (0–1). Reject if below threshold.",
    )
    match_method: MatchMethod = Field(..., description="How the match was established")
    rejection_reason: Optional[str] = Field(
        default=None,
        description="Why the match was rejected (if confidence below threshold)",
    )
