"""
Unit tests for the sportsbook package — models, mock provider, and registry.

Uses only the mock provider so no live API calls are made.
"""

import pytest
import asyncio
from datetime import datetime, timedelta, timezone

from src.sportsbook.models import (
    BookmakerLines,
    MarketLine,
    MarketType,
    Outcome,
    SportsbookGame,
    american_to_implied,
    decimal_to_implied,
    implied_to_american,
)
from src.sportsbook.providers.mock import MockOddsProvider, _game
from src.sportsbook.registry import get_provider


# ---------------------------------------------------------------------------
# Model helpers
# ---------------------------------------------------------------------------


class TestOutcomeImpliedProbComputation:
    def test_american_negative_computed(self):
        o = Outcome(name="Team A", price_american=-200)
        assert abs(o.implied_prob - 2 / 3) < 0.001

    def test_american_positive_computed(self):
        o = Outcome(name="Team B", price_american=150)
        assert abs(o.implied_prob - 100 / 250) < 0.001

    def test_decimal_computed(self):
        o = Outcome(name="Team A", price_decimal=2.0)
        assert abs(o.implied_prob - 0.5) < 0.001

    def test_explicit_implied_not_overwritten(self):
        o = Outcome(name="X", price_american=-200, implied_prob=0.99)
        # explicit implied_prob should be kept
        assert o.implied_prob == 0.99


class TestMarketLineGetOutcome:
    def _market(self) -> MarketLine:
        return MarketLine(
            market_type=MarketType.MONEYLINE,
            outcomes=[
                Outcome(name="Boston Celtics", price_american=-160),
                Outcome(name="Miami Heat", price_american=135),
            ],
        )

    def test_exact_match(self):
        m = self._market()
        o = m.get_outcome("Boston Celtics")
        assert o is not None
        assert o.name == "Boston Celtics"

    def test_partial_match(self):
        m = self._market()
        o = m.get_outcome("Celtics")
        assert o is not None

    def test_no_match_returns_none(self):
        m = self._market()
        o = m.get_outcome("Lakers")
        assert o is None

    def test_case_insensitive(self):
        m = self._market()
        o = m.get_outcome("boston celtics")
        assert o is not None


class TestSportsbookGameHelpers:
    def _game(self) -> SportsbookGame:
        ml = MarketLine(
            market_type=MarketType.MONEYLINE,
            outcomes=[
                Outcome(name="Boston Celtics", price_american=-160),
                Outcome(name="Miami Heat", price_american=135),
            ],
        )
        bk = BookmakerLines(key="fanduel", title="FanDuel", markets=[ml])
        return SportsbookGame(
            game_id="g001",
            provider="mock",
            sport="NBA",
            sport_key="basketball_nba",
            commence_time=datetime.now(timezone.utc) + timedelta(hours=3),
            home_team="Boston Celtics",
            away_team="Miami Heat",
            bookmakers=[bk],
        )

    def test_get_moneyline(self):
        g = self._game()
        ml = g.get_moneyline("fanduel")
        assert ml is not None
        assert ml.market_type == MarketType.MONEYLINE

    def test_get_outcome_odds_home(self):
        g = self._game()
        o = g.get_outcome_odds("Boston Celtics", "fanduel")
        assert o is not None
        assert o.price_american == -160

    def test_get_outcome_odds_away(self):
        g = self._game()
        o = g.get_outcome_odds("Miami Heat", "fanduel")
        assert o is not None
        assert o.price_american == 135

    def test_teams_property(self):
        g = self._game()
        assert "Boston Celtics" in g.teams
        assert "Miami Heat" in g.teams

    def test_missing_bookmaker_returns_first(self):
        g = self._game()
        bk = g.get_bookmaker("draftkings")
        # Falls back to first available bookmaker
        assert bk is not None
        assert bk.key == "fanduel"


# ---------------------------------------------------------------------------
# Mock provider
# ---------------------------------------------------------------------------


class TestMockOddsProvider:
    @pytest.fixture
    def provider(self):
        return MockOddsProvider()

    def test_get_games_nba(self, provider):
        games = asyncio.get_event_loop().run_until_complete(provider.get_games("NBA"))
        assert len(games) > 0
        for g in games:
            assert g.sport == "NBA"

    def test_get_games_nfl(self, provider):
        games = asyncio.get_event_loop().run_until_complete(provider.get_games("NFL"))
        assert len(games) > 0

    def test_unknown_sport_returns_empty(self, provider):
        games = asyncio.get_event_loop().run_until_complete(provider.get_games("XYZZY"))
        assert games == []

    def test_game_has_bookmaker(self, provider):
        games = asyncio.get_event_loop().run_until_complete(provider.get_games("NBA"))
        g = games[0]
        assert len(g.bookmakers) > 0
        ml = g.get_moneyline()
        assert ml is not None

    def test_game_outcomes_have_implied_prob(self, provider):
        games = asyncio.get_event_loop().run_until_complete(provider.get_games("NBA"))
        ml = games[0].get_moneyline()
        for outcome in ml.outcomes:
            assert 0 < outcome.implied_prob < 1

    def test_custom_games_injected(self):
        custom = [
            _game("x001", "NBA", "basketball_nba", "Fake A", "Fake B", -200, +170)
        ]
        provider = MockOddsProvider(games=custom)
        games = asyncio.get_event_loop().run_until_complete(provider.get_games("NBA"))
        assert len(games) == 1
        assert games[0].home_team == "Fake A"

    def test_get_all_games(self, provider):
        result = asyncio.get_event_loop().run_until_complete(
            provider.get_all_games(["NBA", "NFL"])
        )
        assert "NBA" in result
        assert "NFL" in result
        assert len(result["NBA"]) > 0
        assert len(result["NFL"]) > 0

    def test_name_property(self, provider):
        assert provider.name == "mock"

    def test_supported_sports(self, provider):
        sports = provider.supported_sports
        assert "NBA" in sports
        assert "NFL" in sports


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_mock_provider(self):
        p = get_provider("mock")
        assert p.name == "mock"

    def test_test_alias(self):
        p = get_provider("test")
        assert p.name == "mock"

    def test_none_returns_empty_mock(self):
        p = get_provider("none")
        games = asyncio.get_event_loop().run_until_complete(p.get_games("NBA"))
        assert games == []

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown odds provider"):
            get_provider("totally_unknown_xyz")

    def test_the_odds_api_requires_key(self):
        with pytest.raises(ValueError, match="THE_ODDS_API_KEY"):
            get_provider("the_odds_api", api_key="")
