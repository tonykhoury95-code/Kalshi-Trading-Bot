"""
Sportsbook odds provider abstraction layer.

Provides a unified interface for fetching reference odds from external
bookmakers. Adapters are plug-in: swap providers without touching scanner code.

Quick start
-----------
    from src.sportsbook.registry import get_provider
    from src.config import SportsbookConfig

    provider = get_provider(SportsbookConfig(provider="the_odds_api", api_key="..."))
    async with provider:
        games = await provider.get_games("NBA")
"""
