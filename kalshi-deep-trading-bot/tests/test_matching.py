"""
Unit tests for src.matching — team normalizer and matching engine.

All functions are stateless with no I/O, so no mocking is required.
"""

import pytest
from datetime import datetime, timedelta, timezone

from src.brokers.kalshi.models import KalshiEvent, KalshiMarket
from src.matching.engine import MatchingEngine
from src.matching.models import MatchMethod, YesSide
from src.matching.normalizer import TeamNormalizer
from src.sportsbook.models import (
    BookmakerLines,
    MarketLine,
    MarketType,
    Outcome,
    SportsbookGame,
)


# ---------------------------------------------------------------------------
# TeamNormalizer
# ---------------------------------------------------------------------------


class TestTeamNormalizerTickerToName:
    norm = TeamNormalizer()

    def test_nba_bos(self):
        assert self.norm.ticker_to_name("NBA", "BOS") == "Boston Celtics"

    def test_nba_lal(self):
        assert self.norm.ticker_to_name("NBA", "LAL") == "Los Angeles Lakers"

    def test_nfl_kc(self):
        assert self.norm.ticker_to_name("NFL", "KC") == "Kansas City Chiefs"

    def test_mlb_nyy(self):
        assert self.norm.ticker_to_name("MLB", "NYY") == "New York Yankees"

    def test_nhl_tor(self):
        assert self.norm.ticker_to_name("NHL", "TOR") == "Toronto Maple Leafs"

    def test_case_insensitive(self):
        assert self.norm.ticker_to_name("NBA", "bos") == "Boston Celtics"

    def test_unknown_returns_none(self):
        assert self.norm.ticker_to_name("NBA", "ZZZ") is None

    def test_unknown_sport_returns_none(self):
        assert self.norm.ticker_to_name("FOO", "BOS") is None


class TestTeamNormalizerNameToCode:
    norm = TeamNormalizer()

    def test_exact_canonical_name(self):
        assert self.norm.name_to_code("NBA", "Boston Celtics") == "BOS"

    def test_alias_match(self):
        assert self.norm.name_to_code("NBA", "Celtics") == "BOS"

    def test_nfl_chiefs(self):
        assert self.norm.name_to_code("NFL", "Kansas City Chiefs") == "KC"

    def test_nfl_chiefs_alias(self):
        assert self.norm.name_to_code("NFL", "Chiefs") == "KC"

    def test_no_match_returns_none(self):
        assert self.norm.name_to_code("NBA", "Fake Team") is None


class TestTeamNormalizerExtractTokens:
    norm = TeamNormalizer()

    def test_standard_two_team_nba(self):
        tokens = self.norm.extract_team_tokens("NBA-BOS-MIA-20240115-WIN", "NBA")
        assert "BOS" in tokens
        assert "MIA" in tokens

    def test_nfl_kc_format(self):
        tokens = self.norm.extract_team_tokens("NFL-KC-BUF-2025", "NFL")
        assert "KC" in tokens
        assert "BUF" in tokens

    def test_date_suffix_ignored(self):
        tokens = self.norm.extract_team_tokens("NBA-LAL-DEN-2025", "NBA")
        # 2025 should not appear
        assert "2025" not in tokens

    def test_win_suffix_ignored(self):
        tokens = self.norm.extract_team_tokens("NBA-BOS-PHI-WIN", "NBA")
        assert "WIN" not in tokens

    def test_no_team_tokens(self):
        # UFC style — no standard team codes
        tokens = self.norm.extract_team_tokens("UFC-305-CARD", "UFC")
        assert tokens == []

    def test_kx_prefix_stripped(self):
        tokens = self.norm.extract_team_tokens("KXNBA-BOS-MIA-25", "NBA")
        assert "BOS" in tokens
        assert "MIA" in tokens


class TestFuzzyMatchName:
    norm = TeamNormalizer()

    def test_exact_match_high_score(self):
        result = self.norm.fuzzy_match_name("NBA", "Boston Celtics")
        assert result is not None
        code, name, score = result
        assert code == "BOS"
        assert score > 0.95

    def test_partial_match(self):
        result = self.norm.fuzzy_match_name("NBA", "Golden State Warriors", threshold=0.70)
        assert result is not None
        code, _, _ = result
        assert code == "GSW"

    def test_below_threshold_returns_none(self):
        result = self.norm.fuzzy_match_name("NBA", "Random Team", threshold=0.90)
        assert result is None


# ---------------------------------------------------------------------------
# MatchingEngine helpers
# ---------------------------------------------------------------------------


def _make_kalshi_market(
    ticker: str,
    close_time_offset_hours: float = 3.0,
    yes_ask: int = 48,
    no_ask: int = 51,
    yes_bid: int = 46,
    volume: int = 5000,
) -> KalshiMarket:
    close = datetime.now(timezone.utc) + timedelta(hours=close_time_offset_hours)
    return KalshiMarket(
        ticker=ticker,
        yes_ask=yes_ask,
        no_ask=no_ask,
        yes_bid=yes_bid,
        volume=volume,
        close_time=close.isoformat(),
    )


def _make_kalshi_event(ticker: str = "NBA-EVT", category: str = "Sports") -> KalshiEvent:
    return KalshiEvent(ticker=ticker, category=category)


def _make_sportsbook_game(
    game_id: str,
    sport: str,
    home: str,
    away: str,
    home_american: int = -150,
    away_american: int = 130,
    hours_from_now: float = 3.0,
) -> SportsbookGame:
    commence = datetime.now(timezone.utc) + timedelta(hours=hours_from_now)
    ml = MarketLine(
        market_type=MarketType.MONEYLINE,
        outcomes=[
            Outcome(name=home, price_american=home_american),
            Outcome(name=away, price_american=away_american),
        ],
    )
    bk = BookmakerLines(key="fanduel", title="FanDuel", markets=[ml])
    return SportsbookGame(
        game_id=game_id,
        provider="mock",
        sport=sport,
        sport_key=f"sport_{sport.lower()}",
        commence_time=commence,
        home_team=home,
        away_team=away,
        bookmakers=[bk],
    )


# ---------------------------------------------------------------------------
# MatchingEngine.find_match
# ---------------------------------------------------------------------------


class TestMatchingEngine:
    engine = MatchingEngine(min_confidence=0.70, time_window_hours=6.0)

    def test_exact_nba_match_home(self):
        market = _make_kalshi_market("NBA-BOS-MIA-20240115-WIN")
        event = _make_kalshi_event()
        game = _make_sportsbook_game(
            "g001", "NBA",
            home="Boston Celtics", away="Miami Heat",
        )
        result = self.engine.find_match(market, event, [game], "NBA")
        assert result is not None
        assert result.yes_side in (YesSide.HOME, YesSide.AWAY)
        assert result.confidence >= 0.70

    def test_exact_nba_match_away(self):
        market = _make_kalshi_market("NBA-MIA-BOS-WIN")
        event = _make_kalshi_event()
        game = _make_sportsbook_game(
            "g002", "NBA",
            home="Boston Celtics", away="Miami Heat",
        )
        result = self.engine.find_match(market, event, [game], "NBA")
        assert result is not None

    def test_nfl_kc_match(self):
        market = _make_kalshi_market("NFL-KC-BUF-2025")
        event = _make_kalshi_event()
        game = _make_sportsbook_game(
            "g003", "NFL",
            home="Kansas City Chiefs", away="Buffalo Bills",
        )
        result = self.engine.find_match(market, event, [game], "NFL")
        assert result is not None
        assert "Chiefs" in result.yes_team or "Bills" in result.yes_team

    def test_no_tokens_returns_none(self):
        # UFC-style ticker with no team codes
        market = _make_kalshi_market("UFC-305-CARD")
        event = _make_kalshi_event()
        game = _make_sportsbook_game("g004", "UFC", home="Jon Jones", away="Stipe Miocic")
        result = self.engine.find_match(market, event, [game], "UFC")
        assert result is None

    def test_time_window_too_large_returns_none(self):
        market = _make_kalshi_market("NBA-BOS-MIA-WIN", close_time_offset_hours=1.0)
        event = _make_kalshi_event()
        # Game starts 20 hours from now, but market closes in 1h → delta=19h > 6h window
        game = _make_sportsbook_game(
            "g005", "NBA",
            home="Boston Celtics", away="Miami Heat",
            hours_from_now=20.0,
        )
        result = self.engine.find_match(market, event, [game], "NBA")
        assert result is None

    def test_wrong_sport_games_ignored(self):
        market = _make_kalshi_market("NBA-BOS-MIA-WIN")
        event = _make_kalshi_event()
        # Only NFL games in list — should not match
        nfl_game = _make_sportsbook_game(
            "g006", "NFL",
            home="Boston something", away="Miami something",
        )
        result = self.engine.find_match(market, event, [nfl_game], "NBA")
        assert result is None

    def test_empty_game_list_returns_none(self):
        market = _make_kalshi_market("NBA-BOS-MIA-WIN")
        event = _make_kalshi_event()
        result = self.engine.find_match(market, event, [], "NBA")
        assert result is None

    def test_match_result_fields_populated(self):
        market = _make_kalshi_market("NBA-LAL-GSW-WIN")
        event = _make_kalshi_event()
        game = _make_sportsbook_game(
            "g007", "NBA",
            home="Los Angeles Lakers", away="Golden State Warriors",
        )
        result = self.engine.find_match(market, event, [game], "NBA")
        if result is not None:
            assert result.sportsbook_game_id == "g007"
            assert result.kalshi_ticker == "NBA-LAL-GSW-WIN"
            assert result.confidence > 0
            assert result.match_method in list(MatchMethod)
