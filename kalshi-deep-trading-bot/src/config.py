"""
Configuration management for the Kalshi trading bot.

Uses pydantic-settings for type-safe configuration loading from environment
variables and .env files. All config is grouped into nested models.
"""

import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _clean_env_value(value: str) -> str:
    """Clean environment variable value by removing inline comments.

    Handles values like ``100.0  # max bet`` by stripping the comment portion.
    """
    if "#" in value:
        return value.split("#")[0].strip()
    return value.strip()


class KalshiConfig(BaseModel):
    """Kalshi API configuration."""

    api_key: str = Field(
        ...,
        description="Kalshi API key for authentication",
    )
    private_key: str = Field(
        ...,
        description=(
            "RSA private key in PEM format. Accepts an inline PEM string or a "
            "file path ending in .pem / .key (or starting with /)."
        ),
    )
    use_demo: bool = Field(
        default=True,
        description="When True, use the Kalshi demo environment instead of production",
    )

    @property
    def base_url(self) -> str:
        """Return the appropriate Kalshi API base URL."""
        if self.use_demo:
            return "https://demo-api.kalshi.co"
        return "https://api.elections.kalshi.com"

    @model_validator(mode="before")
    @classmethod
    def _resolve_private_key(cls, values: dict) -> dict:
        """Load private key from file when the value looks like a path."""
        pk = values.get("private_key", "")
        if not pk:
            return values

        pk = str(pk).strip()

        # Detect file-path style values
        is_path = (
            pk.endswith(".pem")
            or pk.endswith(".key")
            or pk.startswith("/")
        ) and not pk.startswith("-----BEGIN")

        if is_path:
            path = Path(pk)
            if path.exists():
                pk = path.read_text()
            else:
                raise ValueError(f"Private key file not found: {pk}")

        # Basic PEM validation
        stripped = pk.strip()
        if not stripped.startswith("-----BEGIN") or not stripped.endswith("-----"):
            raise ValueError(
                "Private key must be in PEM format starting with '-----BEGIN' "
                "and ending with '-----'. Include \\n for line breaks in .env."
            )

        values["private_key"] = pk
        return values


class OctagonConfig(BaseModel):
    """Octagon Deep Research API configuration."""

    api_key: str = Field(
        ...,
        description="Octagon API key for deep research access",
    )
    base_url: str = Field(
        default="https://api-gateway.octagonagents.com/v1",
        description="Octagon API gateway base URL",
    )


class OpenAIConfig(BaseModel):
    """OpenAI API configuration."""

    api_key: str = Field(
        ...,
        description="OpenAI API key",
    )
    model: str = Field(
        default="gpt-5",
        description="OpenAI model to use for structured decision making",
    )


class RiskConfig(BaseModel):
    """Risk management configuration."""

    bankroll: float = Field(
        default=1000.0,
        description="Total bankroll for Kelly sizing calculations (dollars)",
    )
    max_bet_amount: float = Field(
        default=100.0,
        description="Maximum bet amount per market (dollars)",
    )
    max_portfolio_positions: int = Field(
        default=10,
        description="Maximum number of positions to hold simultaneously",
    )
    max_per_event_exposure: float = Field(
        default=200.0,
        description="Maximum total dollar exposure per event",
    )
    max_per_category_exposure: float = Field(
        default=500.0,
        description="Maximum total dollar exposure per category",
    )
    max_daily_loss: float = Field(
        default=500.0,
        description="Maximum allowable daily loss before stopping (dollars)",
    )
    max_open_positions: int = Field(
        default=20,
        description="Maximum number of open positions across all events",
    )
    cooldown_after_losses: int = Field(
        default=3,
        description="Number of consecutive losses before pausing trading",
    )
    kill_switch: bool = Field(
        default=False,
        description="When True, all trading is halted immediately",
    )
    circuit_breaker_threshold: int = Field(
        default=5,
        description="Number of consecutive API failures before stopping",
    )
    no_trade_start_hour: int = Field(
        default=-1,
        description="UTC hour (0-23) when no-trade window starts (-1 = disabled)",
    )
    no_trade_end_hour: int = Field(
        default=-1,
        description="UTC hour (0-23) when no-trade window ends (-1 = disabled)",
    )


class StrategyConfig(BaseModel):
    """Strategy parameters for edge computation and market selection."""

    z_threshold: float = Field(
        default=1.5,
        description="Minimum R-score (z-score) threshold for placing bets",
    )
    min_edge: float = Field(
        default=0.05,
        description="Minimum edge (research_prob - implied_prob) required",
    )
    min_volume: int = Field(
        default=1000,
        description=(
            "Minimum market volume to consider. Used when not overridden by "
            "min_volume_demo or min_volume_prod."
        ),
    )
    min_volume_demo: int = Field(
        default=10,
        description=(
            "Minimum market volume in demo environment. "
            "Demo markets have very low liquidity so this should be much lower than prod."
        ),
    )
    min_volume_prod: int = Field(
        default=1000,
        description="Minimum market volume in production environment.",
    )
    min_event_volume: int = Field(
        default=1,
        description=(
            "Minimum event-level 24h volume to include in event ranking. "
            "Events at 0 are almost certainly inactive. Set to 0 to disable."
        ),
    )
    max_spread: float = Field(
        default=0.15,
        description="Maximum bid-ask spread ratio to tolerate",
    )
    min_price: float = Field(
        default=0.05,
        description="Minimum yes_ask price as fraction (e.g. 0.05 = 5 cents)",
    )
    max_price: float = Field(
        default=0.95,
        description="Maximum yes_ask price as fraction (e.g. 0.95 = 95 cents)",
    )
    min_hours_to_expiry: float = Field(
        default=1.0,
        description="Minimum hours until market close to consider",
    )
    max_hours_to_expiry: float = Field(
        default=168.0,
        description="Maximum hours until market close (168 = 7 days)",
    )
    enable_kelly: bool = Field(
        default=True,
        description="Use Kelly criterion for position sizing",
    )
    kelly_fraction: float = Field(
        default=0.5,
        ge=0.1,
        le=1.0,
        description="Fraction of Kelly to use (0.5 = half-Kelly)",
    )
    max_kelly_fraction: float = Field(
        default=0.1,
        ge=0.01,
        le=0.5,
        description="Maximum fraction of bankroll per bet via Kelly",
    )
    max_events_to_analyze: int = Field(
        default=50,
        description="Number of top events to analyze by volume",
    )
    research_batch_size: int = Field(
        default=10,
        description="Number of parallel deep research requests to batch",
    )
    research_timeout_seconds: int = Field(
        default=900,
        description="Per-event research timeout in seconds",
    )
    max_markets_per_event: int = Field(
        default=10,
        description="Maximum number of markets per event to analyze",
    )
    skip_existing_positions: bool = Field(
        default=True,
        description="Skip betting on markets where we already have positions",
    )
    portfolio_selection_method: str = Field(
        default="top_r_scores",
        description="Method for portfolio selection: 'top_r_scores' or 'diversified'",
    )


class SportsArbConfig(BaseModel):
    """Configuration for the Kalshi sports arbitrage scanner."""

    near_arb_threshold_pct: float = Field(
        default=2.0,
        description=(
            "Flag markets where arb% > -near_arb_threshold_pct. "
            "E.g. 2.0 flags any market within 2% of breakeven (arb% > -2.0). "
            "Set lower to be more selective; higher to see more near-arbs."
        ),
    )
    scan_interval_seconds: int = Field(
        default=30,
        description="Seconds between full scan cycles.",
    )
    min_volume: int = Field(
        default=100,
        description="Minimum total market volume to include in the arb scan.",
    )
    min_hours_to_close: float = Field(
        default=0.25,
        description="Ignore markets closing in less than this many hours (15 min default).",
    )
    max_hours_to_close: float = Field(
        default=72.0,
        description="Ignore markets closing more than this many hours out.",
    )
    alert_change_threshold_pct: float = Field(
        default=0.50,
        description="Re-alert a ticker only if arb% improves by at least this much.",
    )
    sports: list[str] = Field(
        default_factory=lambda: ["NBA", "NFL", "MLB", "NHL", "UFC", "SOC", "TEN"],
        description="Sport codes to scan. Empty list = all known sports.",
    )
    max_events_per_scan: int = Field(
        default=500,
        description="Maximum number of events fetched per scan cycle.",
    )
    live_only: bool = Field(
        default=False,
        description="When True, only scan markets that close within 4 hours (in-play / live).",
    )


class SportsbookConfig(BaseModel):
    """Configuration for sportsbook odds providers."""

    provider: str = Field(
        default="none",
        description=(
            "Odds provider: 'the_odds_api', 'mock', 'sportsdataio', or 'none' to disable. "
            "Set to 'the_odds_api' and supply THE_ODDS_API_KEY for live comparison."
        ),
    )
    the_odds_api_key: str = Field(
        default="",
        description="API key for The Odds API (https://the-odds-api.com).",
    )
    sportsdataio_key: str = Field(
        default="",
        description="API key for SportsDataIO (stub — not yet implemented).",
    )
    preferred_bookmaker: str = Field(
        default="fanduel",
        description="Bookmaker to use as the primary reference: 'fanduel', 'draftkings', etc.",
    )
    fallback_bookmakers: list[str] = Field(
        default_factory=lambda: ["draftkings", "betmgm", "caesars"],
        description="Fallback bookmakers when the preferred one has no line.",
    )
    markets: list[str] = Field(
        default_factory=lambda: ["h2h", "spreads", "totals"],
        description=(
            "Market types to fetch from the odds provider. "
            "'h2h' = moneyline, 'spreads' = point spread, 'totals' = over/under. "
            "Fetching all three in one call minimises quota usage."
        ),
    )
    health_check_on_startup: bool = Field(
        default=True,
        description=(
            "Run a provider health check at scanner startup. "
            "Logs quota remaining and verifies the API key is valid."
        ),
    )


class MatchingConfig(BaseModel):
    """Configuration for the Kalshi ↔ sportsbook market matching engine."""

    min_match_confidence: float = Field(
        default=0.75,
        ge=0.0,
        le=1.0,
        description="Minimum match confidence to proceed with comparison (0–1).",
    )
    time_window_hours: float = Field(
        default=6.0,
        description=(
            "Maximum absolute difference (hours) between Kalshi market close time "
            "and sportsbook game commence time for a match to be accepted."
        ),
    )
    fuzzy_team_threshold: float = Field(
        default=0.75,
        ge=0.0,
        le=1.0,
        description="Minimum similarity ratio for fuzzy team-name matching.",
    )
    min_net_edge_pct: float = Field(
        default=1.0,
        description=(
            "Minimum net edge % (after friction) to flag as a positive-EV opportunity. "
            "Lower values surface more opportunities; higher values surface fewer, stronger ones."
        ),
    )


class HedgeConfig(BaseModel):
    """Hedging configuration."""

    enable_hedging: bool = Field(
        default=True,
        description="Enable hedging to minimize risk",
    )
    hedge_ratio: float = Field(
        default=0.25,
        ge=0.0,
        le=0.5,
        description="Default hedge ratio (0.25 = hedge 25% of main bet)",
    )
    min_confidence_for_hedging: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Only hedge bets with confidence below this threshold",
    )
    max_hedge_amount: float = Field(
        default=50.0,
        description="Maximum hedge amount per bet (dollars)",
    )
    skip_instead_of_hedge: bool = Field(
        default=True,
        description=(
            "When True, skip poor trades instead of always hedging. "
            "When False, always create hedge positions for low-confidence bets."
        ),
    )


class BotConfig(BaseSettings):
    """Top-level bot configuration.

    Loads values from environment variables and ``.env`` file.  Sub-configs
    are built from env vars with the appropriate prefix during ``from_env()``.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="allow",
    )

    # Sub-configurations
    kalshi: KalshiConfig
    octagon: OctagonConfig
    openai: OpenAIConfig
    risk: RiskConfig = Field(default_factory=RiskConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    hedge: HedgeConfig = Field(default_factory=HedgeConfig)
    sports_arb: SportsArbConfig = Field(default_factory=SportsArbConfig)
    sportsbook: SportsbookConfig = Field(default_factory=SportsbookConfig)
    matching: MatchingConfig = Field(default_factory=MatchingConfig)

    # Top-level flags
    dry_run: bool = Field(
        default=True,
        description="Run in dry-run mode (no real orders placed)",
    )
    live_trading: bool = Field(
        default=False,
        description="Enable live trading (requires dry_run=False)",
    )

    @model_validator(mode="after")
    def _validate_live_requires_no_dry_run(self) -> "BotConfig":
        """Ensure live_trading=True is only allowed when dry_run=False."""
        if self.live_trading and self.dry_run:
            raise ValueError(
                "live_trading=True requires dry_run=False. "
                "Set DRY_RUN=false in your environment to enable live trading."
            )
        return self

    @classmethod
    def from_env(cls) -> "BotConfig":
        """Build a BotConfig from environment variables.

        This is the primary entry point for constructing configuration.  It
        reads individual env vars and assembles the nested sub-configs before
        delegating to pydantic-settings for validation.
        """
        # Helper to read and clean env values
        def env(key: str, default: str = "") -> str:
            raw = os.getenv(key, default)
            return _clean_env_value(raw) if raw else default

        # Resolve private key: prefer KALSHI_PRIVATE_KEY, fall back to file
        private_key = env("KALSHI_PRIVATE_KEY")
        private_key_file = env("KALSHI_PRIVATE_KEY_FILE")
        if private_key_file and not private_key:
            private_key = private_key_file  # Will be resolved by validator

        kalshi = KalshiConfig(
            api_key=env("KALSHI_API_KEY"),
            private_key=private_key,
            use_demo=env("KALSHI_USE_DEMO", "true").lower() == "true",
        )

        octagon = OctagonConfig(
            api_key=env("OCTAGON_API_KEY"),
            base_url=env("OCTAGON_BASE_URL", "https://api-gateway.octagonagents.com/v1"),
        )

        openai_cfg = OpenAIConfig(
            api_key=env("OPENAI_API_KEY"),
            model=env("OPENAI_MODEL", "gpt-5"),
        )

        risk = RiskConfig(
            bankroll=float(env("BANKROLL", "1000.0")),
            max_bet_amount=float(env("MAX_BET_AMOUNT", "100.0")),
            max_portfolio_positions=int(env("MAX_PORTFOLIO_POSITIONS", "10")),
            max_per_event_exposure=float(env("MAX_PER_EVENT_EXPOSURE", "200.0")),
            max_per_category_exposure=float(env("MAX_PER_CATEGORY_EXPOSURE", "500.0")),
            max_daily_loss=float(env("MAX_DAILY_LOSS", "500.0")),
            max_open_positions=int(env("MAX_OPEN_POSITIONS", "20")),
            cooldown_after_losses=int(env("COOLDOWN_AFTER_LOSSES", "3")),
            kill_switch=env("KILL_SWITCH", "false").lower() == "true",
            circuit_breaker_threshold=int(env("CIRCUIT_BREAKER_THRESHOLD", "5")),
            no_trade_start_hour=int(env("NO_TRADE_START_HOUR", "-1")),
            no_trade_end_hour=int(env("NO_TRADE_END_HOUR", "-1")),
        )

        strategy = StrategyConfig(
            z_threshold=float(env("Z_THRESHOLD", "1.5")),
            min_edge=float(env("MIN_EDGE", "0.05")),
            min_volume=int(env("MIN_VOLUME", "1000")),
            min_volume_demo=int(env("MIN_VOLUME_DEMO", "10")),
            min_volume_prod=int(env("MIN_VOLUME_PROD", "1000")),
            min_event_volume=int(env("MIN_EVENT_VOLUME", "1")),
            max_spread=float(env("MAX_SPREAD", "0.15")),
            min_price=float(env("MIN_PRICE", "0.05")),
            max_price=float(env("MAX_PRICE", "0.95")),
            min_hours_to_expiry=float(env("MIN_HOURS_TO_EXPIRY", "1.0")),
            max_hours_to_expiry=float(env("MAX_HOURS_TO_EXPIRY", "168.0")),
            enable_kelly=env("ENABLE_KELLY_SIZING", "true").lower() == "true",
            kelly_fraction=float(env("KELLY_FRACTION", "0.5")),
            max_kelly_fraction=float(env("MAX_KELLY_BET_FRACTION", "0.1")),
            max_events_to_analyze=int(env("MAX_EVENTS_TO_ANALYZE", "50")),
            research_batch_size=int(env("RESEARCH_BATCH_SIZE", "10")),
            research_timeout_seconds=int(env("RESEARCH_TIMEOUT_SECONDS", "900")),
            max_markets_per_event=int(env("MAX_MARKETS_PER_EVENT", "10")),
            skip_existing_positions=env("SKIP_EXISTING_POSITIONS", "true").lower() == "true",
            portfolio_selection_method=env("PORTFOLIO_SELECTION_METHOD", "top_r_scores"),
        )

        hedge = HedgeConfig(
            enable_hedging=env("ENABLE_HEDGING", "true").lower() == "true",
            hedge_ratio=float(env("HEDGE_RATIO", "0.25")),
            min_confidence_for_hedging=float(env("MIN_CONFIDENCE_FOR_HEDGING", "0.6")),
            max_hedge_amount=float(env("MAX_HEDGE_AMOUNT", "50.0")),
            skip_instead_of_hedge=env("SKIP_INSTEAD_OF_HEDGE", "true").lower() == "true",
        )

        sports_arb = SportsArbConfig(
            near_arb_threshold_pct=float(env("ARB_NEAR_THRESHOLD_PCT", "2.0")),
            scan_interval_seconds=int(env("ARB_SCAN_INTERVAL_SECONDS", "30")),
            min_volume=int(env("ARB_MIN_VOLUME", "100")),
            min_hours_to_close=float(env("ARB_MIN_HOURS_TO_CLOSE", "0.25")),
            max_hours_to_close=float(env("ARB_MAX_HOURS_TO_CLOSE", "72.0")),
            alert_change_threshold_pct=float(env("ARB_ALERT_CHANGE_THRESHOLD_PCT", "0.5")),
            sports=[s.strip() for s in env("ARB_SPORTS", "NBA,NFL,MLB,NHL,UFC,SOC,TEN").split(",") if s.strip()],
            max_events_per_scan=int(env("ARB_MAX_EVENTS_PER_SCAN", "500")),
            live_only=env("ARB_LIVE_ONLY", "false").lower() == "true",
        )

        _sb_markets_raw = env("SPORTSBOOK_MARKETS", "h2h,spreads,totals")
        sportsbook = SportsbookConfig(
            provider=env("SPORTSBOOK_PROVIDER", "none"),
            the_odds_api_key=env("THE_ODDS_API_KEY", ""),
            sportsdataio_key=env("SPORTSDATAIO_API_KEY", ""),
            preferred_bookmaker=env("PREFERRED_BOOKMAKER", "fanduel"),
            markets=[m.strip() for m in _sb_markets_raw.split(",") if m.strip()],
            health_check_on_startup=env("SPORTSBOOK_HEALTH_CHECK", "true").lower() == "true",
        )

        matching = MatchingConfig(
            min_match_confidence=float(env("MATCH_MIN_CONFIDENCE", "0.75")),
            time_window_hours=float(env("MATCH_TIME_WINDOW_HOURS", "6.0")),
            fuzzy_team_threshold=float(env("MATCH_FUZZY_THRESHOLD", "0.75")),
            min_net_edge_pct=float(env("MATCH_MIN_NET_EDGE_PCT", "1.0")),
        )

        return cls(
            kalshi=kalshi,
            octagon=octagon,
            openai=openai_cfg,
            risk=risk,
            strategy=strategy,
            hedge=hedge,
            sports_arb=sports_arb,
            sportsbook=sportsbook,
            matching=matching,
            dry_run=True,
            live_trading=False,
        )
