"""
Base API client with retry logic, timeout, and structured logging.
"""

import asyncio
import random
from typing import Any, Optional

import httpx
from loguru import logger


def _redact_headers(headers: dict) -> dict:
    """Return a copy of headers with sensitive values redacted."""
    sensitive_keys = {
        "authorization",
        "kalshi-access-key",
        "kalshi-access-signature",
        "x-api-key",
        "api-key",
    }
    redacted = {}
    for k, v in headers.items():
        if k.lower() in sensitive_keys:
            redacted[k] = "***REDACTED***"
        else:
            redacted[k] = v
    return redacted


class BaseAPIClient:
    """Async HTTP client with exponential backoff retry.

    Implements async context manager for clean resource management.
    Retries on 5xx and 429 errors with exponential backoff and jitter.
    """

    def __init__(
        self,
        base_url: str,
        timeout: float = 30.0,
        max_retries: int = 3,
    ):
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "BaseAPIClient":
        """Create the underlying HTTP client."""
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
        )
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Close the underlying HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        """Return the active httpx client, raising if not initialized."""
        if self._client is None:
            raise RuntimeError(
                "Client not initialized. Use 'async with' or call __aenter__ first."
            )
        return self._client

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Make an HTTP request with exponential backoff retry.

        Retries on 5xx and 429 responses.  Does not retry on other 4xx errors.
        """
        last_exc: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                logger.debug(
                    "API request: {} {} (attempt {}/{})",
                    method,
                    path,
                    attempt + 1,
                    self.max_retries + 1,
                )

                # Redact headers before logging
                if "headers" in kwargs:
                    safe_headers = _redact_headers(kwargs["headers"])
                    logger.debug("Request headers: {}", safe_headers)

                response = await self.client.request(method, path, **kwargs)

                logger.debug(
                    "API response: {} {} -> {}",
                    method,
                    path,
                    response.status_code,
                )

                # Don't retry on success or non-retryable client errors
                if response.status_code < 500 and response.status_code != 429:
                    response.raise_for_status()
                    return response

                # Retryable error
                if attempt < self.max_retries:
                    backoff = (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(
                        "Retryable error {} on {} {}. Retrying in {:.1f}s...",
                        response.status_code,
                        method,
                        path,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                else:
                    response.raise_for_status()
                    return response

            except httpx.HTTPStatusError:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    backoff = (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(
                        "Request error on {} {}: {}. Retrying in {:.1f}s...",
                        method,
                        path,
                        exc,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                else:
                    raise

        # Should not reach here, but just in case
        if last_exc:
            raise last_exc
        raise RuntimeError("Unexpected retry loop exit")

    async def _get(
        self,
        path: str,
        params: Optional[dict] = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Convenience wrapper for GET requests."""
        return await self._request("GET", path, params=params, **kwargs)

    async def _post(
        self,
        path: str,
        json: Optional[dict] = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Convenience wrapper for POST requests."""
        return await self._request("POST", path, json=json, **kwargs)
