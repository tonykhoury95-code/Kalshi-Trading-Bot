"""
SportsDataIO provider adapter — stub.

Wire this in once a SportsDataIO API key is available.
See https://sportsdata.io/developers/api-documentation/nba for reference.

Environment variable: SPORTSDATAIO_API_KEY
"""

from typing import Optional

from loguru import logger

from src.sportsbook.base import OddsProvider
from src.sportsbook.models import SportsbookGame


class SportsDataIOProvider(OddsProvider):
    """SportsDataIO odds provider (not yet implemented).

    Raises ``NotImplementedError`` on ``get_games()`` until this adapter is
    fully wired. The stub exists so the registry can reference it.
    """

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    @property
    def name(self) -> str:
        return "sportsdataio"

    @property
    def supported_sports(self) -> list[str]:
        return ["NBA", "NFL", "MLB", "NHL", "SOC"]

    async def get_games(
        self,
        sport: str,
        bookmakers: Optional[list[str]] = None,
    ) -> list[SportsbookGame]:
        logger.warning(
            "SportsDataIOProvider.get_games() is not implemented. Returning empty list."
        )
        return []
