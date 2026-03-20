"""
Continuous sports arbitrage scanner.

Each scan cycle:
  1. Fetch all open Kalshi sports events.
  2. (If provider configured) Fetch sportsbook reference odds for each sport.
  3. For every sports market:
     a. Detect Kalshi-internal arb (YES ask + NO ask vs 100¢).
     b. Try to match against a sportsbook game and compute cross-exchange edge.
  4. Rank, alert, and persist all opportunities.
"""

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Optional

from loguru import logger
from rich.console import Console
from rich.table import Table

from src.brokers.kalshi.client import KalshiClient
from src.brokers.kalshi.models import KalshiEvent
from src.config import BotConfig
from src.matching.engine import MatchingEngine
from src.portfolio.ledger import Ledger
from src.sports.alert import AlertManager
from src.sports.arb_engine import compute_arb_result, rank_arb_results
from src.sports.comparison import compute_cross_exchange_opportunity
from src.sports.detector import (
    detect_opportunity,
    filter_sports,
    is_sports_event,
    rank_opportunities,
)
from src.sports.ev_engine import compute_ev_candidate
from src.sports.models import ArbOpportunity, ArbResult, ArbType, EVCandidate, ScanSummary
from src.sportsbook.base import OddsProvider
from src.sportsbook.models import SportsbookGame
from src.utils.time import hours_until

_console = Console()


class SportsArbScanner:
    """Fetches Kalshi sports markets continuously and surfaces arb opportunities.

    Phase 1: Kalshi-internal arb detection (pure and near-arb).
    Phase 2: Cross-exchange comparison via pluggable sportsbook provider.

    All execution is disabled by default (alert and log only).
    """

    def __init__(
        self,
        kalshi_client: KalshiClient,
        ledger: Ledger,
        alert_manager: AlertManager,
        config: BotConfig,
        odds_provider: Optional[OddsProvider] = None,
    ) -> None:
        self._client = kalshi_client
        self._ledger = ledger
        self._alerts = alert_manager
        self._cfg = config
        self._arb_cfg = config.sports_arb
        self._match_cfg = config.matching
        self._sb_cfg = config.sportsbook
        self._sports_filter = filter_sports(self._arb_cfg.sports)
        self._odds_provider = odds_provider
        self._matching_engine: Optional[MatchingEngine] = None

        if odds_provider is not None:
            self._matching_engine = MatchingEngine(
                min_confidence=self._match_cfg.min_match_confidence,
                time_window_hours=self._match_cfg.time_window_hours,
                fuzzy_threshold=self._match_cfg.fuzzy_team_threshold,
            )
            logger.info(
                "Scanner: sportsbook comparison enabled (provider={}, bookmaker={})",
                self._sb_cfg.provider,
                self._sb_cfg.preferred_bookmaker,
            )
        else:
            logger.info("Scanner: sportsbook comparison disabled (no provider configured)")

    # -- Public API -----------------------------------------------------------

    async def scan_once(self) -> ScanSummary:
        """Execute one full scan cycle and return a summary."""
        scan_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc).isoformat()
        t0 = datetime.now(timezone.utc)

        logger.info("=== SCAN CYCLE {} ===", scan_id[:8])

        summary = ScanSummary(
            scan_id=scan_id,
            started_at=started_at,
            finished_at="",
            duration_seconds=0.0,
        )

        # 1. Fetch Kalshi events
        try:
            events: list[KalshiEvent] = await self._client.get_events(
                limit=self._arb_cfg.max_events_per_scan,
                min_hours_to_expiry=self._arb_cfg.min_hours_to_close,
                min_event_volume=0,
            )
        except Exception as exc:
            logger.error("Failed to fetch events: {}", exc)
            events = []

        summary.events_fetched = len(events)

        # 2. Fetch sportsbook games (per active sport)
        sportsbook_games: dict[str, list[SportsbookGame]] = {}
        if self._odds_provider is not None:
            active_sports = list(self._sports_filter) if self._sports_filter else [
                "NBA", "NFL", "MLB", "NHL", "UFC", "SOC", "TEN"
            ]
            try:
                sportsbook_games = await self._odds_provider.get_all_games(
                    sports=active_sports,
                    bookmakers=[self._sb_cfg.preferred_bookmaker]
                    + self._sb_cfg.fallback_bookmakers,
                )
                total_sb_games = sum(len(v) for v in sportsbook_games.values())
                logger.info(
                    "Sportsbook: {} games fetched across {} sports",
                    total_sb_games,
                    len(sportsbook_games),
                )
            except Exception as exc:
                logger.error("Failed to fetch sportsbook games: {}", exc)

        # 3. Detect opportunities
        opportunities: list[ArbOpportunity] = []
        arb_results: list[ArbResult] = []
        ev_candidates: list[EVCandidate] = []

        for event in events:
            if not is_sports_event(event, check_markets=True):
                continue

            summary.sports_events_found += 1

            for market in event.markets:
                # Volume gate
                if market.volume < self._arb_cfg.min_volume:
                    continue

                # Expiry gate
                hrs = hours_until(market.close_time)
                if hrs < self._arb_cfg.min_hours_to_close:
                    continue
                if hrs > self._arb_cfg.max_hours_to_close:
                    continue

                summary.markets_checked += 1

                from src.sports.detector import extract_sport
                sport_code = extract_sport(market.ticker).value

                # 3a. Kalshi-internal arb (legacy detector — for backward compat)
                opp = detect_opportunity(
                    market=market,
                    event=event,
                    near_arb_threshold_pct=self._arb_cfg.near_arb_threshold_pct,
                    scan_id=scan_id,
                    sports_filter=self._sports_filter,
                )
                if opp is not None:
                    opportunities.append(opp)
                    if opp.arb_type == ArbType.PURE_ARB:
                        summary.pure_arbs_found += 1
                    elif opp.arb_type == ArbType.NEAR_ARB:
                        summary.near_arbs_found += 1

                # 3b. New arb engine (with full friction model)
                arb_result = compute_arb_result(
                    market=market,
                    event=event,
                    scan_id=scan_id,
                    near_arb_threshold_pct=self._arb_cfg.near_arb_threshold_pct,
                    min_net_arb_pct=0.0,
                    sports_filter=self._sports_filter,
                )
                if arb_result is not None:
                    arb_results.append(arb_result)
                    try:
                        self._ledger.log_arb_result(arb_result)
                    except Exception as exc:
                        logger.debug("Failed to log arb_result: {}", exc)

                # 3c. Cross-exchange comparison + EV engine
                if self._matching_engine is not None and sportsbook_games:
                    sb_games_for_sport = sportsbook_games.get(sport_code, [])

                    if sb_games_for_sport:
                        match = self._matching_engine.find_match(
                            kalshi_market=market,
                            kalshi_event=event,
                            sportsbook_games=sb_games_for_sport,
                            sport=sport_code,
                        )
                        if match is not None:
                            matched_game = next(
                                (g for g in sb_games_for_sport if g.game_id == match.sportsbook_game_id),
                                None,
                            )
                            if matched_game is not None:
                                # Legacy cross-exchange (Phase 2)
                                cross_opp = compute_cross_exchange_opportunity(
                                    kalshi_market=market,
                                    kalshi_event=event,
                                    match=match,
                                    game=matched_game,
                                    scan_id=scan_id,
                                    bookmaker_key=self._sb_cfg.preferred_bookmaker,
                                    min_net_edge_pct=self._match_cfg.min_net_edge_pct,
                                )
                                if cross_opp is not None:
                                    opportunities.append(cross_opp)
                                    if cross_opp.arb_type == ArbType.POSITIVE_EV:
                                        summary.positive_ev_found += 1
                                    elif cross_opp.arb_type == ArbType.PURE_ARB:
                                        summary.pure_arbs_found += 1

                                # New EV engine
                                ev = compute_ev_candidate(
                                    market=market,
                                    event=event,
                                    match=match,
                                    game=matched_game,
                                    scan_id=scan_id,
                                    bookmaker_key=self._sb_cfg.preferred_bookmaker,
                                    sport=sport_code,
                                    min_edge_pct=self._match_cfg.min_net_edge_pct,
                                    kelly_fraction=0.5,
                                )
                                if ev is not None:
                                    ev_candidates.append(ev)
                                    summary.ev_candidates_found += 1
                                    try:
                                        self._ledger.log_ev_candidate(ev)
                                    except Exception as exc:
                                        logger.debug("Failed to log ev_candidate: {}", exc)

        summary.arb_results_found = len(arb_results)

        # 4. Rank and alert
        ranked = rank_opportunities(opportunities)

        for opp in ranked:
            alerted = self._alerts.process(opp)
            opp.alerted = alerted
            if alerted:
                summary.alerts_emitted += 1

            self._ledger.log_arb_opportunity(scan_id=scan_id, opp=opp, alerted=alerted)

        # 5. Finalise summary
        t1 = datetime.now(timezone.utc)
        summary.finished_at = t1.isoformat()
        summary.duration_seconds = round((t1 - t0).total_seconds(), 2)

        if ranked:
            best = ranked[0]
            summary.best_arb_pct = best.arb_pct
            summary.best_ticker = best.ticker
            summary.best_arb_type = best.arb_type

        self._print_scan_summary(summary, ranked)
        return summary

    async def run_continuous(self) -> None:
        """Run the scanner in an infinite loop until interrupted."""
        interval = self._arb_cfg.scan_interval_seconds
        _console.rule("[bold blue]⚡ Kalshi Sports Arbitrage Scanner — Started")
        logger.info(
            "Scanner started | interval={}s sports={} near_arb_threshold={}% provider={}",
            interval,
            self._arb_cfg.sports or "ALL",
            self._arb_cfg.near_arb_threshold_pct,
            self._sb_cfg.provider,
        )

        if self._odds_provider is not None:
            await self._odds_provider.connect()

            if self._sb_cfg.health_check_on_startup:
                health = await self._odds_provider.health_check()
                if health.ok:
                    logger.info(
                        "Odds provider health OK — provider={} message='{}' quota_remaining={}",
                        health.provider,
                        health.message,
                        health.requests_remaining or "n/a",
                    )
                else:
                    logger.warning(
                        "Odds provider health FAILED — provider={} message='{}' "
                        "— cross-exchange comparison may not work",
                        health.provider,
                        health.message,
                    )

        try:
            while True:
                await self.scan_once()
                logger.info("Next scan in {}s", interval)
                await asyncio.sleep(interval)
        except KeyboardInterrupt:
            _console.rule("[yellow]Scanner stopped by user")
            logger.info("Scanner stopped by KeyboardInterrupt")
        except Exception as exc:
            logger.exception("Scanner crashed: {}", exc)
            raise
        finally:
            if self._odds_provider is not None:
                await self._odds_provider.close()

    # -- Console output -------------------------------------------------------

    def _print_scan_summary(
        self, summary: ScanSummary, ranked: list[ArbOpportunity]
    ) -> None:
        """Print a compact scan result table to the console."""
        duration = f"{summary.duration_seconds:.1f}s"
        ts = summary.finished_at[:19].replace("T", " ") + " UTC"

        _console.print(
            f"[dim]{ts}[/dim]  "
            f"Events: [white]{summary.events_fetched}[/white]  "
            f"Sports: [cyan]{summary.sports_events_found}[/cyan]  "
            f"Checked: [white]{summary.markets_checked}[/white]  "
            f"Pure arbs: [bold red]{summary.pure_arbs_found}[/bold red]  "
            f"Near-arbs: [bold yellow]{summary.near_arbs_found}[/bold yellow]  "
            f"Pos-EV: [bold green]{summary.positive_ev_found}[/bold green]  "
            f"Duration: [dim]{duration}[/dim]"
        )

        if not ranked:
            _console.print("[dim]  No opportunities this cycle.[/dim]")
            return

        table = Table(
            show_header=True,
            header_style="bold dim",
            border_style="dim",
            expand=False,
        )
        table.add_column("Type", style="bold", width=10)
        table.add_column("Sport", width=5)
        table.add_column("Ticker", width=28)
        table.add_column("YES¢", justify="right", width=5)
        table.add_column("NO¢", justify="right", width=5)
        table.add_column("Total", justify="right", width=6)
        table.add_column("Arb %", justify="right", width=8)
        table.add_column("Profit/$100", justify="right", width=11)
        table.add_column("Provider", width=10)
        table.add_column("Match%", justify="right", width=7)
        table.add_column("Vol", justify="right", width=7)

        for opp in ranked[:20]:
            is_pure = opp.arb_type == ArbType.PURE_ARB
            is_pos_ev = opp.arb_type == ArbType.POSITIVE_EV

            if is_pure:
                type_label = "[bold red]PURE ARB[/bold red]"
                arb_str = f"[bold red]{opp.arb_pct:+.2f}%[/bold red]"
            elif is_pos_ev:
                type_label = "[bold green]POS EV[/bold green]"
                arb_str = f"[bold green]{opp.arb_pct:+.2f}%[/bold green]"
            else:
                type_label = "[yellow]NEAR ARB[/yellow]"
                arb_str = f"[yellow]{opp.arb_pct:+.2f}%[/yellow]"

            profit = opp.expected_profit_at_100
            profit_str = (
                f"[green]${profit:+.2f}[/green]"
                if profit >= 0 else f"[red]${profit:.2f}[/red]"
            )
            provider = opp.sportsbook_provider or "—"
            match_pct = (
                f"{opp.match_confidence:.0%}" if opp.match_confidence else "—"
            )
            from src.sports.detector import extract_sport
            sport_code = extract_sport(opp.ticker).value

            table.add_row(
                type_label,
                sport_code,
                opp.ticker[:26],
                f"{opp.yes_ask}",
                f"{opp.no_ask}",
                f"{opp.total_cost}¢",
                arb_str,
                profit_str,
                provider[:10],
                match_pct,
                f"{opp.volume:,}",
            )

        _console.print(table)
