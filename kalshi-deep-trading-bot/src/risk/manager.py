"""
Risk manager for trade gating and position limits.

Maintains state about consecutive losses, API failures, and daily P&L.
All trade proposals pass through ``gate_trade()`` before execution.
"""

from loguru import logger

from src.config import RiskConfig
from src.utils.time import is_in_time_window, utc_now


class RiskManager:
    """Stateful risk manager that gates every trade proposal.

    The single entry point is ``gate_trade()`` which runs all checks
    and returns ``(allowed, reason)``.
    """

    def __init__(self, config: RiskConfig):
        self.config = config
        self.consecutive_losses: int = 0
        self.api_failures: int = 0
        self.daily_loss: float = 0.0
        self.open_positions: int = 0
        self._event_exposure: dict[str, float] = {}

    # -- Individual checks ----------------------------------------------------

    def check_kill_switch(self) -> bool:
        """Return True if the kill switch is engaged."""
        return self.config.kill_switch

    def check_circuit_breaker(self) -> bool:
        """Return True if API failures have hit the circuit breaker threshold."""
        return self.api_failures >= self.config.circuit_breaker_threshold

    def check_daily_loss_limit(self, current_loss: float) -> bool:
        """Return True if daily loss exceeds the configured limit."""
        return current_loss >= self.config.max_daily_loss

    def check_cooldown(self) -> bool:
        """Return True if consecutive losses have triggered cooldown."""
        return self.consecutive_losses >= self.config.cooldown_after_losses

    def check_no_trade_period(self) -> bool:
        """Return True if current UTC hour falls in the no-trade window."""
        start = self.config.no_trade_start_hour
        end = self.config.no_trade_end_hour
        if start < 0 or end < 0:
            return False
        return is_in_time_window(start, end)

    def check_position_count(self, current_open: int) -> bool:
        """Return True if open positions are at or over the limit."""
        return current_open >= self.config.max_open_positions

    def check_event_exposure(
        self,
        event_ticker: str,
        proposed_amount: float,
        current_exposure: dict[str, float],
    ) -> bool:
        """Return True if adding the proposed amount would exceed per-event exposure.

        Args:
            event_ticker: The event to check.
            proposed_amount: Dollar amount of the proposed trade.
            current_exposure: Dict mapping event_ticker -> current dollar exposure.
        """
        existing = current_exposure.get(event_ticker, 0.0)
        return (existing + proposed_amount) > self.config.max_per_event_exposure

    # -- State mutations ------------------------------------------------------

    def record_api_failure(self) -> None:
        """Record an API failure and log a warning."""
        self.api_failures += 1
        logger.warning(
            "API failure recorded (count: {}/{})",
            self.api_failures,
            self.config.circuit_breaker_threshold,
        )

    def record_api_success(self) -> None:
        """Reset API failure counter after a successful call."""
        if self.api_failures > 0:
            logger.info("API success - resetting failure counter from {}", self.api_failures)
        self.api_failures = 0

    def record_loss(self, amount: float) -> None:
        """Record a trading loss."""
        self.consecutive_losses += 1
        self.daily_loss += amount
        logger.warning(
            "Loss recorded: ${:.2f} (consecutive: {}, daily total: ${:.2f})",
            amount,
            self.consecutive_losses,
            self.daily_loss,
        )

    def record_win(self) -> None:
        """Record a win and reset the consecutive loss counter."""
        if self.consecutive_losses > 0:
            logger.info(
                "Win recorded - resetting consecutive losses from {}",
                self.consecutive_losses,
            )
        self.consecutive_losses = 0

    def reset_daily(self) -> None:
        """Reset daily loss counter (call at start of each trading day)."""
        self.daily_loss = 0.0
        logger.info("Daily loss counter reset")

    # -- Main gate ------------------------------------------------------------

    def gate_trade(
        self,
        ticker: str,
        event_ticker: str,
        amount: float,
        current_state: dict,
    ) -> tuple[bool, str]:
        """Run all risk checks for a proposed trade.

        Args:
            ticker: Market ticker.
            event_ticker: Parent event ticker.
            amount: Proposed dollar amount.
            current_state: Dict with keys:
                - ``open_positions``: int, current number of open positions.
                - ``event_exposure``: dict[str, float], current exposure per event.
                - ``daily_loss``: float, current daily loss.

        Returns:
            ``(allowed, reason)`` where ``reason`` explains the block if not allowed.
        """
        # 1. Kill switch
        if self.check_kill_switch():
            reason = "Kill switch is engaged"
            logger.warning("Trade blocked for {}: {}", ticker, reason)
            return False, reason

        # 2. Circuit breaker
        if self.check_circuit_breaker():
            reason = (
                f"Circuit breaker: {self.api_failures} API failures "
                f">= threshold {self.config.circuit_breaker_threshold}"
            )
            logger.warning("Trade blocked for {}: {}", ticker, reason)
            return False, reason

        # 3. Daily loss limit
        daily_loss = current_state.get("daily_loss", self.daily_loss)
        if self.check_daily_loss_limit(daily_loss):
            reason = f"Daily loss limit: ${daily_loss:.2f} >= ${self.config.max_daily_loss:.2f}"
            logger.warning("Trade blocked for {}: {}", ticker, reason)
            return False, reason

        # 4. Cooldown after consecutive losses
        if self.check_cooldown():
            reason = (
                f"Cooldown: {self.consecutive_losses} consecutive losses "
                f">= threshold {self.config.cooldown_after_losses}"
            )
            logger.warning("Trade blocked for {}: {}", ticker, reason)
            return False, reason

        # 5. No-trade period
        if self.check_no_trade_period():
            reason = (
                f"No-trade period: hour {utc_now().hour} is in window "
                f"{self.config.no_trade_start_hour}-{self.config.no_trade_end_hour}"
            )
            logger.warning("Trade blocked for {}: {}", ticker, reason)
            return False, reason

        # 6. Position count
        open_pos = current_state.get("open_positions", self.open_positions)
        if self.check_position_count(open_pos):
            reason = (
                f"Position limit: {open_pos} open positions "
                f">= max {self.config.max_open_positions}"
            )
            logger.warning("Trade blocked for {}: {}", ticker, reason)
            return False, reason

        # 7. Per-event exposure
        event_exposure = current_state.get("event_exposure", self._event_exposure)
        if self.check_event_exposure(event_ticker, amount, event_exposure):
            existing = event_exposure.get(event_ticker, 0.0)
            reason = (
                f"Event exposure: ${existing:.2f} + ${amount:.2f} "
                f"> max ${self.config.max_per_event_exposure:.2f} for {event_ticker}"
            )
            logger.warning("Trade blocked for {}: {}", ticker, reason)
            return False, reason

        # 8. Max bet amount
        if amount > self.config.max_bet_amount:
            reason = (
                f"Amount ${amount:.2f} exceeds max bet ${self.config.max_bet_amount:.2f}"
            )
            logger.warning("Trade blocked for {}: {}", ticker, reason)
            return False, reason

        logger.debug("Trade allowed for {}: ${:.2f}", ticker, amount)
        return True, ""
