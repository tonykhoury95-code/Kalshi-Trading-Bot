# CLAUDE.md - Project Guide for AI Assistants

## Project Architecture

```
kalshi-deep-trading-bot/
  trading_bot.py          # Thin orchestration shell (~350 lines)
  config.py               # Legacy config (kept for backward compat)
  kalshi_client.py         # Legacy Kalshi client (kept for backward compat)
  research_client.py       # Legacy research client (kept for backward compat)
  betting_models.py        # Legacy models (kept for backward compat)
  openai_utils.py          # Legacy OpenAI utils (kept for backward compat)

  src/                     # New modular architecture
    __init__.py
    config.py              # Pydantic-settings config with nested models
    api/
      base.py              # BaseAPIClient with retry & backoff
    brokers/kalshi/
      client.py            # Kalshi REST client (RSA-PSS auth, key cached)
      models.py            # KalshiMarket, KalshiEvent, KalshiOrder, KalshiPosition
    research/
      octagon.py           # Octagon deep research client
    ai/
      client.py            # OpenAI Responses API helpers, probability extraction
      models.py            # All AI/decision Pydantic models
    strategies/
      edge.py              # Pure math: edge, R-score, Kelly (no I/O)
      filters.py           # Deterministic market filters (runs before AI)
    risk/
      manager.py           # RiskManager with gate_trade() entry point
    execution/
      executor.py          # Order executor with dry-run safety
    telemetry/
      logger.py            # Loguru setup, audit logging, secret redaction
    portfolio/
      ledger.py            # SQLite ledger for all bot activity
    dashboard/
      app.py               # Streamlit dashboard (read-only from ledger)
    utils/
      time.py              # UTC helpers, hours_until, format_duration

  tests/
    test_config.py
    test_edge.py
    test_risk.py
    test_filters.py
    test_models.py
```

## Coding Rules

1. **Type hints required** on all function signatures (Python 3.9+ style: `list[X]` not `List[X]`).
2. **Docstrings on all public methods** (Google/numpy style).
3. **Use loguru, not print()** for all logging.
4. **Never log secrets**: API keys, private keys, authorization headers. Use `redact_secrets()` from `src/telemetry/logger.py`.
5. **dry_run=True is the default everywhere**. Live trading requires explicit opt-in.
6. **Pure functions in strategies/**: `edge.py` and `filters.py` do no I/O. They are deterministic and fully testable.
7. **Single entry point for risk**: All trade proposals go through `RiskManager.gate_trade()`.

## How to Run

```bash
# Install dependencies
uv sync

# Run in dry-run mode (default, safe)
uv run python trading_bot.py

# Run with live trading (careful!)
uv run python trading_bot.py --live

# Run tests
uv run pytest tests/ -v

# Launch dashboard
uv run python trading_bot.py --dashboard
# or directly:
uv run streamlit run src/dashboard/app.py
```

## How to Extend Strategies

### Adding a new filter
1. Open `src/strategies/filters.py`
2. Add a new check method to `MarketFilter` (e.g. `_check_open_interest`)
3. Call it from `check_market()` in the appropriate order
4. Add a test in `tests/test_filters.py`

### Adding a new risk check
1. Open `src/risk/manager.py`
2. Add a `check_*()` method that returns `bool`
3. Call it from `gate_trade()` with appropriate reason string
4. Add a test in `tests/test_risk.py`

### Adding a new strategy
1. Add pure math functions to `src/strategies/edge.py` (or a new file)
2. Wire them into `TradingBot._generate_decisions_for_event()` in `trading_bot.py`
3. Add corresponding config fields to `src/config.py` `StrategyConfig`
4. Test the math functions independently

## Critical Files (Do Not Break)

- **`src/config.py`**: All configuration flows through here. Breaking it breaks everything.
- **`src/risk/manager.py`**: Guards against runaway trading. Must always block on kill_switch, circuit_breaker, etc.
- **`src/execution/executor.py`**: Must always default to dry_run=True. The double-check before live orders is intentional.
- **`src/telemetry/logger.py`**: Secret redaction must work. Never log PEM keys or API keys.
- **`src/portfolio/ledger.py`**: Schema changes require migration. Don't alter existing columns.

## What Not to Break

- `dry_run=True` as the default in every component
- Secret redaction in logs
- Ledger SQLite schema (additive changes only)
- CSV export format (backward compat)
- RSA-PSS signing logic (RSASSA-PSS / SHA-256 / MAX_SALT_LENGTH)
- Research happens BEFORE fetching market odds (prevents price anchoring)

## Key Design Decisions

1. **Research before odds**: Octagon research prompt intentionally excludes market prices to prevent anchoring bias in probability estimates.
2. **Deterministic filters before AI**: Market filters run on pure math/rules before any AI call, saving API costs.
3. **Edge-first pipeline**: Edge is computed deterministically. AI is only called if edge exceeds threshold.
4. **Half-Kelly sizing**: Default `kelly_fraction=0.5` uses half-Kelly for conservative position sizing.
5. **SQLite ledger**: All activity is recorded for audit trail and dashboard visualization.
