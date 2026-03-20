"""
Unit tests for src.sports.detector — pure arbitrage math.

All functions are stateless with no I/O, so no mocking is required.
"""

import pytest

from src.brokers.kalshi.models import KalshiEvent, KalshiMarket
from src.sports.detector import (
    classify_opportunity,
    compute_arb_pct,
    compute_expected_profit,
    compute_total_cost,
    detect_opportunity,
    extract_sport,
    filter_sports,
    is_sports_event,
    is_sports_market,
    rank_opportunities,
)
from src.sports.models import ArbType, Sport


# ---------------------------------------------------------------------------
# Sport classification
# ---------------------------------------------------------------------------


class TestExtractSport:
    def test_nba(self):
        assert extract_sport("NBA-CELTICS-25") == Sport.NBA

    def test_nfl(self):
        assert extract_sport("NFL-KC-MAY15") == Sport.NFL

    def test_mlb(self):
        assert extract_sport("MLB-NYY-SEA-25") == Sport.MLB

    def test_nhl(self):
        assert extract_sport("NHL-TOR-BOS-25") == Sport.NHL

    def test_ufc(self):
        assert extract_sport("UFC-305-CARD") == Sport.UFC

    def test_soccer_mls(self):
        assert extract_sport("MLS-LAFC-NYCFC") == Sport.SOCCER

    def test_tennis(self):
        assert extract_sport("TEN-DJOKOVIC-AUS25") == Sport.TENNIS

    def test_case_insensitive(self):
        assert extract_sport("nba-celtics-25") == Sport.NBA

    def test_non_sports_returns_unknown(self):
        assert extract_sport("KXELECTIONS-25") == Sport.UNKNOWN

    def test_empty_string(self):
        assert extract_sport("") == Sport.UNKNOWN

    def test_politics(self):
        assert extract_sport("PRES-2024-DEM") == Sport.UNKNOWN


class TestIsSportsMarket:
    def test_nba_ticker(self):
        assert is_sports_market("NBA-BOS-CELTICS-25") is True

    def test_nfl_ticker(self):
        assert is_sports_market("NFL-KC-CHI-25") is True

    def test_politics_ticker(self):
        assert is_sports_market("KXELECTIONS-25") is False

    def test_empty_ticker(self):
        assert is_sports_market("") is False


class TestIsSportsEvent:
    def _make_event(self, category: str = "", ticker: str = "EVT-1", markets=None) -> KalshiEvent:
        return KalshiEvent(
            ticker=ticker,
            category=category,
            markets=markets or [],
        )

    def test_sports_category_match(self):
        event = self._make_event(category="Sports")
        assert is_sports_event(event) is True

    def test_nba_category(self):
        event = self._make_event(category="NBA")
        assert is_sports_event(event) is True

    def test_sports_ticker(self):
        event = self._make_event(ticker="NBA-CELTICS-EVT")
        assert is_sports_event(event) is True

    def test_sports_via_nested_market(self):
        market = KalshiMarket(ticker="NBA-BOS-25", yes_ask=50, no_ask=51)
        event = self._make_event(category="other", ticker="OTHER-EVT", markets=[market])
        assert is_sports_event(event) is True

    def test_non_sports_event(self):
        event = self._make_event(category="Politics", ticker="KXELECTIONS-25")
        assert is_sports_event(event) is False


class TestFilterSports:
    def test_empty_returns_all(self):
        result = filter_sports([])
        assert Sport.NBA in result
        assert Sport.NFL in result

    def test_specific_sports(self):
        result = filter_sports(["NBA", "NFL"])
        assert Sport.NBA in result
        assert Sport.NFL in result
        assert Sport.MLB not in result

    def test_case_normalised(self):
        result = filter_sports(["nba"])
        assert "NBA" in result


# ---------------------------------------------------------------------------
# Arbitrage math
# ---------------------------------------------------------------------------


class TestComputeTotalCost:
    def test_basic(self):
        assert compute_total_cost(48, 50) == 98

    def test_breakeven(self):
        assert compute_total_cost(50, 50) == 100

    def test_above_breakeven(self):
        assert compute_total_cost(52, 51) == 103


class TestComputeArbPct:
    def test_pure_arb(self):
        pct = compute_arb_pct(98)
        assert abs(pct - 2.04) < 0.01

    def test_breakeven(self):
        assert compute_arb_pct(100) == 0.0

    def test_near_arb(self):
        pct = compute_arb_pct(102)
        assert abs(pct - (-1.96)) < 0.01

    def test_far_from_arb(self):
        pct = compute_arb_pct(110)
        assert abs(pct - (-9.09)) < 0.01

    def test_zero_cost_returns_zero(self):
        assert compute_arb_pct(0) == 0.0

    def test_ordering(self):
        # Lower total_cost = higher arb_pct
        assert compute_arb_pct(96) > compute_arb_pct(98) > compute_arb_pct(100)


class TestComputeExpectedProfit:
    def test_pure_arb_profit(self):
        profit = compute_expected_profit(98, 50.0)
        assert abs(profit - 2.04) < 0.01  # $2.04 on $100 outlay

    def test_breakeven_no_profit(self):
        assert compute_expected_profit(100, 50.0) == 0.0

    def test_near_arb_small_loss(self):
        profit = compute_expected_profit(102, 50.0)
        assert profit < 0

    def test_zero_cost(self):
        assert compute_expected_profit(0, 50.0) == 0.0

    def test_larger_outlay_scales_profit(self):
        p1 = compute_expected_profit(98, 50.0)
        p2 = compute_expected_profit(98, 100.0)
        assert abs(p2 - p1 * 2) < 0.01


class TestClassifyOpportunity:
    def test_pure_arb(self):
        assert classify_opportunity(2.0, 2.0) == ArbType.PURE_ARB

    def test_pure_arb_very_positive(self):
        assert classify_opportunity(5.0, 2.0) == ArbType.PURE_ARB

    def test_near_arb_within_threshold(self):
        assert classify_opportunity(-1.5, 2.0) == ArbType.NEAR_ARB

    def test_near_arb_just_inside(self):
        assert classify_opportunity(-1.99, 2.0) == ArbType.NEAR_ARB

    def test_no_opportunity_outside_threshold(self):
        assert classify_opportunity(-2.1, 2.0) is None

    def test_zero_returns_near_arb(self):
        # arb_pct=0 means breakeven — not a pure arb but within any threshold
        assert classify_opportunity(0.0, 2.0) == ArbType.NEAR_ARB

    def test_very_negative_returns_none(self):
        assert classify_opportunity(-10.0, 2.0) is None

    def test_tight_threshold(self):
        assert classify_opportunity(-0.4, 0.5) == ArbType.NEAR_ARB
        assert classify_opportunity(-0.6, 0.5) is None


# ---------------------------------------------------------------------------
# detect_opportunity integration
# ---------------------------------------------------------------------------


def _make_sports_market(**kwargs) -> KalshiMarket:
    defaults = dict(
        ticker="NBA-BOS-CELTICS-25",
        title="Will Celtics win?",
        volume=5000,
        yes_ask=48,
        yes_bid=47,
        no_ask=50,
        no_bid=49,
        close_time="2099-01-01T00:00:00Z",
    )
    defaults.update(kwargs)
    return KalshiMarket(**defaults)


def _make_event(**kwargs) -> KalshiEvent:
    defaults = dict(ticker="NBA-CELTICS-EVT", title="Celtics Game", category="Sports")
    defaults.update(kwargs)
    return KalshiEvent(**defaults)


class TestDetectOpportunity:
    SCAN_ID = "test-scan-001"
    THRESHOLD = 2.0

    def test_pure_arb_detected(self):
        market = _make_sports_market(yes_ask=48, no_ask=50)  # total=98
        event = _make_event()
        opp = detect_opportunity(market, event, self.THRESHOLD, self.SCAN_ID)
        assert opp is not None
        assert opp.arb_type == ArbType.PURE_ARB
        assert opp.arb_pct > 0

    def test_near_arb_detected(self):
        market = _make_sports_market(yes_ask=51, no_ask=50)  # total=101
        event = _make_event()
        opp = detect_opportunity(market, event, self.THRESHOLD, self.SCAN_ID)
        assert opp is not None
        assert opp.arb_type == ArbType.NEAR_ARB

    def test_no_opportunity_returns_none(self):
        market = _make_sports_market(yes_ask=55, no_ask=55)  # total=110, -9%
        event = _make_event()
        opp = detect_opportunity(market, event, self.THRESHOLD, self.SCAN_ID)
        assert opp is None

    def test_non_sports_market_returns_none(self):
        market = _make_sports_market(ticker="KXELECTIONS-25", yes_ask=48, no_ask=50)
        event = _make_event(ticker="ELECTIONS-EVT", category="Politics")
        opp = detect_opportunity(market, event, self.THRESHOLD, self.SCAN_ID)
        assert opp is None

    def test_missing_yes_ask_returns_none(self):
        market = _make_sports_market(yes_ask=0, no_ask=50)
        event = _make_event()
        opp = detect_opportunity(market, event, self.THRESHOLD, self.SCAN_ID)
        assert opp is None

    def test_missing_no_ask_returns_none(self):
        market = _make_sports_market(yes_ask=48, no_ask=0)
        event = _make_event()
        opp = detect_opportunity(market, event, self.THRESHOLD, self.SCAN_ID)
        assert opp is None

    def test_sport_filter_excludes(self):
        market = _make_sports_market(ticker="NFL-KC-25", yes_ask=48, no_ask=50)
        event = _make_event()
        opp = detect_opportunity(
            market, event, self.THRESHOLD, self.SCAN_ID, sports_filter={"NBA"}
        )
        assert opp is None

    def test_sport_filter_includes(self):
        market = _make_sports_market(ticker="NBA-BOS-25", yes_ask=48, no_ask=50)
        event = _make_event()
        opp = detect_opportunity(
            market, event, self.THRESHOLD, self.SCAN_ID, sports_filter={"NBA", "NFL"}
        )
        assert opp is not None

    def test_fields_populated(self):
        market = _make_sports_market(yes_ask=48, no_ask=50, volume=1234)
        event = _make_event(title="Celtics vs Heat")
        opp = detect_opportunity(market, event, self.THRESHOLD, self.SCAN_ID)
        assert opp is not None
        assert opp.ticker == market.ticker
        assert opp.event_ticker == event.ticker
        assert opp.total_cost == 98
        assert opp.volume == 1234
        assert opp.sport == Sport.NBA
        assert opp.event_title == "Celtics vs Heat"
        assert opp.scan_id == self.SCAN_ID


# ---------------------------------------------------------------------------
# rank_opportunities
# ---------------------------------------------------------------------------


class TestRankOpportunities:
    SCAN_ID = "rank-test"
    THRESHOLD = 2.0

    def _opp(self, yes_ask: int, no_ask: int, ticker_suffix: str = "") -> object:
        market = _make_sports_market(
            ticker=f"NBA-TEST-{ticker_suffix or yes_ask}",
            yes_ask=yes_ask,
            no_ask=no_ask,
        )
        return detect_opportunity(market, _make_event(), self.THRESHOLD, self.SCAN_ID)

    def test_pure_arb_before_near_arb(self):
        near = self._opp(51, 50)    # total=101, near-arb
        pure = self._opp(48, 50)    # total=98,  pure-arb
        ranked = rank_opportunities([near, pure])
        assert ranked[0].arb_type == ArbType.PURE_ARB
        assert ranked[1].arb_type == ArbType.NEAR_ARB

    def test_higher_arb_pct_first_within_type(self):
        a = self._opp(48, 50, "A")    # total=98
        b = self._opp(45, 50, "B")    # total=95
        ranked = rank_opportunities([a, b])
        # b has total=95 → higher arb_pct
        assert ranked[0].ticker.endswith("-B")

    def test_empty_list(self):
        assert rank_opportunities([]) == []
