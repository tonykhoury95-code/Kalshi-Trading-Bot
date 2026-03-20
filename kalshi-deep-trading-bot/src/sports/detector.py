"""
Pure-function sports arbitrage detection.

All functions here are stateless and have no I/O — designed to be tested in
isolation without mocking. The scanner calls these per-market in its hot loop.

Arbitrage math
--------------
Kalshi binary markets have two sides: YES and NO.  Each resolves to $1.00
(100 cents) and is mutually exclusive — exactly one pays out.

If you buy BOTH sides simultaneously:
  - Cost    = yes_ask + no_ask  (cents)
  - Payout  = 100 cents (whichever side wins)
  - Profit  = 100 - total_cost

So:
  arb_pct = ((100 / total_cost) - 1) * 100

Examples
--------
  yes_ask=48, no_ask=50 → total=98  → arb_pct = +2.04%  ← PURE ARB
  yes_ask=50, no_ask=50 → total=100 → arb_pct = 0.00%   ← breakeven
  yes_ask=51, no_ask=52 → total=103 → arb_pct = -2.91%  ← near-arb
  yes_ask=60, no_ask=55 → total=115 → arb_pct = -13.0%  ← no opportunity
"""

from datetime import datetime, timezone
from typing import Optional
import uuid

from src.brokers.kalshi.models import KalshiEvent, KalshiMarket
from src.sports.models import ArbOpportunity, ArbType, Sport

# ---------------------------------------------------------------------------
# Sport classification
# ---------------------------------------------------------------------------

# Maps known Kalshi ticker prefixes to Sport codes.
# Kalshi sports market tickers follow the pattern: SPORT-TEAM-DATE or similar.
SPORT_PREFIX_MAP: dict[str, str] = {
    "NBA": Sport.NBA,
    "NFL": Sport.NFL,
    "MLB": Sport.MLB,
    "NHL": Sport.NHL,
    "UFC": Sport.UFC,
    "SOC": Sport.SOCCER,
    "MLS": Sport.SOCCER,
    "EPL": Sport.SOCCER,
    "UCL": Sport.SOCCER,
    "TEN": Sport.TENNIS,
    "ATP": Sport.TENNIS,
    "WTA": Sport.TENNIS,
    "GOLF": Sport.GOLF,
    "PGA": Sport.GOLF,
}

# Kalshi categories that indicate sports events (case-insensitive match)
SPORTS_CATEGORIES: frozenset[str] = frozenset(
    [
        "sports",
        "nba",
        "nfl",
        "mlb",
        "nhl",
        "ufc",
        "soccer",
        "tennis",
        "golf",
        "basketball",
        "football",
        "baseball",
        "hockey",
    ]
)


def extract_sport(ticker: str) -> str:
    """Return the sport code for a ticker, or Sport.UNKNOWN if unrecognised.

    Matches on the first segment of the ticker (before the first ``-``).
    Case-insensitive.

    Examples
    --------
    >>> extract_sport("NBA-CELTICS-25")
    'NBA'
    >>> extract_sport("NFL-KC-MAY15")
    'NFL'
    >>> extract_sport("KXELECTIONS-25")
    'UNKNOWN'
    """
    if not ticker:
        return Sport.UNKNOWN
    prefix = ticker.split("-")[0].upper()
    return SPORT_PREFIX_MAP.get(prefix, Sport.UNKNOWN)


def is_sports_market(ticker: str) -> bool:
    """Return True if the ticker belongs to a recognised sports category.

    >>> is_sports_market("NBA-BOS-CELTICS-25")
    True
    >>> is_sports_market("KXELECTIONS-25")
    False
    """
    return extract_sport(ticker) != Sport.UNKNOWN


def is_sports_event(event: KalshiEvent, check_markets: bool = True) -> bool:
    """Return True if the event is a sports event.

    Checks the event category field first (fastest), then falls back to
    checking the event ticker, then any nested market tickers.
    """
    if event.category.lower() in SPORTS_CATEGORIES:
        return True
    if is_sports_market(event.ticker):
        return True
    if check_markets:
        return any(is_sports_market(m.ticker) for m in event.markets)
    return False


def filter_sports(sport_filter: list[str]) -> set[str]:
    """Return the canonical set of sport codes to scan.

    An empty list means all known sports. Values are normalised to uppercase.
    """
    if not sport_filter:
        return set(SPORT_PREFIX_MAP.values())
    return {s.upper() for s in sport_filter}


# ---------------------------------------------------------------------------
# Arbitrage math — pure functions
# ---------------------------------------------------------------------------


def compute_total_cost(yes_ask: int, no_ask: int) -> int:
    """Return the total cents paid to hold both sides of a binary market.

    >>> compute_total_cost(48, 50)
    98
    """
    return yes_ask + no_ask


def compute_arb_pct(total_cost: int) -> float:
    """Return the net arbitrage percentage for a given total cost.

    A positive value indicates a guaranteed profit (pure arbitrage).
    A negative value indicates how far away from breakeven the market is.

    Formula: ((100 / total_cost) - 1) * 100

    >>> abs(compute_arb_pct(98) - 2.04) < 0.01
    True
    >>> compute_arb_pct(100)
    0.0
    >>> abs(compute_arb_pct(102) - (-1.96)) < 0.01
    True
    """
    if total_cost <= 0:
        return 0.0
    return round(((100.0 / total_cost) - 1.0) * 100.0, 4)


def compute_expected_profit(total_cost: int, outlay_per_side: float = 50.0) -> float:
    """Return the expected guaranteed profit in dollars for a given outlay.

    Args:
        total_cost: yes_ask + no_ask in cents.
        outlay_per_side: Dollars invested in each side.

    The total outlay is outlay_per_side * 2.  The payout is always
    outlay_per_side / (yes_ask / 100) per contract on the winning side,
    or equivalently: total_outlay * (100 / total_cost).

    Example: yes_ask=48, no_ask=50, outlay_per_side=$50
      total_outlay = $100
      payout = $100 * (100/98) = $102.04
      profit = $2.04

    >>> abs(compute_expected_profit(98, 50.0) - 2.04) < 0.01
    True
    >>> compute_expected_profit(100, 50.0)
    0.0
    """
    if total_cost <= 0:
        return 0.0
    total_outlay = outlay_per_side * 2
    payout = total_outlay * (100.0 / total_cost)
    return round(payout - total_outlay, 4)


def classify_opportunity(arb_pct: float, near_arb_threshold_pct: float) -> Optional[ArbType]:
    """Classify an arbitrage percentage into an ArbType, or return None.

    Args:
        arb_pct: Result of compute_arb_pct().
        near_arb_threshold_pct: Positive number. Near-arb is flagged when
            arb_pct > -near_arb_threshold_pct. E.g. 2.0 flags anything
            within 2% of breakeven.

    >>> classify_opportunity(2.0, 2.0) == ArbType.PURE_ARB
    True
    >>> classify_opportunity(-1.5, 2.0) == ArbType.NEAR_ARB
    True
    >>> classify_opportunity(-3.0, 2.0) is None
    True
    """
    if arb_pct > 0:
        return ArbType.PURE_ARB
    if arb_pct > -abs(near_arb_threshold_pct):
        return ArbType.NEAR_ARB
    return None


def compute_spread_pct(ask: int, bid: int) -> float:
    """Bid-ask spread as a fraction of ask price (0–1 scale)."""
    if ask <= 0:
        return 0.0
    return round((ask - bid) / ask, 4)


# ---------------------------------------------------------------------------
# Main detection entry point
# ---------------------------------------------------------------------------


def detect_opportunity(
    market: KalshiMarket,
    event: KalshiEvent,
    near_arb_threshold_pct: float,
    scan_id: str,
    sports_filter: Optional[set[str]] = None,
) -> Optional[ArbOpportunity]:
    """Detect whether a market offers a pure arb or near-arb opportunity.

    Returns None if:
    - Market is not a sports market
    - Market is filtered out by sports_filter
    - yes_ask or no_ask is missing / zero
    - arb_pct is below -near_arb_threshold_pct

    Args:
        market: The KalshiMarket to evaluate.
        event: The parent KalshiEvent (for title / event_ticker).
        near_arb_threshold_pct: Flag near-arbs within this % of breakeven.
        scan_id: Parent scan cycle identifier.
        sports_filter: Optional set of sport codes to include. None = all sports.
    """
    sport = extract_sport(market.ticker)

    # Filter by sport classification
    if sport == Sport.UNKNOWN:
        return None

    if sports_filter is not None and sport not in sports_filter:
        return None

    # Require valid prices on both sides
    if market.yes_ask <= 0 or market.no_ask <= 0:
        return None

    total_cost = compute_total_cost(market.yes_ask, market.no_ask)
    arb_pct = compute_arb_pct(total_cost)
    arb_type = classify_opportunity(arb_pct, near_arb_threshold_pct)

    if arb_type is None:
        return None

    hours_to_close = 0.0
    if market.close_time:
        try:
            ct = market.close_time.replace("Z", "+00:00")
            close_dt = datetime.fromisoformat(ct)
            hours_to_close = round(
                (close_dt - datetime.now(timezone.utc)).total_seconds() / 3600, 2
            )
        except Exception:
            pass

    return ArbOpportunity(
        opp_id=str(uuid.uuid4()),
        scan_id=scan_id,
        ticker=market.ticker,
        event_ticker=event.ticker,
        market_title=market.title,
        event_title=event.title,
        sport=sport,
        yes_ask=market.yes_ask,
        no_ask=market.no_ask,
        yes_bid=market.yes_bid,
        no_bid=market.no_bid,
        total_cost=total_cost,
        arb_pct=arb_pct,
        arb_type=arb_type,
        expected_profit_at_100=compute_expected_profit(total_cost, 50.0),
        volume=market.volume,
        volume_24h=market.volume_24h,
        spread_yes=compute_spread_pct(market.yes_ask, market.yes_bid),
        spread_no=compute_spread_pct(market.no_ask, market.no_bid),
        close_time=market.close_time,
        hours_to_close=hours_to_close,
        detected_at=datetime.now(timezone.utc).isoformat(),
    )


def rank_opportunities(opps: list[ArbOpportunity]) -> list[ArbOpportunity]:
    """Sort opportunities: pure arbs first, then by arb_pct descending."""
    return sorted(
        opps,
        key=lambda o: (0 if o.arb_type == ArbType.PURE_ARB else 1, -o.arb_pct),
    )
