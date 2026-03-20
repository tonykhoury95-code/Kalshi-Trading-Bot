"""
Cross-exchange arbitrage / value computation.

Compares a Kalshi binary market against a sportsbook reference line to
identify positive-EV opportunities, near cross-exchange arbs, and (rarely)
pure cross-exchange arbitrage candidates.

All functions are pure (stateless, no I/O) for testability.

Definitions
-----------
- **Kalshi implied prob**: ``yes_ask / 100``  (the cost of a YES contract)
- **Sportsbook implied prob**: derived from American / decimal odds
- **Gross edge %**: ``(book_implied - kalshi_implied) * 100``  (raw mispricing)
- **Net edge %**: gross edge minus spread friction and estimated fill risk
- **Positive EV**: Kalshi YES is cheap relative to the sportsbook reference
- **Cross-exchange near-arb**: combined implied probs < 100 (one side on each exchange)
"""

from datetime import datetime, timezone
from typing import Optional
import uuid

from src.brokers.kalshi.models import KalshiEvent, KalshiMarket
from src.matching.models import MatchResult, YesSide
from src.sportsbook.models import MarketType, Outcome, SportsbookGame
from src.sports.models import ArbOpportunity, ArbType


# ---------------------------------------------------------------------------
# Pure math helpers
# ---------------------------------------------------------------------------

def gross_edge_pct(kalshi_implied: float, book_implied: float) -> float:
    """Compute gross edge as a percentage.

    Positive means Kalshi YES is underpriced vs the sportsbook reference
    (good for buying Kalshi YES).

    Args:
        kalshi_implied: Kalshi YES implied probability (0–1).
        book_implied:   Sportsbook implied probability for the same outcome (0–1).

    Returns:
        Edge as a percentage, e.g. ``3.2`` or ``-1.5``.
    """
    return round((book_implied - kalshi_implied) * 100.0, 4)


def spread_friction_pct(yes_ask: int, yes_bid: int) -> float:
    """Estimate friction from the Kalshi bid-ask spread on the YES side.

    Returns the spread as a percentage of the midpoint price.
    """
    if yes_bid <= 0 or yes_ask <= 0 or yes_ask <= yes_bid:
        return 0.0
    mid = (yes_ask + yes_bid) / 2.0
    if mid == 0:
        return 0.0
    return round((yes_ask - yes_bid) / mid * 100.0, 4)


def fill_risk_estimate(volume: int, hours_to_close: float) -> float:
    """Estimate execution / fill risk as a percentage deduction.

    Low volume or markets close to expiry have higher fill risk.

    Args:
        volume:          Total market volume (contracts traded).
        hours_to_close:  Hours until the market closes.

    Returns:
        Fill risk as a percentage (0–5%).
    """
    # Volume penalty: up to 2%
    if volume >= 10_000:
        vol_risk = 0.0
    elif volume >= 1_000:
        vol_risk = 1.0
    elif volume >= 100:
        vol_risk = 2.0
    else:
        vol_risk = 3.0

    # Time penalty: markets closing soon are harder to fill
    if hours_to_close < 0.5:
        time_risk = 2.0
    elif hours_to_close < 2.0:
        time_risk = 1.0
    else:
        time_risk = 0.0

    return min(vol_risk + time_risk, 5.0)


def net_edge_pct(
    gross_pct: float,
    spread_fric: float,
    fill_risk: float,
) -> float:
    """Net edge after friction adjustments."""
    return round(gross_pct - spread_fric - fill_risk, 4)


def required_stake(target_profit_dollars: float, net_edge_pct_val: float) -> float:
    """Stake needed to achieve a target profit at the given net edge %.

    Args:
        target_profit_dollars: Desired profit in dollars.
        net_edge_pct_val:      Net edge as a percentage (e.g. ``2.5``).

    Returns:
        Required stake in dollars, or 0 if edge is non-positive.
    """
    if net_edge_pct_val <= 0:
        return 0.0
    return round(target_profit_dollars / (net_edge_pct_val / 100.0), 2)


def classify_cross_exchange(
    gross_pct: float,
    net_pct: float,
    min_net_pct: float,
) -> Optional[ArbType]:
    """Classify a cross-exchange opportunity.

    Returns:
        ``ArbType.PURE_ARB`` — very unlikely in practice; both sides arb
        ``ArbType.NEAR_ARB`` — close to arbitrage
        ``ArbType.POSITIVE_EV`` — one side is mispriced relative to reference
        ``None`` — below threshold, ignore
    """
    if gross_pct >= 5.0 and net_pct > 0:
        return ArbType.PURE_ARB       # True cross-exchange arb (rare)
    if net_pct >= min_net_pct:
        return ArbType.POSITIVE_EV
    if net_pct >= 0:
        return ArbType.NEAR_ARB
    return None


def build_explanation(
    ticker: str,
    yes_side: YesSide,
    yes_team: str,
    kalshi_implied: float,
    book_implied: float,
    gross_pct: float,
    net_pct: float,
    spread_fric: float,
    fill_risk: float,
    provider: str,
    bookmaker: str,
    match_confidence: float,
) -> str:
    """Generate a plain-English explanation of the opportunity.

    Args:
        ticker:           Kalshi market ticker.
        yes_side:         Whether YES = home or away.
        yes_team:         Full team name for YES side.
        kalshi_implied:   Kalshi implied probability (0–1).
        book_implied:     Sportsbook implied probability (0–1).
        gross_pct:        Gross edge %.
        net_pct:          Net edge % after friction.
        spread_fric:      Spread friction %.
        fill_risk:        Fill risk %.
        provider:         Odds provider name.
        bookmaker:        Bookmaker key.
        match_confidence: Market match confidence (0–1).

    Returns:
        Multi-line explanation string.
    """
    direction = "underpriced" if gross_pct > 0 else "overpriced"
    side_str = f"{yes_team} (YES side)"

    lines = [
        f"Kalshi market {ticker} appears {direction} relative to {bookmaker.title()} "
        f"reference odds.",
        "",
        f"  Kalshi YES ({side_str}) implied prob: {kalshi_implied:.1%}",
        f"  {bookmaker.title()} implied prob:       {book_implied:.1%}",
        f"  Gross edge:                          {gross_pct:+.2f}%",
        f"  Spread friction deducted:            -{spread_fric:.2f}%",
        f"  Fill risk deducted:                  -{fill_risk:.2f}%",
        f"  Net edge:                            {net_pct:+.2f}%",
        "",
        f"Match confidence: {match_confidence:.0%} (via {provider})",
        "",
    ]

    # Risks / caveats
    caveats = []
    if match_confidence < 0.90:
        caveats.append(
            f"⚠ Market matching confidence is {match_confidence:.0%} — verify the "
            "event correspondence manually before acting."
        )
    if fill_risk > 1.5:
        caveats.append(
            "⚠ Fill risk is elevated — low volume or market closing soon. "
            "The edge may not be executable at the listed price."
        )
    if spread_fric > 2.0:
        caveats.append(
            "⚠ Bid-ask spread is wide — actual cost may be higher than YES ask."
        )

    if caveats:
        lines.append("Risks / caveats:")
        lines.extend(f"  {c}" for c in caveats)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main opportunity computation
# ---------------------------------------------------------------------------

def compute_cross_exchange_opportunity(
    kalshi_market: KalshiMarket,
    kalshi_event: KalshiEvent,
    match: MatchResult,
    game: SportsbookGame,
    scan_id: str,
    bookmaker_key: str = "fanduel",
    min_net_edge_pct: float = 1.0,
) -> Optional[ArbOpportunity]:
    """Compare a Kalshi market against a matched sportsbook game.

    Args:
        kalshi_market:   The Kalshi market to evaluate.
        kalshi_event:    Parent Kalshi event.
        match:           Confirmed match result from the matching engine.
        game:            Matched sportsbook game.
        scan_id:         Current scan cycle ID.
        bookmaker_key:   Which bookmaker's odds to use (default ``"fanduel"``).
        min_net_edge_pct: Minimum net edge % to flag (default 1.0).

    Returns:
        An :class:`ArbOpportunity` with cross-exchange fields populated,
        or ``None`` if the opportunity falls below the threshold.
    """
    # Determine which team the Kalshi YES contract covers
    if match.yes_side == YesSide.HOME:
        yes_team = game.home_team
    elif match.yes_side == YesSide.AWAY:
        yes_team = game.away_team
    else:
        return None  # Cannot determine YES side — skip

    # Get sportsbook outcome for the YES-side team
    outcome: Optional[Outcome] = game.get_outcome_odds(
        team_name=yes_team, bookmaker_key=bookmaker_key
    )
    if outcome is None:
        # Try best available bookmaker
        ml = game.get_moneyline()  # best available
        if ml:
            outcome = ml.get_outcome(yes_team)
    if outcome is None or outcome.implied_prob <= 0:
        return None

    # Core probability comparison
    kalshi_implied = kalshi_market.yes_ask / 100.0
    book_implied = outcome.implied_prob

    if kalshi_implied <= 0 or kalshi_implied >= 1:
        return None

    # Compute friction
    gross = gross_edge_pct(kalshi_implied, book_implied)
    fric = spread_friction_pct(kalshi_market.yes_ask, kalshi_market.yes_bid)
    fill = fill_risk_estimate(kalshi_market.volume, _hours_to_close(kalshi_market))
    net = net_edge_pct(gross, fric, fill)

    arb_type = classify_cross_exchange(gross, net, min_net_edge_pct)
    if arb_type is None:
        return None

    # Profit / stake calculations
    stake = required_stake(target_profit_dollars=10.0, net_edge_pct_val=net)
    hrs = _hours_to_close(kalshi_market)

    explanation = build_explanation(
        ticker=kalshi_market.ticker,
        yes_side=match.yes_side,
        yes_team=yes_team,
        kalshi_implied=kalshi_implied,
        book_implied=book_implied,
        gross_pct=gross,
        net_pct=net,
        spread_fric=fric,
        fill_risk=fill,
        provider=game.provider,
        bookmaker=bookmaker_key,
        match_confidence=match.confidence,
    )

    return ArbOpportunity(
        opp_id=str(uuid.uuid4()),
        scan_id=scan_id,
        ticker=kalshi_market.ticker,
        event_ticker=kalshi_event.ticker,
        market_title=kalshi_market.title,
        event_title=kalshi_event.title,
        sport=kalshi_event.category or "UNKNOWN",
        yes_ask=kalshi_market.yes_ask,
        no_ask=kalshi_market.no_ask,
        yes_bid=kalshi_market.yes_bid,
        no_bid=kalshi_market.no_bid,
        total_cost=kalshi_market.yes_ask + kalshi_market.no_ask,
        arb_pct=net,            # net edge is the headline number
        arb_type=arb_type,
        expected_profit_at_100=round(net, 2),
        volume=kalshi_market.volume,
        volume_24h=kalshi_market.volume_24h,
        spread_yes=fric,
        close_time=kalshi_market.close_time,
        hours_to_close=hrs,
        detected_at=datetime.now(timezone.utc).isoformat(),
        # Phase 2 cross-exchange fields
        sportsbook_provider=game.provider,
        sportsbook_game_id=game.game_id,
        sportsbook_bookmaker=bookmaker_key,
        sportsbook_price_american=outcome.price_american,
        sportsbook_implied_prob=round(book_implied, 4),
        kalshi_implied_prob=round(kalshi_implied, 4),
        gross_arb_pct=round(gross, 4),
        net_arb_pct=round(net, 4),
        spread_friction_pct=round(fric, 4),
        fill_risk_pct=round(fill, 4),
        required_stake_dollars=stake,
        match_confidence=round(match.confidence, 3),
        match_method=match.match_method.value,
        yes_team=yes_team,
        explanation=explanation,
        status="new",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hours_to_close(market: KalshiMarket) -> float:
    from src.utils.time import hours_until
    return hours_until(market.close_time)
