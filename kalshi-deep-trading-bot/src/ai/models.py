"""
Pydantic models for AI-driven trading decisions.

All structured data flowing between the AI client, edge computation,
and execution layer is defined here.
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class MarketProbability(BaseModel):
    """Structured probability estimate for a single market."""

    ticker: str = Field(..., description="Market ticker symbol")
    title: str = Field(default="", description="Human-readable market title")
    research_probability: float = Field(
        ..., ge=0, le=100, description="Research predicted probability (0-100)"
    )
    reasoning: str = Field(..., description="Brief reasoning for the probability estimate")
    confidence: float = Field(
        ..., ge=0, le=1, description="Confidence in the probability estimate (0-1)"
    )


class ProbabilityExtraction(BaseModel):
    """Structured extraction of probabilities from research output."""

    market_probabilities: list[MarketProbability] = Field(
        ..., description="List of market probability estimates"
    )
    overall_summary: str = Field(
        ..., description="Overall research summary and key insights"
    )


class EdgeMetrics(BaseModel):
    """Deterministic edge and sizing metrics for a market."""

    ticker: str = Field(..., description="Market ticker")
    research_prob: float = Field(
        ..., description="Research probability (0-1 fraction)"
    )
    implied_prob: float = Field(
        ..., description="Market implied probability from ask price (0-1 fraction)"
    )
    edge: float = Field(
        ..., description="Raw edge: research_prob - implied_prob"
    )
    r_score: float = Field(
        ..., description="Risk-adjusted edge (z-score): (p-q)/sqrt(p*(1-p))"
    )
    kelly_fraction: float = Field(
        ..., description="Optimal Kelly fraction: (p-q)/(1-q), clamped [0,1]"
    )
    expected_return: float = Field(
        default=0.0, description="Expected return on capital: (p-q)/q"
    )


class BettingDecision(BaseModel):
    """A single betting decision for a market."""

    ticker: str = Field(..., description="Market ticker symbol")
    title: Optional[str] = Field(default=None, description="Human-readable market title")
    event_ticker: Optional[str] = Field(default=None, description="Parent event ticker")
    action: Literal["buy_yes", "buy_no", "skip"] = Field(
        ..., description="Action to take"
    )
    confidence: float = Field(..., ge=0, le=1, description="Confidence in decision (0-1)")
    amount: float = Field(..., ge=0, description="Amount to bet in dollars")
    reasoning: str = Field(..., description="Brief reasoning for the decision")
    edge_metrics: Optional[EdgeMetrics] = Field(
        default=None, description="Computed edge metrics"
    )
    is_hedge: bool = Field(default=False, description="Whether this is a hedge bet")
    hedge_for: Optional[str] = Field(
        default=None, description="Ticker of the main bet this hedges"
    )
    hedge_ratio: Optional[float] = Field(
        default=None, ge=0, le=1, description="Proportion of main bet hedged"
    )
    ai_approved: bool = Field(
        default=True, description="Whether the AI approved this decision"
    )
    skip_reason: Optional[str] = Field(
        default=None, description="Reason for skipping (when action='skip')"
    )


class MarketAnalysis(BaseModel):
    """Aggregated analysis results for all markets in a run."""

    decisions: list[BettingDecision] = Field(
        ..., description="List of betting decisions"
    )
    total_recommended_bet: float = Field(
        ..., description="Total dollar amount recommended to bet"
    )
    high_confidence_bets: int = Field(
        ..., description="Number of high confidence bets (confidence > 0.7)"
    )
    summary: str = Field(..., description="Overall market summary and strategy")


class RunSummary(BaseModel):
    """Summary of a single bot execution run."""

    run_id: str = Field(..., description="Unique identifier for the run")
    timestamp: datetime = Field(..., description="When the run started")
    events_scanned: int = Field(default=0, description="Number of events scanned")
    markets_scanned: int = Field(default=0, description="Number of markets scanned")
    decisions_made: int = Field(default=0, description="Total betting decisions made")
    bets_placed: int = Field(default=0, description="Actual bets placed")
    total_wagered: float = Field(default=0.0, description="Total dollars wagered")
    dry_run: bool = Field(default=True, description="Whether this was a dry run")
    errors: list[str] = Field(default_factory=list, description="Errors encountered")
