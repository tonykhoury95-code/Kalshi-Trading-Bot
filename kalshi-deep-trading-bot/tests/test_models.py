"""Tests for src.ai.models module."""

import pytest
from pydantic import ValidationError

from src.ai.models import (
    BettingDecision,
    EdgeMetrics,
    MarketAnalysis,
    MarketProbability,
    ProbabilityExtraction,
)


# -- BettingDecision -----------------------------------------------------------


def test_betting_decision_valid_buy_yes():
    d = BettingDecision(
        ticker="TEST-1",
        action="buy_yes",
        confidence=0.8,
        amount=50.0,
        reasoning="Good edge",
    )
    assert d.action == "buy_yes"
    assert d.is_hedge is False


def test_betting_decision_valid_skip():
    d = BettingDecision(
        ticker="TEST-2",
        action="skip",
        confidence=0.3,
        amount=0.0,
        reasoning="No edge",
        skip_reason="R-score too low",
    )
    assert d.action == "skip"
    assert d.skip_reason == "R-score too low"


def test_betting_decision_invalid_action():
    with pytest.raises(ValidationError):
        BettingDecision(
            ticker="TEST-3",
            action="sell",  # Not a valid literal
            confidence=0.5,
            amount=10.0,
            reasoning="test",
        )


def test_betting_decision_confidence_out_of_range():
    with pytest.raises(ValidationError):
        BettingDecision(
            ticker="TEST-4",
            action="buy_yes",
            confidence=1.5,  # > 1.0
            amount=10.0,
            reasoning="test",
        )


# -- EdgeMetrics ---------------------------------------------------------------


def test_edge_metrics_correct_values():
    em = EdgeMetrics(
        ticker="TEST",
        research_prob=0.7,
        implied_prob=0.5,
        edge=0.2,
        r_score=0.436,
        kelly_fraction=0.4,
        expected_return=0.4,
    )
    assert em.edge == pytest.approx(0.2)
    assert em.research_prob == 0.7
    assert em.implied_prob == 0.5


# -- MarketAnalysis ------------------------------------------------------------


def test_market_analysis_aggregates():
    decisions = [
        BettingDecision(
            ticker="A", action="buy_yes", confidence=0.9, amount=50.0, reasoning="Good"
        ),
        BettingDecision(
            ticker="B", action="skip", confidence=0.3, amount=0.0, reasoning="Bad"
        ),
        BettingDecision(
            ticker="C", action="buy_no", confidence=0.85, amount=30.0, reasoning="OK"
        ),
    ]
    analysis = MarketAnalysis(
        decisions=decisions,
        total_recommended_bet=80.0,
        high_confidence_bets=2,
        summary="Test summary",
    )
    assert len(analysis.decisions) == 3
    assert analysis.total_recommended_bet == 80.0
    assert analysis.high_confidence_bets == 2


# -- ProbabilityExtraction ----------------------------------------------------


def test_probability_extraction_parses():
    pe = ProbabilityExtraction(
        market_probabilities=[
            MarketProbability(
                ticker="MKT-1",
                title="Test Market",
                research_probability=65.0,
                reasoning="Strong fundamentals",
                confidence=0.8,
            ),
            MarketProbability(
                ticker="MKT-2",
                title="Other Market",
                research_probability=30.0,
                reasoning="Weak outlook",
                confidence=0.6,
            ),
        ],
        overall_summary="Mixed signals",
    )
    assert len(pe.market_probabilities) == 2
    assert pe.market_probabilities[0].research_probability == 65.0
    assert pe.overall_summary == "Mixed signals"


def test_probability_out_of_range():
    with pytest.raises(ValidationError):
        MarketProbability(
            ticker="BAD",
            title="Bad",
            research_probability=150.0,  # > 100
            reasoning="test",
            confidence=0.5,
        )
