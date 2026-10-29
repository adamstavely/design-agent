"""A namespaced key-value store for what outlives a run: ledgers, transcripts, check-ins, memos.

Items live under a namespace tuple and a key; `asearch(prefix)` lists the items whose namespace
starts with `prefix`, newest first, optionally matching `filter` on top-level value fields. A `ttl`
(minutes) lets an item expire. Three backings with one interface: memory (tests and one-shot
runs), SQLite (the CLI and a single machine) and Postgres (self-hosted deployments).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

Namespace = tuple[str, ...]


@dataclass
class Item:
    namespace: Namespace
    key: str
    value: dict[str, Any]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    score: float | None = None


SearchItem = Item


@dataclass(frozen=True)
class PutOp:
    """One write in `abatch`; `value=None` deletes."""

    namespace: Namespace
    key: str
    value: dict[str, Any] | None
    ttl: float | None = None


@dataclass(frozen=True)
class GetOp:
    namespace: Namespace
    key: str


def _matches(value: Mapping[str, Any], filter_: Mapping[str, Any] | None) -> bool:
    return all(value.get(k) == v for k, v in (filter_ or {}).items())


class BaseStore:
    """The async interface. Subclasses implement the five `_` methods."""

    supports_ttl = True

    async def aget(self, namespace: Sequence[str], key: str, **_: Any) -> Item | None:
        return await self._get(tuple(namespace), str(key))

    async def aput(
        self,
        namespace: Sequence[str],
        key: str,
        value: Mapping[str, Any],
        index: Any = None,
        *,
        ttl: float | None = None,
    ) -> None:
        await self._put(tuple(namespace), str(key), dict(value), ttl)

    async def adelete(self, namespace: Sequence[str], key: str) -> None:
        await self._delete(tuple(namespace), str(key))

    async def asearch(
        self,
        namespace_prefix: Sequence[str],
        /,
        *,
        query: str | None = None,
        filter: Mapping[str, Any] | None = None,
        limit: int = 10,
        offset: int = 0,
        **_: Any,
    ) -> list[Item]:
        items = [
            i for i in await self._search(tuple(namespace_prefix)) if _matches(i.value, filter)
        ]
        items.sort(key=lambda i: i.updated_at, reverse=True)
        return items[offset : offset + limit]

    async def alist_namespaces(
        self, *, prefix: Sequence[str] = (), limit: int = 100, **_: Any
    ) -> list[Namespace]:
        found = {i.namespace for i in await self._search(tuple(prefix))}
        return sorted(found)[:limit]

    async def abatch(self, ops: Iterable[Any]) -> list[Any]:
        out: list[Any] = []
        for op in ops:
            if isinstance(op, PutOp):
                if op.value is None:
                    await self._delete(tuple(op.namespace), op.key)
                else:
                    await self._put(tuple(op.namespace), op.key, dict(op.value), op.ttl)
                out.append(None)
            elif isinstance(op, GetOp):
                out.append(await self._get(tuple(op.namespace), op.key))
            else:
                raise TypeError(f"unsupported store op {type(op).__name__}")
        return out

    async def setup(self) -> None:
        return None

    # -- sync conveniences ---------------------------------------------------------------------

    def get(self, namespace: Sequence[str], key: str) -> Item | None:
        found: Item | None = _run(self.aget(namespace, key))
        return found

    def put(self, namespace: Sequence[str], key: str, value: Mapping[str, Any], **kw: Any) -> None:
        _run(self.aput(namespace, key, value, **kw))

    def delete(self, namespace: Sequence[str], key: str) -> None:
        _run(self.adelete(namespace, key))

    def search(self, namespace_prefix: Sequence[str], /, **kw: Any) -> list[Item]:
        found: list[Item] = _run(self.asearch(namespace_prefix, **kw))
        return found

    # -- to implement --------------------------------------------------------------------------

    async def _get(self, namespace: Namespace, key: str) -> Item | None:
        raise NotImplementedError

    async def _put(
        self, namespace: Namespace, key: str, value: dict[str, Any], ttl: float | None
    ) -> None:
        raise NotImplementedError

    async def _delete(self, namespace: Namespace, key: str) -> None:
        raise NotImplementedError

    async def _search(self, prefix: Namespace) -> list[Item]:
        raise NotImplementedError


def _run(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("use the async store methods inside a running event loop")


class InMemoryStore(BaseStore):
    def __init__(self, **_: Any) -> None:
        self._data: dict[tuple[Namespace, str], tuple[Item, float | None]] = {}
        self._clock = time.time

    def _live(self, entry: tuple[Item, float | None]) -> bool:
        return entry[1] is None or entry[1] > self._clock()

    async def _get(self, namespace: Namespace, key: str) -> Item | None:
        entry = self._data.get((namespace, key))
        return entry[0] if entry is not None and self._live(entry) else None

    async def _put(
        self, namespace: Namespace, key: str, value: dict[str, Any], ttl: float | None
    ) -> None:
        existing = self._data.get((namespace, key))
        now = datetime.now(UTC)
        created = existing[0].created_at if existing else now
        expires = self._clock() + ttl * 60 if ttl else None
        self._data[(namespace, key)] = (Item(namespace, key, value, created, now), expires)

    async def _delete(self, namespace: Namespace, key: str) -> None:
        self._data.pop((namespace, key), None)

    async def _search(self, prefix: Namespace) -> list[Item]:
        return [
            entry[0]
            for (ns, _), entry in list(self._data.items())
            if ns[: len(prefix)] == prefix and self._live(entry)
        ]


# -- SQL backings -----------------------------------------------------------------------------

_SEP = "\x1f"


def _ns_text(namespace: Namespace) -> str:
    return _SEP.join(namespace)


def _ns_tuple(text: str) -> Namespace:
    return tuple(text.split(_SEP)) if text else ()


class AsyncSqliteStore(BaseStore):
    """SQLite, through the standard library on a worker thread."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.Lock()

    @classmethod
    @asynccontextmanager
    async def from_conn_string(cls, path: str) -> AsyncIterator[AsyncSqliteStore]:
        conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        store = cls(conn)
        try:
            await store.setup()
            yield store
        finally:
            conn.close()

    async def _exec(self, sql: str, params: Sequence[Any] = ()) -> list[tuple[Any, ...]]:
        def run() -> list[tuple[Any, ...]]:
            with self._lock:
                return list(self._conn.execute(sql, params).fetchall())

        return await asyncio.to_thread(run)

    async def setup(self) -> None:
        await self._exec(
            "CREATE TABLE IF NOT EXISTS store (namespace TEXT NOT NULL, key TEXT NOT NULL, "
            "value TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL, "
            "expires_at REAL, PRIMARY KEY (namespace, key))"
        )

    def _item(self, row: tuple[Any, ...]) -> Item:
        ns, key, value, created, updated = row[:5]
        return Item(
            _ns_tuple(ns),
            key,
            json.loads(value),
            datetime.fromtimestamp(created, UTC),
            datetime.fromtimestamp(updated, UTC),
        )

    async def _get(self, namespace: Namespace, key: str) -> Item | None:
        rows = await self._exec(
            "SELECT namespace, key, value, created_at, updated_at FROM store WHERE namespace=? "
            "AND key=? AND (expires_at IS NULL OR expires_at > ?)",
            (_ns_text(namespace), key, time.time()),
        )
        return self._item(rows[0]) if rows else None

    async def _put(
        self, namespace: Namespace, key: str, value: dict[str, Any], ttl: float | None
    ) -> None:
        now = time.time()
        await self._exec(
            "INSERT INTO store VALUES (?,?,?,?,?,?) ON CONFLICT(namespace, key) DO UPDATE SET "
            "value=excluded.value, updated_at=excluded.updated_at, expires_at=excluded.expires_at",
            (
                _ns_text(namespace),
                key,
                json.dumps(value, default=str),
                now,
                now,
                now + ttl * 60 if ttl else None,
            ),
        )

    async def _delete(self, namespace: Namespace, key: str) -> None:
        await self._exec(
            "DELETE FROM store WHERE namespace=? AND key=?", (_ns_text(namespace), key)
        )

    async def _search(self, prefix: Namespace) -> list[Item]:
        text = _ns_text(prefix)
        rows = await self._exec(
            "SELECT namespace, key, value, created_at, updated_at FROM store WHERE "
            "(?='' OR namespace=? OR substr(namespace, 1, ?)=?) AND "
            "(expires_at IS NULL OR expires_at > ?)",
            (text, text, len(text) + 1, text + _SEP, time.time()),
        )
        return [self._item(r) for r in rows]


class AsyncPostgresStore(BaseStore):
    """Postgres, through psycopg's async pool (the `self-host` extra)."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    @classmethod
    @asynccontextmanager
    async def from_conn_string(cls, url: str) -> AsyncIterator[AsyncPostgresStore]:
        from psycopg_pool import AsyncConnectionPool

        async with AsyncConnectionPool(url, open=False, kwargs={"autocommit": True}) as pool:
            await pool.open()
            yield cls(pool)

    async def _exec(self, sql: str, params: Sequence[Any] = ()) -> list[tuple[Any, ...]]:
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(sql, params)
            return list(await cur.fetchall()) if cur.description else []

    async def setup(self) -> None:
        await self._exec(
            "CREATE TABLE IF NOT EXISTS design_agent_store (namespace TEXT NOT NULL, key TEXT NOT "
            "NULL, value JSONB NOT NULL, created_at DOUBLE PRECISION NOT NULL, updated_at DOUBLE "
            "PRECISION NOT NULL, expires_at DOUBLE PRECISION, PRIMARY KEY (namespace, key))"
        )

    def _item(self, row: tuple[Any, ...]) -> Item:
        ns, key, value, created, updated = row[:5]
        return Item(
            _ns_tuple(ns),
            key,
            value if isinstance(value, dict) else json.loads(value),
            datetime.fromtimestamp(created, UTC),
            datetime.fromtimestamp(updated, UTC),
        )

    async def _get(self, namespace: Namespace, key: str) -> Item | None:
        rows = await self._exec(
            "SELECT namespace, key, value, created_at, updated_at FROM design_agent_store WHERE "
            "namespace=%s AND key=%s AND (expires_at IS NULL OR expires_at > %s)",
            (_ns_text(namespace), key, time.time()),
        )
        return self._item(rows[0]) if rows else None

    async def _put(
        self, namespace: Namespace, key: str, value: dict[str, Any], ttl: float | None
    ) -> None:
        now = time.time()
        await self._exec(
            "INSERT INTO design_agent_store VALUES (%s,%s,%s::jsonb,%s,%s,%s) ON CONFLICT "
            "(namespace, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, "
            "expires_at=excluded.expires_at",
            (
                _ns_text(namespace),
                key,
                json.dumps(value, default=str),
                now,
                now,
                now + ttl * 60 if ttl else None,
            ),
        )

    async def _delete(self, namespace: Namespace, key: str) -> None:
        await self._exec(
            "DELETE FROM design_agent_store WHERE namespace=%s AND key=%s",
            (_ns_text(namespace), key),
        )

    async def _search(self, prefix: Namespace) -> list[Item]:
        text = _ns_text(prefix)
        rows = await self._exec(
            "SELECT namespace, key, value, created_at, updated_at FROM design_agent_store WHERE "
            "(%s = '' OR namespace = %s OR namespace LIKE %s) AND (expires_at IS NULL OR "
            "expires_at > %s)",
            (text, text, text.replace("%", r"\%").replace("_", r"\_") + _SEP + "%", time.time()),
        )
        return [self._item(r) for r in rows]
