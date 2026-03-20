"""
Deterministic rule-based market filter.

Runs BEFORE the AI to eliminate markets that do not meet basic quality
and liquidity criteria.  Each check is independent and returns a clear
rejection reason when a market fails.
"""

from loguru import logger

from src.brokers.kalshi.models import KalshiEvent, KalshiMarket
from src.config import StrategyConfig
from src.utils.time import hours_until


class MarketFilter:
    """Filter markets using deterministic rules from StrategyConfig.

    Checks are evaluated in order.  The first failing check determines the
    rejection reason.

    The effective min_volume is selected based on the ``is_demo`` flag:
    - demo: uses ``config.min_volume_demo`` (default 10)
    - prod: uses ``config.min_volume_prod`` (default 1000)
    """

    def __init__(self, config: StrategyConfig, is_demo: bool = True):
        self.config = config
        self.is_demo = is_demo
        self.effective_min_volume = (
            config.min_volume_demo if is_demo else config.min_volume_prod
        )
        logger.info(
            "MarketFilter initialized: env={} min_volume={}",
            "demo" if is_demo else "prod",
            self.effective_min_volume,
        )

    def check_market(
        self, market: KalshiMarket, event: KalshiEvent
    ) -> tuple[bool, str]:
        """Check whether a market passes all filter criteria.

        Args:
            market: The market to evaluate.
            event: The parent event (used for context logging).

        Returns:
            A tuple of ``(passes, reason_if_rejected)``.  When ``passes``
            is ``True``, the reason string is empty.
        """
        # 1. Minimum volume — uses demo or prod threshold based on environment
        if market.volume < self.effective_min_volume:
            return False, f"volume {market.volume} < min {self.effective_min_volume}"

        # 2. Maximum spread
        if market.yes_ask > 0 and market.yes_bid > 0:
            spread = (market.yes_ask - market.yes_bid) / market.yes_ask
            if spread > self.config.max_spread:
                return False, (
                    f"spread {spread:.3f} > max {self.config.max_spread} "
                    f"(ask={market.yes_ask}, bid={market.yes_bid})"
                )

        # 3. Minimum price
        min_price_cents = self.config.min_price * 100
        if market.yes_ask < min_price_cents:
            return False, (
                f"yes_ask {market.yes_ask} < min price {min_price_cents:.0f} cents"
            )

        # 4. Maximum price
        max_price_cents = self.config.max_price * 100
        if market.yes_ask > max_price_cents:
            return False, (
                f"yes_ask {market.yes_ask} > max price {max_price_cents:.0f} cents"
            )

        # 5. Minimum hours to expiry
        if market.close_time:
            try:
                hours_left = hours_until(market.close_time)
                if hours_left < self.config.min_hours_to_expiry:
                    return False, (
                        f"expires in {hours_left:.1f}h < min {self.config.min_hours_to_expiry}h"
                    )
            except Exception:
                pass  # If we can't parse, don't reject on this criterion

        # 6. Maximum hours to expiry
        if market.close_time:
            try:
                hours_left = hours_until(market.close_time)
                if hours_left > self.config.max_hours_to_expiry:
                    return False, (
                        f"expires in {hours_left:.1f}h > max {self.config.max_hours_to_expiry}h"
                    )
            except Exception:
                pass

        # 7. Orderbook quality: both sides must exist
        if market.yes_ask <= 0 or market.yes_bid <= 0:
            return False, (
                f"missing orderbook side (yes_ask={market.yes_ask}, yes_bid={market.yes_bid})"
            )

        return True, ""

    def filter_markets(
        self,
        markets: list[KalshiMarket],
        event: KalshiEvent,
    ) -> tuple[list[KalshiMarket], list[dict]]:
        """Filter a list of markets, returning passing and rejected lists.

        Args:
            markets: Markets to evaluate.
            event: Parent event for context.

        Returns:
            A tuple of ``(passing_markets, rejected_with_reasons)`` where
            each rejected entry is a dict with ``ticker`` and ``reason``.
        """
        passing: list[KalshiMarket] = []
        rejected: list[dict] = []

        rejection_summary: dict[str, int] = {}
        for market in markets:
            passes, reason = self.check_market(market, event)
            if passes:
                passing.append(market)
            else:
                rejected.append({"ticker": market.ticker, "reason": reason})
                # Bucket by reason prefix for summary (avoid flooding logs)
                bucket = reason.split(" ")[0]  # e.g. "volume", "spread", "expires"
                rejection_summary[bucket] = rejection_summary.get(bucket, 0) + 1
                logger.debug("Filter rejected {}: {}", market.ticker, reason)

        if not passing:
            logger.info(
                "Event {}: 0/{} markets passed filters — rejections: {}",
                event.ticker,
                len(markets),
                rejection_summary,
            )
        else:
            logger.info(
                "Event {}: {}/{} markets passed filters",
                event.ticker,
                len(passing),
                len(markets),
            )

        return passing, rejected
