"""
Sportsbook data models.

All models are provider-agnostic.  Adapters translate raw API responses
into these standardised types before returning them to the scanner.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class MarketType(str, Enum):
    """Type of betting market."""

    MONEYLINE = "h2h"       # Win/lose — maps directly to Kalshi YES/NO
    SPREAD = "spreads"      # Point spread
    TOTAL = "totals"        # Over/under
    DRAW = "draw"           # Draw (soccer)


def american_to_implied(american: int) -> float:
    """Convert American odds to implied probability (0–1).

    Args:
        american: American odds, e.g. ``-150`` or ``+130``.

    Returns:
        Implied probability as a fraction between 0 and 1.
    """
    if american > 0:
        return 100.0 / (american + 100.0)
    return abs(american) / (abs(american) + 100.0)


def decimal_to_implied(decimal: float) -> float:
    """Convert decimal odds to implied probability.

    Args:
        decimal: Decimal odds, e.g. ``1.667``.
    """
    if decimal <= 0:
        return 0.0
    return 1.0 / decimal


def implied_to_american(implied: float) -> int:
    """Convert implied probability to American odds (rounded).

    Args:
        implied: Probability as a fraction (0–1).
    """
    if implied <= 0 or implied >= 1:
        return 0
    if implied < 0.5:
        return round(100 / implied - 100)
    return round(-100 / (1 / implied - 1))


class Outcome(BaseModel):
    """Odds for one outcome (team / player / over / under)."""

    name: str = Field(..., description="Team name, player name, or 'Over'/'Under'")
    price_american: Optional[int] = Field(
        default=None, description="American odds, e.g. -150 or +130"
    )
    price_decimal: Optional[float] = Field(
        default=None, description="Decimal odds, e.g. 1.667"
    )
    implied_prob: float = Field(
        default=0.0, description="Implied probability (0–1), computed from price"
    )
    point: Optional[float] = Field(
        default=None, description="Spread or total line (e.g. -3.5 or 215.5)"
    )

    @model_validator(mode="before")
    @classmethod
    def _compute_implied(cls, values: dict) -> dict:
        """Compute implied_prob from whichever price field is present."""
        if values.get("implied_prob", 0.0) > 0:
            return values

        american = values.get("price_american")
        decimal = values.get("price_decimal")

        if american is not None and american != 0:
            values["implied_prob"] = american_to_implied(int(american))
        elif decimal is not None and decimal > 0:
            values["implied_prob"] = decimal_to_implied(float(decimal))

        return values


class MarketLine(BaseModel):
    """Odds for one market type from one bookmaker."""

    market_type: MarketType = Field(..., description="Market type (h2h, spreads, totals)")
    outcomes: list[Outcome] = Field(default_factory=list)
    last_updated: Optional[datetime] = None

    def get_outcome(self, name: str) -> Optional[Outcome]:
        """Find an outcome by exact or partial name match (case-insensitive)."""
        name_lower = name.lower()
        for o in self.outcomes:
            if o.name.lower() == name_lower:
                return o
        # Partial match fallback
        for o in self.outcomes:
            if name_lower in o.name.lower() or o.name.lower() in name_lower:
                return o
        return None


class BookmakerLines(BaseModel):
    """All markets from one bookmaker for one game."""

    key: str = Field(..., description="Bookmaker API key, e.g. 'fanduel'")
    title: str = Field(..., description="Human-readable name, e.g. 'FanDuel'")
    markets: list[MarketLine] = Field(default_factory=list)
    last_updated: Optional[datetime] = None

    def get_market(self, market_type: MarketType) -> Optional[MarketLine]:
        """Return the first market of the given type."""
        for m in self.markets:
            if m.market_type == market_type:
                return m
        return None


class SportsbookGame(BaseModel):
    """A single game / event from an odds provider.

    Normalised across all providers — adapters translate raw API responses
    into this model before returning.
    """

    game_id: str = Field(..., description="Provider-assigned unique game ID")
    provider: str = Field(..., description="Provider name: 'the_odds_api', 'mock', etc.")
    sport: str = Field(..., description="Our sport code: 'NBA', 'NFL', 'MLB', etc.")
    sport_key: str = Field(..., description="Provider-side sport key, e.g. 'basketball_nba'")
    commence_time: datetime = Field(..., description="UTC game start time")
    home_team: str = Field(..., description="Home team full name")
    away_team: str = Field(..., description="Away team full name")
    bookmakers: list[BookmakerLines] = Field(default_factory=list)

    def get_bookmaker(self, key: str = "fanduel") -> Optional[BookmakerLines]:
        """Return odds from a specific bookmaker."""
        for b in self.bookmakers:
            if b.key == key:
                return b
        return self.bookmakers[0] if self.bookmakers else None

    def get_moneyline(self, bookmaker_key: str = "fanduel") -> Optional[MarketLine]:
        """Return the moneyline market from a bookmaker."""
        bk = self.get_bookmaker(bookmaker_key)
        if bk is None:
            return None
        return bk.get_market(MarketType.MONEYLINE)

    def get_outcome_odds(
        self, team_name: str, bookmaker_key: str = "fanduel"
    ) -> Optional[Outcome]:
        """Return moneyline odds for a specific team."""
        ml = self.get_moneyline(bookmaker_key)
        if ml is None:
            return None
        return ml.get_outcome(team_name)

    @property
    def teams(self) -> list[str]:
        """Both team names."""
        return [self.home_team, self.away_team]
