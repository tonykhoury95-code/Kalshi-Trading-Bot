"""
Data models for sports arbitrage opportunities.

Keeps sports-specific domain types cleanly separated from the Kalshi broker models.
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ArbType(str, Enum):
    """Classification of an arbitrage or near-arbitrage opportunity."""

    PURE_ARB = "pure_arb"
    # YES_ask + NO_ask < 100: buying both sides guarantees profit regardless of outcome.

    NEAR_ARB = "near_arb"
    # Total cost is 100–(100 + near_arb_threshold): not yet profitable but close.
    # Useful for monitoring: spreads can tighten further, turning into pure arb.

    POSITIVE_EV = "positive_ev"
    # One side appears mispriced vs. a reference (e.g. FanDuel line). Not a full arb.


class Sport(str, Enum):
    """Recognised sport categories inferred from Kalshi market tickers."""

    NBA = "NBA"
    NFL = "NFL"
    MLB = "MLB"
    NHL = "NHL"
    UFC = "UFC"
    SOCCER = "SOC"
    TENNIS = "TEN"
    GOLF = "GOLF"
    UNKNOWN = "UNKNOWN"


class ArbOpportunity(BaseModel):
    """A detected arbitrage or near-arbitrage opportunity on a Kalshi sports market."""

    opp_id: str = Field(..., description="UUID stable for the lifetime of one scan cycle")
    scan_id: str = Field(..., description="Parent scan cycle ID")

    # Market identifiers
    ticker: str = Field(..., description="KalshiMarket.ticker")
    event_ticker: str = Field(..., description="KalshiEvent.ticker")
    market_title: str = Field(default="", description="Human-readable market title")
    event_title: str = Field(default="", description="Human-readable event title")

    # Sport classification
    sport: str = Field(..., description="Sport code, e.g. 'NBA'")

    # Raw prices (in cents, 0–100 scale)
    yes_ask: int = Field(..., description="Current YES ask price in cents")
    no_ask: int = Field(..., description="Current NO ask price in cents")
    yes_bid: int = Field(default=0, description="Current YES bid price in cents")
    no_bid: int = Field(default=0, description="Current NO bid price in cents")

    # Derived arbitrage metrics
    total_cost: int = Field(..., description="yes_ask + no_ask in cents")
    arb_pct: float = Field(
        ...,
        description=(
            "Arbitrage percentage. Positive = guaranteed profit (pure arb). "
            "Negative = cost to achieve breakeven. Formula: ((100/total_cost)-1)*100"
        ),
    )
    arb_type: ArbType = Field(..., description="Classification of this opportunity")

    # Expected profit for a reference bet size
    expected_profit_at_100: float = Field(
        default=0.0,
        description="Expected guaranteed profit in dollars when wagering $50 each side ($100 total)",
    )

    # Market quality
    volume: int = Field(default=0, description="Total market volume")
    volume_24h: int = Field(default=0, description="24h market volume")
    spread_yes: float = Field(default=0.0, description="YES bid-ask spread as fraction")
    spread_no: float = Field(default=0.0, description="NO bid-ask spread as fraction")

    # Timing
    close_time: str = Field(default="", description="ISO 8601 market close time")
    hours_to_close: float = Field(default=0.0, description="Hours until market closes")

    # Meta
    detected_at: str = Field(..., description="ISO 8601 UTC timestamp of detection")
    alerted: bool = Field(default=False, description="Whether an alert was emitted")

    # ---------------------------------------------------------------------------
    # Phase 2 — cross-exchange comparison fields (all optional, default None)
    # ---------------------------------------------------------------------------

    # Provider
    sportsbook_provider: Optional[str] = Field(
        default=None, description="Odds provider name: 'the_odds_api', 'mock', etc."
    )
    sportsbook_game_id: Optional[str] = Field(
        default=None, description="Provider-assigned game ID"
    )
    sportsbook_bookmaker: Optional[str] = Field(
        default=None, description="Bookmaker used for comparison, e.g. 'fanduel'"
    )
    sportsbook_price_american: Optional[int] = Field(
        default=None, description="Sportsbook American odds for the YES-side team"
    )
    sportsbook_implied_prob: Optional[float] = Field(
        default=None, description="Sportsbook implied probability for YES side (0–1)"
    )
    kalshi_implied_prob: Optional[float] = Field(
        default=None, description="Kalshi implied probability for YES side (yes_ask/100)"
    )

    # Edge metrics
    gross_arb_pct: Optional[float] = Field(
        default=None, description="Gross edge % before friction (book_implied - kalshi_implied)*100"
    )
    net_arb_pct: Optional[float] = Field(
        default=None, description="Net edge % after spread friction and fill risk"
    )
    spread_friction_pct: Optional[float] = Field(
        default=None, description="Bid-ask spread friction deducted from gross edge (%)"
    )
    fill_risk_pct: Optional[float] = Field(
        default=None, description="Estimated fill risk deducted from gross edge (%)"
    )
    required_stake_dollars: Optional[float] = Field(
        default=None, description="Stake required for $10 target profit at net edge"
    )

    # Matching
    yes_team: Optional[str] = Field(
        default=None, description="Full team name the Kalshi YES contract covers"
    )
    match_confidence: Optional[float] = Field(
        default=None, description="Market-matching confidence (0–1)"
    )
    match_method: Optional[str] = Field(
        default=None, description="How the match was established: exact/fuzzy/time_only"
    )

    # Status and explanation
    status: str = Field(
        default="new",
        description="Opportunity status: new | active | stale | expired",
    )
    explanation: Optional[str] = Field(
        default=None, description="Plain-English explanation of why this qualifies"
    )

    # Legacy Phase 1 stub fields — kept for backward DB compatibility
    fanduel_price: Optional[float] = Field(
        default=None, description="[Legacy] FanDuel reference price for YES side (cents)"
    )
    fanduel_implied_prob: Optional[float] = Field(
        default=None, description="[Legacy] FanDuel implied probability for YES (fraction)"
    )
    cross_exchange_edge: Optional[float] = Field(
        default=None, description="[Legacy] Edge vs FanDuel reference (fraction)"
    )


class ArbStatus(str, Enum):
    """Status of a same-market Kalshi arbitrage result."""

    PURE_ARB = "pure_arb"   # net_arb_pct > 0 — buy both sides for guaranteed profit
    NEAR_ARB = "near_arb"   # gross arb within threshold but friction makes it unprofitable
    REJECTED = "rejected"   # too far from breakeven


class ArbFriction(BaseModel):
    """Decomposed friction costs for a same-market Kalshi arb opportunity."""

    yes_spread_cents: float = Field(description="YES ask minus YES bid")
    no_spread_cents: float = Field(description="NO ask minus NO bid")
    total_spread_cents: float = Field(description="Sum of both spreads")
    spread_penalty_pct: float = Field(description="Spread cost as % of $100 payout")
    slippage_pct: float = Field(description="Volume-tier slippage estimate (%)")
    liquidity_penalty_pct: float = Field(description="Total-volume liquidity penalty (%)")
    stale_quote_penalty_pct: float = Field(description="Age-of-quote staleness penalty (%)")
    total_friction_pct: float = Field(description="Sum of all friction components (%)")


class ArbResult(BaseModel):
    """Result of the same-market Kalshi arb engine for a single market."""

    result_id: str = Field(description="UUID for this result")
    scan_id: str = Field(description="Parent scan cycle ID")

    ticker: str
    event_ticker: str
    market_title: str = ""
    event_title: str = ""
    sport: str

    yes_ask: int
    no_ask: int
    yes_bid: int = 0
    no_bid: int = 0

    gross_arb_cents: float = Field(description="100 - (yes_ask + no_ask)")
    gross_arb_pct: float = Field(description="((100/total_cost)-1)*100")
    friction: ArbFriction
    net_arb_cents: float = Field(description="gross_arb_cents - total friction in cents")
    net_arb_pct: float = Field(description="gross_arb_pct - total_friction_pct")

    status: ArbStatus
    rejection_reason: Optional[str] = None
    explanation: str = ""

    volume: int = 0
    volume_24h: int = 0
    hours_to_close: float = 0.0
    close_time: str = ""
    detected_at: str


class EVSideMetrics(BaseModel):
    """Per-side (YES or NO) expected-value metrics from sportsbook comparison."""

    side: str = Field(description="'YES' or 'NO'")
    kalshi_ask_cents: int
    kalshi_bid_cents: int
    kalshi_implied_prob: float = Field(description="ask / 100")
    raw_book_prob: float = Field(description="Sportsbook implied prob before vig removal")
    vig_free_prob: float = Field(description="Vig-removed sportsbook probability")
    edge_pct: float = Field(description="(vig_free_prob - kalshi_implied_prob) * 100")
    ev: float = Field(description="Expected value per dollar wagered")
    kelly_fraction: float = Field(description="Fractional Kelly (0–1)")
    recommended_stake_pct: float = Field(description="Recommended bankroll % to wager")


class EVCandidate(BaseModel):
    """A positive-EV betting opportunity derived from sportsbook vs Kalshi comparison."""

    candidate_id: str
    scan_id: str

    ticker: str
    event_ticker: str
    market_title: str = ""
    event_title: str = ""
    sport: str

    yes_ask: int
    no_ask: int
    yes_bid: int = 0
    no_bid: int = 0

    # Sportsbook match
    sportsbook_provider: str = ""
    sportsbook_game_id: str = ""
    sportsbook_bookmaker: str = ""
    match_confidence: float = 0.0
    match_method: str = ""
    yes_team: str = ""
    no_team: str = ""

    # Per-side metrics (None if edge is negative on that side)
    yes_metrics: Optional[EVSideMetrics] = None
    no_metrics: Optional[EVSideMetrics] = None

    # Best side summary
    best_side: str = "NONE"   # "YES", "NO", or "NONE"
    best_edge_pct: float = 0.0
    best_kelly_fraction: float = 0.0

    # Friction
    spread_friction_pct: float = 0.0
    fill_risk_pct: float = 0.0

    explanation: str = ""
    volume: int = 0
    volume_24h: int = 0
    hours_to_close: float = 0.0
    close_time: str = ""
    detected_at: str


class ScanSummary(BaseModel):
    """Summary of a single completed scan cycle."""

    scan_id: str
    started_at: str
    finished_at: str
    duration_seconds: float

    # Counts
    events_fetched: int = 0
    sports_events_found: int = 0
    markets_checked: int = 0
    pure_arbs_found: int = 0
    near_arbs_found: int = 0
    positive_ev_found: int = 0
    arb_results_found: int = 0
    ev_candidates_found: int = 0
    alerts_emitted: int = 0

    # Best opportunity this cycle
    best_arb_pct: Optional[float] = None
    best_ticker: Optional[str] = None
    best_arb_type: Optional[str] = None
