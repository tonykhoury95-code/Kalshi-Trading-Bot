"""
Provider registry / factory.

Maps provider names to their adapter classes.  The scanner calls
``get_provider(config)`` once at startup and then uses the abstract
``OddsProvider`` interface everywhere.
"""

from typing import Optional

from loguru import logger

from src.sportsbook.base import OddsProvider


def get_provider(
    provider: str,
    api_key: str = "",
    markets: Optional[list[str]] = None,
    **kwargs,
) -> OddsProvider:
    """Instantiate and return an ``OddsProvider`` by name.

    Args:
        provider: Provider identifier: ``"the_odds_api"``, ``"mock"``,
                  or ``"sportsdataio"``.
        api_key:  API key for the provider (ignored for ``mock``).
        **kwargs: Additional keyword arguments forwarded to the adapter.

    Returns:
        A concrete :class:`~src.sportsbook.base.OddsProvider` instance.

    Raises:
        ValueError: If ``provider`` is not recognised.
    """
    provider_lower = provider.lower()

    if provider_lower == "the_odds_api":
        from src.sportsbook.providers.the_odds_api import TheOddsAPIProvider
        if not api_key:
            raise ValueError(
                "THE_ODDS_API_KEY is required for the_odds_api provider. "
                "Set it in your .env file."
            )
        logger.info("OddsProvider: using TheOddsAPI")
        return TheOddsAPIProvider(api_key=api_key, markets=markets, **kwargs)

    if provider_lower in ("mock", "test"):
        from src.sportsbook.providers.mock import MockOddsProvider
        logger.info("OddsProvider: using Mock (no live API calls)")
        games = kwargs.pop("games", None)
        return MockOddsProvider(games=games)

    if provider_lower == "sportsdataio":
        from src.sportsbook.providers.sportsdataio import SportsDataIOProvider
        logger.info("OddsProvider: using SportsDataIO (stub)")
        return SportsDataIOProvider(api_key=api_key)

    if provider_lower == "oddsjam":
        from src.sportsbook.providers.oddsjam import OddsJamProvider
        logger.info("OddsProvider: using OddsJam (stub — no live data)")
        return OddsJamProvider(api_key=api_key)

    if provider_lower == "none" or not provider_lower:
        logger.info("OddsProvider: none configured — cross-exchange comparison disabled")
        from src.sportsbook.providers.mock import MockOddsProvider
        return MockOddsProvider(games=[])

    raise ValueError(
        f"Unknown odds provider: {provider!r}. "
        "Supported: 'the_odds_api', 'mock', 'sportsdataio', 'oddsjam', 'none'."
    )
