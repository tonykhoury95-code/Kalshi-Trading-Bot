"""
OpenAI utilities for structured probability extraction and betting decisions.

Refactored from openai_utils.py and trading_bot.py decision logic.
"""

import asyncio
import json
from typing import Any, Optional, Sequence, Type, TypeVar, cast

from loguru import logger
from pydantic import BaseModel

from src.ai.models import (
    BettingDecision,
    MarketAnalysis,
    MarketProbability,
    ProbabilityExtraction,
)

T = TypeVar("T", bound=BaseModel)

# Semaphore to limit concurrent OpenAI calls.
# NOT created at module level — asyncio primitives created outside a running
# event loop bind to the wrong loop in Python 3.9, causing
# "Future attached to a different loop" errors.
# Created lazily on first use inside an async context instead.
_openai_semaphore: Optional[asyncio.Semaphore] = None


def _get_semaphore() -> asyncio.Semaphore:
    """Return the shared OpenAI semaphore, creating it lazily if needed.

    Must only be called from within a running event loop.
    """
    global _openai_semaphore
    if _openai_semaphore is None:
        _openai_semaphore = asyncio.Semaphore(5)
    return _openai_semaphore


# ---------------------------------------------------------------------------
# Core extraction helpers (from openai_utils.py)
# ---------------------------------------------------------------------------


def extract_completed_message_text(response: Any) -> str:
    """Extract plain text from the completed assistant message in a Responses API result.

    Handles outputs that include a reasoning step before the completed message.
    Returns an empty string if nothing is found.
    """
    text_chunks: list[str] = []

    try:
        output_items = getattr(response, "output", None) or response.get("output", [])
    except Exception:
        output_items = []

    for item in output_items or []:
        item_type = getattr(item, "type", None) or (
            isinstance(item, dict) and item.get("type")
        )
        item_status = getattr(item, "status", None) or (
            isinstance(item, dict) and item.get("status")
        )
        if item_type == "message" and item_status == "completed":
            content = (
                getattr(item, "content", None)
                or (isinstance(item, dict) and item.get("content"))
                or []
            )
            for part in content or []:
                part_type = getattr(part, "type", None) or (
                    isinstance(part, dict) and part.get("type")
                )
                if part_type == "output_text":
                    text_value = getattr(part, "text", None) or (
                        isinstance(part, dict) and part.get("text")
                    )
                    if isinstance(text_value, str) and text_value:
                        text_chunks.append(text_value)
            break

    return "".join(text_chunks).strip()


async def responses_parse_pydantic(
    client: Any,
    *,
    model: str,
    messages: Sequence[dict[str, Any]],
    response_format: Type[T],
    reasoning_effort: str = "low",
    text_verbosity: str = "medium",
) -> T:
    """Parse structured output using the Responses API into a Pydantic model.

    Injects a schema instruction and parses the JSON response.
    """
    normalized = _normalize_messages_input(messages)

    try:
        schema = response_format.model_json_schema()
    except Exception:
        schema = response_format.schema()  # type: ignore[attr-defined]

    schema_str = json.dumps(schema)
    schema_instruction = {
        "role": "system",
        "content": (
            "You must respond with ONLY a single JSON object that validates against "
            "the following JSON Schema. Do not include any prose, code fences, or "
            "additional text. If a field is optional, omit it instead of writing null.\n\n"
            f"JSON Schema: {schema_str}"
        ),
    }

    normalized_with_schema = [schema_instruction] + list(normalized)

    async with _get_semaphore():
        resp = await client.responses.create(
            model=model,
            input=normalized_with_schema,
            reasoning={"effort": reasoning_effort},
            text={"verbosity": text_verbosity},
        )

    text_value = extract_completed_message_text(resp)
    if text_value:
        try:
            data = json.loads(text_value)
            try:
                return cast(T, response_format.model_validate(data))
            except Exception:
                return cast(T, response_format.parse_obj(data))  # type: ignore[attr-defined]
        except Exception as exc:
            raise RuntimeError(
                f"Structured output parsing failed: invalid JSON in model output: {exc}"
            )

    raise RuntimeError(
        "Structured output parsing failed: no output_parsed or JSON text found."
    )


# ---------------------------------------------------------------------------
# Probability extraction
# ---------------------------------------------------------------------------


async def extract_probabilities_from_text(
    client: Any,
    research_text: str,
    markets: list[dict[str, Any]],
    event_title: str = "",
    model: str = "gpt-5",
) -> ProbabilityExtraction:
    """Extract structured probability estimates from research text.

    Args:
        client: AsyncOpenAI client instance.
        research_text: Raw research text from Octagon.
        markets: List of market dicts with ``ticker`` and ``title`` keys.
        event_title: Human-readable event title for context.
        model: OpenAI model to use.

    Returns:
        A ``ProbabilityExtraction`` with per-market probabilities.
    """
    market_info = [
        {"ticker": m.get("ticker", ""), "title": m.get("title", "")}
        for m in markets
    ]

    prompt = f"""
    Based on the following deep research, extract the probability estimates for each market.

    Event: {event_title}

    Markets:
    {json.dumps(market_info, indent=2)}

    Research Results:
    {research_text}

    For each market, provide:
    1. The research-based probability estimate (0-100%)
    2. Clear reasoning for that probability
    3. Confidence level in the estimate (0-1)

    Focus on extracting concrete probability estimates from the research, not market prices.
    If the research doesn't provide a clear probability for a market, make your best estimate
    based on the available information.
    """

    extraction = await responses_parse_pydantic(
        client,
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a professional prediction market analyst. "
                    "Extract probability estimates from research with structured output."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        response_format=ProbabilityExtraction,
    )

    return extraction


# ---------------------------------------------------------------------------
# Betting decision generation
# ---------------------------------------------------------------------------


async def generate_betting_decisions(
    client: Any,
    event_data: dict[str, Any],
    probability_extraction: ProbabilityExtraction,
    market_odds: dict[str, dict[str, Any]],
    max_bet_amount: float = 100.0,
    z_threshold: float = 1.5,
    max_kelly_bet_fraction: float = 0.1,
    model: str = "gpt-5",
    is_mutually_exclusive: bool = False,
) -> MarketAnalysis:
    """Generate betting decisions for an event using AI.

    The AI receives research probabilities, current market odds, and
    strategy parameters.  It returns structured ``MarketAnalysis``.
    """
    mutually_exclusive_guidance = ""
    if is_mutually_exclusive:
        mutually_exclusive_guidance = """

        MUTUALLY EXCLUSIVE EVENT - STRATEGIC HEDGE BETTING:
        This event is MUTUALLY EXCLUSIVE - only ONE outcome can be true.
        Focus on finding the best value opportunity for your largest bet.
        You CAN place multiple bets but position sizing should reflect probability and value.
        """
    else:
        mutually_exclusive_guidance = """

        NON-MUTUALLY EXCLUSIVE EVENT:
        Multiple outcomes can be true simultaneously.
        Evaluate each market independently.
        """

    prompt = f"""
    You are a professional prediction market trader. Based on the research provided
    for this event AND the current market odds, make betting decisions.

    Max bet per market: ${max_bet_amount}
    {mutually_exclusive_guidance}

    IMPORTANT TRADING CONSTRAINTS:
    - You can ONLY buy YES or buy NO positions (no shorting)
    - Prices are in cents (0-100, where 50 = 50% probability)
    - Look for value where research probability differs significantly from market odds
    - Only bet on exceptional opportunities with R-score >= {z_threshold}

    Event Data, Research Probabilities, and Current Market Odds:
    {json.dumps(event_data, indent=2)}

    For each market, decide:
    1. Action: "buy_yes", "buy_no", or "skip"
    2. Confidence: 0-1 (only bet if confidence > 0.75)
    3. Amount: How much to bet (max ${max_bet_amount})
    4. Reasoning: Brief explanation

    Return your analysis in the specified JSON format.
    """

    analysis = await responses_parse_pydantic(
        client,
        model=model,
        messages=[
            {"role": "system", "content": "You are a professional prediction market trader."},
            {"role": "user", "content": prompt},
        ],
        response_format=MarketAnalysis,
    )

    return analysis


# ---------------------------------------------------------------------------
# Preflight health check
# ---------------------------------------------------------------------------


async def check_openai_availability(client: Any, model: str = "gpt-5") -> tuple[bool, str]:
    """Make a minimal OpenAI call to verify the account has quota and auth is valid.

    Returns:
        (available, error_message). When available is True, error_message is "".

    This should be called ONCE before any batch extraction so a quota or auth
    failure is surfaced as a single clear error rather than N repeated failures.
    """
    try:
        resp = await client.responses.create(
            model=model,
            input=[{"role": "user", "content": "Reply with the word OK only."}],
        )
        text = extract_completed_message_text(resp)
        if text:
            logger.info("OpenAI preflight check passed (model={})", model)
            return True, ""
        # Got a response but no text — still counts as available
        return True, ""
    except Exception as exc:
        err = str(exc)
        if "insufficient_quota" in err or "429" in err:
            msg = f"OPENAI_UNAVAILABLE: quota exceeded or rate limited — {exc}"
        elif "401" in err or "authentication" in err.lower():
            msg = f"OPENAI_UNAVAILABLE: authentication failed — {exc}"
        else:
            msg = f"OPENAI_UNAVAILABLE: {exc}"
        logger.error(msg)
        return False, msg


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalize_messages_input(
    messages: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert chat-style messages into Responses API input format."""
    normalized: list[dict[str, Any]] = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            normalized.append({"role": msg.get("role", "user"), "content": content})
        else:
            normalized.append(
                {
                    "role": msg.get("role", "user"),
                    "content": str(content) if content is not None else "",
                }
            )
    return normalized
