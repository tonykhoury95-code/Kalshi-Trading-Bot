"""
Kalshi API client with RSA-PSS authentication.

The private key is loaded once at initialization and cached for signing.
"""

import base64
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from loguru import logger

from src.brokers.kalshi.models import (
    KalshiEvent,
    KalshiMarket,
    KalshiOrder,
    KalshiPosition,
)
from src.config import KalshiConfig
from src.utils.time import hours_until


class KalshiClient:
    """Async Kalshi API client with RSA-PSS signature authentication.

    The RSA private key is loaded once during ``__init__`` and reused for
    every request signature, avoiding repeated PEM parsing.
    """

    def __init__(self, config: KalshiConfig):
        self.config = config
        self.base_url = config.base_url
        self.api_key = config.api_key
        self._client: Optional[httpx.AsyncClient] = None

        # Load private key ONCE and cache the key object
        if not config.private_key or not config.private_key.strip():
            raise ValueError(
                "KALSHI_PRIVATE_KEY is missing or empty.\n"
                "  1. Copy env_template.txt to .env\n"
                "  2. Fill in KALSHI_API_KEY and KALSHI_PRIVATE_KEY\n"
                "  Get your keys at: https://kalshi.com (or demo.kalshi.co for demo)"
            )
        try:
            self._private_key = serialization.load_pem_private_key(
                config.private_key.encode(),
                password=None,
                backend=default_backend(),
            )
        except Exception as exc:
            raise ValueError(
                f"Failed to parse KALSHI_PRIVATE_KEY: {exc}\n"
                "  Make sure the key is a valid RSA PEM key (starts with "
                "-----BEGIN RSA PRIVATE KEY----- or -----BEGIN PRIVATE KEY-----)"
            ) from exc
        logger.info("Kalshi client initialized (key loaded, base_url={})", self.base_url)

    async def connect(self) -> None:
        """Create the underlying HTTP client."""
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=30.0,
        )
        logger.info("Connected to Kalshi API at {}", self.base_url)

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        """Return the active client, raising if not connected."""
        if self._client is None:
            raise RuntimeError("KalshiClient not connected. Call connect() first.")
        return self._client

    # -- Authentication -------------------------------------------------------

    def _sign_message(self, message: str) -> str:
        """Sign a message using RSA-PSS with SHA-256 and MAX_LENGTH salt.

        Uses the cached private key object so no PEM parsing occurs per call.
        """
        signature = self._private_key.sign(
            message.encode(),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode()

    def _get_auth_headers(self, method: str, path: str) -> dict[str, str]:
        """Build Kalshi authentication headers for a request."""
        timestamp = str(int(time.time() * 1000))
        message = f"{timestamp}{method}{path}"
        signature = self._sign_message(message)

        return {
            "KALSHI-ACCESS-KEY": self.api_key,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "Content-Type": "application/json",
        }

    # -- Events ---------------------------------------------------------------

    async def get_events(
        self,
        limit: int = 50,
        max_close_ts: Optional[int] = None,
        min_hours_to_expiry: float = 1.0,
        max_markets_per_event: int = 10,
        min_event_volume: int = 0,
        category: Optional[str] = None,
    ) -> list[KalshiEvent]:
        """Fetch events with nested markets, paginating through all pages.

        Filters markets by close time and expiry, sorts events by ``volume_24h``
        descending, and returns the top ``limit`` events.

        Args:
            limit: Maximum number of events to return.
            max_close_ts: Unix timestamp upper bound for market close time.
            min_hours_to_expiry: Minimum hours until strike date.
            max_markets_per_event: Cap on markets included per event.
            min_event_volume: Exclude events whose 24h volume is below this.
                              Set to 0 to include all events (including zero-volume).
        """
        all_raw_events = await self._fetch_all_events(category=category)
        now = datetime.now(timezone.utc)
        min_remaining_seconds = min_hours_to_expiry * 3600

        skipped_zero_volume = 0
        skipped_expiry = 0
        skipped_no_markets = 0
        enriched: list[KalshiEvent] = []

        for raw_event in all_raw_events:
            # Use the event-level volume_24h field directly.
            # DO NOT sum market-level volume_24h — that field is unreliable
            # (often 0) in Kalshi's nested market response.
            event_vol_24h = int(raw_event.get("volume_24h", 0) or 0)

            # Exclude zero-volume events from ranking pool
            if event_vol_24h <= min_event_volume and min_event_volume > 0:
                skipped_zero_volume += 1
                continue

            raw_markets = raw_event.get("markets", [])

            # Filter by max_close_ts
            if max_close_ts is not None:
                filtered = []
                for m in raw_markets:
                    ct = m.get("close_time", "")
                    if not ct:
                        continue
                    try:
                        close_dt = self._parse_iso(ct)
                        if int(close_dt.timestamp()) <= max_close_ts:
                            filtered.append(m)
                    except Exception:
                        continue
                raw_markets = filtered

            if not raw_markets:
                skipped_no_markets += 1
                continue

            # Check strike date for min time remaining
            strike_str = raw_event.get("strike_date", "")
            if strike_str:
                try:
                    strike_dt = self._parse_iso(strike_str)
                    remaining = (strike_dt - now).total_seconds()
                    if 0 < remaining < min_remaining_seconds:
                        skipped_expiry += 1
                        logger.debug(
                            "Event {} strikes in {:.1f}m, skipping",
                            raw_event.get("event_ticker", ""),
                            remaining / 60,
                        )
                        continue
                except (ValueError, TypeError):
                    pass

            # Sort by volume, take top N
            raw_markets.sort(key=lambda m: m.get("volume", 0), reverse=True)
            top_markets = raw_markets[:max_markets_per_event]

            # Build KalshiMarket models
            markets = [
                KalshiMarket(
                    ticker=m.get("ticker", ""),
                    title=m.get("title", ""),
                    subtitle=m.get("subtitle", ""),
                    volume=m.get("volume", 0),
                    volume_24h=m.get("volume_24h", 0),
                    open_time=m.get("open_time", ""),
                    close_time=m.get("close_time", ""),
                    event_ticker=raw_event.get("event_ticker", ""),
                    yes_ask=m.get("yes_ask", 0),
                    yes_bid=m.get("yes_bid", 0),
                    no_ask=m.get("no_ask", 0),
                    no_bid=m.get("no_bid", 0),
                    result=m.get("result"),
                )
                for m in top_markets
            ]

            event = KalshiEvent(
                ticker=raw_event.get("event_ticker", ""),
                title=raw_event.get("title", ""),
                subtitle=raw_event.get("sub_title", ""),
                category=raw_event.get("category", ""),
                volume_24h=event_vol_24h,   # use event-level field, not sum of markets
                markets=markets,
                mutually_exclusive=raw_event.get("mutually_exclusive", False),
                strike_date=strike_str,
                strike_period=raw_event.get("strike_period", ""),
            )
            enriched.append(event)

        logger.info(
            "Event selection: {} total | {} zero-volume skipped | "
            "{} expiry skipped | {} no-markets skipped | {} qualified",
            len(all_raw_events),
            skipped_zero_volume,
            skipped_expiry,
            skipped_no_markets,
            len(enriched),
        )

        # Sort by volume_24h descending — now uses the real event-level field
        enriched.sort(key=lambda e: e.volume_24h, reverse=True)
        result = enriched[:limit]

        logger.info(
            "Returning top {} events (highest 24h vol: {:,}, lowest: {:,})",
            len(result),
            result[0].volume_24h if result else 0,
            result[-1].volume_24h if result else 0,
        )
        return result

    async def _fetch_all_events(
        self, category: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """Paginate through all open events.

        Args:
            category: Optional Kalshi series_ticker / category filter (e.g. ``"Sports"``).
        """
        all_events: list[dict[str, Any]] = []
        cursor: Optional[str] = None
        page = 1

        while True:
            try:
                headers = self._get_auth_headers("GET", "/trade-api/v2/events")
                params: dict[str, Any] = {
                    "limit": 100,
                    "status": "open",
                    "with_nested_markets": "true",
                }
                if category:
                    params["series_ticker"] = category
                if cursor:
                    params["cursor"] = cursor

                logger.info("Fetching events page {}...", page)
                response = await self.client.get(
                    "/trade-api/v2/events",
                    headers=headers,
                    params=params,
                )
                response.raise_for_status()
                data = response.json()

                if data is None:
                    break

                events = data.get("events", []) if isinstance(data, dict) else []
                if not events:
                    break

                all_events.extend(events)
                logger.info("Page {}: {} events (total: {})", page, len(events), len(all_events))

                cursor = data.get("cursor")
                if not cursor:
                    break
                page += 1

            except Exception as exc:
                logger.error("Error fetching events page {}: {}", page, exc)
                break

        logger.info("Fetched {} total events from {} pages", len(all_events), page)
        return all_events

    # -- Markets --------------------------------------------------------------

    async def get_market(self, ticker: str) -> Optional[KalshiMarket]:
        """Fetch a single market by ticker with full bid/ask data."""
        try:
            path = f"/trade-api/v2/markets/{ticker}"
            headers = self._get_auth_headers("GET", path)
            response = await self.client.get(path, headers=headers)
            response.raise_for_status()

            data = response.json()
            m = data.get("market", {})

            return KalshiMarket(
                ticker=m.get("ticker", ""),
                title=m.get("title", ""),
                subtitle=m.get("subtitle", ""),
                volume=m.get("volume", 0),
                volume_24h=m.get("volume_24h", 0),
                open_time=m.get("open_time", ""),
                close_time=m.get("close_time", ""),
                event_ticker=m.get("event_ticker", ""),
                yes_ask=m.get("yes_ask", 0),
                yes_bid=m.get("yes_bid", 0),
                no_ask=m.get("no_ask", 0),
                no_bid=m.get("no_bid", 0),
                result=m.get("result"),
            )
        except Exception as exc:
            logger.error("Error getting market {}: {}", ticker, exc)
            return None

    # -- Positions ------------------------------------------------------------

    async def get_positions(self) -> list[KalshiPosition]:
        """Fetch all user positions."""
        try:
            path = "/trade-api/v2/portfolio/positions"
            headers = self._get_auth_headers("GET", path)
            response = await self.client.get(path, headers=headers)
            response.raise_for_status()

            data = response.json()
            raw_positions = data.get("market_positions", [])

            positions = [
                KalshiPosition(
                    ticker=p.get("ticker", ""),
                    position=p.get("position", 0),
                    total_traded=float(p.get("total_traded", 0)),
                )
                for p in raw_positions
            ]

            logger.info("Retrieved {} positions", len(positions))
            return positions

        except Exception as exc:
            logger.error("Error getting positions: {}", exc)
            return []

    # -- Orders ---------------------------------------------------------------

    async def place_order(
        self,
        ticker: str,
        side: str,
        action: str,
        amount_dollars: float,
    ) -> KalshiOrder:
        """Place a market order on Kalshi.

        Args:
            ticker: Market ticker symbol.
            side: ``"yes"`` or ``"no"``.
            action: ``"buy"`` or ``"sell"``.
            amount_dollars: Maximum cost in dollars.

        Returns:
            A ``KalshiOrder`` with the result.
        """
        client_order_id = str(uuid.uuid4())
        buy_max_cost_cents = int(amount_dollars * 100)

        order_data = {
            "ticker": ticker,
            "side": side,
            "action": action,
            "type": "market",
            "client_order_id": client_order_id,
            "count": 1000,  # High count; actual spend capped by buy_max_cost
            "buy_max_cost": buy_max_cost_cents,
        }

        path = "/trade-api/v2/portfolio/orders"
        headers = self._get_auth_headers("POST", path)

        try:
            response = await self.client.post(path, headers=headers, json=order_data)
            response.raise_for_status()
            result = response.json()

            logger.info(
                "Order placed: {} {} {} ${} (max cost: {} cents)",
                ticker,
                side,
                action,
                amount_dollars,
                buy_max_cost_cents,
            )

            return KalshiOrder(
                ticker=ticker,
                side=side,
                action=action,
                count=result.get("order", {}).get("count", 0),
                client_order_id=client_order_id,
                status="placed",
            )

        except Exception as exc:
            logger.error("Error placing order on {}: {}", ticker, exc)
            return KalshiOrder(
                ticker=ticker,
                side=side,
                action=action,
                count=0,
                client_order_id=client_order_id,
                status=f"error: {exc}",
            )

    # -- Helpers --------------------------------------------------------------

    @staticmethod
    def _parse_iso(dt_str: str) -> datetime:
        """Parse an ISO 8601 datetime string into a timezone-aware datetime."""
        if dt_str.endswith("Z"):
            dt_str = dt_str[:-1] + "+00:00"
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
