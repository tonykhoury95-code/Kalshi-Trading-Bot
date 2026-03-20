"""
OddsJam odds provider stub.

Not yet implemented — returns empty results with a warning.
OddsJam API docs: https://oddsjam.com/api

To implement:
  1. Register at https://oddsjam.com
  2. Set ODDSJAM_API_KEY in .env
  3. Replace this stub with a real implementation following the
     TheOddsAPIProvider pattern in the_odds_api.py
"""

from typing import Optional

from loguru import logger

from src.sportsbook.base import HealthStatus, OddsProvider
from src.sportsbook.models import SportsbookGame


class OddsJamProvider(OddsProvider):
    """Stub adapter for OddsJam.

    Always returns empty game lists. Replace with a real implementation
    once an OddsJam API key is available.
    """

    def __init__(self, api_key: str = "", **kwargs) -> None:
        self._api_key = api_key

    @property
    def name(self) -> str:
        return "oddsjam"

    @property
    def supported_sports(self) -> list[str]:
        return ["NBA", "NFL", "MLB", "NHL", "UFC", "SOC", "TEN", "GOLF"]

    async def health_check(self) -> HealthStatus:
        return HealthStatus(
            ok=False,
            provider=self.name,
            message="OddsJam adapter is a stub — not yet implemented",
        )

    async def get_games(
        self,
        sport: str,
        bookmakers: Optional[list[str]] = None,
    ) -> list[SportsbookGame]:
        logger.warning(
            "OddsJamProvider: stub called for sport='{}' — returning empty list. "
            "Implement the real adapter to use OddsJam odds.",
            sport,
        )
        return []
