from __future__ import annotations

import asyncio
import re
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from notification.api.routes import health_router, router
from notification.application.pipeline import NotificationPipeline
from notification.application.ports import NotificationProvider, RequestRepository
from notification.application.runtime import ServiceRuntime, WorkerPool
from notification.config import Settings
from notification.container import Container
from notification.infrastructure.provider import ProviderClient
from notification.infrastructure.rate_limit import AsyncTokenBucket
from notification.infrastructure.repository_memory import InMemoryRequestRepository
from notification.infrastructure.repository_sqlite import SQLiteRequestRepository
from notification.observability.logging import configure_logging, get_logger
from notification.observability.metrics import HTTP_DURATION, QUEUE_DEPTH

logger = get_logger("notification.main")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def create_app(
    *,
    settings: Settings | None = None,
    provider: NotificationProvider | None = None,
    repository: RequestRepository | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        limiter = AsyncTokenBucket(
            rate_per_second=settings.notify_rate_per_second,
            capacity=settings.notify_burst,
        )
        active_provider = provider or ProviderClient(settings, limiter=limiter)
        active_repository = repository or _build_repository(settings)

        extraction_queue: asyncio.Queue[str] = asyncio.Queue(
            maxsize=settings.extraction_queue_size
        )
        delivery_queue: asyncio.Queue[str] = asyncio.Queue(
            maxsize=settings.delivery_queue_size
        )

        pipeline = NotificationPipeline(
            active_repository,
            active_provider,
            delivery_queue,
            settings,
        )
        extraction_pool = WorkerPool(
            "extraction",
            settings.extraction_workers,
            extraction_queue,
            pipeline.extract,
            on_error=pipeline.fail_internal,
        )
        delivery_pool = WorkerPool(
            "delivery",
            settings.delivery_workers,
            delivery_queue,
            pipeline.deliver,
            on_error=pipeline.fail_internal,
        )
        runtime = ServiceRuntime(
            extraction_pool,
            delivery_pool,
            shutdown_timeout_seconds=settings.shutdown_timeout_seconds,
        )
        container = Container(
            settings=settings,
            repository=active_repository,
            provider=active_provider,
            extraction_queue=extraction_queue,
            delivery_queue=delivery_queue,
            runtime=runtime,
        )
        app.state.container = container

        try:
            await runtime.start()
            recovered = await active_repository.recover_pending()
            for request_id, stage in recovered:
                if stage == "deliver":
                    await delivery_queue.put(request_id)
                else:
                    await extraction_queue.put(request_id)
            if recovered:
                logger.info(
                    "service_recovered",
                    extra={"recovered_requests": len(recovered)},
                )

            logger.info(
                "service_started",
                extra={
                    "service": settings.service_name,
                    "provider_base_url": settings.provider_base_url,
                    "repository_backend": settings.repository_backend,
                    "extraction_workers": settings.extraction_workers,
                    "delivery_workers": settings.delivery_workers,
                    "notify_rate_per_second": settings.notify_rate_per_second,
                },
            )
            yield
        finally:
            container.accepting = False
            await runtime.stop()
            await active_repository.aclose()
            await active_provider.aclose()
            logger.info("service_stopped", extra={"service": settings.service_name})

    app = FastAPI(
        title="Notification Service",
        description=(
            "Extracts notification intents from natural language, validates the "
            "result and delivers email or SMS through the configured provider."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.settings = settings

    app.include_router(router)
    app.include_router(health_router)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "service": settings.service_name,
            "version": "1.0.0",
            "docs": "/docs",
        }

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request) -> Response:
        container: Container | None = getattr(
            request.app.state, "container", None
        )
        if container is not None:
            for queue_name, depth in container.runtime.queue_depths().items():
                QUEUE_DEPTH.labels(queue=queue_name).set(depth)
        return Response(
            content=generate_latest(),
            media_type=CONTENT_TYPE_LATEST,
        )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = _request_id(request)
        request.state.request_id = request_id
        started_at = time.perf_counter()

        try:
            content_length = request.headers.get("content-length")
            too_large = False
            if content_length:
                try:
                    too_large = int(content_length) > settings.max_request_bytes
                except ValueError:
                    too_large = False

            if too_large:
                response: Response = JSONResponse(
                    status_code=413,
                    content={
                        "error": {
                            "code": "payload_too_large",
                            "message": (
                                "Request body exceeds the configured maximum of "
                                f"{settings.max_request_bytes} bytes"
                            ),
                        }
                    },
                )
            else:
                response = await call_next(request)
        except Exception:
            logger.exception(
                "http_request_failed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                },
            )
            raise

        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith("/v1"):
            response.headers.setdefault("Cache-Control", "no-store")

        route = request.scope.get("route")
        route_path = getattr(route, "path", "unmatched")
        HTTP_DURATION.labels(
            method=request.method,
            route=route_path,
            status=str(response.status_code),
        ).observe(duration_ms / 1000.0)

        if not request.url.path.startswith(("/health", "/metrics")):
            logger.info(
                "http_request",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                },
            )
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": "Request validation failed",
                    "details": jsonable_encoder(exc.errors()),
                }
            },
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
        code = {
            404: "not_found",
            503: "unavailable",
        }.get(exc.status_code, "request_error")
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": code, "message": exc.detail}},
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unhandled_exception",
            extra={
                "request_id": getattr(request.state, "request_id", None),
                "method": request.method,
                "path": request.url.path,
            },
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "Internal server error",
                }
            },
        )

    return app


def _build_repository(settings: Settings) -> RequestRepository:
    # SQLite is the durable local adapter. Production is expected to provide a
    # Postgres implementation of the same port (see ADR-012).
    if settings.repository_backend == "memory":
        return InMemoryRequestRepository()
    if settings.repository_backend == "sqlite":
        return SQLiteRequestRepository(settings.sqlite_path)
    raise ValueError(
        f"Unsupported repository backend: {settings.repository_backend}"
    )


def _request_id(request: Request) -> str:
    candidate = request.headers.get("X-Request-ID", "")
    if _REQUEST_ID_RE.match(candidate):
        return candidate
    return uuid4().hex


app = create_app()
