from __future__ import annotations

from notification.domain.models import (
    ErrorCode,
    Notification,
    NotificationType,
    RequestStatus,
)
from notification.infrastructure.repository_memory import InMemoryRequestRepository


async def test_repository_lifecycle_and_guarded_transitions() -> None:
    repository = InMemoryRequestRepository()
    record, created = await repository.create(
        "Manda un mail a feda@test.com diciendo hola"
    )

    assert created is True
    assert record.status is RequestStatus.QUEUED
    assert (await repository.stats())["queued"] == 1

    claimed, claimed_record = await repository.claim_for_processing(record.id)
    assert claimed is True
    assert claimed_record is not None
    assert claimed_record.status is RequestStatus.PROCESSING

    claimed_again, current = await repository.claim_for_processing(record.id)
    assert claimed_again is False
    assert current is not None
    assert current.status is RequestStatus.PROCESSING

    notification = Notification(
        to="feda@test.com",
        message="hola",
        type=NotificationType.EMAIL,
    )
    assert (
        await repository.mark_extracted(
            record.id,
            notification,
            source="llm",
            ai_attempts=1,
        )
        is True
    )

    delivered = await repository.claim_delivery(record.id)
    assert delivered == notification
    assert await repository.claim_delivery(record.id) is None

    assert await repository.mark_sent(record.id, provider_id="p-1") is True
    final = await repository.get(record.id)
    assert final is not None
    assert final.status is RequestStatus.SENT
    assert final.provider_id == "p-1"

    # A late failure must not overwrite a sent request.
    assert (
        await repository.mark_failed(
            record.id,
            error_code=ErrorCode.INTERNAL_ERROR,
        )
        is False
    )
    assert (await repository.stats())["sent"] == 1


async def test_release_returns_a_claimed_request_to_the_queue() -> None:
    repository = InMemoryRequestRepository()
    record, _ = await repository.create("hola")
    await repository.claim_for_processing(record.id)
    await repository.release(record.id)

    released = await repository.get(record.id)
    assert released is not None
    assert released.status is RequestStatus.QUEUED


async def test_mark_failed_is_terminal() -> None:
    repository = InMemoryRequestRepository()
    record, _ = await repository.create("hola")

    assert (
        await repository.mark_failed(
            record.id,
            error_code=ErrorCode.EXTRACTION_FAILED,
        )
        is True
    )
    assert (
        await repository.mark_failed(
            record.id,
            error_code=ErrorCode.INTERNAL_ERROR,
        )
        is False
    )

    final = await repository.get(record.id)
    assert final is not None
    assert final.status is RequestStatus.FAILED
    assert final.error_code is ErrorCode.EXTRACTION_FAILED


async def test_idempotency_key_returns_the_same_request() -> None:
    repository = InMemoryRequestRepository()

    first, first_created = await repository.create(
        "Manda un mail a feda@test.com diciendo hola",
        idempotency_key="key-1",
    )
    second, second_created = await repository.create(
        "Manda un mail a feda@test.com diciendo hola",
        idempotency_key="key-1",
    )

    assert first_created is True
    assert second_created is False
    assert first.id == second.id

    other, other_created = await repository.create(
        "Otro mensaje",
        idempotency_key="key-2",
    )
    assert other_created is True
    assert other.id != first.id
