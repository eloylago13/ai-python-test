from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from ..application.ports import ProviderError
from ..config import Settings
from ..domain.models import ErrorCode, Notification
from ..domain.prompt import build_messages
from ..observability.logging import get_logger
from ..observability.metrics import PROVIDER_RETRIES
from .rate_limit import AsyncTokenBucket

logger = get_logger(__name__)


class ProviderClient:
    """Adapter for the external AI and notification provider.

    Retries live here because they are transport concerns. The application
    pipeline only sees a typed ProviderError and decides whether the request is
    terminal.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        limiter: AsyncTokenBucket | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._limiter = limiter
        self._client = httpx.AsyncClient(
            base_url=settings.provider_base_url,
            headers={"X-API-Key": settings.provider_api_key},
            limits=httpx.Limits(
                max_connections=200,
                max_keepalive_connections=50,
            ),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def extract(
        self,
        user_input: str,
        *,
        trace_id: str | None = None,
    ) -> str:
        payload = {"messages": build_messages(user_input)}
        headers = {"X-Request-ID": trace_id} if trace_id else None
        data = await self._request_with_retry(
            "POST",
            "/v1/ai/extract",
            operation="ai",
            max_attempts=self._settings.max_ai_attempts,
            json=payload,
            headers=headers,
        )

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                "AI response does not contain message content",
                code=ErrorCode.AI_INVALID_RESPONSE,
                retryable=False,
            ) from exc

        if not isinstance(content, str) or not content.strip():
            raise ProviderError(
                "AI response content is empty",
                code=ErrorCode.AI_INVALID_RESPONSE,
                retryable=False,
            )
        return content

    async def notify(self, notification: Notification, *, trace_id: str) -> str:
        payload = {
            "to": notification.to,
            "message": notification.message,
            "type": notification.type.value,
        }
        data = await self._request_with_retry(
            "POST",
            "/v1/notify",
            operation="notify",
            max_attempts=self._settings.max_notify_attempts,
            json=payload,
            params={"priority": "normal", "trace_id": trace_id},
            headers={"X-Request-ID": trace_id},
        )

        if not isinstance(data, dict):
            raise ProviderError(
                "Notification provider returned an invalid payload",
                code=ErrorCode.PROVIDER_REJECTED,
                retryable=False,
            )
        provider_id = data.get("provider_id")
        return str(provider_id) if provider_id is not None else "unknown"

    async def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        max_attempts: int,
        **kwargs: Any,
    ) -> Any:
        retryer = AsyncRetrying(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential_jitter(initial=0.5, max=8.0, jitter=1.0),
            retry=retry_if_exception(
                lambda exc: isinstance(exc, ProviderError) and exc.retryable
            ),
            before_sleep=lambda state: self._log_retry(operation, state),
            reraise=True,
        )

        async for attempt in retryer:
            with attempt:
                if operation == "notify" and self._limiter is not None:
                    await self._limiter.acquire()
                return await self._request(
                    method,
                    path,
                    operation=operation,
                    **kwargs,
                )

        raise ProviderError(
            "Provider retry loop exited unexpectedly",
            code=ErrorCode.INTERNAL_ERROR,
            retryable=False,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        **kwargs: Any,
    ) -> Any:
        try:
            response = await self._client.request(
                method,
                path,
                timeout=self._timeout_for(operation),
                **kwargs,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProviderError(
                f"{operation} transport failure",
                code=self._unavailable_code(operation),
                retryable=True,
            ) from exc

        if 200 <= response.status_code < 300:
            try:
                return response.json()
            except (ValueError, json.JSONDecodeError) as exc:
                raise ProviderError(
                    f"{operation} returned a non-JSON response",
                    code=(
                        ErrorCode.AI_INVALID_RESPONSE
                        if operation == "ai"
                        else ErrorCode.PROVIDER_REJECTED
                    ),
                    retryable=False,
                ) from exc

        code, retryable = self._classify_status(operation, response.status_code)
        if response.status_code == 429:
            retry_after = _parse_retry_after(response)
            if retry_after:
                await asyncio.sleep(min(retry_after, 2.0))

        raise ProviderError(
            f"{operation} failed with HTTP {response.status_code}",
            code=code,
            retryable=retryable,
        )

    def _timeout_for(self, operation: str) -> httpx.Timeout:
        read_timeout = (
            self._settings.ai_timeout_seconds
            if operation == "ai"
            else self._settings.notify_timeout_seconds
        )
        return httpx.Timeout(
            connect=2.0,
            read=read_timeout,
            write=5.0,
            pool=5.0,
        )

    def _classify_status(
        self, operation: str, status_code: int
    ) -> tuple[ErrorCode, bool]:
        if status_code == 429:
            if operation == "ai":
                return ErrorCode.AI_UNAVAILABLE, True
            return ErrorCode.PROVIDER_RATE_LIMITED, True

        if 500 <= status_code < 600:
            return self._unavailable_code(operation), True

        if status_code in (401, 403):
            return (
                ErrorCode.AI_UNAVAILABLE
                if operation == "ai"
                else ErrorCode.PROVIDER_REJECTED
            ), False

        return (
            ErrorCode.AI_INVALID_RESPONSE
            if operation == "ai"
            else ErrorCode.PROVIDER_REJECTED
        ), False

    @staticmethod
    def _unavailable_code(operation: str) -> ErrorCode:
        return (
            ErrorCode.AI_UNAVAILABLE
            if operation == "ai"
            else ErrorCode.PROVIDER_UNAVAILABLE
        )

    @staticmethod
    def _log_retry(operation: str, state: Any) -> None:
        outcome = state.outcome
        error = outcome.exception() if outcome is not None else None
        PROVIDER_RETRIES.labels(operation=operation).inc()
        logger.warning(
            "provider_retry",
            extra={
                "operation": operation,
                "attempt": state.attempt_number,
                "error": str(error) if error is not None else "unknown",
            },
        )


def _parse_retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None
