"""
Pure mathematical functions for edge computation and position sizing.

All functions are stateless and perform no I/O.  They operate on plain
numeric inputs and return numeric outputs (or ``EdgeMetrics`` models).
"""

import math

from src.ai.models import EdgeMetrics


def compute_implied_probability(price_cents: float) -> float:
    """Convert a price in cents (0-100) to an implied probability (0-1).

    Args:
        price_cents: Market price in cents (e.g. 50 = 50 cents).

    Returns:
        Implied probability as a fraction (e.g. 0.50).
    """
    return price_cents / 100.0


def compute_edge(research_prob: float, implied_prob: float) -> float:
    """Compute raw edge as the difference between research and implied probabilities.

    Args:
        research_prob: Our estimated probability (0-1 fraction).
        implied_prob: Market implied probability (0-1 fraction).

    Returns:
        Edge value (positive = underpriced, negative = overpriced).
    """
    return research_prob - implied_prob


def compute_r_score(research_prob: float, implied_prob: float) -> float:
    """Compute risk-adjusted edge (R-score / z-score).

    Formula: (p - q) / sqrt(p * (1 - p))

    This measures how many standard deviations the market is from our
    estimate of fair value.

    Args:
        research_prob: Our estimated probability (0-1).
        implied_prob: Market implied probability (0-1).

    Returns:
        R-score value. Returns 0.0 for edge cases where p is 0 or 1.
    """
    p = research_prob
    q = implied_prob

    # Handle edge cases
    if p <= 0.0 or p >= 1.0:
        return 0.0

    variance = p * (1.0 - p)
    if variance <= 0.0:
        return 0.0

    return (p - q) / math.sqrt(variance)


def compute_kelly_fraction(research_prob: float, implied_prob: float) -> float:
    """Compute the optimal Kelly fraction for position sizing.

    Formula: (p - q) / (1 - q)

    Args:
        research_prob: Our estimated probability (0-1).
        implied_prob: Market implied probability (0-1).

    Returns:
        Kelly fraction clamped to [0, 1].
    """
    p = research_prob
    q = implied_prob

    if q >= 1.0:
        return 0.0

    kelly = (p - q) / (1.0 - q)
    return max(0.0, min(1.0, kelly))


def compute_kelly_position_size(
    kelly_fraction: float,
    bankroll: float,
    kelly_multiplier: float,
    max_fraction: float,
    max_bet: float,
) -> float:
    """Calculate position size in dollars using fractional Kelly criterion.

    Args:
        kelly_fraction: Raw Kelly fraction (0-1).
        bankroll: Total bankroll in dollars.
        kelly_multiplier: Fraction of Kelly to use (e.g. 0.5 = half-Kelly).
        max_fraction: Maximum fraction of bankroll per bet.
        max_bet: Absolute maximum bet in dollars.

    Returns:
        Position size in dollars, floored at 1.0.
    """
    if kelly_fraction <= 0:
        return 1.0

    # Apply fractional Kelly
    adjusted = kelly_fraction * kelly_multiplier

    # Dollar size
    size = bankroll * adjusted

    # Cap at max fraction of bankroll
    size = min(size, bankroll * max_fraction)

    # Cap at absolute max
    size = min(size, max_bet)

    # Floor at $1
    return max(size, 1.0)


def compute_edge_metrics(
    ticker: str,
    research_prob_pct: float,
    market_ask_cents: float,
    action: str,
) -> EdgeMetrics:
    """Orchestrate all edge computations for a single market.

    Handles buy_yes vs buy_no direction correctly:
    - For buy_yes: use research_prob directly, implied from yes_ask.
    - For buy_no: use (1 - research_prob), implied from no_ask.

    Args:
        ticker: Market ticker symbol.
        research_prob_pct: Research probability (0-100 scale).
        market_ask_cents: Ask price in cents for the relevant side.
        action: ``"buy_yes"`` or ``"buy_no"``.

    Returns:
        Fully computed ``EdgeMetrics``.
    """
    # Convert research probability to 0-1
    research_prob_raw = research_prob_pct / 100.0

    # Adjust for direction
    if action == "buy_no":
        p = 1.0 - research_prob_raw
    else:
        p = research_prob_raw

    q = compute_implied_probability(market_ask_cents)

    edge = compute_edge(p, q)
    r_score = compute_r_score(p, q)
    kelly = compute_kelly_fraction(p, q)

    # Expected return: (p - q) / q, guarded against division by zero
    expected_return = (p - q) / q if q > 0 else 0.0

    return EdgeMetrics(
        ticker=ticker,
        research_prob=p,
        implied_prob=q,
        edge=edge,
        r_score=r_score,
        kelly_fraction=kelly,
        expected_return=expected_return,
    )
