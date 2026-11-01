from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from ..observability.logging import get_logger

logger = get_logger(__name__)

Handler = Callable[[str], Awaitable[None]]
ErrorHandler = Callable[[str], Awaitable[None]]


class WorkerPool:
    def __init__(
        self,
        name: str,
        worker_count: int,
        queue: asyncio.Queue[str],
        handler: Handler,
        *,
        on_error: ErrorHandler | None = None,
    ) -> None:
        self._name = name
        self._worker_count = worker_count
        self._queue = queue
        self._handler = handler
        self._on_error = on_error
        self._tasks: list[asyncio.Task[None]] = []

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    async def start(self) -> None:
        if self._tasks:
            return
        self._tasks = [
            asyncio.create_task(self._run(index), name=f"{self._name}-{index}")
            for index in range(self._worker_count)
        ]

    async def _run(self, index: int) -> None:
        while True:
            request_id = await self._queue.get()
            try:
                await self._handler(request_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "worker_unhandled_error",
                    extra={
                        "pool": self._name,
                        "worker": index,
                        "request_id": request_id,
                    },
                )
                if self._on_error is not None:
                    try:
                        await self._on_error(request_id)
                    except Exception:
                        logger.exception(
                            "worker_error_handler_failed",
                            extra={
                                "pool": self._name,
                                "worker": index,
                                "request_id": request_id,
                            },
                        )
            finally:
                self._queue.task_done()

    async def stop(self, drain_timeout_seconds: float) -> None:
        if not self._tasks:
            return

        try:
            await asyncio.wait_for(
                self._queue.join(),
                timeout=drain_timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "worker_pool_drain_timeout",
                extra={
                    "pool": self._name,
                    "queue_depth": self._queue.qsize(),
                    "timeout_seconds": drain_timeout_seconds,
                },
            )

        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()


class ServiceRuntime:
    def __init__(
        self,
        extraction_pool: WorkerPool,
        delivery_pool: WorkerPool,
        *,
        shutdown_timeout_seconds: float,
    ) -> None:
        self._extraction_pool = extraction_pool
        self._delivery_pool = delivery_pool
        self._shutdown_timeout_seconds = shutdown_timeout_seconds

    async def start(self) -> None:
        await self._extraction_pool.start()
        await self._delivery_pool.start()

    async def stop(self) -> None:
        # Drain extraction first: it feeds the delivery queue. Then drain
        # delivery. Both stages share one deadline so shutdown is bounded even
        # when the provider is slow or the backlog is large.
        deadline = time.monotonic() + self._shutdown_timeout_seconds
        await self._extraction_pool.stop(
            max(0.0, deadline - time.monotonic())
        )
        await self._delivery_pool.stop(
            max(0.0, deadline - time.monotonic())
        )

    def queue_depths(self) -> dict[str, int]:
        return {
            "extraction": self._extraction_pool.queue_depth,
            "delivery": self._delivery_pool.queue_depth,
        }
