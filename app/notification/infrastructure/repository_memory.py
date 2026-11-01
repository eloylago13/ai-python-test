from __future__ import annotations

import asyncio
from dataclasses import replace
from uuid import uuid4

from ..domain.models import (
    ErrorCode,
    Notification,
    RequestRecord,
    RequestStatus,
    utc_now,
)


class InMemoryRequestRepository:
    """Async, lock-protected request store.

    The interface is a port. Replacing this class with Redis or PostgreSQL is an
    infrastructure change, not a domain or API change. That is deliberate: the
    in-memory implementation keeps the local deployment small, while production
    can move to durable storage without rewriting the pipeline.
    """

    def __init__(self) -> None:
        self._records: dict[str, RequestRecord] = {}
        self._idempotency: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        user_input: str,
        *,
        idempotency_key: str | None = None,
    ) -> tuple[RequestRecord, bool]:
        async with self._lock:
            if idempotency_key:
                existing_id = self._idempotency.get(idempotency_key)
                if existing_id:
                    existing = self._records.get(existing_id)
                    if existing is not None:
                        return _copy(existing), False

            now = utc_now()
            record = RequestRecord(
                id=uuid4().hex,
                user_input=user_input,
                status=RequestStatus.QUEUED,
                created_at=now,
                updated_at=now,
                idempotency_key=idempotency_key,
            )
            self._records[record.id] = record
            if idempotency_key:
                self._idempotency[idempotency_key] = record.id
            return _copy(record), True

    async def get(self, request_id: str) -> RequestRecord | None:
        async with self._lock:
            record = self._records.get(request_id)
            return _copy(record) if record is not None else None

    async def claim_for_processing(
        self, request_id: str
    ) -> tuple[bool, RequestRecord | None]:
        async with self._lock:
            record = self._records.get(request_id)
            if record is None:
                return False, None
            if record.status is not RequestStatus.QUEUED:
                return False, _copy(record)

            record.status = RequestStatus.PROCESSING
            record.updated_at = utc_now()
            return True, _copy(record)

    async def release(self, request_id: str) -> None:
        async with self._lock:
            record = self._records.get(request_id)
            if record is None:
                return
            if (
                record.status is RequestStatus.PROCESSING
                and record.notification is None
                and not record.delivery_claimed
            ):
                record.status = RequestStatus.QUEUED
                record.updated_at = utc_now()

    async def mark_extracted(
        self,
        request_id: str,
        notification: Notification,
        *,
        source: str,
        ai_attempts: int,
    ) -> bool:
        async with self._lock:
            record = self._records.get(request_id)
            if record is None or record.status is not RequestStatus.PROCESSING:
                return False
            if record.notification is not None:
                return False

            record.notification = notification
            record.extraction_source = source
            record.ai_attempts = ai_attempts
            record.updated_at = utc_now()
            return True

    async def claim_delivery(self, request_id: str) -> Notification | None:
        async with self._lock:
            record = self._records.get(request_id)
            if record is None or record.status is not RequestStatus.PROCESSING:
                return None
            if record.notification is None or record.delivery_claimed:
                return None

            record.delivery_claimed = True
            record.updated_at = utc_now()
            return record.notification

    async def mark_sent(self, request_id: str, *, provider_id: str) -> bool:
        async with self._lock:
            record = self._records.get(request_id)
            if record is None or record.status is not RequestStatus.PROCESSING:
                return False
            record.status = RequestStatus.SENT
            record.provider_id = provider_id
            record.updated_at = utc_now()
            return True

    async def mark_failed(
        self,
        request_id: str,
        *,
        error_code: ErrorCode,
        source: str | None = None,
        ai_attempts: int = 0,
    ) -> bool:
        async with self._lock:
            record = self._records.get(request_id)
            if record is None:
                return False
            if record.status in (RequestStatus.SENT, RequestStatus.FAILED):
                return False

            record.status = RequestStatus.FAILED
            record.error_code = error_code
            if source is not None:
                record.extraction_source = source
            if ai_attempts:
                record.ai_attempts = ai_attempts
            record.updated_at = utc_now()
            return True

    async def stats(self) -> dict[str, int]:
        async with self._lock:
            counts = {status.value: 0 for status in RequestStatus}
            for record in self._records.values():
                counts[record.status.value] += 1
            return counts

    async def recover_pending(self) -> list[tuple[str, str]]:
        # The in-memory adapter has no state to recover across restarts.
        return []

    async def aclose(self) -> None:
        return None


def _copy(record: RequestRecord) -> RequestRecord:
    return replace(record)
