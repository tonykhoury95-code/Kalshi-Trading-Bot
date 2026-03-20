"""
Pydantic models for Kalshi API data structures.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class KalshiMarket(BaseModel):
    """A single Kalshi prediction market."""

    ticker: str = Field(..., description="Unique market ticker symbol")
    title: str = Field(default="", description="Human-readable market title")
    subtitle: str = Field(default="", description="Market subtitle")
    volume: int = Field(default=0, description="Total volume traded")
    volume_24h: int = Field(default=0, description="Volume in the last 24 hours")
    open_time: str = Field(default="", description="Market open time (ISO 8601)")
    close_time: str = Field(default="", description="Market close time (ISO 8601)")
    event_ticker: str = Field(default="", description="Parent event ticker")
    yes_ask: int = Field(default=0, description="Current YES ask price in cents")
    yes_bid: int = Field(default=0, description="Current YES bid price in cents")
    no_ask: int = Field(default=0, description="Current NO ask price in cents")
    no_bid: int = Field(default=0, description="Current NO bid price in cents")
    result: Optional[str] = Field(default=None, description="Resolved result (yes/no/null)")

    @property
    def yes_mid(self) -> float:
        """Mid-price for YES side in cents."""
        if self.yes_bid > 0 and self.yes_ask > 0:
            return (self.yes_bid + self.yes_ask) / 2.0
        return float(self.yes_ask or self.yes_bid)

    @property
    def no_mid(self) -> float:
        """Mid-price for NO side in cents."""
        if self.no_bid > 0 and self.no_ask > 0:
            return (self.no_bid + self.no_ask) / 2.0
        return float(self.no_ask or self.no_bid)


class KalshiEvent(BaseModel):
    """A Kalshi event containing one or more markets."""

    ticker: str = Field(..., description="Event ticker symbol")
    title: str = Field(default="", description="Event title")
    category: str = Field(default="", description="Event category")
    volume_24h: int = Field(default=0, description="Aggregate 24h volume across markets")
    markets: list[KalshiMarket] = Field(
        default_factory=list, description="Markets in this event"
    )
    mutually_exclusive: bool = Field(
        default=False,
        description="Whether only one market outcome can be true",
    )
    strike_date: str = Field(default="", description="Event strike/resolution date")
    strike_period: str = Field(default="", description="Event strike period label")
    subtitle: str = Field(default="", description="Event subtitle")


class KalshiOrder(BaseModel):
    """A Kalshi order (placed or pending)."""

    ticker: str = Field(..., description="Market ticker")
    side: Literal["yes", "no"] = Field(..., description="Order side")
    action: Literal["buy", "sell"] = Field(..., description="Order action")
    count: int = Field(default=0, description="Number of contracts")
    client_order_id: str = Field(default="", description="Client-generated order ID")
    status: str = Field(default="", description="Order status")


class KalshiPosition(BaseModel):
    """A user position in a Kalshi market."""

    ticker: str = Field(..., description="Market ticker")
    position: int = Field(
        default=0,
        description="Net position (positive = YES, negative = NO, 0 = none)",
    )
    total_traded: float = Field(
        default=0.0, description="Total dollar amount traded"
    )
