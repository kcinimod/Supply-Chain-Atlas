"""Postgres connection management.

v2: a process-wide psycopg connection pool replaces v1's
connection-per-statement pattern (which paid a TCP+auth handshake per row
during backfills). The public API is unchanged — ``with connection() as conn``
— so every store module keeps working; they just borrow from the pool now.

The pool is created lazily on first use so that merely importing this module
(e.g. during tests with no database) never opens sockets.
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager

from psycopg_pool import ConnectionPool

_lock = threading.Lock()
_pool: ConnectionPool | None = None


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        # Every DB consumer funnels through here, so loading .env once at the
        # choke point spares each entrypoint from remembering load_dotenv().
        from dotenv import load_dotenv
        load_dotenv()
        dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set (see .env.example)")
    return dsn


def get_pool() -> ConnectionPool:
    """Return the process-wide pool, creating it on first use."""
    global _pool
    if _pool is None:
        with _lock:
            if _pool is None:
                _pool = ConnectionPool(
                    _dsn(),
                    min_size=int(os.environ.get("DB_POOL_MIN", "1")),
                    max_size=int(os.environ.get("DB_POOL_MAX", "8")),
                    # Fail fast instead of queueing forever if the DB is down.
                    timeout=30,
                    name="atlas",
                    open=True,
                )
    return _pool


def close_pool() -> None:
    """Close the pool (used by tests and short-lived CLI exits)."""
    global _pool
    with _lock:
        if _pool is not None:
            _pool.close()
            _pool = None


@contextmanager
def connection(*, readonly: bool = False):
    """Borrow a pooled connection that commits on success, rolls back on error.

    Same contract as v1: read-only connections can never mutate the warehouse.
    """
    pool = get_pool()
    with pool.connection() as conn:
        # pool.connection() commits/rolls back and returns the conn to the
        # pool on exit; we add the read-only guard on top.
        if readonly:
            conn.execute("SET TRANSACTION READ ONLY")
        yield conn
