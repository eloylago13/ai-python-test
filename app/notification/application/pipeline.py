from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ..config import Settings
from ..domain.guardrails import (
    ExtractionOutcome,
    deterministic_extract,
    parse_llm_output,
)
from ..domain.models import ErrorCode, Notification, RequestStatus
from ..observability.logging import get_logger
from ..observability.metrics import (
    DELIVERY_FAILURES,
    EXTRACTION_FAILURES,
    EXTRACTION_SOURCES,
    REQUESTS_PROCESSED,
)
from .ports import NotificationProvider, ProviderError, RequestRepository

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PipelineExtraction:
    notification: Notification | None
    error_code: ErrorCode | None = None
    source: str = "llm"
    ai_attempts: int = 0
    inferred_fields: tuple[str, ...] = ()


class NotificationPipeline:
    """Two-stage application pipeline.

    Extraction and delivery are separate stages on purpose. The provider limits
    notifications to 50 per 10 seconds; if delivery were coupled to extraction,
    every extraction worker would eventually block on the delivery token bucket
    and the whole service would slow down. Separate queues let extraction keep
    making progress while delivery is rate limited.
    """

    def __init__(
        self,
        repository: RequestRepository,
        provider: NotificationProvider,
        delivery_queue: asyncio.Queue[str],
        settings: Settings,
    ) -> None:
        self._repository = repository
        self._provider = provider
        self._delivery_queue = delivery_queue
        self._settings = settings

    async def extract(self, request_id: str) -> None:
        record = await self._repository.get(request_id)
        if record is None or record.status is not RequestStatus.PROCESSING:
            return

        result = await self._extract_with_recovery(request_id, record.user_input)
        if result.notification is None:
            error_code = result.error_code or ErrorCode.EXTRACTION_FAILED
            failed = await self._repository.mark_failed(
                request_id,
                error_code=error_code,
                source=result.source,
                ai_attempts=result.ai_attempts,
            )
            if failed:
                EXTRACTION_FAILURES.labels(code=error_code.value).inc()
                REQUESTS_PROCESSED.labels(result="failed").inc()
            logger.warning(
                "extraction_failed",
                extra={
                    "request_id": request_id,
                    "error_code": error_code.value,
                    "ai_attempts": result.ai_attempts,
                },
            )
            return

        stored = await self._repository.mark_extracted(
            request_id,
            result.notification,
            source=result.source,
            ai_attempts=result.ai_attempts,
        )
        if not stored:
            return

        EXTRACTION_SOURCES.labels(source=result.source).inc()
        logger.info(
            "extraction_completed",
            extra={
                "request_id": request_id,
                "source": result.source,
                "ai_attempts": result.ai_attempts,
                "inferred_fields": ",".join(result.inferred_fields),
            },
        )
        await self._delivery_queue.put(request_id)

    async def deliver(self, request_id: str) -> None:
        notification = await self._repository.claim_delivery(request_id)
        if notification is None:
            return

        try:
            provider_id = await self._provider.notify(
                notification,
                trace_id=request_id,
            )
        except ProviderError as exc:
            failed = await self._repository.mark_failed(
                request_id, error_code=exc.code
            )
            if failed:
                DELIVERY_FAILURES.labels(code=exc.code.value).inc()
                REQUESTS_PROCESSED.labels(result="failed").inc()
            logger.warning(
                "delivery_failed",
                extra={
                    "request_id": request_id,
                    "error_code": exc.code.value,
                    "retryable": exc.retryable,
                },
            )
            return
        except Exception:
            failed = await self._repository.mark_failed(
                request_id,
                error_code=ErrorCode.INTERNAL_ERROR,
            )
            if failed:
                DELIVERY_FAILURES.labels(code=ErrorCode.INTERNAL_ERROR.value).inc()
                REQUESTS_PROCESSED.labels(result="failed").inc()
            logger.exception(
                "delivery_unhandled_error",
                extra={"request_id": request_id},
            )
            return

        if await self._repository.mark_sent(request_id, provider_id=provider_id):
            REQUESTS_PROCESSED.labels(result="sent").inc()
        logger.info(
            "notification_sent",
            extra={
                "request_id": request_id,
                "provider_id": provider_id,
                "type": notification.type.value,
            },
        )

    async def fail_internal(self, request_id: str) -> None:
        if await self._repository.mark_failed(
            request_id,
            error_code=ErrorCode.INTERNAL_ERROR,
        ):
            REQUESTS_PROCESSED.labels(result="failed").inc()

    async def _extract_with_recovery(
        self, request_id: str, user_input: str
    ) -> PipelineExtraction:
        last_error: PipelineExtraction | None = None

        for attempt in range(1, self._settings.ai_parse_attempts + 1):
            try:
                raw = await self._provider.extract(
                    user_input,
                    trace_id=request_id,
                )
            except ProviderError as exc:
                last_error = PipelineExtraction(
                    notification=None,
                    error_code=exc.code,
                    source="llm",
                    ai_attempts=attempt,
                )
                logger.warning(
                    "ai_provider_error",
                    extra={
                        "request_id": request_id,
                        "error_code": exc.code.value,
                        "retryable": exc.retryable,
                        "ai_attempts": attempt,
                    },
                )
                break

            outcome = parse_llm_output(raw, user_input)
            if outcome.notification is not None:
                return PipelineExtraction(
                    notification=outcome.notification,
                    source=outcome.source,
                    ai_attempts=attempt,
                    inferred_fields=outcome.inferred_fields,
                )

            if outcome.error_code is ErrorCode.AI_REFUSED:
                return PipelineExtraction(
                    notification=None,
                    error_code=ErrorCode.AI_REFUSED,
                    source="llm",
                    ai_attempts=attempt,
                )

            last_error = PipelineExtraction(
                notification=None,
                error_code=outcome.error_code or ErrorCode.EXTRACTION_FAILED,
                source="llm",
                ai_attempts=attempt,
            )
            logger.info(
                "ai_extraction_retry",
                extra={
                    "request_id": request_id,
                    "reason": outcome.reason,
                    "ai_attempts": attempt,
                },
            )

        if last_error is not None and last_error.error_code is ErrorCode.AI_REFUSED:
            return last_error

        # A deterministic fallback keeps the service available when the model
        # is down or keeps returning unusable output. It is deliberately not
        # used for policy refusals: a refusal must be respected.
        fallback: ExtractionOutcome = deterministic_extract(user_input)
        if fallback.notification is not None:
            logger.warning(
                "deterministic_fallback_used",
                extra={
                    "request_id": request_id,
                    "ai_attempts": last_error.ai_attempts if last_error else 0,
                },
            )
            return PipelineExtraction(
                notification=fallback.notification,
                source=fallback.source,
                ai_attempts=last_error.ai_attempts if last_error else 0,
                inferred_fields=fallback.inferred_fields,
            )

        return last_error or PipelineExtraction(
            notification=None,
            error_code=ErrorCode.EXTRACTION_FAILED,
            source="llm",
            ai_attempts=0,
        )
