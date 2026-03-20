"""
EV (Expected-Value) betting finder engine.

This module is completely separate from the same-market arb engine. It compares
Kalshi binary markets against sportsbook odds to find markets where Kalshi's
price appears mispriced relative to the "true" probability implied by the book.

Method
------
1. Fetch sportsbook moneyline for the matched game.
2. Remove the bookmaker's vig (overround) to get vig-free probabilities.
3. For each side (YES = home team wins, NO = away team wins):
     edge_pct = (vig_free_prob - kalshi_implied_prob) * 100
4. If edge_pct > min_edge_pct, compute Kelly fraction and stake.
5. Combine into an EVCandidate.

Key formula (Kalshi-specific Kelly)
-------------------------------------
  b = (100 - yes_ask) / yes_ask   # net fractional odds (payout per dollar wagered)
  kelly_full = (p * b - (1 - p)) / b  where p = vig_free_prob
  kelly_fractional = max(0, kelly_full * kelly_multiplier)

All functions are pure (no I/O) and fully testable in isolation.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from src.brokers.kalshi.models import KalshiEvent, KalshiMarket
from src.matching.models import MatchResult, YesSide
from src.sports.comparison import fill_risk_estimate, spread_friction_pct
from src.sports.models import EVCandidate, EVSideMetrics
from src.sportsbook.models import Outcome, SportsbookGame
from src.utils.time import hours_until


# ---------------------------------------------------------------------------
# Vig removal
# ---------------------------------------------------------------------------

def remove_vig(outcomes: list[Outcome]) -> dict[str, float]:
    """Return vig-free probabilities for a list of outcomes.

    Normalises raw implied probabilities so they sum to 1.0, removing the
    bookmaker's built-in overround (vig).

    Args:
        outcomes: List of Outcome objects with implied_prob populated.

    Returns:
        Dict mapping outcome.name → vig-free probability (0–1).

    Returns {} if total implied prob is outside the sanity range [0.8, 1.5].
    """
    if not outcomes:
        return {}

    total = sum(o.implied_prob for o in outcomes if o.implied_prob > 0)
    if total < 0.8 or total > 1.5:
        # Sanity guard: something is wrong with the data
        return {}

    return {o.name: round(o.implied_prob / total, 6) for o in outcomes if o.implied_prob > 0}


# ---------------------------------------------------------------------------
# Per-side EV computation
# ---------------------------------------------------------------------------

def compute_ev_side(
    kalshi_ask_cents: int,
    kalshi_bid_cents: int,
    vig_free_prob: float,
    volume: int,
    hours_to_close: float,
    kelly_fraction: float = 0.5,
) -> Optional[EVSideMetrics]:
    """Compute EV metrics for one side (YES or NO) of a Kalshi market.

    Args:
        kalshi_ask_cents: Ask price in cents (0–100) — the cost to buy.
        kalshi_bid_cents: Bid price in cents — used for spread friction.
        vig_free_prob: Vig-removed sportsbook probability for this side (0–1).
        volume: Total market volume for liquidity context.
        hours_to_close: Hours until market close for fill-risk context.
        kelly_fraction: Kelly multiplier (default 0.5 = half-Kelly).

    Returns:
        EVSideMetrics if edge is positive, else None.
    """
    if kalshi_ask_cents <= 0 or kalshi_ask_cents >= 100:
        return None
    if vig_free_prob <= 0 or vig_free_prob >= 1.0:
        return None

    kalshi_implied = kalshi_ask_cents / 100.0
    edge_pct = round((vig_free_prob - kalshi_implied) * 100.0, 4)

    # b = net fractional odds: profit per dollar wagered on a winning bet
    b = (100.0 - kalshi_ask_cents) / kalshi_ask_cents
    if b <= 0:
        return None

    p = vig_free_prob
    kelly_full = (p * b - (1 - p)) / b
    kelly_frac = max(0.0, round(kelly_full * kelly_fraction, 4))

    ev = round(p * b - (1 - p), 4)  # EV per dollar wagered

    # Friction adjustments (informational — not applied to edge, but shown)
    fric = spread_friction_pct(kalshi_ask_cents, kalshi_bid_cents)
    fill_risk = fill_risk_estimate(volume=volume, hours_to_close=hours_to_close)

    return EVSideMetrics(
        side="computed",  # caller overwrites
        kalshi_ask_cents=kalshi_ask_cents,
        kalshi_bid_cents=kalshi_bid_cents,
        kalshi_implied_prob=round(kalshi_implied, 6),
        raw_book_prob=vig_free_prob,  # vig-free is stored here; caller has raw
        vig_free_prob=vig_free_prob,
        edge_pct=edge_pct,
        ev=ev,
        kelly_fraction=kelly_frac,
        recommended_stake_pct=round(kelly_frac * 100, 2),
    )


# ---------------------------------------------------------------------------
# Full candidate builder
# ---------------------------------------------------------------------------

def compute_ev_candidate(
    market: KalshiMarket,
    event: KalshiEvent,
    match: MatchResult,
    game: SportsbookGame,
    scan_id: str,
    bookmaker_key: str,
    sport: str = "",
    min_edge_pct: float = 1.0,
    kelly_fraction: float = 0.5,
) -> Optional[EVCandidate]:
    """Build an EVCandidate by comparing one Kalshi market against sportsbook odds.

    Args:
        market: Kalshi market to analyse.
        event: Parent Kalshi event.
        match: Result from MatchingEngine (links Kalshi ↔ sportsbook game).
        game: The matched SportsbookGame.
        scan_id: Current scan cycle ID.
        bookmaker_key: Preferred bookmaker slug (e.g. 'fanduel').
        min_edge_pct: Minimum edge % on at least one side to emit a candidate.
        kelly_fraction: Kelly multiplier (default 0.5 = half-Kelly).

    Returns:
        EVCandidate if at least one side has edge >= min_edge_pct, else None.
    """
    # --- Resolve teams ---
    yes_team = match.yes_team or game.home_team
    no_team = (
        game.home_team if match.yes_side == YesSide.AWAY else game.away_team
    )
    if match.yes_side == YesSide.HOME:
        no_team = game.away_team
    else:
        no_team = game.home_team

    # --- Get moneyline ---
    ml = game.get_moneyline(bookmaker_key)
    if ml is None or not ml.outcomes:
        return None

    # --- Vig removal ---
    vig_free = remove_vig(ml.outcomes)
    if not vig_free:
        return None

    # --- Resolve YES-side team in sportsbook ---
    yes_outcome = game.get_outcome_odds(yes_team, bookmaker_key)
    no_outcome = game.get_outcome_odds(no_team, bookmaker_key)

    yes_vig_free = vig_free.get(yes_outcome.name) if yes_outcome else None
    no_vig_free = vig_free.get(no_outcome.name) if no_outcome else None

    # --- Friction context ---
    hours_to_close = hours_until(market.close_time) if market.close_time else 0.0

    spread_fric = spread_friction_pct(market.yes_ask, market.yes_bid)
    fill_risk = fill_risk_estimate(volume=market.volume, hours_to_close=hours_to_close)

    # --- Per-side EV ---
    yes_m: Optional[EVSideMetrics] = None
    no_m: Optional[EVSideMetrics] = None

    if yes_vig_free is not None:
        yes_m = compute_ev_side(
            kalshi_ask_cents=market.yes_ask,
            kalshi_bid_cents=market.yes_bid,
            vig_free_prob=yes_vig_free,
            volume=market.volume,
            hours_to_close=hours_to_close,
            kelly_fraction=kelly_fraction,
        )
        if yes_m:
            yes_m = yes_m.model_copy(update={"side": "YES"})
            if yes_outcome:
                yes_m = yes_m.model_copy(update={"raw_book_prob": yes_outcome.implied_prob})

    if no_vig_free is not None:
        no_m = compute_ev_side(
            kalshi_ask_cents=market.no_ask,
            kalshi_bid_cents=market.no_bid,
            vig_free_prob=no_vig_free,
            volume=market.volume,
            hours_to_close=hours_to_close,
            kelly_fraction=kelly_fraction,
        )
        if no_m:
            no_m = no_m.model_copy(update={"side": "NO"})
            if no_outcome:
                no_m = no_m.model_copy(update={"raw_book_prob": no_outcome.implied_prob})

    # --- Check minimum edge ---
    yes_edge = yes_m.edge_pct if yes_m else -999
    no_edge = no_m.edge_pct if no_m else -999

    # Zero out metrics if below min threshold
    if yes_m and yes_m.edge_pct < min_edge_pct:
        yes_m = None
    if no_m and no_m.edge_pct < min_edge_pct:
        no_m = None

    if yes_m is None and no_m is None:
        return None

    # --- Best side ---
    if yes_m and (no_m is None or yes_m.edge_pct >= no_m.edge_pct):
        best_side = "YES"
        best_edge = yes_m.edge_pct
        best_kelly = yes_m.kelly_fraction
    else:
        assert no_m is not None
        best_side = "NO"
        best_edge = no_m.edge_pct
        best_kelly = no_m.kelly_fraction

    # --- Bookmaker key from game ---
    bk = game.get_bookmaker(bookmaker_key)
    bk_key = bk.key if bk else bookmaker_key

    candidate = EVCandidate(
        candidate_id=str(uuid.uuid4()),
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
        sportsbook_provider=game.provider,
        sportsbook_game_id=game.game_id,
        sportsbook_bookmaker=bk_key,
        match_confidence=match.confidence,
        match_method=match.match_method.value if hasattr(match.match_method, "value") else str(match.match_method),
        yes_team=yes_team,
        no_team=no_team,
        yes_metrics=yes_m,
        no_metrics=no_m,
        best_side=best_side,
        best_edge_pct=round(best_edge, 4),
        best_kelly_fraction=round(best_kelly, 4),
        spread_friction_pct=round(spread_fric, 4),
        fill_risk_pct=round(fill_risk, 4),
        volume=market.volume,
        volume_24h=market.volume_24h,
        hours_to_close=hours_to_close,
        close_time=market.close_time or "",
        detected_at=datetime.now(timezone.utc).isoformat(),
    )
    candidate.explanation = build_ev_explanation(candidate)
    return candidate


# ---------------------------------------------------------------------------
# Explanation builder
# ---------------------------------------------------------------------------

def build_ev_explanation(candidate: EVCandidate) -> str:
    """Build a plain-English explanation of this EV opportunity."""
    lines = [
        f"Market: {candidate.ticker} ({candidate.sport})",
        f"Teams:  YES = {candidate.yes_team}  |  NO = {candidate.no_team}",
        f"Prices: YES ask {candidate.yes_ask}¢  |  NO ask {candidate.no_ask}¢",
        f"Source: {candidate.sportsbook_bookmaker.upper()} via {candidate.sportsbook_provider}",
        f"Match confidence: {candidate.match_confidence:.0%} ({candidate.match_method})",
        "",
    ]

    def _side_block(m: EVSideMetrics, label: str) -> list[str]:
        return [
            f"📊 {label} side analysis:",
            f"  • Kalshi price:       {m.kalshi_ask_cents}¢  (implied {m.kalshi_implied_prob:.1%})",
            f"  • Sportsbook raw:     {m.raw_book_prob:.1%}",
            f"  • Vig-free estimate:  {m.vig_free_prob:.1%}",
            f"  • Edge:               {m.edge_pct:+.2f}%",
            f"  • EV per $1 wagered:  {m.ev:+.3f}",
            f"  • Kelly stake:        {m.recommended_stake_pct:.1f}% of bankroll",
            "",
        ]

    if candidate.yes_metrics:
        lines.extend(_side_block(candidate.yes_metrics, "YES"))
    if candidate.no_metrics:
        lines.extend(_side_block(candidate.no_metrics, "NO"))

    lines += [
        f"🏆 Best side: {candidate.best_side}  |  "
        f"Edge: {candidate.best_edge_pct:+.2f}%  |  "
        f"Kelly: {candidate.best_kelly_fraction:.1%}",
        "",
        "ℹ️  How to read this:",
        "  The 'vig-free estimate' is what the sportsbook's price implies after",
        "  removing the bookmaker's profit margin. When it's higher than Kalshi's",
        "  implied probability, you have a positive expected value (EV) on that side.",
        "",
        f"⚡ Friction: spread ~{candidate.spread_friction_pct:.2f}%  |  "
        f"fill risk ~{candidate.fill_risk_pct:.2f}%",
        f"   Volume: {candidate.volume:,} contracts  |  {candidate.hours_to_close:.1f}h to close",
    ]
    return "\n".join(lines)
