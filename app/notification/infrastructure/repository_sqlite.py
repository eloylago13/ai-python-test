from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import aiosqlite

from ..domain.models import (
    ErrorCode,
    Notification,
    NotificationType,
    RequestRecord,
    RequestStatus,
    utc_now,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id TEXT PRIMARY KEY,
    user_input TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'processing', 'sent', 'failed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    notification_json TEXT,
    provider_id TEXT,
    error_code TEXT,
    extraction_source TEXT,
    ai_attempts INTEGER NOT NULL DEFAULT 0,
    delivery_claimed INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_requests_status_created
    ON requests(status, created_at);
"""


class SQLiteRequestRepository:
    """Durable local repository.

    SQLite keeps the request state across process restarts without requiring an
    external service. Production is expected to provide a Postgres-backed
    implementation of the same port; queues still live in memory in this
    deployment.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None
        self._connect_lock = asyncio.Lock()
        self._lock = asyncio.Lock()

    async def _connection(self) -> aiosqlite.Connection:
        if self._db is not None:
            return self._db

        async with self._connect_lock:
            if self._db is not None:
                return self._db

            if self._path != ":memory:":
                Path(self._path).expanduser().resolve().parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )

            db = await aiosqlite.connect(self._path)
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")
            await db.execute("PRAGMA busy_timeout=5000")
            await db.executescript(_SCHEMA)
            await db.commit()
            self._db = db

        return self._db

    async def aclose(self) -> None:
        async with self._lock:
            if self._db is not None:
                await self._db.close()
                self._db = None

    async def create(
        self,
        user_input: str,
        *,
        idempotency_key: str | None = None,
    ) -> tuple[RequestRecord, bool]:
        async with self._lock:
            db = await self._connection()
            now = utc_now()
            record = RequestRecord(
                id=uuid4().hex,
                user_input=user_input,
                status=RequestStatus.QUEUED,
                created_at=now,
                updated_at=now,
                idempotency_key=idempotency_key,
            )

            try:
                await db.execute(
                    """
                    INSERT INTO requests (
                        id, user_input, status, created_at, updated_at,
                        ai_attempts, delivery_claimed, idempotency_key
                    ) VALUES (?, ?, ?, ?, ?, 0, 0, ?)
                    """,
                    (
                        record.id,
                        record.user_input,
                        record.status.value,
                        record.created_at.isoformat(),
                        record.updated_at.isoformat(),
                        idempotency_key,
                    ),
                )
                await db.commit()
            except sqlite3.IntegrityError:
                await db.rollback()
                if idempotency_key:
                    existing = await self._get_by_idempotency_key(idempotency_key)
                    if existing is not None:
                        return existing, False
                raise

            return record, True

    async def get(self, request_id: str) -> RequestRecord | None:
        async with self._lock:
            db = await self._connection()
            cursor = await db.execute(
                "SELECT * FROM requests WHERE id = ?",
                (request_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            return self._row_to_record(row) if row is not None else None

    async def claim_for_processing(
        self, request_id: str
    ) -> tuple[bool, RequestRecord | None]:
        async with self._lock:
            db = await self._connection()
            now = utc_now().isoformat()
            cursor = await db.execute(
                """
                UPDATE requests
                SET status = 'processing', updated_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (now, request_id),
            )
            claimed = cursor.rowcount > 0
            await cursor.close()
            await db.commit()

            row = await self._get_row(db, request_id)
            return claimed, self._row_to_record(row) if row is not None else None

    async def release(self, request_id: str) -> None:
        async with self._lock:
            db = await self._connection()
            await db.execute(
                """
                UPDATE requests
                SET status = 'queued', updated_at = ?
                WHERE id = ?
                  AND status = 'processing'
                  AND notification_json IS NULL
                  AND delivery_claimed = 0
                """,
                (utc_now().isoformat(), request_id),
            )
            await db.commit()

    async def mark_extracted(
        self,
        request_id: str,
        notification: Notification,
        *,
        source: str,
        ai_attempts: int,
    ) -> bool:
        async with self._lock:
            db = await self._connection()
            cursor = await db.execute(
                """
                UPDATE requests
                SET notification_json = ?,
                    extraction_source = ?,
                    ai_attempts = ?,
                    updated_at = ?
                WHERE id = ?
                  AND status = 'processing'
                  AND notification_json IS NULL
                """,
                (
                    self._notification_json(notification),
                    source,
                    ai_attempts,
                    utc_now().isoformat(),
                    request_id,
                ),
            )
            changed = cursor.rowcount > 0
            await cursor.close()
            await db.commit()
            return changed

    async def claim_delivery(self, request_id: str) -> Notification | None:
        async with self._lock:
            db = await self._connection()
            cursor = await db.execute(
                """
                UPDATE requests
                SET delivery_claimed = 1, updated_at = ?
                WHERE id = ?
                  AND status = 'processing'
                  AND notification_json IS NOT NULL
                  AND delivery_claimed = 0
                """,
                (utc_now().isoformat(), request_id),
            )
            claimed = cursor.rowcount > 0
            await cursor.close()
            await db.commit()
            if not claimed:
                return None

            row = await self._get_row(db, request_id)
            if row is None or not row["notification_json"]:
                return None
            return self._notification_from_json(row["notification_json"])

    async def mark_sent(self, request_id: str, *, provider_id: str) -> bool:
        async with self._lock:
            db = await self._connection()
            cursor = await db.execute(
                """
                UPDATE requests
                SET status = 'sent', provider_id = ?, updated_at = ?
                WHERE id = ? AND status = 'processing'
                """,
                (provider_id, utc_now().isoformat(), request_id),
            )
            changed = cursor.rowcount > 0
            await cursor.close()
            await db.commit()
            return changed

    async def mark_failed(
        self,
        request_id: str,
        *,
        error_code: ErrorCode,
        source: str | None = None,
        ai_attempts: int = 0,
    ) -> bool:
        async with self._lock:
            db = await self._connection()
            now = utc_now().isoformat()

            if source is not None and ai_attempts:
                sql = """
                    UPDATE requests
                    SET status = 'failed',
                        error_code = ?,
                        extraction_source = ?,
                        ai_attempts = ?,
                        updated_at = ?
                    WHERE id = ? AND status IN ('queued', 'processing')
                """
                params = (
                    error_code.value,
                    source,
                    ai_attempts,
                    now,
                    request_id,
                )
            elif source is not None:
                sql = """
                    UPDATE requests
                    SET status = 'failed',
                        error_code = ?,
                        extraction_source = ?,
                        updated_at = ?
                    WHERE id = ? AND status IN ('queued', 'processing')
                """
                params = (error_code.value, source, now, request_id)
            elif ai_attempts:
                sql = """
                    UPDATE requests
                    SET status = 'failed',
                        error_code = ?,
                        ai_attempts = ?,
                        updated_at = ?
                    WHERE id = ? AND status IN ('queued', 'processing')
                """
                params = (error_code.value, ai_attempts, now, request_id)
            else:
                sql = """
                    UPDATE requests
                    SET status = 'failed',
                        error_code = ?,
                        updated_at = ?
                    WHERE id = ? AND status IN ('queued', 'processing')
                """
                params = (error_code.value, now, request_id)

            cursor = await db.execute(sql, params)
            changed = cursor.rowcount > 0
            await cursor.close()
            await db.commit()
            return changed

    async def stats(self) -> dict[str, int]:
        async with self._lock:
            db = await self._connection()
            cursor = await db.execute(
                "SELECT status, COUNT(*) AS count FROM requests GROUP BY status"
            )
            rows = await cursor.fetchall()
            await cursor.close()
            counts = {status.value: 0 for status in RequestStatus}
            for row in rows:
                counts[row["status"]] = int(row["count"])
            return counts

    async def recover_pending(self) -> list[tuple[str, str]]:
        """Return in-flight requests and reset their delivery claim.

        Extraction requests are returned as ("id", "extract") and requests
        that already have a notification as ("id", "deliver"). Delivery claims
        are reset, so a crash between notify and mark_sent can cause a retry.
        That is the documented at-least-once trade-off.
        """

        async with self._lock:
            db = await self._connection()
            now = utc_now().isoformat()
            await db.execute(
                """
                UPDATE requests
                SET delivery_claimed = 0, updated_at = ?
                WHERE status = 'processing'
                """,
                (now,),
            )
            cursor = await db.execute(
                """
                SELECT id, notification_json
                FROM requests
                WHERE status = 'processing'
                ORDER BY created_at
                """
            )
            rows = await cursor.fetchall()
            await cursor.close()
            await db.commit()

            return [
                (
                    row["id"],
                    "deliver" if row["notification_json"] else "extract",
                )
                for row in rows
            ]

    async def _get_by_idempotency_key(
        self, key: str
    ) -> RequestRecord | None:
        db = await self._connection()
        cursor = await db.execute(
            "SELECT * FROM requests WHERE idempotency_key = ?",
            (key,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return self._row_to_record(row) if row is not None else None

    async def _get_row(
        self, db: aiosqlite.Connection, request_id: str
    ) -> aiosqlite.Row | None:
        cursor = await db.execute(
            "SELECT * FROM requests WHERE id = ?",
            (request_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row

    @staticmethod
    def _notification_json(notification: Notification) -> str:
        return json.dumps(
            {
                "to": notification.to,
                "message": notification.message,
                "type": notification.type.value,
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _notification_from_json(value: str) -> Notification:
        data = json.loads(value)
        return Notification(
            to=data["to"],
            message=data["message"],
            type=NotificationType(data["type"]),
        )

    @staticmethod
    def _row_to_record(row: aiosqlite.Row) -> RequestRecord:
        notification = None
        if row["notification_json"]:
            notification = SQLiteRequestRepository._notification_from_json(
                row["notification_json"]
            )

        error_code = (
            ErrorCode(row["error_code"]) if row["error_code"] else None
        )
        return RequestRecord(
            id=row["id"],
            user_input=row["user_input"],
            status=RequestStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            notification=notification,
            provider_id=row["provider_id"],
            error_code=error_code,
            extraction_source=row["extraction_source"],
            ai_attempts=int(row["ai_attempts"]),
            delivery_claimed=bool(row["delivery_claimed"]),
            idempotency_key=row["idempotency_key"],
        )
