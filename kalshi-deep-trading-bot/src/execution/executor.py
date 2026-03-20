"""
Order execution with dry-run safety.

CRITICAL: Defaults to dry_run=True.  Live trading requires explicit
``live_trading=True`` in config AND ``--live`` CLI flag.
"""

import asyncio
from datetime import datetime, timezone

from loguru import logger

from src.ai.models import BettingDecision
from src.brokers.kalshi.client import KalshiClient


class Executor:
    """Execute orders against Kalshi, with dry-run as the default.

    Every live order is double-checked before submission.  Dry-run orders
    are logged but never sent to the exchange.
    """

    def __init__(self, kalshi_client: KalshiClient, dry_run: bool = True):
        self._client = kalshi_client
        self.dry_run = dry_run

        if not self.dry_run:
            logger.warning(
                "Executor initialized in LIVE mode - real orders will be placed!"
            )
        else:
            logger.info("Executor initialized in DRY RUN mode")

    async def execute_order(self, decision: BettingDecision) -> dict:
        """Execute a single order based on a betting decision.

        Args:
            decision: The betting decision to execute.

        Returns:
            A dict with execution details including ticker, action, amount,
            fill_price, order_id, timestamp, and dry_run flag.
        """
        timestamp = datetime.now(timezone.utc).isoformat()

        if decision.action == "skip":
            logger.debug("Skipping {} (reason: {})", decision.ticker, decision.skip_reason)
            return {
                "ticker": decision.ticker,
                "action": "skip",
                "amount": 0.0,
                "fill_price": None,
                "order_id": None,
                "timestamp": timestamp,
                "dry_run": self.dry_run,
                "skipped": True,
            }

        # Convert decision action to Kalshi side format
        side = "yes" if decision.action == "buy_yes" else "no"

        if self.dry_run:
            logger.info(
                "DRY RUN: Would place {} {} ${:.2f} on {}",
                decision.action,
                side,
                decision.amount,
                decision.ticker,
            )
            return {
                "ticker": decision.ticker,
                "action": decision.action,
                "amount": decision.amount,
                "fill_price": None,
                "order_id": None,
                "timestamp": timestamp,
                "dry_run": True,
                "skipped": False,
            }

        # LIVE ORDER - double-check safety
        if self.dry_run:
            # This should never happen, but belt-and-suspenders
            raise RuntimeError(
                "Safety check failed: attempted live order while dry_run=True"
            )

        logger.warning(
            "LIVE ORDER: Placing {} {} ${:.2f} on {}",
            decision.action,
            side,
            decision.amount,
            decision.ticker,
        )

        order = await self._client.place_order(
            ticker=decision.ticker,
            side=side,
            action="buy",
            amount_dollars=decision.amount,
        )

        success = order.status == "placed"
        if success:
            logger.info(
                "Order filled: {} {} ${:.2f} on {} (order_id: {})",
                decision.action,
                side,
                decision.amount,
                decision.ticker,
                order.client_order_id,
            )
        else:
            logger.error(
                "Order failed: {} {} ${:.2f} on {} (status: {})",
                decision.action,
                side,
                decision.amount,
                decision.ticker,
                order.status,
            )

        return {
            "ticker": decision.ticker,
            "action": decision.action,
            "amount": decision.amount,
            "fill_price": None,  # Market orders don't have a fixed fill price
            "order_id": order.client_order_id if success else None,
            "timestamp": timestamp,
            "dry_run": False,
            "skipped": False,
            "status": order.status,
        }

    async def execute_batch(self, decisions: list[BettingDecision]) -> list[dict]:
        """Execute a batch of decisions sequentially.

        Live orders have a 0.5-second delay between them to avoid
        overwhelming the exchange.

        Args:
            decisions: List of betting decisions to execute.

        Returns:
            List of execution result dicts.
        """
        results: list[dict] = []

        actionable = [d for d in decisions if d.action != "skip"]
        skipped = [d for d in decisions if d.action == "skip"]

        logger.info(
            "Executing batch: {} actionable, {} skipped",
            len(actionable),
            len(skipped),
        )

        # Log skipped decisions
        for decision in skipped:
            result = await self.execute_order(decision)
            results.append(result)

        # Execute actionable decisions
        for i, decision in enumerate(actionable):
            result = await self.execute_order(decision)
            results.append(result)

            # Delay between live orders
            if not self.dry_run and i < len(actionable) - 1:
                await asyncio.sleep(0.5)

        return results
