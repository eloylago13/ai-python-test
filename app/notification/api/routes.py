from __future__ import annotations

import asyncio
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from ..container import Container
from ..domain.models import ErrorCode, RequestStatus
from ..observability.metrics import REQUESTS_CREATED


async def require_api_key(request: Request) -> None:
    """Optional inbound authentication.

    It is disabled unless INBOUND_API_KEY is configured. The bundled load test
    and local development therefore keep working without an extra header.
    """

    container = _container(request)
    expected = container.settings.inbound_api_key
    if not expected:
        return

    provided = request.headers.get("X-API-Key", "")
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )


router = APIRouter(
    prefix="/v1",
    tags=["requests"],
    dependencies=[Depends(require_api_key)],
)
health_router = APIRouter(tags=["health"])


class CreateRequestPayload(BaseModel):
    user_input: str = Field(..., min_length=1, max_length=4_000)

    @field_validator("user_input")
    @classmethod
    def _strip_user_input(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("user_input must not be blank")
        return value


class CreateRequestResponse(BaseModel):
    id: str


class ProcessResponse(BaseModel):
    id: str
    status: RequestStatus


class RequestStatusResponse(BaseModel):
    id: str
    status: RequestStatus
    error_code: ErrorCode | None = None
    created_at: datetime
    updated_at: datetime


@router.post(
    "/requests",
    status_code=status.HTTP_201_CREATED,
    response_model=CreateRequestResponse,
)
async def create_request(
    payload: CreateRequestPayload,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
) -> CreateRequestResponse:
    container = _container(request)
    if len(payload.user_input) > container.settings.max_input_chars:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "user_input exceeds the configured maximum of "
                f"{container.settings.max_input_chars} characters"
            ),
        )

    record, created = await container.repository.create(
        payload.user_input,
        idempotency_key=idempotency_key,
    )
    response.headers["Location"] = f"/v1/requests/{record.id}"
    if idempotency_key:
        response.headers["Idempotency-Key"] = idempotency_key
        if not created:
            response.status_code = status.HTTP_200_OK
            response.headers["Idempotency-Replayed"] = "true"
    if created:
        REQUESTS_CREATED.inc()
    return CreateRequestResponse(id=record.id)


@router.post(
    "/requests/{request_id}/process",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ProcessResponse,
)
async def process_request(
    request_id: str,
    request: Request,
    response: Response,
) -> ProcessResponse:
    container = _container(request)
    if not container.accepting:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service is shutting down",
            headers={"Retry-After": "1"},
        )

    claimed, record = await container.repository.claim_for_processing(request_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Request not found",
        )

    if not claimed:
        # Idempotent by design: a second process call never duplicates a
        # notification. It reports the current state instead.
        response.status_code = status.HTTP_200_OK
        return ProcessResponse(id=record.id, status=record.status)

    try:
        container.extraction_queue.put_nowait(request_id)
    except asyncio.QueueFull:
        await container.repository.release(request_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service is at capacity; retry later",
            headers={"Retry-After": "1"},
        )

    return ProcessResponse(id=request_id, status=RequestStatus.PROCESSING)


@router.get(
    "/requests/{request_id}",
    response_model=RequestStatusResponse,
    response_model_exclude_none=True,
)
async def get_request(
    request_id: str,
    request: Request,
    response: Response,
) -> RequestStatusResponse:
    container = _container(request)
    record = await container.repository.get(request_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Request not found",
        )

    response.headers["Cache-Control"] = "no-store"
    if record.status in (RequestStatus.QUEUED, RequestStatus.PROCESSING):
        response.headers["Retry-After"] = "1"

    return RequestStatusResponse(
        id=record.id,
        status=record.status,
        error_code=record.error_code,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@health_router.get("/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@health_router.get("/health/ready")
async def readiness(request: Request) -> JSONResponse:
    container = _container(request)
    queues = container.runtime.queue_depths()
    if not container.accepting:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "draining", "queues": queues},
        )
    return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "ok", "queues": queues})


def _container(request: Request) -> Container:
    container: Container | None = getattr(request.app.state, "container", None)
    if container is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service is not ready",
        )
    return container
