"""Tests for src.strategies.filters module."""

from unittest.mock import patch

import pytest

from src.brokers.kalshi.models import KalshiEvent, KalshiMarket
from src.config import StrategyConfig
from src.strategies.filters import MarketFilter


@pytest.fixture
def config():
    return StrategyConfig(
        min_volume=1000,
        min_volume_demo=10,
        min_volume_prod=1000,
        max_spread=0.15,
        min_price=0.05,
        max_price=0.95,
        min_hours_to_expiry=1.0,
        max_hours_to_expiry=168.0,
    )


@pytest.fixture
def market_filter(config):
    # Use is_demo=False so min_volume_prod=1000 applies, matching test expectations
    return MarketFilter(config, is_demo=False)


@pytest.fixture
def event():
    return KalshiEvent(ticker="EVT-1", title="Test Event")


def _make_market(**overrides) -> KalshiMarket:
    defaults = {
        "ticker": "MKT-1",
        "title": "Test Market",
        "volume": 5000,
        "yes_ask": 50,
        "yes_bid": 45,
        "no_ask": 55,
        "no_bid": 50,
        "close_time": "2099-01-01T00:00:00Z",
    }
    defaults.update(overrides)
    return KalshiMarket(**defaults)


# -- Individual filter checks --------------------------------------------------


def test_market_below_min_volume_rejected(market_filter, event):
    market = _make_market(volume=500)
    passes, reason = market_filter.check_market(market, event)
    assert not passes
    assert "volume" in reason


def test_market_wide_spread_rejected(market_filter, event):
    # Spread = (60 - 40) / 60 = 0.333 > 0.15
    market = _make_market(yes_ask=60, yes_bid=40)
    passes, reason = market_filter.check_market(market, event)
    assert not passes
    assert "spread" in reason


def test_market_price_too_low_rejected(market_filter, event):
    # min_price = 0.05 -> 5 cents, yes_ask = 3 -> rejected
    # Set bid close to ask to avoid spread rejection first
    market = _make_market(yes_ask=3, yes_bid=3)
    passes, reason = market_filter.check_market(market, event)
    assert not passes
    assert "min price" in reason


def test_market_price_too_high_rejected(market_filter, event):
    # max_price = 0.95 -> 95 cents, yes_ask = 97 -> rejected
    market = _make_market(yes_ask=97, yes_bid=95)
    passes, reason = market_filter.check_market(market, event)
    assert not passes
    assert "max price" in reason


def test_market_too_close_to_expiry_rejected(market_filter, event):
    # close_time in the past or very soon
    with patch("src.strategies.filters.hours_until", return_value=0.5):
        market = _make_market(close_time="2025-01-01T00:00:00Z")
        passes, reason = market_filter.check_market(market, event)
        assert not passes
        assert "expires" in reason


def test_market_too_far_from_expiry_rejected(market_filter, event):
    with patch("src.strategies.filters.hours_until", return_value=200.0):
        market = _make_market(close_time="2099-01-01T00:00:00Z")
        passes, reason = market_filter.check_market(market, event)
        assert not passes
        assert "expires" in reason


def test_market_missing_orderbook_rejected(market_filter, event):
    with patch("src.strategies.filters.hours_until", return_value=48.0):
        market = _make_market(yes_ask=50, yes_bid=0)
        passes, reason = market_filter.check_market(market, event)
        assert not passes
        assert "orderbook" in reason


# -- Good market passes --------------------------------------------------------


def test_good_market_passes_all_filters(market_filter, event):
    with patch("src.strategies.filters.hours_until", return_value=48.0):
        market = _make_market()
        passes, reason = market_filter.check_market(market, event)
        assert passes
        assert reason == ""


# -- filter_markets batch -----------------------------------------------------


def test_filter_markets_batch(market_filter, event):
    with patch("src.strategies.filters.hours_until", return_value=48.0):
        good = _make_market(ticker="GOOD-1")
        bad = _make_market(ticker="BAD-1", volume=100)

        passing, rejected = market_filter.filter_markets([good, bad], event)
        assert len(passing) == 1
        assert passing[0].ticker == "GOOD-1"
        assert len(rejected) == 1
        assert rejected[0]["ticker"] == "BAD-1"
