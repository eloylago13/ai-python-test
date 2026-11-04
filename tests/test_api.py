from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest

from main import create_app
from notification.config import Settings
from notification.application.ports import ProviderError
from notification.domain.models import (
    ErrorCode,
    Notification,
    NotificationType,
    RequestStatus,
)
from notification.infrastructure.repository_sqlite import SQLiteRequestRepository

USER_INPUT = "Manda un mail a feda@test.com diciendo hola"


class FakeProvider:
    def __init__(
        self,
        *,
        ai_responses: list[str] | None = None,
        notify_error: ProviderError | None = None,
    ) -> None:
        self._ai_responses = list(
            ai_responses
            or ['{"to": "feda@test.com", "message": "hola", "type": "email"}']
        )
        self._notify_error = notify_error
        self.ai_calls = 0
        self.notifications: list[Notification] = []
        self.closed = False

    async def extract(self, user_input: str, *, trace_id: str | None = None) -> str:
        self.ai_calls += 1
        if len(self._ai_responses) > 1:
            return self._ai_responses.pop(0)
        return self._ai_responses[0]

    async def notify(self, notification: Notification, *, trace_id: str) -> str:
        if self._notify_error is not None:
            raise self._notify_error
        self.notifications.append(notification)
        return "p-test"

    async def aclose(self) -> None:
        self.closed = True


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "repository_backend": "memory",
        "extraction_workers": 2,
        "delivery_workers": 2,
        "extraction_queue_size": 100,
        "delivery_queue_size": 100,
        "ai_parse_attempts": 1,
        "max_ai_attempts": 1,
        "max_notify_attempts": 1,
        "shutdown_timeout_seconds": 1,
    }
    values.update(overrides)
    return Settings(**values)


async def wait_for_status(
    client: httpx.AsyncClient,
    request_id: str,
    expected: set[str],
    *,
    timeout: float = 3.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_body: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = await client.get(f"/v1/requests/{request_id}")
        assert response.status_code == 200
        last_body = response.json()
        if last_body["status"] in expected:
            return last_body
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"Request {request_id} did not reach {expected}; last body: {last_body}"
    )


def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )


async def test_request_lifecycle_is_idempotent() -> None:
    provider = FakeProvider()
    app = create_app(settings=settings(), provider=provider)

    async with app.router.lifespan_context(app):
        async with client_for(app) as client:
            created = await client.post(
                "/v1/requests",
                json={"user_input": USER_INPUT},
            )
            assert created.status_code == 201
            request_id = created.json()["id"]
            assert isinstance(request_id, str) and request_id

            initial = await client.get(f"/v1/requests/{request_id}")
            assert initial.status_code == 200
            assert initial.json()["status"] in {"queued", "processing", "sent"}

            processed = await client.post(f"/v1/requests/{request_id}/process")
            assert processed.status_code == 202
            assert processed.json()["status"] == "processing"

            final = await wait_for_status(client, request_id, {"sent"})
            assert final["status"] == "sent"
            assert "error_code" not in final
            assert len(provider.notifications) == 1
            assert provider.notifications[0].to == "feda@test.com"

            again = await client.post(f"/v1/requests/{request_id}/process")
            assert again.status_code == 200
            assert again.json()["status"] == "sent"
            assert len(provider.notifications) == 1

    assert provider.closed is True


async def test_concurrent_process_calls_do_not_duplicate_notifications() -> None:
    provider = FakeProvider()
    app = create_app(settings=settings(), provider=provider)

    async with app.router.lifespan_context(app):
        async with client_for(app) as client:
            created = await client.post("/v1/requests", json={"user_input": USER_INPUT})
            request_id = created.json()["id"]

            first, second = await asyncio.gather(
                client.post(f"/v1/requests/{request_id}/process"),
                client.post(f"/v1/requests/{request_id}/process"),
            )

            assert first.status_code in {200, 202}
            assert second.status_code in {200, 202}
            await wait_for_status(client, request_id, {"sent"})
            assert len(provider.notifications) == 1


async def test_policy_refusal_ends_in_failed_state() -> None:
    provider = FakeProvider(
        ai_responses=[
            "Lo siento, como IA no tengo permitido procesar datos de contacto personales."
        ]
    )
    app = create_app(settings=settings(), provider=provider)

    async with app.router.lifespan_context(app):
        async with client_for(app) as client:
            created = await client.post("/v1/requests", json={"user_input": USER_INPUT})
            request_id = created.json()["id"]
            processed = await client.post(f"/v1/requests/{request_id}/process")
            assert processed.status_code == 202

            final = await wait_for_status(client, request_id, {"failed"})
            assert final["error_code"] == "ai_refused"
            assert provider.notifications == []


async def test_unparseable_ai_output_falls_back_to_deterministic_extraction() -> None:
    provider = FakeProvider(ai_responses=["not a json object"])
    app = create_app(settings=settings(ai_parse_attempts=2), provider=provider)

    async with app.router.lifespan_context(app):
        async with client_for(app) as client:
            created = await client.post("/v1/requests", json={"user_input": USER_INPUT})
            request_id = created.json()["id"]
            await client.post(f"/v1/requests/{request_id}/process")

            final = await wait_for_status(client, request_id, {"sent"})
            assert final["status"] == "sent"
            assert provider.ai_calls == 2
            assert len(provider.notifications) == 1
            assert provider.notifications[0].to == "feda@test.com"
            assert provider.notifications[0].message == "hola"


async def test_wrong_llm_recipient_is_replaced_by_the_requested_one() -> None:
    provider = FakeProvider(
        ai_responses=[
            '{"to": "juan@example.com", "message": "hola", "type": "email"}'
        ]
    )
    app = create_app(settings=settings(), provider=provider)

    async with app.router.lifespan_context(app):
        async with client_for(app) as client:
            created = await client.post(
                "/v1/requests",
                json={"user_input": USER_INPUT},
            )
            request_id = created.json()["id"]
            await client.post(f"/v1/requests/{request_id}/process")
            await wait_for_status(client, request_id, {"sent"})

            assert len(provider.notifications) == 1
            assert provider.notifications[0].to == "feda@test.com"


async def test_ambiguous_destination_ends_in_failed_state() -> None:
    provider = FakeProvider(
        ai_responses=[
            '{"to": "a@test.com", "message": "hola", "type": "email"}'
        ]
    )
    app = create_app(settings=settings(), provider=provider)

    async with app.router.lifespan_context(app):
        async with client_for(app) as client:
            created = await client.post(
                "/v1/requests",
                json={
                    "user_input": (
                        "Envia el mensaje a a@test.com y tambien a b@test.com"
                    )
                },
            )
            request_id = created.json()["id"]
            await client.post(f"/v1/requests/{request_id}/process")

            final = await wait_for_status(client, request_id, {"failed"})
            assert final["error_code"] == "ambiguous_destination"
            assert provider.notifications == []


async def test_notification_provider_failure_ends_in_failed_state() -> None:
    provider = FakeProvider(
        notify_error=ProviderError(
            "provider unavailable",
            code=ErrorCode.PROVIDER_UNAVAILABLE,
            retryable=False,
        )
    )
    app = create_app(settings=settings(), provider=provider)

    async with app.router.lifespan_context(app):
        async with client_for(app) as client:
            created = await client.post("/v1/requests", json={"user_input": USER_INPUT})
            request_id = created.json()["id"]
            await client.post(f"/v1/requests/{request_id}/process")

            final = await wait_for_status(client, request_id, {"failed"})
            assert final["error_code"] == "provider_unavailable"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"user_input": ""},
        {"user_input": "   "},
        {"user_input": "x" * 4_001},
    ],
)
async def test_create_request_validates_input(payload: dict[str, Any]) -> None:
    app = create_app(settings=settings(), provider=FakeProvider())

    async with app.router.lifespan_context(app):
        async with client_for(app) as client:
            response = await client.post("/v1/requests", json=payload)
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "invalid_request"


async def test_unknown_request_returns_404() -> None:
    app = create_app(settings=settings(), provider=FakeProvider())

    async with app.router.lifespan_context(app):
        async with client_for(app) as client:
            get_response = await client.get("/v1/requests/does-not-exist")
            assert get_response.status_code == 404
            assert get_response.json()["error"]["code"] == "not_found"

            process_response = await client.post(
                "/v1/requests/does-not-exist/process"
            )
            assert process_response.status_code == 404


async def test_health_endpoints_and_request_id_echo() -> None:
    app = create_app(settings=settings(), provider=FakeProvider())

    async with app.router.lifespan_context(app):
        async with client_for(app) as client:
            live = await client.get("/health/live")
            assert live.status_code == 200

            ready = await client.get("/health/ready")
            assert ready.status_code == 200
            assert ready.json()["status"] == "ok"

            created = await client.post(
                "/v1/requests",
                json={"user_input": USER_INPUT},
                headers={"X-Request-ID": "trace-123"},
            )
            assert created.headers["X-Request-ID"] == "trace-123"


async def test_idempotency_key_reuses_the_request_and_sets_location() -> None:
    provider = FakeProvider()
    app = create_app(settings=settings(), provider=provider)

    async with app.router.lifespan_context(app):
        async with client_for(app) as client:
            first = await client.post(
                "/v1/requests",
                json={"user_input": USER_INPUT},
                headers={"Idempotency-Key": "intent-1"},
            )
            assert first.status_code == 201
            request_id = first.json()["id"]
            assert first.headers["Location"] == f"/v1/requests/{request_id}"
            assert first.headers["Idempotency-Key"] == "intent-1"

            second = await client.post(
                "/v1/requests",
                json={"user_input": USER_INPUT},
                headers={"Idempotency-Key": "intent-1"},
            )
            assert second.status_code == 200
            assert second.json()["id"] == request_id
            assert second.headers["Idempotency-Replayed"] == "true"


async def test_inbound_api_key_is_optional_but_enforced_when_configured() -> None:
    app = create_app(
        settings=settings(inbound_api_key="inbound-secret"),
        provider=FakeProvider(),
    )

    async with app.router.lifespan_context(app):
        async with client_for(app) as client:
            unauthorized = await client.post(
                "/v1/requests",
                json={"user_input": USER_INPUT},
            )
            assert unauthorized.status_code == 401

            authorized = await client.post(
                "/v1/requests",
                json={"user_input": USER_INPUT},
                headers={"X-API-Key": "inbound-secret"},
            )
            assert authorized.status_code == 201

            # Health and metrics remain available to orchestration.
            assert (await client.get("/health/live")).status_code == 200
            assert (await client.get("/metrics")).status_code == 200


async def test_request_body_limit_returns_413() -> None:
    app = create_app(
        settings=settings(max_request_bytes=1_024),
        provider=FakeProvider(),
    )

    async with app.router.lifespan_context(app):
        async with client_for(app) as client:
            response = await client.post(
                "/v1/requests",
                json={"user_input": "x" * 2_000},
            )
            assert response.status_code == 413
            assert response.json()["error"]["code"] == "payload_too_large"


async def test_status_exposes_retry_after_while_pending() -> None:
    app = create_app(settings=settings(), provider=FakeProvider())

    async with app.router.lifespan_context(app):
        async with client_for(app) as client:
            created = await client.post(
                "/v1/requests",
                json={"user_input": USER_INPUT},
            )
            request_id = created.json()["id"]
            status_response = await client.get(f"/v1/requests/{request_id}")

            assert status_response.status_code == 200
            assert status_response.json()["status"] == "queued"
            assert status_response.headers["Retry-After"] == "1"
            assert status_response.headers["Cache-Control"] == "no-store"


async def test_metrics_endpoint_exposes_pipeline_metrics() -> None:
    provider = FakeProvider()
    app = create_app(settings=settings(), provider=provider)

    async with app.router.lifespan_context(app):
        async with client_for(app) as client:
            created = await client.post(
                "/v1/requests",
                json={"user_input": USER_INPUT},
            )
            request_id = created.json()["id"]
            await client.post(f"/v1/requests/{request_id}/process")
            await wait_for_status(client, request_id, {"sent"})

            metrics = await client.get("/metrics")
            assert metrics.status_code == 200
            assert "notification_requests_created_total" in metrics.text
            assert "notification_requests_processed_total" in metrics.text
            assert "notification_queue_depth" in metrics.text
            assert "notification_http_request_duration_seconds" in metrics.text


async def test_sqlite_backend_persists_state_across_app_lifespan(tmp_path) -> None:
    db_path = tmp_path / "api.db"
    app = create_app(
        settings=settings(
            repository_backend="sqlite",
            sqlite_path=str(db_path),
        ),
        provider=FakeProvider(),
    )

    async with app.router.lifespan_context(app):
        async with client_for(app) as client:
            created = await client.post(
                "/v1/requests",
                json={"user_input": USER_INPUT},
            )
            request_id = created.json()["id"]
            await client.post(f"/v1/requests/{request_id}/process")
            await wait_for_status(client, request_id, {"sent"})

    reopened = SQLiteRequestRepository(str(db_path))
    try:
        persisted = await reopened.get(request_id)
    finally:
        await reopened.aclose()

    assert persisted is not None
    assert persisted.status is RequestStatus.SENT
    assert persisted.provider_id == "p-test"
