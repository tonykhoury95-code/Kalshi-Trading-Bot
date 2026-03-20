"""
SQLite ledger for recording all bot activity.

Uses raw sqlite3 (no ORM) for simplicity and portability.  Tables are
created on first use.  All write methods use context-managed connections.
"""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from loguru import logger

if TYPE_CHECKING:
    from src.sports.models import ArbOpportunity, ArbResult, EVCandidate


class Ledger:
    """SQLite-backed ledger for tracking runs, scans, research, decisions, and orders.

    Creates all tables on initialization if they do not exist.
    """

    def __init__(self, db_path: Path = Path("./data/ledger.db")):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._create_tables()
        self._migrate()
        logger.info("Ledger initialized at {}", self.db_path)

    @contextmanager
    def _connect(self):
        """Yield a sqlite3 connection with row_factory set to Row."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _migrate(self) -> None:
        """Add any new columns to existing tables (safe, additive only)."""
        new_cols = [
            ("runs", "pipeline_stats", "TEXT"),
            ("runs", "openai_available", "INTEGER"),
            ("runs", "total_wagered", "REAL DEFAULT 0.0"),
            ("runs", "finished_at", "TEXT"),
            # arb_opportunities Phase 1 legacy stubs
            ("arb_opportunities", "fanduel_price", "REAL"),
            ("arb_opportunities", "cross_exchange_edge", "REAL"),
            # arb_opportunities Phase 2 — sportsbook comparison
            ("arb_opportunities", "sportsbook_provider", "TEXT"),
            ("arb_opportunities", "sportsbook_game_id", "TEXT"),
            ("arb_opportunities", "sportsbook_bookmaker", "TEXT"),
            ("arb_opportunities", "sportsbook_price_american", "INTEGER"),
            ("arb_opportunities", "sportsbook_implied_prob", "REAL"),
            ("arb_opportunities", "kalshi_implied_prob", "REAL"),
            ("arb_opportunities", "gross_arb_pct", "REAL"),
            ("arb_opportunities", "net_arb_pct", "REAL"),
            ("arb_opportunities", "spread_friction_pct", "REAL"),
            ("arb_opportunities", "fill_risk_pct", "REAL"),
            ("arb_opportunities", "required_stake_dollars", "REAL"),
            ("arb_opportunities", "yes_team", "TEXT"),
            ("arb_opportunities", "match_confidence", "REAL"),
            ("arb_opportunities", "match_method", "TEXT"),
            ("arb_opportunities", "status", "TEXT DEFAULT 'new'"),
            ("arb_opportunities", "explanation", "TEXT"),
        ]
        with self._connect() as conn:
            for table, col, col_type in new_cols:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                except Exception:
                    pass  # Column already exists — safe to ignore

    def _create_tables(self) -> None:
        """Create all tables if they do not exist."""
        with self._connect() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    config_snapshot TEXT,
                    dry_run INTEGER NOT NULL DEFAULT 1,
                    pipeline_stats TEXT,
                    openai_available INTEGER,
                    total_wagered REAL DEFAULT 0.0,
                    finished_at TEXT
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS scanned_markets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    event_ticker TEXT NOT NULL,
                    volume INTEGER,
                    yes_ask INTEGER,
                    yes_bid INTEGER,
                    close_time TEXT,
                    passed_filters INTEGER NOT NULL DEFAULT 0,
                    filter_rejection TEXT,
                    FOREIGN KEY (run_id) REFERENCES runs(run_id)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS research (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    event_ticker TEXT NOT NULL,
                    research_text TEXT,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES runs(run_id)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS probability_estimates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    research_probability REAL,
                    confidence REAL,
                    reasoning TEXT,
                    FOREIGN KEY (run_id) REFERENCES runs(run_id)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS edge_calculations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    implied_prob REAL,
                    edge REAL,
                    r_score REAL,
                    kelly_fraction REAL,
                    FOREIGN KEY (run_id) REFERENCES runs(run_id)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    action TEXT NOT NULL,
                    confidence REAL,
                    amount REAL,
                    reasoning TEXT,
                    ai_approved INTEGER NOT NULL DEFAULT 1,
                    skip_reason TEXT,
                    is_hedge INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (run_id) REFERENCES runs(run_id)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    action TEXT NOT NULL,
                    amount REAL,
                    order_id TEXT,
                    fill_price REAL,
                    timestamp TEXT NOT NULL,
                    dry_run INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY (run_id) REFERENCES runs(run_id)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS arb_opportunities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_id TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    event_ticker TEXT NOT NULL,
                    sport TEXT NOT NULL,
                    market_title TEXT,
                    event_title TEXT,
                    yes_ask INTEGER,
                    no_ask INTEGER,
                    yes_bid INTEGER,
                    no_bid INTEGER,
                    total_cost INTEGER,
                    arb_pct REAL,
                    arb_type TEXT,
                    expected_profit_at_100 REAL,
                    volume INTEGER,
                    volume_24h INTEGER,
                    spread_yes REAL,
                    spread_no REAL,
                    close_time TEXT,
                    hours_to_close REAL,
                    alerted INTEGER NOT NULL DEFAULT 0,
                    fanduel_price REAL,
                    cross_exchange_edge REAL,
                    detected_at TEXT NOT NULL
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS arb_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    result_id TEXT NOT NULL,
                    scan_id TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    event_ticker TEXT NOT NULL,
                    market_title TEXT,
                    event_title TEXT,
                    sport TEXT NOT NULL,
                    yes_ask INTEGER,
                    no_ask INTEGER,
                    yes_bid INTEGER,
                    no_bid INTEGER,
                    gross_arb_cents REAL,
                    gross_arb_pct REAL,
                    yes_spread_cents REAL,
                    no_spread_cents REAL,
                    total_spread_cents REAL,
                    spread_penalty_pct REAL,
                    slippage_pct REAL,
                    liquidity_penalty_pct REAL,
                    stale_quote_penalty_pct REAL,
                    total_friction_pct REAL,
                    net_arb_cents REAL,
                    net_arb_pct REAL,
                    status TEXT,
                    rejection_reason TEXT,
                    explanation TEXT,
                    volume INTEGER,
                    volume_24h INTEGER,
                    hours_to_close REAL,
                    close_time TEXT,
                    detected_at TEXT NOT NULL
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ev_candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    candidate_id TEXT NOT NULL,
                    scan_id TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    event_ticker TEXT NOT NULL,
                    market_title TEXT,
                    event_title TEXT,
                    sport TEXT,
                    yes_ask INTEGER,
                    no_ask INTEGER,
                    yes_bid INTEGER,
                    no_bid INTEGER,
                    sportsbook_provider TEXT,
                    sportsbook_game_id TEXT,
                    sportsbook_bookmaker TEXT,
                    match_confidence REAL,
                    match_method TEXT,
                    yes_team TEXT,
                    no_team TEXT,
                    yes_edge_pct REAL,
                    yes_vig_free_prob REAL,
                    yes_kalshi_implied REAL,
                    yes_kelly_fraction REAL,
                    yes_recommended_stake_pct REAL,
                    no_edge_pct REAL,
                    no_vig_free_prob REAL,
                    no_kalshi_implied REAL,
                    no_kelly_fraction REAL,
                    no_recommended_stake_pct REAL,
                    best_side TEXT,
                    best_edge_pct REAL,
                    best_kelly_fraction REAL,
                    spread_friction_pct REAL,
                    fill_risk_pct REAL,
                    explanation TEXT,
                    volume INTEGER,
                    volume_24h INTEGER,
                    hours_to_close REAL,
                    close_time TEXT,
                    detected_at TEXT NOT NULL
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    resolved_at TEXT,
                    result TEXT,
                    pnl REAL,
                    FOREIGN KEY (run_id) REFERENCES runs(run_id)
                )
            """)

    # -- Write methods --------------------------------------------------------

    def start_run(
        self,
        config_snapshot: Optional[dict] = None,
        dry_run: bool = True,
    ) -> str:
        """Record the start of a new run and return its ID."""
        run_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()
        config_json = json.dumps(config_snapshot) if config_snapshot else None

        with self._connect() as conn:
            conn.execute(
                "INSERT INTO runs (run_id, timestamp, config_snapshot, dry_run) VALUES (?, ?, ?, ?)",
                (run_id, timestamp, config_json, int(dry_run)),
            )

        logger.info("Started run {} (dry_run={})", run_id, dry_run)
        return run_id

    def finish_run(
        self,
        run_id: str,
        pipeline_stats: dict,
        openai_available: bool,
        total_wagered: float,
    ) -> None:
        """Record final pipeline stats and mark the run as finished."""
        with self._connect() as conn:
            conn.execute(
                """UPDATE runs SET
                    pipeline_stats = ?,
                    openai_available = ?,
                    total_wagered = ?,
                    finished_at = ?
                WHERE run_id = ?""",
                (
                    json.dumps(pipeline_stats),
                    int(openai_available),
                    total_wagered,
                    datetime.now(timezone.utc).isoformat(),
                    run_id,
                ),
            )
        logger.info("Run {} finished (wagered=${:.2f})", run_id, total_wagered)

    def log_scan(
        self,
        run_id: str,
        ticker: str,
        event_ticker: str,
        volume: int = 0,
        yes_ask: int = 0,
        yes_bid: int = 0,
        close_time: str = "",
        passed_filters: bool = False,
        filter_rejection: str = "",
    ) -> None:
        """Record a scanned market."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO scanned_markets
                   (run_id, ticker, event_ticker, volume, yes_ask, yes_bid,
                    close_time, passed_filters, filter_rejection)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    ticker,
                    event_ticker,
                    volume,
                    yes_ask,
                    yes_bid,
                    close_time,
                    int(passed_filters),
                    filter_rejection,
                ),
            )

    def log_research(
        self,
        run_id: str,
        event_ticker: str,
        research_text: str,
    ) -> None:
        """Record research results for an event."""
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO research (run_id, event_ticker, research_text, timestamp) VALUES (?, ?, ?, ?)",
                (run_id, event_ticker, research_text, timestamp),
            )

    def log_probability(
        self,
        run_id: str,
        ticker: str,
        research_probability: float,
        confidence: float,
        reasoning: str = "",
    ) -> None:
        """Record a probability estimate."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO probability_estimates
                   (run_id, ticker, research_probability, confidence, reasoning)
                   VALUES (?, ?, ?, ?, ?)""",
                (run_id, ticker, research_probability, confidence, reasoning),
            )

    def log_edge(
        self,
        run_id: str,
        ticker: str,
        implied_prob: float,
        edge: float,
        r_score: float,
        kelly_fraction: float,
    ) -> None:
        """Record edge calculation results."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO edge_calculations
                   (run_id, ticker, implied_prob, edge, r_score, kelly_fraction)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (run_id, ticker, implied_prob, edge, r_score, kelly_fraction),
            )

    def log_decision(
        self,
        run_id: str,
        ticker: str,
        action: str,
        confidence: float = 0.0,
        amount: float = 0.0,
        reasoning: str = "",
        ai_approved: bool = True,
        skip_reason: str = "",
        is_hedge: bool = False,
    ) -> None:
        """Record a betting decision."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO decisions
                   (run_id, ticker, action, confidence, amount, reasoning,
                    ai_approved, skip_reason, is_hedge)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    ticker,
                    action,
                    confidence,
                    amount,
                    reasoning,
                    int(ai_approved),
                    skip_reason,
                    int(is_hedge),
                ),
            )

    def log_order(
        self,
        run_id: str,
        ticker: str,
        action: str,
        amount: float,
        order_id: Optional[str] = None,
        fill_price: Optional[float] = None,
        dry_run: bool = True,
    ) -> None:
        """Record an executed order."""
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO orders
                   (run_id, ticker, action, amount, order_id, fill_price, timestamp, dry_run)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, ticker, action, amount, order_id, fill_price, timestamp, int(dry_run)),
            )

    def log_outcome(
        self,
        ticker: str,
        run_id: str,
        result: str,
        pnl: float,
        resolved_at: Optional[str] = None,
    ) -> None:
        """Record a market outcome/resolution."""
        resolved_at = resolved_at or datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO outcomes (ticker, run_id, resolved_at, result, pnl) VALUES (?, ?, ?, ?, ?)",
                (ticker, run_id, resolved_at, result, pnl),
            )

    def log_arb_opportunity(
        self,
        scan_id: str,
        opp: "ArbOpportunity",
        alerted: bool,
    ) -> None:
        """Record a detected arbitrage opportunity."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO arb_opportunities
                   (scan_id, ticker, event_ticker, sport, market_title, event_title,
                    yes_ask, no_ask, yes_bid, no_bid, total_cost, arb_pct, arb_type,
                    expected_profit_at_100, volume, volume_24h, spread_yes, spread_no,
                    close_time, hours_to_close, alerted, fanduel_price,
                    cross_exchange_edge, detected_at,
                    sportsbook_provider, sportsbook_game_id, sportsbook_bookmaker,
                    sportsbook_price_american, sportsbook_implied_prob, kalshi_implied_prob,
                    gross_arb_pct, net_arb_pct, spread_friction_pct, fill_risk_pct,
                    required_stake_dollars, yes_team, match_confidence, match_method,
                    status, explanation)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                           ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    scan_id,
                    opp.ticker,
                    opp.event_ticker,
                    opp.sport,
                    opp.market_title,
                    opp.event_title,
                    opp.yes_ask,
                    opp.no_ask,
                    opp.yes_bid,
                    opp.no_bid,
                    opp.total_cost,
                    opp.arb_pct,
                    opp.arb_type,
                    opp.expected_profit_at_100,
                    opp.volume,
                    opp.volume_24h,
                    opp.spread_yes,
                    opp.spread_no,
                    opp.close_time,
                    opp.hours_to_close,
                    int(alerted),
                    opp.fanduel_price,
                    opp.cross_exchange_edge,
                    opp.detected_at,
                    # Phase 2 fields
                    opp.sportsbook_provider,
                    opp.sportsbook_game_id,
                    opp.sportsbook_bookmaker,
                    opp.sportsbook_price_american,
                    opp.sportsbook_implied_prob,
                    opp.kalshi_implied_prob,
                    opp.gross_arb_pct,
                    opp.net_arb_pct,
                    opp.spread_friction_pct,
                    opp.fill_risk_pct,
                    opp.required_stake_dollars,
                    opp.yes_team,
                    opp.match_confidence,
                    opp.match_method,
                    opp.status,
                    opp.explanation,
                ),
            )

    def log_arb_result(self, result: "ArbResult") -> None:
        """Record a same-market Kalshi arb result (new engine)."""
        f = result.friction
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO arb_results
                   (result_id, scan_id, ticker, event_ticker, market_title, event_title,
                    sport, yes_ask, no_ask, yes_bid, no_bid,
                    gross_arb_cents, gross_arb_pct,
                    yes_spread_cents, no_spread_cents, total_spread_cents,
                    spread_penalty_pct, slippage_pct, liquidity_penalty_pct,
                    stale_quote_penalty_pct, total_friction_pct,
                    net_arb_cents, net_arb_pct, status, rejection_reason,
                    explanation, volume, volume_24h, hours_to_close, close_time, detected_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    result.result_id, result.scan_id, result.ticker, result.event_ticker,
                    result.market_title, result.event_title, result.sport,
                    result.yes_ask, result.no_ask, result.yes_bid, result.no_bid,
                    result.gross_arb_cents, result.gross_arb_pct,
                    f.yes_spread_cents, f.no_spread_cents, f.total_spread_cents,
                    f.spread_penalty_pct, f.slippage_pct, f.liquidity_penalty_pct,
                    f.stale_quote_penalty_pct, f.total_friction_pct,
                    result.net_arb_cents, result.net_arb_pct,
                    result.status, result.rejection_reason,
                    result.explanation, result.volume, result.volume_24h,
                    result.hours_to_close, result.close_time, result.detected_at,
                ),
            )

    def log_ev_candidate(self, candidate: "EVCandidate") -> None:
        """Record a positive-EV candidate from the EV engine."""
        y = candidate.yes_metrics
        n = candidate.no_metrics
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO ev_candidates
                   (candidate_id, scan_id, ticker, event_ticker, market_title, event_title,
                    sport, yes_ask, no_ask, yes_bid, no_bid,
                    sportsbook_provider, sportsbook_game_id, sportsbook_bookmaker,
                    match_confidence, match_method, yes_team, no_team,
                    yes_edge_pct, yes_vig_free_prob, yes_kalshi_implied, yes_kelly_fraction, yes_recommended_stake_pct,
                    no_edge_pct, no_vig_free_prob, no_kalshi_implied, no_kelly_fraction, no_recommended_stake_pct,
                    best_side, best_edge_pct, best_kelly_fraction,
                    spread_friction_pct, fill_risk_pct, explanation,
                    volume, volume_24h, hours_to_close, close_time, detected_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    candidate.candidate_id, candidate.scan_id,
                    candidate.ticker, candidate.event_ticker,
                    candidate.market_title, candidate.event_title, candidate.sport,
                    candidate.yes_ask, candidate.no_ask, candidate.yes_bid, candidate.no_bid,
                    candidate.sportsbook_provider, candidate.sportsbook_game_id,
                    candidate.sportsbook_bookmaker,
                    candidate.match_confidence, candidate.match_method,
                    candidate.yes_team, candidate.no_team,
                    y.edge_pct if y else None,
                    y.vig_free_prob if y else None,
                    y.kalshi_implied_prob if y else None,
                    y.kelly_fraction if y else None,
                    y.recommended_stake_pct if y else None,
                    n.edge_pct if n else None,
                    n.vig_free_prob if n else None,
                    n.kalshi_implied_prob if n else None,
                    n.kelly_fraction if n else None,
                    n.recommended_stake_pct if n else None,
                    candidate.best_side, candidate.best_edge_pct, candidate.best_kelly_fraction,
                    candidate.spread_friction_pct, candidate.fill_risk_pct,
                    candidate.explanation,
                    candidate.volume, candidate.volume_24h, candidate.hours_to_close,
                    candidate.close_time, candidate.detected_at,
                ),
            )

    # -- Read methods ---------------------------------------------------------

    def get_run_summary(self, run_id: str) -> Optional[dict]:
        """Get summary statistics for a run."""
        with self._connect() as conn:
            cursor = conn.cursor()

            run = cursor.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if not run:
                return None

            scanned = cursor.execute(
                "SELECT COUNT(*) as cnt FROM scanned_markets WHERE run_id = ?",
                (run_id,),
            ).fetchone()

            decisions = cursor.execute(
                "SELECT COUNT(*) as cnt FROM decisions WHERE run_id = ?",
                (run_id,),
            ).fetchone()

            orders = cursor.execute(
                "SELECT COUNT(*) as cnt, COALESCE(SUM(amount), 0) as total FROM orders WHERE run_id = ?",
                (run_id,),
            ).fetchone()

            return {
                "run_id": run["run_id"],
                "timestamp": run["timestamp"],
                "dry_run": bool(run["dry_run"]),
                "markets_scanned": scanned["cnt"],
                "decisions_made": decisions["cnt"],
                "orders_placed": orders["cnt"],
                "total_wagered": orders["total"],
            }

    def get_pnl_summary(self) -> dict:
        """Get aggregate P&L across all resolved outcomes."""
        with self._connect() as conn:
            cursor = conn.cursor()
            row = cursor.execute(
                "SELECT COUNT(*) as cnt, COALESCE(SUM(pnl), 0) as total_pnl FROM outcomes"
            ).fetchone()

            wins = cursor.execute(
                "SELECT COUNT(*) as cnt FROM outcomes WHERE pnl > 0"
            ).fetchone()

            losses = cursor.execute(
                "SELECT COUNT(*) as cnt FROM outcomes WHERE pnl <= 0"
            ).fetchone()

            return {
                "total_resolved": row["cnt"],
                "total_pnl": row["total_pnl"],
                "wins": wins["cnt"],
                "losses": losses["cnt"],
                "win_rate": wins["cnt"] / row["cnt"] if row["cnt"] > 0 else 0.0,
            }

    def get_open_positions_from_ledger(self) -> list[str]:
        """Return tickers with non-zero net position from orders table.

        Used for paper-trading position tracking.
        """
        with self._connect() as conn:
            cursor = conn.cursor()
            rows = cursor.execute("""
                SELECT ticker, SUM(
                    CASE WHEN action IN ('buy_yes', 'buy_no') THEN amount ELSE -amount END
                ) as net
                FROM orders
                WHERE dry_run = 1
                GROUP BY ticker
                HAVING net != 0
            """).fetchall()

            return [row["ticker"] for row in rows]

    def get_daily_loss(self) -> float:
        """Sum of losses from resolved outcomes today."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._connect() as conn:
            cursor = conn.cursor()
            row = cursor.execute(
                "SELECT COALESCE(SUM(pnl), 0) as loss FROM outcomes WHERE pnl < 0 AND resolved_at LIKE ?",
                (f"{today}%",),
            ).fetchone()
            return abs(row["loss"])

    def get_latest_run_id(self) -> Optional[str]:
        """Return the most recent run_id."""
        with self._connect() as conn:
            cursor = conn.cursor()
            row = cursor.execute(
                "SELECT run_id FROM runs ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            return row["run_id"] if row else None

    def get_arb_opportunities(
        self,
        scan_id: Optional[str] = None,
        arb_type: Optional[str] = None,
        sport: Optional[str] = None,
        limit: int = 200,
        latest_scan_only: bool = False,
    ) -> list[dict]:
        """Return arb opportunities, optionally filtered.

        Args:
            scan_id: Filter to a specific scan cycle.
            arb_type: Filter by arb type string ('pure_arb', 'near_arb').
            sport: Filter by sport code (e.g. 'NBA').
            limit: Maximum rows to return.
            latest_scan_only: When True, return only the most recent scan cycle.
        """
        with self._connect() as conn:
            cursor = conn.cursor()

            if latest_scan_only and scan_id is None:
                row = cursor.execute(
                    "SELECT scan_id FROM arb_opportunities ORDER BY detected_at DESC LIMIT 1"
                ).fetchone()
                if row:
                    scan_id = row["scan_id"]

            clauses = []
            params: list = []
            if scan_id:
                clauses.append("scan_id = ?")
                params.append(scan_id)
            if arb_type:
                clauses.append("arb_type = ?")
                params.append(arb_type)
            if sport:
                clauses.append("sport = ?")
                params.append(sport)

            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            params.append(limit)

            rows = cursor.execute(
                f"SELECT * FROM arb_opportunities {where} ORDER BY arb_pct DESC LIMIT ?",
                params,
            ).fetchall()

            return [dict(row) for row in rows]

    def get_arb_scan_ids(self, limit: int = 50) -> list[str]:
        """Return distinct scan_ids ordered by most recent first."""
        with self._connect() as conn:
            cursor = conn.cursor()
            rows = cursor.execute(
                """SELECT scan_id, MAX(detected_at) as last_seen
                   FROM arb_opportunities
                   GROUP BY scan_id
                   ORDER BY last_seen DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            return [row["scan_id"] for row in rows]

    def get_arb_results(
        self,
        status: Optional[str] = None,
        sport: Optional[str] = None,
        limit: int = 200,
        latest_scan_only: bool = False,
    ) -> list[dict]:
        """Return arb results from the new engine, optionally filtered."""
        with self._connect() as conn:
            cursor = conn.cursor()

            scan_id = None
            if latest_scan_only:
                row = cursor.execute(
                    "SELECT scan_id FROM arb_results ORDER BY detected_at DESC LIMIT 1"
                ).fetchone()
                if row:
                    scan_id = row["scan_id"]

            clauses = []
            params: list = []
            if scan_id:
                clauses.append("scan_id = ?")
                params.append(scan_id)
            if status:
                clauses.append("status = ?")
                params.append(status)
            if sport:
                clauses.append("sport = ?")
                params.append(sport)

            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            params.append(limit)

            rows = cursor.execute(
                f"SELECT * FROM arb_results {where} ORDER BY net_arb_pct DESC LIMIT ?",
                params,
            ).fetchall()
            return [dict(row) for row in rows]

    def get_ev_candidates(
        self,
        sport: Optional[str] = None,
        min_edge_pct: float = 0.0,
        limit: int = 200,
        latest_scan_only: bool = False,
    ) -> list[dict]:
        """Return EV candidates from the new engine, optionally filtered."""
        with self._connect() as conn:
            cursor = conn.cursor()

            scan_id = None
            if latest_scan_only:
                row = cursor.execute(
                    "SELECT scan_id FROM ev_candidates ORDER BY detected_at DESC LIMIT 1"
                ).fetchone()
                if row:
                    scan_id = row["scan_id"]

            clauses = []
            params: list = []
            if scan_id:
                clauses.append("scan_id = ?")
                params.append(scan_id)
            if sport:
                clauses.append("sport = ?")
                params.append(sport)
            if min_edge_pct > 0:
                clauses.append("best_edge_pct >= ?")
                params.append(min_edge_pct)

            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            params.append(limit)

            rows = cursor.execute(
                f"SELECT * FROM ev_candidates {where} ORDER BY best_edge_pct DESC LIMIT ?",
                params,
            ).fetchall()
            return [dict(row) for row in rows]
