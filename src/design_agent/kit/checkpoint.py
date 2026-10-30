"""Thread checkpoints: each thread's agent state (its messages and middleware fields) between runs.

The agent saves a thread's state after every step, so a crash loses at most the step in flight.
States are pickled: they hold message models and middleware values, written and read only by this
process's own code.
"""

from __future__ import annotations

import asyncio
import pickle
import sqlite3
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any


class BaseCheckpointSaver:
    async def aget(self, thread_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    async def aput(self, thread_id: str, state: dict[str, Any]) -> None:
        raise NotImplementedError

    async def adelete_thread(self, thread_id: str) -> None:
        raise NotImplementedError

    async def setup(self) -> None:
        return None


Checkpointer = BaseCheckpointSaver


class InMemorySaver(BaseCheckpointSaver):
    def __init__(self) -> None:
        self._threads: dict[str, bytes] = {}

    async def aget(self, thread_id: str) -> dict[str, Any] | None:
        blob = self._threads.get(thread_id)
        return pickle.loads(blob) if blob is not None else None  # noqa: S301 - our own data

    async def aput(self, thread_id: str, state: dict[str, Any]) -> None:
        self._threads[thread_id] = pickle.dumps(state)

    async def adelete_thread(self, thread_id: str) -> None:
        self._threads.pop(thread_id, None)


MemorySaver = InMemorySaver


class AsyncSqliteSaver(BaseCheckpointSaver):
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.Lock()

    @classmethod
    @asynccontextmanager
    async def from_conn_string(cls, path: str) -> AsyncIterator[AsyncSqliteSaver]:
        conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        saver = cls(conn)
        try:
            await saver.setup()
            yield saver
        finally:
            conn.close()

    async def _exec(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        def run() -> list[tuple[Any, ...]]:
            with self._lock:
                return list(self._conn.execute(sql, params).fetchall())

        return await asyncio.to_thread(run)

    async def setup(self) -> None:
        await self._exec(
            "CREATE TABLE IF NOT EXISTS threads (thread_id TEXT PRIMARY KEY, state BLOB NOT NULL)"
        )

    async def aget(self, thread_id: str) -> dict[str, Any] | None:
        rows = await self._exec("SELECT state FROM threads WHERE thread_id=?", (thread_id,))
        return pickle.loads(rows[0][0]) if rows else None  # noqa: S301 - our own data

    async def aput(self, thread_id: str, state: dict[str, Any]) -> None:
        await self._exec(
            "INSERT INTO threads VALUES (?, ?) ON CONFLICT(thread_id) DO UPDATE SET state=excluded.state",
            (thread_id, pickle.dumps(state)),
        )

    async def adelete_thread(self, thread_id: str) -> None:
        await self._exec("DELETE FROM threads WHERE thread_id=?", (thread_id,))


class AsyncPostgresSaver(BaseCheckpointSaver):
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    @classmethod
    @asynccontextmanager
    async def from_conn_string(cls, url: str) -> AsyncIterator[AsyncPostgresSaver]:
        from psycopg_pool import AsyncConnectionPool

        async with AsyncConnectionPool(url, open=False, kwargs={"autocommit": True}) as pool:
            await pool.open()
            yield cls(pool)

    async def _exec(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(sql, params)
            return list(await cur.fetchall()) if cur.description else []

    async def setup(self) -> None:
        await self._exec(
            "CREATE TABLE IF NOT EXISTS design_agent_threads (thread_id TEXT PRIMARY KEY, "
            "state BYTEA NOT NULL)"
        )

    async def aget(self, thread_id: str) -> dict[str, Any] | None:
        rows = await self._exec(
            "SELECT state FROM design_agent_threads WHERE thread_id=%s", (thread_id,)
        )
        return pickle.loads(rows[0][0]) if rows else None  # noqa: S301 - our own data

    async def aput(self, thread_id: str, state: dict[str, Any]) -> None:
        await self._exec(
            "INSERT INTO design_agent_threads VALUES (%s, %s) ON CONFLICT (thread_id) DO UPDATE "
            "SET state=excluded.state",
            (thread_id, pickle.dumps(state)),
        )

    async def adelete_thread(self, thread_id: str) -> None:
        await self._exec("DELETE FROM design_agent_threads WHERE thread_id=%s", (thread_id,))
