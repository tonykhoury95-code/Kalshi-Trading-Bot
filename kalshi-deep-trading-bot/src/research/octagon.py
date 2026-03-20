"""
Octagon Deep Research API client.

Uses the OpenAI SDK to communicate with the Octagon research gateway.
Research is performed WITHOUT market odds to prevent price anchoring.
"""

import openai
from loguru import logger

from src.brokers.kalshi.models import KalshiEvent
from src.config import OctagonConfig


class OctagonClient:
    """Client for Octagon Deep Research API.

    Sends event and market information (without prices) to the Octagon
    deep research agent and returns raw research text.
    """

    def __init__(self, config: OctagonConfig):
        self.config = config
        self._client = openai.AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=1800.0,  # 30-minute timeout for deep research
        )

    async def research_event(self, event: KalshiEvent) -> str:
        """Research an event and its markets using Octagon deep research.

        Args:
            event: The event to research, including its markets.

        Returns:
            Raw research text from the Octagon agent.

        Note:
            Market odds/prices are intentionally omitted from the research
            prompt to avoid anchoring bias in probability estimates.
        """
        try:
            event_info = (
                f"Event: {event.title}\n"
                f"Subtitle: {event.subtitle}\n"
                f"Mutually Exclusive: {event.mutually_exclusive}\n"
            )

            markets_info = "Markets:\n"
            included_count = 0
            for i, market in enumerate(event.markets, 1):
                if market.volume < 1000:
                    logger.debug(
                        "Filtering market {} from research: volume {} < 1000",
                        market.ticker,
                        market.volume,
                    )
                    continue

                included_count += 1
                markets_info += f"{i}. {market.title}"
                if market.ticker:
                    markets_info += f" (Ticker: {market.ticker})"
                markets_info += "\n"
                if market.subtitle:
                    markets_info += f"   Subtitle: {market.subtitle}\n"
                markets_info += f"   Open: {market.open_time}\n"
                markets_info += f"   Close: {market.close_time}\n\n"

            logger.info(
                "Research prompt for {}: {} markets included ({} filtered by volume)",
                event.ticker,
                included_count,
                len(event.markets) - included_count,
            )

            prompt = f"""
            You are a prediction market expert. Research this event and predict the probability for each market independently.

            {event_info}

            {markets_info}

            Please provide:
            1. Overall event analysis and key factors
            2. Current sentiment and news analysis
            3. For each market, predict the probability of YES outcome (0-100%)
            4. Confidence level for each prediction (1-10)
            5. Key risks and catalysts that could affect outcomes
            6. Trading recommendations for each market

            Focus on:
            - Independent analysis of each market's probability
            - If mutually exclusive: probabilities should sum to ~100% (only one outcome can be true)
            - If not mutually exclusive: each market can be evaluated independently
            - Provide actionable insights for trading decisions
            - Be specific about probability estimates

            IMPORTANT: When providing probability predictions, include both the market ticker AND the probability percentage.
            Example format: "KXTHEOPEN-25-DMCC: 15%" or "McCarthy (KXTHEOPEN-25-DMCC): 15% probability"

            Format your response clearly with market tickers and probability predictions.
            """

            logger.info(
                "Starting deep research for event {} (this may take several minutes)...",
                event.ticker,
            )

            response = await self._client.responses.create(
                model="octagon-deep-research-agent",
                input=[{"role": "user", "content": prompt}],
                reasoning={"effort": "low"},
                text={"verbosity": "medium"},
            )

            logger.info("Completed deep research for event {}", event.ticker)

            # Extract completed message text
            from src.ai.client import extract_completed_message_text

            content_text = extract_completed_message_text(response)
            if content_text:
                return content_text

            return ""

        except Exception as exc:
            logger.error("Error researching event {}: {}", event.ticker, exc)
            return f"Error researching event: {exc}"

    async def close(self) -> None:
        """Close the client (OpenAI client does not need explicit closing)."""
        pass
