# Trading Strategy Documentation

## Overview

This bot implements a research-driven edge trading strategy for Kalshi prediction markets. The core thesis is that deep research (via Octagon AI) can identify mispricings in prediction markets, and systematic edge computation can filter for statistically significant opportunities.

## Pipeline

1. **Fetch events** from Kalshi sorted by 24h volume
2. **Filter markets** using deterministic rules (volume, spread, price bounds, expiry)
3. **Research events** via Octagon deep research (NO market prices in prompt)
4. **Extract probabilities** from research text using GPT-5
5. **Fetch market odds** (AFTER research to prevent price anchoring)
6. **Compute edge** deterministically using pure math
7. **Risk check** every proposed trade through the risk manager
8. **AI decision** only for markets with edge > threshold
9. **Execute** (dry-run by default)

## Edge Computation

### Raw Edge
```
edge = research_probability - implied_probability
```
Where:
- `research_probability` = our estimate from deep research (0-1)
- `implied_probability` = market ask price / 100 (0-1)

### R-Score (Risk-Adjusted Edge)
```
R = (p - q) / sqrt(p * (1 - p))
```
Where:
- `p` = research probability
- `q` = implied probability (market price)
- The denominator is the standard deviation of a Bernoulli distribution

This is a z-score measuring how many standard deviations the market is from our estimate of fair value. Higher R-scores indicate stronger statistical edge relative to the uncertainty.

**Thresholds:**
- `z_threshold = 1.5` (default) - minimum R-score to consider a trade
- `min_edge = 0.05` (default) - minimum raw edge (5 percentage points)

### Expected Return
```
E[R] = (p - q) / q
```
Expected return on capital if our probability estimate is correct.

## Position Sizing (Kelly Criterion)

### Kelly Fraction
```
f* = (p - q) / (1 - q)
```
The theoretical optimal fraction of bankroll to wager. Clamped to [0, 1].

### Fractional Kelly (Half-Kelly)
```
bet_size = bankroll * f* * kelly_multiplier
```
Default `kelly_multiplier = 0.5` (half-Kelly) for conservative sizing.

### Caps
- `max_kelly_fraction = 0.10` - never bet more than 10% of bankroll per trade
- `max_bet_amount = $100` - absolute maximum dollar bet
- Floor at $1.00 minimum

## When to Skip Trades

A trade is skipped when any of these conditions are true:

### Filter-Level (Before AI)
- Market volume < 1,000 contracts
- Bid-ask spread > 15% of ask price
- Yes ask price < 5 cents or > 95 cents
- Market expires in < 1 hour or > 7 days
- Missing orderbook (no bid or no ask)

### Edge-Level (After Research)
- Raw edge < 5% (min_edge)
- R-score < 1.5 (z_threshold)

### Risk-Level (Before Execution)
- Kill switch engaged
- Circuit breaker tripped (5+ consecutive API failures)
- Daily loss limit exceeded ($500 default)
- Cooldown active (3+ consecutive losses)
- No-trade time window
- Position count at limit (20 max)
- Per-event exposure exceeded ($200 max)
- Bet exceeds max amount ($100)

## Hedging Rules

### Current Logic
When `enable_hedging = True`:
- For bets with confidence < `min_confidence_for_hedging` (0.6):
  - Calculate hedge amount = main_bet * hedge_ratio (25%)
  - Place opposite side bet (buy_no if main is buy_yes)
  - Cap at max_hedge_amount ($50)
  - Skip if hedge amount < $1

### Skip-Instead-of-Hedge Option
When `skip_instead_of_hedge = True` (default):
- Instead of hedging low-confidence bets, simply skip them
- This reduces complexity and fees

## What AI Is Used For

The AI (GPT-5) serves two purposes in this pipeline:

1. **Probability Extraction**: Parsing research text into structured probability estimates per market. The AI interprets natural language research and assigns quantitative probabilities.

2. **Decision Refinement**: When called, the AI provides reasoning and confidence levels for trading decisions. However, the primary decision is driven by deterministic edge computation, not AI judgment alone.

The AI does NOT make the final trading decision independently. Edge must exceed mathematical thresholds before the AI is even consulted.

## Future Ideas

- **Momentum filters**: Track price movement over time, prefer markets moving toward our estimate
- **Orderbook depth analysis**: Use bid/ask depth for better implied probability and liquidity assessment
- **Historical calibration**: Compare past research probability estimates to actual outcomes to calibrate model
- **Category diversification**: Limit exposure correlation across related event categories
- **Volatility regime detection**: Adjust Kelly fraction based on market volatility
- **Multi-timeframe analysis**: Different strategies for different time horizons
- **Sentiment scoring**: Incorporate social media / news sentiment as additional signal
