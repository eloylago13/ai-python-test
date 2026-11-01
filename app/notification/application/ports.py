from __future__ import annotations

from typing import Protocol

from ..domain.models import ErrorCode, Notification, RequestRecord


class ProviderError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode,
        retryable: bool,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class RequestRepository(Protocol):
    async def create(
        self,
        user_input: str,
        *,
        idempotency_key: str | None = None,
    ) -> tuple[RequestRecord, bool]: ...

    async def get(self, request_id: str) -> RequestRecord | None: ...

    async def claim_for_processing(
        self, request_id: str
    ) -> tuple[bool, RequestRecord | None]: ...

    async def release(self, request_id: str) -> None: ...

    async def mark_extracted(
        self,
        request_id: str,
        notification: Notification,
        *,
        source: str,
        ai_attempts: int,
    ) -> bool: ...

    async def claim_delivery(self, request_id: str) -> Notification | None: ...

    async def mark_sent(self, request_id: str, *, provider_id: str) -> bool: ...

    async def mark_failed(
        self,
        request_id: str,
        *,
        error_code: ErrorCode,
        source: str | None = None,
        ai_attempts: int = 0,
    ) -> bool: ...

    async def stats(self) -> dict[str, int]: ...

    async def recover_pending(self) -> list[tuple[str, str]]: ...

    async def aclose(self) -> None: ...


class NotificationProvider(Protocol):
    async def extract(self, user_input: str, *, trace_id: str | None = None) -> str: ...

    async def notify(self, notification: Notification, *, trace_id: str) -> str: ...

    async def aclose(self) -> None: ...
