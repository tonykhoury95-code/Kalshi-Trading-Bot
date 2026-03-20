"""
Structured logging setup using loguru.

Provides console, file, and audit sinks with secret redaction.
"""

import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger


def redact_secrets(text: str) -> str:
    """Redact known secret patterns from text.

    Handles:
    - API keys (common patterns)
    - PEM private key content
    - Authorization header values
    - Bearer tokens
    """
    # PEM key blocks
    text = re.sub(
        r"-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]*?-----END[A-Z ]*PRIVATE KEY-----",
        "***PEM_KEY_REDACTED***",
        text,
    )

    # Common API key patterns (long alphanumeric strings following key= or key:)
    text = re.sub(
        r"((?:api[_-]?key|apikey|secret|token|password|authorization)[=:\s]+)['\"]?([a-zA-Z0-9_\-]{20,})['\"]?",
        r"\1***REDACTED***",
        text,
        flags=re.IGNORECASE,
    )

    # Bearer tokens
    text = re.sub(
        r"(Bearer\s+)[a-zA-Z0-9_\-\.]+",
        r"\1***REDACTED***",
        text,
    )

    # KALSHI-ACCESS-KEY values
    text = re.sub(
        r"(KALSHI-ACCESS-KEY[=:\s]+)[a-zA-Z0-9_\-]+",
        r"\1***REDACTED***",
        text,
        flags=re.IGNORECASE,
    )

    # KALSHI-ACCESS-SIGNATURE values
    text = re.sub(
        r"(KALSHI-ACCESS-SIGNATURE[=:\s]+)[a-zA-Z0-9+/=]+",
        r"\1***REDACTED***",
        text,
        flags=re.IGNORECASE,
    )

    return text


def setup_logging(
    log_level: str = "INFO",
    log_dir: str = "./logs",
) -> None:
    """Configure loguru with console, file, and audit sinks.

    Args:
        log_level: Minimum log level for console output.
        log_dir: Directory for log files.
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # Remove default sink
    logger.remove()

    # Console sink: human-readable with colors, INFO level
    logger.add(
        sys.stderr,
        level=log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        colorize=True,
        filter=lambda record: True,
    )

    # File sink: detailed logs, DEBUG level, with rotation
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    logger.add(
        str(log_path / f"run_{timestamp}.log"),
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
        rotation="50 MB",
        retention="7 days",
        encoding="utf-8",
    )

    # Audit sink: trade decisions only
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    logger.add(
        str(log_path / f"audit_{date_str}.log"),
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | AUDIT | {message}",
        filter=lambda record: record.get("extra", {}).get("audit", False),
        rotation="50 MB",
        encoding="utf-8",
    )

    logger.info("Logging initialized (level={}, dir={})", log_level, log_dir)


def get_logger(name: str):
    """Return a bound logger with a ``name`` context field.

    Args:
        name: Module or component name for the logger.
    """
    return logger.bind(name=name)


def log_trade_decision(
    decision: dict,
    edge_metrics: Optional[dict] = None,
    risk_outcome: Optional[dict] = None,
) -> None:
    """Write a structured audit log entry for a trade decision.

    Args:
        decision: Dict with decision details (ticker, action, amount, etc.).
        edge_metrics: Optional edge computation details.
        risk_outcome: Optional risk check result.
    """
    entry = {
        "type": "trade_decision",
        "decision": decision,
    }
    if edge_metrics:
        entry["edge_metrics"] = edge_metrics
    if risk_outcome:
        entry["risk_outcome"] = risk_outcome

    # Use the audit-filtered logger
    logger.bind(audit=True).info("TRADE_DECISION: {}", entry)


def log_run_summary(summary) -> None:
    """Log a formatted run summary.

    Args:
        summary: A ``RunSummary`` model instance.
    """
    logger.info("=" * 60)
    logger.info("RUN SUMMARY")
    logger.info("=" * 60)
    logger.info("Run ID:          {}", summary.run_id)
    logger.info("Timestamp:       {}", summary.timestamp)
    logger.info("Events scanned:  {}", summary.events_scanned)
    logger.info("Markets scanned: {}", summary.markets_scanned)
    logger.info("Decisions made:  {}", summary.decisions_made)
    logger.info("Bets placed:     {}", summary.bets_placed)
    logger.info("Total wagered:   ${:.2f}", summary.total_wagered)
    logger.info("Dry run:         {}", summary.dry_run)
    if summary.errors:
        logger.warning("Errors ({}): {}", len(summary.errors), summary.errors)
    logger.info("=" * 60)

    # Also write to audit log
    logger.bind(audit=True).info("RUN_SUMMARY: {}", summary.model_dump())
