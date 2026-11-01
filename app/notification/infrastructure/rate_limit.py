from __future__ import annotations

import asyncio
import time


class AsyncTokenBucket:
    """Small async token bucket used to stay below the provider's rate limit.

    The provider accepts 50 notifications per 10 seconds. Sending at 4.0/s with
    a burst of 8 keeps the worst case at 48 per 10 seconds, leaving headroom for
    retries and clock jitter.
    """

    def __init__(self, rate_per_second: float, capacity: int) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        if capacity < 1:
            raise ValueError("capacity must be at least 1")

        self._rate = float(rate_per_second)
        self._capacity = float(capacity)
        self._tokens = float(capacity)
        self._updated_at = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._updated_at
                self._tokens = min(
                    self._capacity,
                    self._tokens + elapsed * self._rate,
                )
                self._updated_at = now

                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return

                wait_seconds = (1.0 - self._tokens) / self._rate

            await asyncio.sleep(wait_seconds)
