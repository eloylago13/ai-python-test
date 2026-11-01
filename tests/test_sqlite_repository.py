from __future__ import annotations

from notification.domain.models import (
    ErrorCode,
    Notification,
    NotificationType,
    RequestStatus,
)
from notification.infrastructure.repository_sqlite import SQLiteRequestRepository


def notification() -> Notification:
    return Notification(
        to="feda@test.com",
        message="hola",
        type=NotificationType.EMAIL,
    )


async def test_sqlite_lifecycle_and_persistence(tmp_path) -> None:
    path = str(tmp_path / "state.db")
    repository = SQLiteRequestRepository(path)

    record, created = await repository.create(
        "Manda un mail a feda@test.com diciendo hola",
        idempotency_key="intent-1",
    )
    assert created is True
    assert record.status is RequestStatus.QUEUED

    claimed, current = await repository.claim_for_processing(record.id)
    assert claimed is True
    assert current is not None
    assert current.status is RequestStatus.PROCESSING

    assert (
        await repository.mark_extracted(
            record.id,
            notification(),
            source="llm",
            ai_attempts=1,
        )
        is True
    )
    assert await repository.claim_delivery(record.id) == notification()
    assert await repository.mark_sent(record.id, provider_id="p-1") is True

    await repository.aclose()

    reopened = SQLiteRequestRepository(path)
    try:
        persisted = await reopened.get(record.id)
        assert persisted is not None
        assert persisted.status is RequestStatus.SENT
        assert persisted.provider_id == "p-1"
        assert persisted.notification == notification()

        replay, replay_created = await reopened.create(
            "Manda un mail a feda@test.com diciendo hola",
            idempotency_key="intent-1",
        )
        assert replay_created is False
        assert replay.id == record.id
        assert (await reopened.stats())["sent"] == 1
    finally:
        await reopened.aclose()


async def test_sqlite_recovery_distinguishes_extraction_and_delivery(
    tmp_path,
) -> None:
    path = str(tmp_path / "recovery.db")
    repository = SQLiteRequestRepository(path)

    record, _ = await repository.create("Manda un mail a feda@test.com diciendo hola")
    await repository.claim_for_processing(record.id)

    assert await repository.recover_pending() == [(record.id, "extract")]

    assert (
        await repository.mark_extracted(
            record.id,
            notification(),
            source="llm",
            ai_attempts=1,
        )
        is True
    )

    assert await repository.recover_pending() == [(record.id, "deliver")]
    await repository.aclose()


async def test_sqlite_terminal_failure_is_not_overwritten(tmp_path) -> None:
    repository = SQLiteRequestRepository(str(tmp_path / "failure.db"))

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
    await repository.aclose()
