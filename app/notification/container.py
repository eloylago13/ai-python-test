from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .application.ports import NotificationProvider, RequestRepository
from .application.runtime import ServiceRuntime
from .config import Settings


@dataclass(slots=True)
class Container:
    settings: Settings
    repository: RequestRepository
    provider: NotificationProvider
    extraction_queue: asyncio.Queue[str]
    delivery_queue: asyncio.Queue[str]
    runtime: ServiceRuntime
    accepting: bool = True
