"""
Alert management for sports arbitrage opportunities.

Handles deduplication across a scanner session and emits rich console alerts
plus structured audit log entries when a meaningful new opportunity is found.
"""

from datetime import datetime, timezone

from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from src.sports.models import ArbOpportunity, ArbType

_console = Console()

# Minimum improvement in arb_pct before re-alerting the same ticker
_DEFAULT_CHANGE_THRESHOLD = 0.50


class AlertManager:
    """Deduplicates arb alerts within a scanner session and emits notifications.

    State is in-memory only.  When the scanner restarts, dedup resets so
    stale opportunities are re-alerted rather than silently missed.
    """

    def __init__(self, change_threshold_pct: float = _DEFAULT_CHANGE_THRESHOLD):
        """
        Args:
            change_threshold_pct: Re-alert when arb_pct improves by at least
                this many percentage points since the last alert. Default 0.5%.
        """
        self._change_threshold = change_threshold_pct
        self._seen: dict[str, ArbOpportunity] = {}  # ticker → last alerted opp

    def is_new_or_improved(self, opp: ArbOpportunity) -> bool:
        """Return True if this opportunity warrants an alert.

        Triggers when:
        - The ticker has never been alerted this session, OR
        - arb_pct has improved (grown more positive) by ≥ change_threshold_pct
        """
        previous = self._seen.get(opp.ticker)
        if previous is None:
            return True
        improvement = opp.arb_pct - previous.arb_pct
        return improvement >= self._change_threshold

    def record(self, opp: ArbOpportunity) -> None:
        """Update the seen-state for this ticker."""
        self._seen[opp.ticker] = opp

    def process(self, opp: ArbOpportunity) -> bool:
        """Main entry point.  Returns True if an alert was emitted."""
        if not self.is_new_or_improved(opp):
            return False

        self._emit_console(opp)
        self._emit_log(opp)
        self.record(opp)
        return True

    # -- Output channels -------------------------------------------------------

    def _emit_console(self, opp: ArbOpportunity) -> None:
        """Print a styled Rich alert to the console."""
        if opp.arb_type == ArbType.PURE_ARB:
            border_style = "bold red"
            tag = "[PURE ARB 🔴]"
            arb_color = "bold red"
        else:
            border_style = "yellow"
            tag = "[NEAR ARB 🟡]"
            arb_color = "bold yellow"

        arb_str = f"+{opp.arb_pct:.2f}%" if opp.arb_pct >= 0 else f"{opp.arb_pct:.2f}%"
        profit_str = f"+${opp.expected_profit_at_100:.2f}" if opp.expected_profit_at_100 >= 0 else f"${opp.expected_profit_at_100:.2f}"

        body = Text()
        body.append(f"{tag} ", style=arb_color)
        body.append(f"{opp.sport} · ", style="dim")
        body.append(f"{opp.ticker}", style="bold white")
        body.append(f"\n{opp.market_title or opp.event_title}", style="dim")
        body.append(f"\n\nYES ask: ", style="dim")
        body.append(f"{opp.yes_ask}¢", style="cyan")
        body.append(f"  +  NO ask: ", style="dim")
        body.append(f"{opp.no_ask}¢", style="cyan")
        body.append(f"  =  total: ", style="dim")
        body.append(f"{opp.total_cost}¢", style="white")
        body.append(f"\nArb%:  ", style="dim")
        body.append(arb_str, style=arb_color)
        body.append(f"   Profit at $100 outlay:  ", style="dim")
        body.append(profit_str, style="green" if opp.expected_profit_at_100 >= 0 else "red")
        body.append(f"\nVol: {opp.volume:,}   Closes in: {opp.hours_to_close:.1f}h", style="dim")
        if opp.fanduel_implied_prob is not None:
            body.append(
                f"\nFanDuel ref: {opp.fanduel_implied_prob:.1%}  "
                f"(cross-exchange edge: {opp.cross_exchange_edge:+.1%})",
                style="magenta",
            )

        _console.print(Panel(body, border_style=border_style, expand=False))

    def _emit_log(self, opp: ArbOpportunity) -> None:
        """Write a structured audit log entry."""
        logger.bind(audit=True).info(
            "ARB_ALERT | type={} arb_pct={:+.2f}% ticker={} sport={} "
            "yes_ask={}c no_ask={}c total={}c profit_at_100=${:.2f} "
            "vol={} hours_to_close={:.1f} detected_at={}",
            opp.arb_type,
            opp.arb_pct,
            opp.ticker,
            opp.sport,
            opp.yes_ask,
            opp.no_ask,
            opp.total_cost,
            opp.expected_profit_at_100,
            opp.volume,
            opp.hours_to_close,
            opp.detected_at,
        )

    # -- Session stats ----------------------------------------------------------

    def session_count(self) -> int:
        """Number of unique tickers alerted this session."""
        return len(self._seen)

    def best_opportunity(self) -> "ArbOpportunity | None":
        """Return the best (highest arb_pct) opportunity seen this session."""
        if not self._seen:
            return None
        return max(self._seen.values(), key=lambda o: o.arb_pct)
