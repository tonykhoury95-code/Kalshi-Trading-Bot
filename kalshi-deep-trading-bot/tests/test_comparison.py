"""
Unit tests for src.sports.comparison — pure cross-exchange arb math.

All functions are stateless with no I/O, so no mocking is required.
"""

import pytest

from src.sports.comparison import (
    classify_cross_exchange,
    fill_risk_estimate,
    gross_edge_pct,
    net_edge_pct,
    required_stake,
    spread_friction_pct,
)
from src.sports.models import ArbType
from src.sportsbook.models import american_to_implied, decimal_to_implied


# ---------------------------------------------------------------------------
# Odds conversion helpers
# ---------------------------------------------------------------------------


class TestAmericanToImplied:
    def test_favourite_negative(self):
        # -150 → 150/250 = 0.60
        assert abs(american_to_implied(-150) - 0.60) < 0.001

    def test_underdog_positive(self):
        # +130 → 100/230 ≈ 0.435
        assert abs(american_to_implied(130) - 0.4348) < 0.001

    def test_even_money(self):
        assert abs(american_to_implied(100) - 0.50) < 0.001

    def test_heavy_favourite(self):
        # -300 → 300/400 = 0.75
        assert abs(american_to_implied(-300) - 0.75) < 0.001


class TestDecimalToImplied:
    def test_even_money(self):
        assert abs(decimal_to_implied(2.0) - 0.50) < 0.001

    def test_favourite(self):
        # 1.667 → 1/1.667 ≈ 0.5999
        assert abs(decimal_to_implied(1.667) - 0.5999) < 0.001

    def test_zero_returns_zero(self):
        assert decimal_to_implied(0) == 0.0


# ---------------------------------------------------------------------------
# Gross edge
# ---------------------------------------------------------------------------


class TestGrossEdgePct:
    def test_positive_edge(self):
        # Kalshi YES = 45¢ (0.45), book = 0.52 → edge = 7%
        pct = gross_edge_pct(0.45, 0.52)
        assert abs(pct - 7.0) < 0.01

    def test_zero_edge(self):
        assert gross_edge_pct(0.50, 0.50) == 0.0

    def test_negative_edge_overpriced_kalshi(self):
        pct = gross_edge_pct(0.55, 0.48)
        assert pct < 0

    def test_direction(self):
        # Higher book_implied relative to kalshi = positive edge
        assert gross_edge_pct(0.40, 0.55) > gross_edge_pct(0.40, 0.45)


# ---------------------------------------------------------------------------
# Spread friction
# ---------------------------------------------------------------------------


class TestSpreadFrictionPct:
    def test_narrow_spread(self):
        # YES ask=50, bid=49 → spread=1, mid=49.5 → friction ≈ 2.02%
        fric = spread_friction_pct(50, 49)
        assert 1.5 < fric < 2.5

    def test_wide_spread(self):
        fric = spread_friction_pct(55, 45)
        assert fric > 10.0

    def test_zero_bid_returns_zero(self):
        assert spread_friction_pct(50, 0) == 0.0

    def test_inverted_returns_zero(self):
        # ask <= bid is invalid
        assert spread_friction_pct(45, 50) == 0.0


# ---------------------------------------------------------------------------
# Fill risk
# ---------------------------------------------------------------------------


class TestFillRiskEstimate:
    def test_high_volume_long_time(self):
        risk = fill_risk_estimate(volume=50_000, hours_to_close=24.0)
        assert risk == 0.0

    def test_low_volume_adds_risk(self):
        risk = fill_risk_estimate(volume=50, hours_to_close=24.0)
        assert risk >= 2.0

    def test_closing_soon_adds_risk(self):
        risk_near = fill_risk_estimate(volume=5_000, hours_to_close=0.3)
        risk_far = fill_risk_estimate(volume=5_000, hours_to_close=24.0)
        assert risk_near > risk_far

    def test_capped_at_five(self):
        risk = fill_risk_estimate(volume=0, hours_to_close=0.1)
        assert risk <= 5.0


# ---------------------------------------------------------------------------
# Net edge
# ---------------------------------------------------------------------------


class TestNetEdgePct:
    def test_basic(self):
        net = net_edge_pct(gross_pct=7.0, spread_fric=1.0, fill_risk=1.0)
        assert abs(net - 5.0) < 0.01

    def test_friction_exceeds_gross(self):
        net = net_edge_pct(gross_pct=2.0, spread_fric=3.0, fill_risk=0.0)
        assert net < 0

    def test_zero_friction(self):
        net = net_edge_pct(gross_pct=5.0, spread_fric=0.0, fill_risk=0.0)
        assert abs(net - 5.0) < 0.01


# ---------------------------------------------------------------------------
# Required stake
# ---------------------------------------------------------------------------


class TestRequiredStake:
    def test_basic(self):
        # $10 target at 2% net edge → $500 stake
        stake = required_stake(target_profit_dollars=10.0, net_edge_pct_val=2.0)
        assert abs(stake - 500.0) < 0.01

    def test_higher_edge_lower_stake(self):
        s1 = required_stake(10.0, 2.0)
        s2 = required_stake(10.0, 4.0)
        assert s2 < s1

    def test_zero_edge_returns_zero(self):
        assert required_stake(10.0, 0.0) == 0.0

    def test_negative_edge_returns_zero(self):
        assert required_stake(10.0, -1.0) == 0.0


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


class TestClassifyCrossExchange:
    def test_strong_positive_ev(self):
        result = classify_cross_exchange(gross_pct=4.0, net_pct=2.5, min_net_pct=1.0)
        assert result == ArbType.POSITIVE_EV

    def test_near_arb_at_breakeven(self):
        result = classify_cross_exchange(gross_pct=0.5, net_pct=0.0, min_net_pct=1.0)
        assert result == ArbType.NEAR_ARB

    def test_below_threshold_returns_none(self):
        result = classify_cross_exchange(gross_pct=1.0, net_pct=-2.0, min_net_pct=1.0)
        assert result is None

    def test_pure_arb_rare(self):
        # gross >= 5% AND net > 0 → PURE_ARB label (cross-exchange)
        result = classify_cross_exchange(gross_pct=6.0, net_pct=3.0, min_net_pct=1.0)
        assert result == ArbType.PURE_ARB

    def test_high_gross_but_negative_net_is_none(self):
        result = classify_cross_exchange(gross_pct=6.0, net_pct=-1.0, min_net_pct=1.0)
        assert result is None
