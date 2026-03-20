"""
Kalshi Trading Bot - Thin Orchestration Shell.

This module coordinates the modular components in src/ to execute the
trading pipeline:

    fetch events -> filter markets -> research -> extract probabilities
    -> compute edge -> risk check -> AI decision -> execute orders

All heavy lifting is delegated to the src/ package.
"""

import argparse
import asyncio
import csv
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import openai
from loguru import logger
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from src.ai.client import check_openai_availability, extract_probabilities_from_text, generate_betting_decisions
from src.ai.models import BettingDecision, MarketAnalysis, ProbabilityExtraction, RunSummary
from src.brokers.kalshi.client import KalshiClient
from src.config import BotConfig
from src.execution.executor import Executor
from src.portfolio.ledger import Ledger
from src.research.octagon import OctagonClient
from src.risk.manager import RiskManager
from src.strategies.edge import compute_edge_metrics, compute_kelly_position_size
from src.strategies.filters import MarketFilter
from src.telemetry.logger import log_run_summary, log_trade_decision, setup_logging
from src.utils.time import format_duration, hours_until

# Keep backward-compat imports for external consumers
from config import load_config as _legacy_load_config  # noqa: F401
from betting_models import (  # noqa: F401
    BettingDecision as LegacyBettingDecision,
    MarketAnalysis as LegacyMarketAnalysis,
    ProbabilityExtraction as LegacyProbabilityExtraction,
)


class TradingBot:
    """Orchestrates the trading pipeline using modular src/ components."""

    def __init__(
        self,
        live_trading: bool = False,
        max_close_ts: Optional[int] = None,
    ):
        self.config = BotConfig.from_env()
        self.config.dry_run = not live_trading
        self.config.live_trading = live_trading if not live_trading else True

        # Validate live_trading safety
        if live_trading:
            # Re-validate after override
            if self.config.dry_run:
                raise RuntimeError("Cannot enable live trading while dry_run is True")

        self.max_close_ts = max_close_ts
        self.console = Console()

        # Components
        self.kalshi_client: Optional[KalshiClient] = None
        self.research_client: Optional[OctagonClient] = None
        self.openai_client: Optional[openai.AsyncOpenAI] = None
        self.risk_manager = RiskManager(self.config.risk)
        self.market_filter = MarketFilter(
            self.config.strategy,
            is_demo=self.config.kalshi.use_demo,
        )
        self.ledger = Ledger()
        self.executor: Optional[Executor] = None
        self.run_id: Optional[str] = None

    async def initialize(self) -> None:
        """Initialize all API clients and display configuration."""
        self.console.print("[bold blue]Initializing trading bot v2.0...[/bold blue]")

        try:
            self.kalshi_client = KalshiClient(self.config.kalshi)
        except ValueError as exc:
            self.console.print(f"\n[bold red]Configuration error:[/bold red] {exc}\n")
            raise SystemExit(1) from exc
        await self.kalshi_client.connect()
        self.research_client = OctagonClient(self.config.octagon)
        self.openai_client = openai.AsyncOpenAI(api_key=self.config.openai.api_key)
        self.executor = Executor(self.kalshi_client, dry_run=self.config.dry_run)

        # Start a ledger run
        self.run_id = self.ledger.start_run(dry_run=self.config.dry_run)

        # Display configuration
        env_name = "DEMO" if self.config.kalshi.use_demo else "PRODUCTION"
        mode = "DRY RUN" if self.config.dry_run else "LIVE TRADING"
        self.console.print(f"[green]Environment: {env_name}[/green]")
        self.console.print(f"[blue]Mode: {mode}[/blue]")
        self.console.print(f"[blue]Max events: {self.config.strategy.max_events_to_analyze}[/blue]")
        self.console.print(f"[blue]Max bet: ${self.config.risk.max_bet_amount}[/blue]")
        self.console.print(f"[blue]Z-threshold: {self.config.strategy.z_threshold}[/blue]")
        self.console.print(f"[blue]Min edge: {self.config.strategy.min_edge}[/blue]")

        if not self.config.dry_run:
            self.console.print(
                "\n[bold red]WARNING: LIVE TRADING MODE ENABLED. "
                "Real orders will be placed![/bold red]\n"
            )

    async def run(self) -> None:
        """Execute the full trading pipeline."""
        errors: list[str] = []
        all_decisions: list[BettingDecision] = []
        all_research: dict[str, str] = {}
        all_prob_extractions: dict[str, ProbabilityExtraction] = {}
        all_market_odds: dict[str, dict] = {}

        # Pipeline stage counters for final summary
        stats: dict[str, int] = {
            "events_fetched": 0,
            "events_zero_volume": 0,
            "events_no_markets": 0,
            "events_qualified": 0,
            "events_researched": 0,
            "events_skipped_no_viable_markets": 0,
            "prob_extraction_success": 0,
            "prob_extraction_failed": 0,
            "markets_scanned": 0,
            "markets_passed_filters": 0,
            "candidate_trades": 0,
            "actionable_decisions": 0,
            "bets_placed": 0,
        }
        total_wagered = 0.0
        openai_available = True

        try:
            await self.initialize()

            # ----------------------------------------------------------------
            # Step 1 — Fetch events
            # ----------------------------------------------------------------
            self.console.print("\n[bold]Step 1: Fetching top events by 24h volume...[/bold]")
            events = await self.kalshi_client.get_events(
                limit=self.config.strategy.max_events_to_analyze,
                max_close_ts=self.max_close_ts,
                min_hours_to_expiry=self.config.strategy.min_hours_to_expiry,
                max_markets_per_event=self.config.strategy.max_markets_per_event,
                min_event_volume=self.config.strategy.min_event_volume,
            )
            stats["events_fetched"] = len(events)

            if not events:
                self.console.print("[red]No events found.[/red]")
                return

            # Exclude zero-volume events entirely
            events_with_volume = [e for e in events if e.volume_24h > 0]
            stats["events_zero_volume"] = len(events) - len(events_with_volume)
            if stats["events_zero_volume"] > 0:
                self.console.print(
                    f"[yellow]Excluded {stats['events_zero_volume']} zero-volume events[/yellow]"
                )
            events = events_with_volume

            stats["events_qualified"] = len(events)
            self.console.print(f"[green]Found {len(events)} qualified events[/green]")
            self._display_top_events(events[:10])

            # ----------------------------------------------------------------
            # Step 2 — Filter markets (deterministic, before any AI)
            # ----------------------------------------------------------------
            self.console.print("\n[bold]Step 2: Filtering markets...[/bold]")
            events_to_research: list = []  # only events with ≥1 passing market

            for event in events:
                passing, rejected = self.market_filter.filter_markets(
                    event.markets, event
                )
                stats["markets_scanned"] += len(event.markets)
                stats["markets_passed_filters"] += len(passing)

                # Log every scanned market to ledger
                passing_tickers = {m.ticker for m in passing}
                for market in event.markets:
                    rej_reason = ""
                    passed = market.ticker in passing_tickers
                    if not passed:
                        for r in rejected:
                            if r["ticker"] == market.ticker:
                                rej_reason = r["reason"]
                                break
                    self.ledger.log_scan(
                        self.run_id, market.ticker, event.ticker,
                        volume=market.volume, yes_ask=market.yes_ask,
                        yes_bid=market.yes_bid, close_time=market.close_time,
                        passed_filters=passed, filter_rejection=rej_reason,
                    )

                if not passing:
                    # No viable markets — skip research entirely
                    stats["events_skipped_no_viable_markets"] += 1
                    logger.info(
                        "SKIPPED_RESEARCH: event={} reason=no_viable_markets",
                        event.ticker,
                    )
                    continue

                # Replace event's market list with only the passing ones
                event.markets = passing
                events_to_research.append(event)

            self.console.print(
                f"[green]{stats['markets_passed_filters']} markets passed filters "
                f"across {len(events_to_research)} events "
                f"({stats['events_skipped_no_viable_markets']} events skipped — no viable markets)[/green]"
            )

            if not events_to_research:
                self.console.print(
                    "[red]No events have viable markets after filtering. "
                    "Consider lowering MIN_VOLUME_DEMO or MIN_VOLUME_PROD in .env[/red]"
                )
                return

            # ----------------------------------------------------------------
            # Step 3 — Research events (no odds in prompt)
            # ----------------------------------------------------------------
            self.console.print(
                f"\n[bold]Step 3: Researching {len(events_to_research)} events "
                f"(skipping {stats['events_skipped_no_viable_markets']} with no viable markets)...[/bold]"
            )
            research_results = await self._batch_research(events_to_research)
            all_research.update(research_results)
            stats["events_researched"] = len(research_results)

            if not research_results:
                self.console.print("[red]No research results returned.[/red]")
                return

            # ----------------------------------------------------------------
            # Step 4 — Preflight OpenAI check, then extract probabilities
            # ----------------------------------------------------------------
            self.console.print("\n[bold]Step 4: Checking OpenAI availability...[/bold]")
            openai_available, openai_err = await check_openai_availability(
                self.openai_client, model=self.config.openai.model
            )

            if not openai_available:
                self.console.print(f"[bold red]{openai_err}[/bold red]")
                self.console.print(
                    "[yellow]Skipping probability extraction and AI decisions. "
                    "Research data is preserved.[/yellow]"
                )
                errors.append(openai_err)
                # Fall through to summary — no decisions will be made but
                # research text is saved to the ledger
            else:
                self.console.print("[green]OpenAI available — extracting probabilities...[/green]")
                prob_extractions = await self._batch_extract_probabilities(
                    research_results, events_to_research
                )
                all_prob_extractions.update(prob_extractions)
                stats["prob_extraction_success"] = len(prob_extractions)
                stats["prob_extraction_failed"] = len(research_results) - len(prob_extractions)

                # ----------------------------------------------------------------
                # Step 5 — Fetch market odds & compute edge (AFTER research)
                # ----------------------------------------------------------------
                self.console.print("\n[bold]Step 5: Fetching market odds & computing edge...[/bold]")
                for event in events_to_research:
                    if event.ticker not in prob_extractions:
                        continue
                    extraction = prob_extractions[event.ticker]
                    for market in event.markets:
                        fresh = await self.kalshi_client.get_market(market.ticker)
                        if not fresh:
                            continue
                        all_market_odds[market.ticker] = {
                            "yes_ask": fresh.yes_ask,
                            "yes_bid": fresh.yes_bid,
                            "no_ask": fresh.no_ask,
                            "no_bid": fresh.no_bid,
                        }
                        research_prob = next(
                            (mp.research_probability for mp in extraction.market_probabilities
                             if mp.ticker == market.ticker),
                            None,
                        )
                        if research_prob is None:
                            continue
                        if fresh.yes_ask > 0:
                            metrics_yes = compute_edge_metrics(
                                market.ticker, research_prob, fresh.yes_ask, "buy_yes"
                            )
                            self.ledger.log_edge(
                                self.run_id, market.ticker,
                                metrics_yes.implied_prob, metrics_yes.edge,
                                metrics_yes.r_score, metrics_yes.kelly_fraction,
                            )

                # ----------------------------------------------------------------
                # Step 6 — Generate AI decisions for markets with sufficient edge
                # ----------------------------------------------------------------
                self.console.print("\n[bold]Step 6: Generating decisions...[/bold]")
                for event in events_to_research:
                    if event.ticker not in prob_extractions:
                        continue
                    extraction = prob_extractions[event.ticker]
                    event_decisions = await self._generate_decisions_for_event(
                        event, extraction, all_market_odds
                    )
                    all_decisions.extend(event_decisions)
                    stats["candidate_trades"] += len(event_decisions)

            # ----------------------------------------------------------------
            # Step 7 — Risk check & execute
            # ----------------------------------------------------------------
            self.console.print("\n[bold]Step 7: Risk check & execution...[/bold]")
            actionable = [d for d in all_decisions if d.action != "skip"]
            stats["actionable_decisions"] = len(actionable)
            self.console.print(
                f"[blue]{len(actionable)} actionable out of {len(all_decisions)} decisions[/blue]"
            )

            if actionable and self.executor:
                results = await self.executor.execute_batch(actionable)
                for result in results:
                    if not result.get("skipped"):
                        stats["bets_placed"] += 1
                        total_wagered += result.get("amount", 0)
                        self.ledger.log_order(
                            self.run_id,
                            result["ticker"],
                            result["action"],
                            result["amount"],
                            order_id=result.get("order_id"),
                            fill_price=result.get("fill_price"),
                            dry_run=result.get("dry_run", True),
                        )

            # Save CSV for backward compatibility
            self._save_csv(all_decisions, all_research, all_prob_extractions, all_market_odds, events_to_research)

            # Display decisions summary
            self._display_summary(all_decisions)

        except Exception as exc:
            errors.append(str(exc))
            self.console.print(f"[red]Bot execution error: {exc}[/red]")
            logger.exception("Bot execution failed")
        finally:
            # Persist pipeline stats to ledger
            if self.run_id:
                self.ledger.finish_run(
                    self.run_id,
                    pipeline_stats=stats,
                    openai_available=openai_available,
                    total_wagered=total_wagered,
                )

            # Print clean pipeline summary
            self._display_pipeline_stats(stats, openai_available, total_wagered, errors)

            summary = RunSummary(
                run_id=self.run_id or "unknown",
                timestamp=datetime.now(timezone.utc),
                events_scanned=stats["events_fetched"],
                markets_scanned=stats["markets_scanned"],
                decisions_made=stats["candidate_trades"],
                bets_placed=stats["bets_placed"],
                total_wagered=total_wagered,
                dry_run=self.config.dry_run,
                errors=errors,
            )
            log_run_summary(summary)

            if self.research_client:
                await self.research_client.close()
            if self.kalshi_client:
                await self.kalshi_client.close()

    # -- Internal pipeline steps -----------------------------------------------

    async def _batch_research(self, events) -> dict[str, str]:
        """Research events in parallel batches."""
        results: dict[str, str] = {}
        batch_size = self.config.strategy.research_batch_size

        for i in range(0, len(events), batch_size):
            batch = events[i : i + batch_size]
            tasks = []
            for event in batch:
                if event.markets:
                    coro = self.research_client.research_event(event)
                    tasks.append(
                        asyncio.wait_for(
                            coro, timeout=self.config.strategy.research_timeout_seconds
                        )
                    )

            if tasks:
                batch_results = await asyncio.gather(*tasks, return_exceptions=True)
                for event, result in zip(batch, batch_results):
                    if isinstance(result, Exception):
                        logger.error("Research failed for {}: {}", event.ticker, result)
                    elif result:
                        results[event.ticker] = result
                        self.ledger.log_research(self.run_id, event.ticker, result)
                        self.console.print(f"[green]Researched {event.ticker}[/green]")

            await asyncio.sleep(1)

        return results

    async def _batch_extract_probabilities(
        self, research_results, events
    ) -> dict[str, ProbabilityExtraction]:
        """Extract probabilities from research in parallel."""
        extractions: dict[str, ProbabilityExtraction] = {}
        tasks = []
        tickers = []

        for event in events:
            if event.ticker not in research_results:
                continue
            research_text = research_results[event.ticker]
            markets_info = [
                {"ticker": m.ticker, "title": m.title} for m in event.markets
            ]
            tickers.append(event.ticker)
            tasks.append(
                extract_probabilities_from_text(
                    self.openai_client,
                    research_text,
                    markets_info,
                    event_title=event.title,
                    model=self.config.openai.model,
                )
            )

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for ticker, result in zip(tickers, results):
                if isinstance(result, Exception):
                    logger.error("Probability extraction failed for {}: {}", ticker, result)
                else:
                    extractions[ticker] = result
                    for mp in result.market_probabilities:
                        self.ledger.log_probability(
                            self.run_id, mp.ticker,
                            mp.research_probability, mp.confidence, mp.reasoning,
                        )

        return extractions

    async def _generate_decisions_for_event(
        self, event, extraction, market_odds
    ) -> list[BettingDecision]:
        """Generate decisions for a single event with edge filtering."""
        decisions: list[BettingDecision] = []

        for mp in extraction.market_probabilities:
            odds = market_odds.get(mp.ticker)
            if not odds:
                continue

            yes_ask = odds.get("yes_ask", 0)
            no_ask = odds.get("no_ask", 0)

            if yes_ask <= 0:
                continue

            # Compute edge metrics
            metrics = compute_edge_metrics(mp.ticker, mp.research_probability, yes_ask, "buy_yes")

            # Check if edge meets threshold
            if metrics.edge < self.config.strategy.min_edge:
                decisions.append(BettingDecision(
                    ticker=mp.ticker, title=mp.title, event_ticker=event.ticker,
                    action="skip", confidence=mp.confidence, amount=0,
                    reasoning=f"Edge {metrics.edge:.3f} < min {self.config.strategy.min_edge}",
                    edge_metrics=metrics, skip_reason="edge_below_threshold",
                ))
                continue

            if metrics.r_score < self.config.strategy.z_threshold:
                decisions.append(BettingDecision(
                    ticker=mp.ticker, title=mp.title, event_ticker=event.ticker,
                    action="skip", confidence=mp.confidence, amount=0,
                    reasoning=f"R-score {metrics.r_score:.2f} < threshold {self.config.strategy.z_threshold}",
                    edge_metrics=metrics, skip_reason="r_score_below_threshold",
                ))
                continue

            # Compute position size
            amount = compute_kelly_position_size(
                metrics.kelly_fraction,
                self.config.risk.bankroll,
                self.config.strategy.kelly_fraction,
                self.config.strategy.max_kelly_fraction,
                self.config.risk.max_bet_amount,
            )

            # Determine action
            action = "buy_yes" if metrics.edge > 0 else "buy_no"

            # Risk check
            current_state = {
                "open_positions": self.risk_manager.open_positions,
                "event_exposure": self.risk_manager._event_exposure,
                "daily_loss": self.risk_manager.daily_loss,
            }
            allowed, reason = self.risk_manager.gate_trade(
                mp.ticker, event.ticker, amount, current_state
            )

            if not allowed:
                decisions.append(BettingDecision(
                    ticker=mp.ticker, title=mp.title, event_ticker=event.ticker,
                    action="skip", confidence=mp.confidence, amount=0,
                    reasoning=f"Risk blocked: {reason}",
                    edge_metrics=metrics, skip_reason=reason,
                ))
                continue

            decisions.append(BettingDecision(
                ticker=mp.ticker, title=mp.title, event_ticker=event.ticker,
                action=action, confidence=mp.confidence, amount=amount,
                reasoning=f"Edge={metrics.edge:.3f} R={metrics.r_score:.2f} Kelly={metrics.kelly_fraction:.3f}",
                edge_metrics=metrics,
            ))

            # Log decision
            self.ledger.log_decision(
                self.run_id, mp.ticker, action,
                confidence=mp.confidence, amount=amount,
                reasoning=f"Edge={metrics.edge:.3f}",
            )

            log_trade_decision(
                {"ticker": mp.ticker, "action": action, "amount": amount},
                edge_metrics=metrics.model_dump(),
            )

        return decisions

    # -- Display helpers -------------------------------------------------------

    def _display_top_events(self, events) -> None:
        """Show a rich table of top events."""
        table = Table(title="Top Events by 24h Volume")
        table.add_column("Ticker", style="cyan")
        table.add_column("Title", style="yellow")
        table.add_column("24h Vol", style="magenta", justify="right")
        table.add_column("Category", style="green")

        for event in events:
            table.add_row(
                event.ticker,
                event.title[:40],
                f"{event.volume_24h:,}",
                event.category,
            )
        self.console.print(table)

    def _display_summary(self, decisions) -> None:
        """Display a summary table of all decisions."""
        actionable = [d for d in decisions if d.action != "skip"]
        if not actionable:
            self.console.print("[yellow]No actionable decisions[/yellow]")
            return

        table = Table(title="Betting Decisions Summary", show_lines=True)
        table.add_column("Ticker", style="cyan")
        table.add_column("Action", style="yellow")
        table.add_column("Amount", style="green", justify="right")
        table.add_column("Confidence", style="magenta", justify="right")
        table.add_column("Edge", style="blue", justify="right")
        table.add_column("R-Score", style="blue", justify="right")

        for d in actionable:
            edge_str = f"{d.edge_metrics.edge:.3f}" if d.edge_metrics else "N/A"
            r_str = f"{d.edge_metrics.r_score:.2f}" if d.edge_metrics else "N/A"
            table.add_row(
                d.ticker, d.action, f"${d.amount:.2f}",
                f"{d.confidence:.2f}", edge_str, r_str,
            )
        self.console.print(table)

        total = sum(d.amount for d in actionable)
        self.console.print(f"\n[blue]Total recommended: ${total:.2f}[/blue]")

    def _display_pipeline_stats(
        self,
        stats: dict,
        openai_available: bool,
        total_wagered: float,
        errors: list[str],
    ) -> None:
        """Print a clean per-stage run summary."""
        table = Table(title="Pipeline Run Summary", show_header=False, box=None)
        table.add_column("Stage", style="cyan", min_width=32)
        table.add_column("Value", style="white", justify="right")

        def row(label: str, value, *, ok_if_zero: bool = False) -> None:
            color = "white"
            if isinstance(value, int) and value == 0 and not ok_if_zero:
                color = "yellow"
            table.add_row(label, f"[{color}]{value}[/{color}]")

        row("Events fetched", stats.get("events_fetched", 0), ok_if_zero=True)
        row("  └ zero-volume excluded", stats.get("events_zero_volume", 0), ok_if_zero=True)
        row("  └ qualified events", stats.get("events_qualified", 0))
        row("Markets scanned", stats.get("markets_scanned", 0))
        row("  └ passed filters", stats.get("markets_passed_filters", 0))
        row("Events skipped (no viable markets)", stats.get("events_skipped_no_viable_markets", 0), ok_if_zero=True)
        row("Events researched", stats.get("events_researched", 0))

        openai_str = "[green]✓ available[/green]" if openai_available else "[red]✗ unavailable[/red]"
        table.add_row("OpenAI status", openai_str)

        row("Prob extractions succeeded", stats.get("prob_extraction_success", 0))
        row("Prob extractions failed", stats.get("prob_extraction_failed", 0), ok_if_zero=True)
        row("Candidate trades evaluated", stats.get("candidate_trades", 0))
        row("Actionable decisions", stats.get("actionable_decisions", 0))
        row("Bets placed", stats.get("bets_placed", 0), ok_if_zero=True)
        table.add_row("Total wagered", f"[green]${total_wagered:.2f}[/green]")

        self.console.print("\n")
        self.console.print(table)

        if errors:
            self.console.print(f"\n[red]Errors ({len(errors)}):[/red]")
            for e in errors[:5]:
                self.console.print(f"  [red]• {e[:120]}[/red]")

    def _save_csv(self, decisions, research_results, prob_extractions, market_odds, events) -> None:
        """Save decisions to CSV for backward compatibility."""
        output_dir = Path("betting_decisions")
        output_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = output_dir / f"betting_decisions_{timestamp}.csv"

        rows = []
        for d in decisions:
            odds = market_odds.get(d.ticker, {})
            yes_ask = odds.get("yes_ask", 0)
            no_ask = odds.get("no_ask", 0)

            # Skip markets without valid prices
            if not yes_ask or not no_ask:
                continue

            row = {
                "timestamp": datetime.now().isoformat(),
                "event_ticker": d.event_ticker or "",
                "market_ticker": d.ticker,
                "market_title": d.title or "",
                "action": d.action,
                "bet_amount": d.amount,
                "confidence": d.confidence,
                "reasoning": d.reasoning,
                "market_yes_price": yes_ask,
                "market_no_price": no_ask,
                "is_hedge": d.is_hedge,
            }

            if d.edge_metrics:
                row.update({
                    "research_probability": d.edge_metrics.research_prob * 100,
                    "edge": d.edge_metrics.edge,
                    "r_score": d.edge_metrics.r_score,
                    "kelly_fraction": d.edge_metrics.kelly_fraction,
                    "expected_return": d.edge_metrics.expected_return,
                })

            rows.append(row)

        if rows:
            fieldnames = list(rows[0].keys())
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            self.console.print(f"[green]CSV saved: {filepath}[/green]")


# -- Legacy compatibility wrapper ---------------------------------------------


class SimpleTradingBot(TradingBot):
    """Legacy alias for backward compatibility."""
    pass


# -- CLI ----------------------------------------------------------------------


async def main(live_trading: bool = False, max_close_ts: Optional[int] = None) -> None:
    """Main entry point."""
    setup_logging()
    bot = TradingBot(live_trading=live_trading, max_close_ts=max_close_ts)
    await bot.run()


def cli() -> None:
    """Command line interface entry point."""
    parser = argparse.ArgumentParser(
        description="Kalshi trading bot with Octagon research and OpenAI decisions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run trading-bot                    # Dry run mode (default)
  uv run trading-bot --live             # Live trading (use with caution!)
  uv run trading-bot --max-expiration-hours 24
  uv run trading-bot --dashboard        # Launch Streamlit dashboard
        """,
    )

    parser.add_argument(
        "--live", action="store_true", help="Enable live trading"
    )
    parser.add_argument(
        "--max-expiration-hours",
        type=int,
        default=None,
        dest="max_expiration_hours",
        help="Only include markets closing within this many hours",
    )
    parser.add_argument(
        "--version", action="version", version="Kalshi Trading Bot 2.0.0"
    )
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Launch the Streamlit dashboard instead of running the bot",
    )

    args = parser.parse_args()

    if args.dashboard:
        import subprocess

        subprocess.run(
            [sys.executable, "-m", "streamlit", "run", "src/dashboard/app.py"],
            check=True,
        )
        return

    try:
        max_close_ts = None
        if args.max_expiration_hours is not None:
            hours = max(1, args.max_expiration_hours)
            max_close_ts = int(time.time()) + (hours * 3600)

        if args.live:
            console = Console()
            console.print(
                "\n[bold red]WARNING: You are about to run in LIVE TRADING mode.[/bold red]"
            )
            console.print("[bold red]Real money will be at risk.[/bold red]\n")

        asyncio.run(main(live_trading=args.live, max_close_ts=max_close_ts))
    except Exception as exc:
        console = Console()
        console.print(f"[red]Error: {exc}[/red]")
        console.print("[yellow]Check your .env file. Run with --help for info.[/yellow]")
        sys.exit(1)


if __name__ == "__main__":
    cli()
