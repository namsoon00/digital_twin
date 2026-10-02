"""Small process-local MySQL connection pool.

The service has several long-running workers. Opening a TCP connection for
every repository call caused thousands of handshakes per hour even though each
worker is mostly single threaded. This pool is deliberately process-local so
connections are never shared across a fork or between service boundaries.
"""

from __future__ import annotations

import os
import queue
import threading
import time
from typing import Callable, Dict, Tuple


def mysql_pool_size(settings: Dict[str, object] = None) -> int:
    try:
        parsed = int(float(str((settings or {}).get("mysqlConnectionPoolSize") or "2").strip()))
    except (TypeError, ValueError, OverflowError):
        parsed = 2
    return max(1, min(16, parsed))


class MySQLPoolTimeout(TimeoutError):
    """Safe capacity diagnostics without connection credentials or SQL."""

    def __init__(self, size, created, waited_seconds):
        self.pool_size = size
        self.created_connections = created
        self.waited_seconds = waited_seconds
        super().__init__(f"MySQL pool wait exhausted: size={size}, created={created}")


class MySQLPoolUnavailable(ConnectionError):
    pass


class MySQLConnectionPool:
    def __init__(self, factory: Callable[[bool], object], size: int, acquire_timeout: float = 10):
        self.factory = factory
        self.size = max(1, int(size or 1))
        self.idle = queue.LifoQueue(maxsize=self.size)
        self.created = 0
        self.lock = threading.Lock()
        self.available = threading.Condition(self.lock)
        self.acquire_timeout = max(0, float(acquire_timeout))

    def acquire(self, autocommit: bool = True):
        started = time.monotonic()
        deadline = started + self.acquire_timeout
        invalid = 0
        while True:
            with self.available:
                try:
                    connection = self.idle.get_nowait()
                    create = False
                except queue.Empty:
                    if self.created < self.size:
                        self.created += 1
                        create = True
                    else:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise MySQLPoolTimeout(self.size, self.created, time.monotonic() - started)
                        # Both a returned connection and a freed creation slot
                        # wake waiters. Queue.get alone misses discarded sockets.
                        self.available.wait(remaining)
                        continue
            if create:
                try:
                    connection = self.factory(autocommit)
                except BaseException:
                    with self.available:
                        self.created = max(0, self.created - 1)
                        self.available.notify()
                    raise
            try:
                connection.ping(reconnect=False)
                connection.autocommit(bool(autocommit))
                return connection
            except Exception as error:
                self.discard(connection)
                invalid += 1
                if invalid >= 3 or time.monotonic() >= deadline:
                    raise MySQLPoolUnavailable("MySQL connection validation failed after bounded replacement") from error

    def release(self, connection) -> None:
        if connection is None:
            return
        try:
            connection.rollback()
            connection.ping(reconnect=False)
        except Exception:
            self.discard(connection)
            return
        try:
            with self.available:
                self.idle.put_nowait(connection)
                self.available.notify()
        except queue.Full:
            self.discard(connection)

    def discard(self, connection) -> None:
        try:
            if connection is not None:
                connection.close()
        except Exception:
            pass  # A failed socket close must still release its capacity slot.
        finally:
            with self.available:
                self.created = max(0, self.created - 1)
                self.available.notify()


_POOLS: Dict[Tuple[object, ...], MySQLConnectionPool] = {}
_POOLS_LOCK = threading.Lock()


def pooled_mysql_connection(
    key: Tuple[object, ...],
    factory: Callable[[bool], object],
    autocommit: bool,
    settings: Dict[str, object] = None,
):
    process_key = (os.getpid(),) + tuple(key)
    with _POOLS_LOCK:
        pool = _POOLS.get(process_key)
        if pool is None:
            pool = MySQLConnectionPool(factory, mysql_pool_size(settings))
            _POOLS[process_key] = pool
    return pool.acquire(autocommit=autocommit), pool.release
