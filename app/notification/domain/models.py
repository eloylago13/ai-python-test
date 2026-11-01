from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class NotificationType(str, Enum):
    EMAIL = "email"
    SMS = "sms"


class RequestStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    SENT = "sent"
    FAILED = "failed"


class ErrorCode(str, Enum):
    AI_UNAVAILABLE = "ai_unavailable"
    AI_REFUSED = "ai_refused"
    AI_INVALID_RESPONSE = "ai_invalid_response"
    EXTRACTION_FAILED = "extraction_failed"
    PROVIDER_RATE_LIMITED = "provider_rate_limited"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_REJECTED = "provider_rejected"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, slots=True)
class Notification:
    to: str
    message: str
    type: NotificationType


@dataclass(slots=True)
class RequestRecord:
    id: str
    user_input: str
    status: RequestStatus
    created_at: datetime
    updated_at: datetime
    notification: Notification | None = None
    provider_id: str | None = None
    error_code: ErrorCode | None = None
    extraction_source: str | None = None
    ai_attempts: int = 0
    delivery_claimed: bool = False
    idempotency_key: str | None = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
