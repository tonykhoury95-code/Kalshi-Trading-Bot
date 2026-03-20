"""
Same-market Kalshi arbitrage engine.

This module handles the detection and analysis of Kalshi-internal arbitrage:
buying both YES and NO sides of the same market simultaneously.

Math
----
  total_cost  = yes_ask + no_ask  (in cents)
  gross_arb_cents = 100 - total_cost
  gross_arb_pct   = ((100 / total_cost) - 1) * 100

After friction (spread penalty + slippage + liquidity + stale quote):
  net_arb_pct = gross_arb_pct - total_friction_pct
  net_arb_cents = gross_arb_cents - (total_friction_pct / 100 * 100)

Pure arb  → net_arb_pct > 0
Near arb  → gross_arb_pct > -near_arb_threshold_pct (not yet net-profitable)
Rejected  → everything else

All functions are pure (no I/O) and fully testable in isolation.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from src.brokers.kalshi.models import KalshiEvent, KalshiMarket
from src.sports.detector import extract_sport
from src.sports.models import ArbFriction, ArbResult, ArbStatus, Sport
from src.utils.time import hours_until as _hours_until_util


# ---------------------------------------------------------------------------
# Friction model
# ---------------------------------------------------------------------------

def _slippage_pct(volume_24h: int) -> float:
    """Estimate slippage % from 24-hour volume tier."""
    if volume_24h >= 5_000:
        return 0.0
    if volume_24h >= 1_000:
        return 0.25
    if volume_24h >= 200:
        return 0.75
    return 1.5


def _liquidity_penalty_pct(volume: int) -> float:
    """Estimate fill-difficulty penalty from total market volume."""
    if volume >= 10_000:
        return 0.0
    if volume >= 2_000:
        return 0.5
    if volume >= 500:
        return 1.0
    return 2.0


def _stale_quote_penalty_pct(quote_age_seconds: float) -> float:
    """Penalty for potentially stale quotes."""
    if quote_age_seconds <= 30.0:
        return 0.0
    if quote_age_seconds <= 120.0:
        return 0.5
    return 1.5


def compute_arb_friction(
    yes_ask: int,
    yes_bid: int,
    no_ask: int,
    no_bid: int,
    volume: int,
    volume_24h: int,
    hours_to_close: float,
    quote_age_seconds: float = 0.0,
) -> ArbFriction:
    """Decompose all friction costs for a Kalshi same-market arb.

    Args:
        yes_ask: YES ask price in cents.
        yes_bid: YES bid price in cents.
        no_ask: NO ask price in cents.
        no_bid: NO bid price in cents.
        volume: Total market volume (contracts).
        volume_24h: 24-hour market volume (contracts).
        hours_to_close: Hours until market closes.
        quote_age_seconds: Age of the quote snapshot in seconds.

    Returns:
        ArbFriction with all penalty components filled in.
    """
    yes_spread = float(max(0, yes_ask - yes_bid))
    no_spread = float(max(0, no_ask - no_bid))
    total_spread = yes_spread + no_spread

    # Spread penalty: cost of crossing both spreads on a $100 payout basis
    spread_penalty = total_spread  # each cent of spread = 1% of 100-cent payout

    slippage = _slippage_pct(volume_24h)
    liquidity = _liquidity_penalty_pct(volume)
    stale = _stale_quote_penalty_pct(quote_age_seconds)

    total_friction = spread_penalty + slippage + liquidity + stale

    return ArbFriction(
        yes_spread_cents=yes_spread,
        no_spread_cents=no_spread,
        total_spread_cents=total_spread,
        spread_penalty_pct=round(spread_penalty, 4),
        slippage_pct=round(slippage, 4),
        liquidity_penalty_pct=round(liquidity, 4),
        stale_quote_penalty_pct=round(stale, 4),
        total_friction_pct=round(total_friction, 4),
    )


# ---------------------------------------------------------------------------
# Core result builder
# ---------------------------------------------------------------------------

def compute_arb_result(
    market: KalshiMarket,
    event: KalshiEvent,
    scan_id: str,
    near_arb_threshold_pct: float,
    min_net_arb_pct: float = 0.0,
    sports_filter: Optional[set[str]] = None,
    quote_age_seconds: float = 0.0,
) -> Optional[ArbResult]:
    """Compute a full ArbResult for a single Kalshi market.

    Returns None if:
    - Market is not a recognised sports market.
    - Market is excluded by sports_filter.
    - YES ask or NO ask is missing / zero.
    - gross_arb_pct is below -near_arb_threshold_pct (completely off).

    Args:
        market: The KalshiMarket to evaluate.
        event: Parent KalshiEvent (for titles and event_ticker).
        scan_id: Current scan cycle ID.
        near_arb_threshold_pct: Include near-arbs within this % of breakeven.
        min_net_arb_pct: Minimum net arb % for PURE_ARB classification.
        sports_filter: Set of sport codes to include. None = all sports.
        quote_age_seconds: Age of the price snapshot (for stale-quote penalty).
    """
    sport = extract_sport(market.ticker)
    if sport == Sport.UNKNOWN:
        return None
    if sports_filter is not None and sport not in sports_filter:
        return None
    if market.yes_ask <= 0 or market.no_ask <= 0:
        return None

    total_cost = market.yes_ask + market.no_ask
    gross_arb_cents = 100.0 - total_cost
    if total_cost <= 0:
        return None
    gross_arb_pct = round(((100.0 / total_cost) - 1.0) * 100.0, 4)

    # Completely hopeless — skip early
    if gross_arb_pct < -abs(near_arb_threshold_pct):
        return None

    hours_to_close = _hours_until(market.close_time)

    friction = compute_arb_friction(
        yes_ask=market.yes_ask,
        yes_bid=market.yes_bid,
        no_ask=market.no_ask,
        no_bid=market.no_bid,
        volume=market.volume,
        volume_24h=market.volume_24h,
        hours_to_close=hours_to_close,
        quote_age_seconds=quote_age_seconds,
    )

    net_arb_pct = round(gross_arb_pct - friction.total_friction_pct, 4)
    net_arb_cents = round(gross_arb_cents - friction.total_friction_pct, 4)

    if net_arb_pct > min_net_arb_pct:
        status = ArbStatus.PURE_ARB
        rejection_reason = None
    elif gross_arb_pct > -abs(near_arb_threshold_pct):
        status = ArbStatus.NEAR_ARB
        rejection_reason = (
            f"Net arb {net_arb_pct:+.2f}% is negative after "
            f"{friction.total_friction_pct:.2f}% friction"
        )
    else:
        status = ArbStatus.REJECTED
        rejection_reason = (
            f"Gross arb {gross_arb_pct:+.2f}% is below threshold -{near_arb_threshold_pct}%"
        )

    result = ArbResult(
        result_id=str(uuid.uuid4()),
        scan_id=scan_id,
        ticker=market.ticker,
        event_ticker=event.ticker,
        market_title=getattr(market, "title", "") or "",
        event_title=getattr(event, "title", "") or "",
        sport=sport,
        yes_ask=market.yes_ask,
        no_ask=market.no_ask,
        yes_bid=market.yes_bid,
        no_bid=market.no_bid,
        gross_arb_cents=gross_arb_cents,
        gross_arb_pct=gross_arb_pct,
        friction=friction,
        net_arb_cents=net_arb_cents,
        net_arb_pct=net_arb_pct,
        status=status,
        rejection_reason=rejection_reason,
        volume=market.volume,
        volume_24h=market.volume_24h,
        hours_to_close=hours_to_close,
        close_time=market.close_time or "",
        detected_at=datetime.now(timezone.utc).isoformat(),
    )
    result.explanation = build_arb_explanation(result)
    return result


# ---------------------------------------------------------------------------
# Explanation builder
# ---------------------------------------------------------------------------

def build_arb_explanation(result: ArbResult) -> str:
    """Build a plain-English explanation of why this market qualifies (or not).

    Written for a non-expert audience — explains what the numbers mean.
    """
    tc = result.yes_ask + result.no_ask
    lines = [
        f"Market: {result.ticker} ({result.sport})",
        f"Prices: YES {result.yes_ask}¢  |  NO {result.no_ask}¢  |  Total cost: {tc}¢",
        "",
    ]

    if result.gross_arb_pct > 0:
        lines.append(
            f"✅ GROSS ARB: Buying both sides costs only {tc}¢ — less than the guaranteed "
            f"$1.00 payout. That's a {result.gross_arb_pct:+.2f}¢ gross profit on every "
            "$1 you invest."
        )
    else:
        shortfall = abs(result.gross_arb_pct)
        lines.append(
            f"📊 Near-arb: Buying both sides costs {tc}¢, which is {shortfall:.2f}¢ more "
            "than the $1.00 payout — not profitable yet, but close."
        )

    # Friction breakdown
    f = result.friction
    lines += [
        "",
        "💸 Friction breakdown (why net profit differs from gross):",
        f"  • Spread cost:      {f.spread_penalty_pct:+.2f}%  "
        f"(YES spread {f.yes_spread_cents:.0f}¢ + NO spread {f.no_spread_cents:.0f}¢)",
        f"  • Slippage:         {f.slippage_pct:+.2f}%  (from trading volume)",
        f"  • Liquidity risk:   {f.liquidity_penalty_pct:+.2f}%  (market depth)",
        f"  • Stale quote risk: {f.stale_quote_penalty_pct:+.2f}%",
        f"  ─────────────────────────────────────",
        f"  • Total friction:   {f.total_friction_pct:+.2f}%",
        "",
    ]

    if result.status == ArbStatus.PURE_ARB:
        lines.append(
            f"🟢 NET ARB: {result.net_arb_pct:+.2f}% after friction — this is a real "
            "risk-free profit if both orders fill."
        )
    elif result.status == ArbStatus.NEAR_ARB:
        lines.append(
            f"🟡 NET: {result.net_arb_pct:+.2f}% — friction wipes out the gross edge. "
            "Worth monitoring; tightening spreads could tip this into profit."
        )
        if result.rejection_reason:
            lines.append(f"   Reason: {result.rejection_reason}")
    else:
        lines.append(
            f"🔴 REJECTED — {result.rejection_reason or 'too far from breakeven'}"
        )

    lines += [
        "",
        f"Volume: {result.volume:,} contracts total | "
        f"{result.volume_24h:,} in last 24h | "
        f"{result.hours_to_close:.1f}h to close",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def rank_arb_results(results: list[ArbResult]) -> list[ArbResult]:
    """Sort arb results: PURE_ARB first, then by net_arb_pct descending."""
    priority = {ArbStatus.PURE_ARB: 0, ArbStatus.NEAR_ARB: 1, ArbStatus.REJECTED: 2}
    return sorted(
        results,
        key=lambda r: (priority.get(r.status, 3), -r.net_arb_pct),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _hours_until(close_time: Optional[str]) -> float:
    """Return hours until close_time, or 0.0 if unavailable."""
    if not close_time:
        return 0.0
    try:
        return round(_hours_until_util(close_time), 2)
    except Exception:
        return 0.0
