from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from notification.config import Settings
from notification.application.ports import ProviderError
from notification.domain.models import ErrorCode, Notification, NotificationType
from notification.infrastructure.provider import ProviderClient


def notification() -> Notification:
    return Notification(
        to="feda@test.com",
        message="hola",
        type=NotificationType.EMAIL,
    )


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "provider_base_url": "http://provider",
        "max_ai_attempts": 2,
        "max_notify_attempts": 2,
    }
    values.update(overrides)
    return Settings(**values)


class FakeLimiter:
    def __init__(self) -> None:
        self.calls = 0

    async def acquire(self) -> None:
        self.calls += 1


async def test_extract_sends_system_and_user_messages() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/ai/extract"
        assert request.headers["X-API-Key"] == "test-dev-2026"
        assert request.headers["X-Request-ID"] == "trace-1"

        body = json.loads(request.content)
        assert body["messages"][0]["role"] == "system"
        assert "extraction component" in body["messages"][0]["content"]
        assert body["messages"][-1] == {
            "role": "user",
            "content": "Manda un mail a feda@test.com diciendo hola",
        }
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": '{"to": "feda@test.com", "message": "hola", "type": "email"}',
                        }
                    }
                ]
            },
        )

    client = ProviderClient(
        settings(),
        transport=httpx.MockTransport(handler),
    )
    try:
        content = await client.extract(
            "Manda un mail a feda@test.com diciendo hola",
            trace_id="trace-1",
        )
    finally:
        await client.aclose()

    assert "feda@test.com" in content


async def test_notify_retries_429_and_acquires_a_token_per_attempt() -> None:
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": "Rate limit exceeded"})
        return httpx.Response(
            200,
            json={"status": "delivered", "provider_id": "p-1"},
        )

    limiter = FakeLimiter()
    client = ProviderClient(
        settings(),
        limiter=limiter,
        transport=httpx.MockTransport(handler),
    )
    try:
        provider_id = await client.notify(notification(), trace_id="trace-1")
    finally:
        await client.aclose()

    assert provider_id == "p-1"
    assert len(calls) == 2
    assert limiter.calls == 2
    assert calls[0].headers["X-API-Key"] == "test-dev-2026"
    assert calls[0].url.params["trace_id"] == "trace-1"
    assert calls[0].url.params["priority"] == "normal"


async def test_notify_accepts_any_2xx() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            202,
            json={"status": "accepted", "provider_id": "p-202"},
        )

    client = ProviderClient(settings(), transport=httpx.MockTransport(handler))
    try:
        provider_id = await client.notify(notification(), trace_id="trace-1")
    finally:
        await client.aclose()

    assert provider_id == "p-202"


async def test_auth_error_is_not_retried() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": "Invalid API Key"})

    client = ProviderClient(
        settings(max_notify_attempts=5),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(ProviderError) as raised:
            await client.notify(notification(), trace_id="trace-1")
    finally:
        await client.aclose()

    assert raised.value.code is ErrorCode.PROVIDER_REJECTED
    assert raised.value.retryable is False
    assert calls == 1


async def test_server_error_is_retried_until_exhausted() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, json={"error": "External server error"})

    client = ProviderClient(
        settings(max_notify_attempts=2),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(ProviderError) as raised:
            await client.notify(notification(), trace_id="trace-1")
    finally:
        await client.aclose()

    assert raised.value.code is ErrorCode.PROVIDER_UNAVAILABLE
    assert calls == 2


async def test_malformed_ai_payload_is_not_retried() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"choices": []})

    client = ProviderClient(
        settings(max_ai_attempts=5),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(ProviderError) as raised:
            await client.extract("hola", trace_id="trace-1")
    finally:
        await client.aclose()

    assert raised.value.code is ErrorCode.AI_INVALID_RESPONSE
    assert raised.value.retryable is False
    assert calls == 1
