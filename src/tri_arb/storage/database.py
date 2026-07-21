"""Versioned SQLite WAL store with a single serialized async writer."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import (
    BigInteger,
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    delete,
    event,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tri_arb.scanner.lifecycle import CloseReason, LifecycleEvent, LifecycleEventType
from tri_arb.storage.codec import serialize_lifecycle

DATABASE_SCHEMA_VERSION = 1

metadata = MetaData()
schema_version = Table(
    "schema_version",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("version", Integer, nullable=False),
)
lifecycles = Table(
    "opportunity_lifecycles",
    metadata,
    Column("lifecycle_id", String(36), primary_key=True),
    Column("route_id", Text, nullable=False),
    Column("state", String(16), nullable=False),
    Column("first_seen_ms", BigInteger, nullable=False),
    Column("last_confirmed_ms", BigInteger, nullable=False),
    Column("peak_net_return_bps", Text, nullable=False),
    Column("closed_at_ms", BigInteger),
    Column("close_reason", String(64)),
    Column("snapshot_json", Text, nullable=False),
)
Index("ix_lifecycles_state_last", lifecycles.c.state, lifecycles.c.last_confirmed_ms)
Index("ix_lifecycles_route_last", lifecycles.c.route_id, lifecycles.c.last_confirmed_ms)
events = Table(
    "opportunity_events",
    metadata,
    Column("event_id", Integer, primary_key=True, autoincrement=True),
    Column(
        "lifecycle_id",
        String(36),
        ForeignKey("opportunity_lifecycles.lifecycle_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("event_type", String(16), nullable=False),
    Column("occurred_at_ms", BigInteger, nullable=False),
    Column("snapshot_json", Text, nullable=False),
    UniqueConstraint("lifecycle_id", "event_type", "occurred_at_ms", name="uq_event_identity"),
)
Index("ix_events_lifecycle_time", events.c.lifecycle_id, events.c.occurred_at_ms)


@dataclass(frozen=True, slots=True)
class StoredLifecycle:
    lifecycle_id: str
    route_id: str
    state: str
    first_seen_ms: int
    last_confirmed_ms: int
    peak_net_return_bps: str
    closed_at_ms: int | None
    close_reason: str | None
    snapshot_json: str


@dataclass(frozen=True, slots=True)
class StoredEvent:
    event_id: int
    lifecycle_id: str
    event_type: str
    occurred_at_ms: int
    snapshot_json: str


@dataclass(slots=True)
class _Command:
    kind: str
    payload: Any
    future: asyncio.Future[Any]


_STOP = object()


class OpportunityStore:
    def __init__(self, database_url: str, *, queue_size: int = 1_000) -> None:
        if not database_url.startswith("sqlite+aiosqlite://"):
            raise ValueError("v0.1 opportunity storage requires sqlite+aiosqlite")
        if queue_size <= 0:
            raise ValueError("storage queue size must be positive")
        self._engine: AsyncEngine = create_async_engine(database_url)
        self._queue: asyncio.Queue[_Command | object] = asyncio.Queue(maxsize=queue_size)
        self._worker: asyncio.Task[None] | None = None
        self._started = False

        @event.listens_for(self._engine.sync_engine, "connect")
        def _configure_sqlite(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    async def _initialize_schema(self) -> None:
        async with self._engine.begin() as connection:
            await connection.run_sync(schema_version.create, checkfirst=True)
            version = await connection.scalar(
                select(schema_version.c.version).where(schema_version.c.id == 1)
            )
            if version is None:
                await connection.execute(
                    insert(schema_version).values(id=1, version=DATABASE_SCHEMA_VERSION)
                )
            elif version != DATABASE_SCHEMA_VERSION:
                raise RuntimeError(
                    f"unsupported database schema version {version}; "
                    f"expected {DATABASE_SCHEMA_VERSION}"
                )
            await connection.run_sync(metadata.create_all)

    async def start(self, *, started_at_ms: int | None = None) -> int:
        if self._started:
            raise RuntimeError("opportunity store is already started")
        try:
            await self._initialize_schema()
        except Exception:
            await self._engine.dispose()
            raise
        self._started = True
        self._worker = asyncio.create_task(self._writer(), name="tri-arb-sqlite-writer")
        occurred_at_ms = time.time_ns() // 1_000_000 if started_at_ms is None else started_at_ms
        try:
            return await self.close_active_for_restart(occurred_at_ms)
        except Exception:
            await self.stop()
            raise

    async def _submit(self, kind: str, payload: Any) -> Any:
        if not self._started or self._worker is None:
            raise RuntimeError("opportunity store is not started")
        future = asyncio.get_running_loop().create_future()
        await self._queue.put(_Command(kind, payload, future))
        return await future

    async def record_events(self, lifecycle_events: tuple[LifecycleEvent, ...]) -> None:
        if lifecycle_events:
            await self._submit("record", lifecycle_events)

    async def close_active_for_restart(self, occurred_at_ms: int) -> int:
        if occurred_at_ms <= 0:
            raise ValueError("restart close time must be positive")
        return await self._submit("restart", occurred_at_ms)

    async def cleanup(self, cutoff_ms: int) -> int:
        if cutoff_ms <= 0:
            raise ValueError("cleanup cutoff must be positive")
        return await self._submit("cleanup", cutoff_ms)

    async def _record(self, lifecycle_events: tuple[LifecycleEvent, ...]) -> None:
        async with self._engine.begin() as connection:
            for lifecycle_event in lifecycle_events:
                lifecycle = lifecycle_event.lifecycle
                if lifecycle_event.occurred_at_ms <= 0:
                    raise ValueError("lifecycle event time must be positive")
                if (lifecycle_event.event_type is LifecycleEventType.CLOSED) != (
                    not lifecycle.active
                ):
                    raise ValueError("lifecycle event type does not match lifecycle state")
                snapshot = serialize_lifecycle(lifecycle)
                state = "active" if lifecycle.active else "closed"
                statement = sqlite_insert(lifecycles).values(
                    lifecycle_id=lifecycle.lifecycle_id,
                    route_id=lifecycle.route_id,
                    state=state,
                    first_seen_ms=lifecycle.first_seen_ms,
                    last_confirmed_ms=lifecycle.last_confirmed_ms,
                    peak_net_return_bps=str(lifecycle.peak_net_return_bps),
                    closed_at_ms=lifecycle.closed_at_ms,
                    close_reason=lifecycle.close_reason.value if lifecycle.close_reason else None,
                    snapshot_json=snapshot,
                )
                statement = statement.on_conflict_do_update(
                    index_elements=[lifecycles.c.lifecycle_id],
                    set_={
                        "state": statement.excluded.state,
                        "last_confirmed_ms": statement.excluded.last_confirmed_ms,
                        "peak_net_return_bps": statement.excluded.peak_net_return_bps,
                        "closed_at_ms": statement.excluded.closed_at_ms,
                        "close_reason": statement.excluded.close_reason,
                        "snapshot_json": statement.excluded.snapshot_json,
                    },
                )
                await connection.execute(statement)
                await connection.execute(
                    sqlite_insert(events)
                    .values(
                        lifecycle_id=lifecycle.lifecycle_id,
                        event_type=lifecycle_event.event_type.value,
                        occurred_at_ms=lifecycle_event.occurred_at_ms,
                        snapshot_json=snapshot,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[
                            events.c.lifecycle_id,
                            events.c.event_type,
                            events.c.occurred_at_ms,
                        ]
                    )
                )

    async def _restart(self, occurred_at_ms: int) -> int:
        async with self._engine.begin() as connection:
            rows = (
                await connection.execute(
                    select(
                        lifecycles.c.lifecycle_id,
                        lifecycles.c.last_confirmed_ms,
                        lifecycles.c.snapshot_json,
                    ).where(lifecycles.c.state == "active")
                )
            ).all()
            if any(last_confirmed_ms > occurred_at_ms for _, last_confirmed_ms, _ in rows):
                raise ValueError("restart close time precedes an active lifecycle")
            for lifecycle_id, _last_confirmed_ms, snapshot_json in rows:
                payload = json.loads(snapshot_json)
                payload["lifecycle"]["closed_at_ms"] = occurred_at_ms
                payload["lifecycle"]["close_reason"] = CloseReason.PROCESS_RESTART.value
                closed_snapshot = json.dumps(payload, sort_keys=True, separators=(",", ":"))
                await connection.execute(
                    update(lifecycles)
                    .where(lifecycles.c.lifecycle_id == lifecycle_id)
                    .values(
                        state="closed",
                        closed_at_ms=occurred_at_ms,
                        close_reason=CloseReason.PROCESS_RESTART.value,
                        snapshot_json=closed_snapshot,
                    )
                )
                await connection.execute(
                    insert(events).values(
                        lifecycle_id=lifecycle_id,
                        event_type=LifecycleEventType.CLOSED.value,
                        occurred_at_ms=occurred_at_ms,
                        snapshot_json=closed_snapshot,
                    )
                )
            return len(rows)

    async def _cleanup(self, cutoff_ms: int) -> int:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                delete(lifecycles).where(
                    lifecycles.c.state == "closed",
                    lifecycles.c.closed_at_ms < cutoff_ms,
                )
            )
            return result.rowcount or 0

    async def _writer(self) -> None:
        while True:
            item = await self._queue.get()
            if item is _STOP:
                self._queue.task_done()
                return
            command = item
            assert isinstance(command, _Command)
            try:
                if command.kind == "record":
                    result = await self._record(command.payload)
                elif command.kind == "restart":
                    result = await self._restart(command.payload)
                elif command.kind == "cleanup":
                    result = await self._cleanup(command.payload)
                else:
                    raise RuntimeError(f"unknown storage command: {command.kind}")
            except Exception as error:
                if not command.future.done():
                    command.future.set_exception(error)
            else:
                if not command.future.done():
                    command.future.set_result(result)
            finally:
                self._queue.task_done()

    async def list_lifecycles(self, *, state: str | None = None) -> tuple[StoredLifecycle, ...]:
        if not self._started:
            raise RuntimeError("opportunity store is not started")
        statement = select(lifecycles).order_by(lifecycles.c.last_confirmed_ms.desc())
        if state is not None:
            if state not in {"active", "closed"}:
                raise ValueError("state must be active or closed")
            statement = statement.where(lifecycles.c.state == state)
        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return tuple(StoredLifecycle(**row) for row in rows)

    async def list_events(self, lifecycle_id: str | None = None) -> tuple[StoredEvent, ...]:
        if not self._started:
            raise RuntimeError("opportunity store is not started")
        statement = select(events).order_by(events.c.event_id)
        if lifecycle_id is not None:
            statement = statement.where(events.c.lifecycle_id == lifecycle_id)
        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return tuple(StoredEvent(**row) for row in rows)

    async def get_lifecycle(self, lifecycle_id: str) -> StoredLifecycle | None:
        if not self._started:
            raise RuntimeError("opportunity store is not started")
        async with self._engine.connect() as connection:
            row = (
                (
                    await connection.execute(
                        select(lifecycles).where(lifecycles.c.lifecycle_id == lifecycle_id)
                    )
                )
                .mappings()
                .one_or_none()
            )
        return StoredLifecycle(**row) if row is not None else None

    async def stop(self) -> None:
        if not self._started or self._worker is None:
            return
        await self._queue.put(_STOP)
        await self._worker
        self._worker = None
        self._started = False
        await self._engine.dispose()
