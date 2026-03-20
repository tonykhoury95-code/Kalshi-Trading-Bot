"""
Deterministic mock odds provider for unit testing and offline development.

Returns a stable set of pre-built games so tests never hit a live API.
Supports injecting custom game data for scenario testing.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from src.sportsbook.base import HealthStatus, OddsProvider
from src.sportsbook.models import (
    BookmakerLines,
    MarketLine,
    MarketType,
    Outcome,
    SportsbookGame,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _game(
    game_id: str,
    sport: str,
    sport_key: str,
    home: str,
    away: str,
    home_american: int,
    away_american: int,
    hours_from_now: float = 3.0,
    bookmaker_key: str = "fanduel",
) -> SportsbookGame:
    """Convenience factory for building a mock game."""
    commence = datetime.now(timezone.utc) + timedelta(hours=hours_from_now)
    ml = MarketLine(
        market_type=MarketType.MONEYLINE,
        outcomes=[
            Outcome(name=home, price_american=home_american),
            Outcome(name=away, price_american=away_american),
        ],
    )
    bk = BookmakerLines(
        key=bookmaker_key,
        title=bookmaker_key.title(),
        markets=[ml],
    )
    return SportsbookGame(
        game_id=game_id,
        provider="mock",
        sport=sport,
        sport_key=sport_key,
        commence_time=commence,
        home_team=home,
        away_team=away,
        bookmakers=[bk],
    )


# ---------------------------------------------------------------------------
# Default fixture data — one realistic game per sport
# ---------------------------------------------------------------------------

def _default_games() -> list[SportsbookGame]:
    return [
        # NBA
        _game(
            "mock-nba-001", "NBA", "basketball_nba",
            home="Boston Celtics", away="Miami Heat",
            home_american=-160, away_american=+135,
        ),
        _game(
            "mock-nba-002", "NBA", "basketball_nba",
            home="Los Angeles Lakers", away="Golden State Warriors",
            home_american=+110, away_american=-130,
            hours_from_now=6.0,
        ),
        # NFL
        _game(
            "mock-nfl-001", "NFL", "americanfootball_nfl",
            home="Kansas City Chiefs", away="Buffalo Bills",
            home_american=-180, away_american=+155,
            hours_from_now=24.0,
        ),
        # MLB
        _game(
            "mock-mlb-001", "MLB", "baseball_mlb",
            home="New York Yankees", away="Boston Red Sox",
            home_american=-140, away_american=+120,
            hours_from_now=5.0,
        ),
        # NHL
        _game(
            "mock-nhl-001", "NHL", "icehockey_nhl",
            home="Toronto Maple Leafs", away="Boston Bruins",
            home_american=+105, away_american=-125,
            hours_from_now=4.0,
        ),
        # UFC
        _game(
            "mock-ufc-001", "UFC", "mma_mixed_martial_arts",
            home="Jon Jones", away="Stipe Miocic",
            home_american=-350, away_american=+280,
            hours_from_now=48.0,
        ),
        # Soccer (MLS)
        _game(
            "mock-mls-001", "SOC", "soccer_usa_mls",
            home="LAFC", away="New York City FC",
            home_american=-120, away_american=+300,
            hours_from_now=8.0,
        ),
    ]


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class MockOddsProvider(OddsProvider):
    """Deterministic mock for testing.

    Args:
        games: Optional list of games to serve. Defaults to a small set of
               realistic fixture games covering all supported sports.
    """

    def __init__(self, games: Optional[list[SportsbookGame]] = None) -> None:
        self._games = games if games is not None else _default_games()

    @property
    def name(self) -> str:
        return "mock"

    @property
    def supported_sports(self) -> list[str]:
        return ["NBA", "NFL", "MLB", "NHL", "UFC", "SOC", "TEN", "GOLF"]

    async def health_check(self) -> HealthStatus:
        return HealthStatus(
            ok=True,
            provider=self.name,
            message=f"mock provider — {len(self._games)} fixture games loaded",
        )

    async def get_games(
        self,
        sport: str,
        bookmakers: Optional[list[str]] = None,
    ) -> list[SportsbookGame]:
        """Return mock games filtered by sport."""
        return [g for g in self._games if g.sport == sport.upper()]
