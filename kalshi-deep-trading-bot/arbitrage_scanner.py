"""
Kalshi Sports Arbitrage Scanner — main entry point.

Phase 1: Scan-and-alert only. No orders are placed.

Usage
-----
    # Continuous scan (default):
    uv run python arbitrage_scanner.py

    # Single scan then exit:
    uv run python arbitrage_scanner.py --once

    # Live/in-play markets only:
    uv run python arbitrage_scanner.py --live-only

    # Specific sport(s):
    uv run python arbitrage_scanner.py --sports NBA,NFL

Environment
-----------
All settings are read from .env (or environment variables).
See .env.example for the full list. Key vars for the arb scanner:

    ARB_NEAR_THRESHOLD_PCT=2.0       # flag markets within 2% of breakeven
    ARB_SCAN_INTERVAL_SECONDS=30     # seconds between scans
    ARB_MIN_VOLUME=100               # minimum market volume
    ARB_SPORTS=NBA,NFL,MLB,NHL,UFC   # comma-separated sport codes
    ARB_LIVE_ONLY=false              # true = in-play markets only
"""

import argparse
import asyncio
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure the repo root is on sys.path when run directly
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent))

from loguru import logger

from src.brokers.kalshi.client import KalshiClient
from src.config import BotConfig
from src.portfolio.ledger import Ledger
from src.sports.alert import AlertManager
from src.sports.scanner import SportsArbScanner
from src.sportsbook.registry import get_provider
from src.telemetry.logger import setup_logging


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Kalshi Sports Arbitrage Scanner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single scan cycle then exit (default: continuous loop)",
    )
    parser.add_argument(
        "--live-only",
        action="store_true",
        help="Override ARB_LIVE_ONLY: scan only markets closing within 4 hours",
    )
    parser.add_argument(
        "--sports",
        type=str,
        default="",
        help="Comma-separated sport codes to scan, e.g. NBA,NFL (overrides ARB_SPORTS in .env)",
    )
    parser.add_argument(
        "--near-threshold",
        type=float,
        default=None,
        help="Near-arb threshold %% (overrides ARB_NEAR_THRESHOLD_PCT in .env)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Scan interval in seconds (overrides ARB_SCAN_INTERVAL_SECONDS in .env)",
    )
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> None:
    """Async entry point."""
    config = BotConfig.from_env()

    # CLI overrides
    if args.live_only:
        config.sports_arb.live_only = True
        config.sports_arb.max_hours_to_close = 4.0
    if args.sports:
        config.sports_arb.sports = [s.strip().upper() for s in args.sports.split(",") if s.strip()]
    if args.near_threshold is not None:
        config.sports_arb.near_arb_threshold_pct = args.near_threshold
    if args.interval is not None:
        config.sports_arb.scan_interval_seconds = args.interval

    logger.info(
        "Arb scanner config: sports={} threshold={}% interval={}s live_only={}",
        config.sports_arb.sports,
        config.sports_arb.near_arb_threshold_pct,
        config.sports_arb.scan_interval_seconds,
        config.sports_arb.live_only,
    )

    # Build sportsbook odds provider (None = Kalshi-internal arb only)
    odds_provider = None
    try:
        odds_provider = get_provider(
            provider=config.sportsbook.provider,
            api_key=config.sportsbook.the_odds_api_key,
            markets=config.sportsbook.markets,
        )
        logger.info(
            "Odds provider: {} (markets={}, bookmaker={})",
            config.sportsbook.provider,
            config.sportsbook.markets,
            config.sportsbook.preferred_bookmaker,
        )
    except Exception as exc:
        logger.warning("Could not initialise odds provider: {} — comparison disabled", exc)

    kalshi_client = KalshiClient(config.kalshi)
    ledger = Ledger()
    alert_manager = AlertManager(
        change_threshold_pct=config.sports_arb.alert_change_threshold_pct
    )
    scanner = SportsArbScanner(
        kalshi_client=kalshi_client,
        ledger=ledger,
        alert_manager=alert_manager,
        config=config,
        odds_provider=odds_provider,
    )

    await kalshi_client.connect()
    try:
        if args.once:
            summary = await scanner.scan_once()
            logger.info(
                "Single scan complete: {} pure arbs, {} near-arbs, {} positive-EV",
                summary.pure_arbs_found,
                summary.near_arbs_found,
                summary.positive_ev_found,
            )
        else:
            await scanner.run_continuous()
    finally:
        await kalshi_client.close()


def main() -> None:
    """CLI entry point."""
    setup_logging()
    args = _parse_args()
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        pass


# pyproject.toml script entry: arb-scanner = "arbitrage_scanner:main"

if __name__ == "__main__":
    main()
