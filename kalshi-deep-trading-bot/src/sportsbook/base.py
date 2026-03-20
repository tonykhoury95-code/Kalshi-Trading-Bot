"""
Abstract base class for sportsbook odds providers.

All adapters must implement this interface. The scanner talks only to
OddsProvider — never to concrete adapter classes directly.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from .models import SportsbookGame


@dataclass
class HealthStatus:
    """Result of an OddsProvider health check."""

    ok: bool
    provider: str
    message: str = ""
    requests_remaining: Optional[int] = None
    requests_used: Optional[int] = None
    details: dict = field(default_factory=dict)


class OddsProvider(ABC):
    """Abstract sportsbook odds provider.

    Implementations must be async context managers and provide
    ``get_games()`` for each sport the scanner needs.

    Example usage::

        async with provider:
            games = await provider.get_games("NBA", bookmakers=["fanduel"])
    """

    async def __aenter__(self) -> "OddsProvider":
        await self.connect()
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()

    async def connect(self) -> None:
        """Open any long-lived connections (HTTP client, etc.).

        Default implementation is a no-op; override when needed.
        """

    async def close(self) -> None:
        """Release resources.

        Default implementation is a no-op; override when needed.
        """

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier, e.g. ``'the_odds_api'``."""

    @property
    @abstractmethod
    def supported_sports(self) -> list[str]:
        """Sport codes this provider can serve, e.g. ``['NBA', 'NFL', 'MLB']``."""

    @abstractmethod
    async def get_games(
        self,
        sport: str,
        bookmakers: Optional[list[str]] = None,
    ) -> list[SportsbookGame]:
        """Fetch upcoming / live games for a sport.

        Args:
            sport: Our sport code, e.g. ``"NBA"``.
            bookmakers: Optional list of bookmaker keys to request
                        (e.g. ``["fanduel", "draftkings"]``).
                        ``None`` means use provider default.

        Returns:
            List of :class:`SportsbookGame` objects, normalised across providers.
            Returns an empty list on error (never raises).
        """

    async def health_check(self) -> HealthStatus:
        """Verify the provider is reachable and the API key is valid.

        Should be called once at scanner startup.  Default implementation
        returns a passing status — override to add a real connectivity check.

        Returns:
            :class:`HealthStatus` describing the provider state.
        """
        return HealthStatus(ok=True, provider=self.name, message="health check not implemented")

    async def get_all_games(
        self,
        sports: list[str],
        bookmakers: Optional[list[str]] = None,
    ) -> dict[str, list[SportsbookGame]]:
        """Fetch games for multiple sports in sequence.

        Args:
            sports: List of sport codes.
            bookmakers: Passed through to ``get_games()``.

        Returns:
            Dict mapping sport code → list of games.
        """
        result: dict[str, list[SportsbookGame]] = {}
        for sport in sports:
            if sport in self.supported_sports:
                result[sport] = await self.get_games(sport, bookmakers=bookmakers)
            else:
                result[sport] = []
        return result
