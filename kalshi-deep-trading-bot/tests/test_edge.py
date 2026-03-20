"""Tests for src.strategies.edge module."""

import math

import pytest

from src.strategies.edge import (
    compute_edge,
    compute_edge_metrics,
    compute_implied_probability,
    compute_kelly_fraction,
    compute_kelly_position_size,
    compute_r_score,
)


# -- compute_implied_probability ----------------------------------------------


def test_implied_probability_50_cents():
    assert compute_implied_probability(50) == 0.50


def test_implied_probability_0_cents():
    assert compute_implied_probability(0) == 0.0


def test_implied_probability_100_cents():
    assert compute_implied_probability(100) == 1.0


def test_implied_probability_25_cents():
    assert compute_implied_probability(25) == 0.25


# -- compute_edge -------------------------------------------------------------


def test_edge_positive():
    assert compute_edge(0.65, 0.50) == pytest.approx(0.15)


def test_edge_negative():
    assert compute_edge(0.40, 0.50) == pytest.approx(-0.10)


def test_edge_zero():
    assert compute_edge(0.50, 0.50) == pytest.approx(0.0)


# -- compute_r_score ----------------------------------------------------------


def test_r_score_known_values():
    # p=0.7, q=0.5 -> (0.7-0.5)/sqrt(0.7*0.3) = 0.2/sqrt(0.21)
    expected = 0.2 / math.sqrt(0.21)
    assert compute_r_score(0.7, 0.5) == pytest.approx(expected, rel=1e-6)


def test_r_score_p_equals_zero():
    assert compute_r_score(0.0, 0.5) == 0.0


def test_r_score_p_equals_one():
    assert compute_r_score(1.0, 0.5) == 0.0


def test_r_score_negative():
    # p < q -> negative r_score
    assert compute_r_score(0.3, 0.5) < 0


def test_r_score_symmetric():
    # Edge case: p=0.5, q=0.5 -> 0
    assert compute_r_score(0.5, 0.5) == pytest.approx(0.0)


# -- compute_kelly_fraction ---------------------------------------------------


def test_kelly_fraction_positive_edge():
    # p=0.6, q=0.4 -> (0.6-0.4)/(1-0.4) = 0.2/0.6
    expected = 0.2 / 0.6
    assert compute_kelly_fraction(0.6, 0.4) == pytest.approx(expected)


def test_kelly_fraction_no_edge():
    # p=0.5, q=0.5 -> 0
    assert compute_kelly_fraction(0.5, 0.5) == pytest.approx(0.0)


def test_kelly_fraction_negative_edge():
    # p=0.3, q=0.5 -> (0.3-0.5)/(1-0.5) = -0.4 -> clamped to 0
    assert compute_kelly_fraction(0.3, 0.5) == 0.0


def test_kelly_fraction_clamped_at_one():
    # p=0.99, q=0.01 -> (0.99-0.01)/(1-0.01) = 0.98/0.99 ~= 0.9899
    result = compute_kelly_fraction(0.99, 0.01)
    assert 0.0 <= result <= 1.0


def test_kelly_fraction_q_equals_one():
    assert compute_kelly_fraction(0.5, 1.0) == 0.0


# -- compute_kelly_position_size ----------------------------------------------


def test_position_size_basic():
    size = compute_kelly_position_size(
        kelly_fraction=0.1,
        bankroll=1000.0,
        kelly_multiplier=0.5,
        max_fraction=0.1,
        max_bet=100.0,
    )
    # 0.1 * 0.5 = 0.05 -> 1000 * 0.05 = 50
    assert size == pytest.approx(50.0)


def test_position_size_respects_max_fraction():
    size = compute_kelly_position_size(
        kelly_fraction=0.5,
        bankroll=1000.0,
        kelly_multiplier=1.0,
        max_fraction=0.1,
        max_bet=1000.0,
    )
    # 0.5 * 1.0 = 0.5 -> 1000 * 0.5 = 500, but max_fraction caps at 100
    assert size == pytest.approx(100.0)


def test_position_size_respects_max_bet():
    size = compute_kelly_position_size(
        kelly_fraction=0.5,
        bankroll=10000.0,
        kelly_multiplier=1.0,
        max_fraction=0.5,
        max_bet=50.0,
    )
    assert size == pytest.approx(50.0)


def test_position_size_floors_at_one():
    size = compute_kelly_position_size(
        kelly_fraction=0.0001,
        bankroll=100.0,
        kelly_multiplier=0.5,
        max_fraction=0.1,
        max_bet=100.0,
    )
    assert size == 1.0


def test_position_size_zero_kelly():
    size = compute_kelly_position_size(
        kelly_fraction=0.0,
        bankroll=1000.0,
        kelly_multiplier=0.5,
        max_fraction=0.1,
        max_bet=100.0,
    )
    assert size == 1.0


# -- compute_edge_metrics (integration) ---------------------------------------


def test_edge_metrics_buy_yes():
    metrics = compute_edge_metrics(
        ticker="TEST-YES",
        research_prob_pct=70.0,
        market_ask_cents=50,
        action="buy_yes",
    )
    assert metrics.ticker == "TEST-YES"
    assert metrics.research_prob == pytest.approx(0.7)
    assert metrics.implied_prob == pytest.approx(0.5)
    assert metrics.edge == pytest.approx(0.2)
    assert metrics.r_score > 0
    assert 0 <= metrics.kelly_fraction <= 1


def test_edge_metrics_buy_no():
    metrics = compute_edge_metrics(
        ticker="TEST-NO",
        research_prob_pct=30.0,  # YES prob = 30%, so NO prob = 70%
        market_ask_cents=40,  # NO ask is 40 cents
        action="buy_no",
    )
    assert metrics.ticker == "TEST-NO"
    assert metrics.research_prob == pytest.approx(0.7)  # 1 - 0.30
    assert metrics.implied_prob == pytest.approx(0.4)
    assert metrics.edge == pytest.approx(0.3)
    assert metrics.r_score > 0
