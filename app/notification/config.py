from __future__ import annotations

import os
from dataclasses import dataclass


def _env_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip()
    return value or default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)


def _env_optional(name: str) -> str | None:
    raw = os.getenv(name)
    if raw is None:
        return None
    value = raw.strip()
    return value or None


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime configuration.

    Every value can be overridden through an environment variable. Defaults are
    intentionally safe for local development against the bundled provider.
    """

    service_name: str = "notification-service"
    provider_base_url: str = "http://localhost:3001"
    provider_api_key: str = "test-dev-2026"
    inbound_api_key: str | None = None

    repository_backend: str = "sqlite"
    sqlite_path: str = "data/notifications.db"

    ai_timeout_seconds: float = 15.0
    notify_timeout_seconds: float = 6.0
    max_ai_attempts: int = 3
    max_notify_attempts: int = 5
    ai_parse_attempts: int = 2

    notify_rate_per_second: float = 4.0
    notify_burst: int = 8

    extraction_workers: int = 40
    delivery_workers: int = 10
    extraction_queue_size: int = 20_000
    delivery_queue_size: int = 20_000
    shutdown_timeout_seconds: float = 10.0

    max_input_chars: int = 2_000
    max_request_bytes: int = 65_536
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_base_url", self.provider_base_url.rstrip("/"))
        object.__setattr__(self, "log_level", self.log_level.upper())

        if not self.provider_base_url:
            raise ValueError("PROVIDER_BASE_URL must not be empty")
        if not self.provider_api_key:
            raise ValueError("PROVIDER_API_KEY must not be empty")
        if self.repository_backend not in {"sqlite", "memory"}:
            raise ValueError(
                "REPOSITORY_BACKEND must be 'sqlite' or 'memory'"
            )
        if self.repository_backend == "sqlite" and not self.sqlite_path.strip():
            raise ValueError("SQLITE_PATH must not be empty for the sqlite backend")
        if self.ai_timeout_seconds <= 0 or self.notify_timeout_seconds <= 0:
            raise ValueError("Provider timeouts must be positive")
        if self.max_ai_attempts < 1 or self.max_notify_attempts < 1:
            raise ValueError("Retry attempts must be at least 1")
        if self.ai_parse_attempts < 1:
            raise ValueError("AI_PARSE_ATTEMPTS must be at least 1")
        if self.notify_rate_per_second <= 0:
            raise ValueError("NOTIFY_RATE_PER_SECOND must be positive")
        if self.notify_burst < 1:
            raise ValueError("NOTIFY_BURST must be at least 1")
        if self.extraction_workers < 1 or self.delivery_workers < 1:
            raise ValueError("Worker counts must be at least 1")
        if self.extraction_queue_size < 1 or self.delivery_queue_size < 1:
            raise ValueError("Queue sizes must be at least 1")
        if self.shutdown_timeout_seconds < 0:
            raise ValueError("SHUTDOWN_TIMEOUT_SECONDS must not be negative")
        if self.max_input_chars < 1:
            raise ValueError("MAX_INPUT_CHARS must be positive")
        if self.max_request_bytes < 1:
            raise ValueError("MAX_REQUEST_BYTES must be positive")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            service_name=_env_str("SERVICE_NAME", "notification-service"),
            provider_base_url=_env_str("PROVIDER_BASE_URL", "http://localhost:3001"),
            provider_api_key=_env_str("PROVIDER_API_KEY", "test-dev-2026"),
            inbound_api_key=_env_optional("INBOUND_API_KEY"),
            repository_backend=_env_str("REPOSITORY_BACKEND", "sqlite"),
            sqlite_path=_env_str("SQLITE_PATH", "data/notifications.db"),
            ai_timeout_seconds=_env_float("AI_TIMEOUT_SECONDS", 15.0),
            notify_timeout_seconds=_env_float("NOTIFY_TIMEOUT_SECONDS", 6.0),
            max_ai_attempts=_env_int("AI_MAX_ATTEMPTS", 3),
            max_notify_attempts=_env_int("NOTIFY_MAX_ATTEMPTS", 5),
            ai_parse_attempts=_env_int("AI_PARSE_ATTEMPTS", 2),
            notify_rate_per_second=_env_float("NOTIFY_RATE_PER_SECOND", 4.0),
            notify_burst=_env_int("NOTIFY_BURST", 8),
            extraction_workers=_env_int("EXTRACTION_WORKERS", 40),
            delivery_workers=_env_int("DELIVERY_WORKERS", 10),
            extraction_queue_size=_env_int("EXTRACTION_QUEUE_SIZE", 20_000),
            delivery_queue_size=_env_int("DELIVERY_QUEUE_SIZE", 20_000),
            shutdown_timeout_seconds=_env_float("SHUTDOWN_TIMEOUT_SECONDS", 10.0),
            max_input_chars=_env_int("MAX_INPUT_CHARS", 2_000),
            max_request_bytes=_env_int("MAX_REQUEST_BYTES", 65_536),
            log_level=_env_str("LOG_LEVEL", "INFO"),
        )
