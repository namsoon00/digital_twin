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
from typing import Callable, Dict, Tuple


def mysql_pool_size(settings: Dict[str, object] = None) -> int:
    try:
        parsed = int(float(str((settings or {}).get("mysqlConnectionPoolSize") or "4").strip()))
    except (TypeError, ValueError):
        parsed = 4
    return max(1, min(16, parsed))


class MySQLConnectionPool:
    def __init__(self, factory: Callable[[bool], object], size: int):
        self.factory = factory
        self.size = max(1, int(size or 1))
        self.idle = queue.LifoQueue(maxsize=self.size)
        self.created = 0
        self.lock = threading.Lock()

    def acquire(self, autocommit: bool = True):
        connection = None
        try:
            connection = self.idle.get_nowait()
        except queue.Empty:
            with self.lock:
                if self.created < self.size:
                    self.created += 1
                    create = True
                else:
                    create = False
            if create:
                try:
                    connection = self.factory(autocommit)
                except Exception:
                    with self.lock:
                        self.created = max(0, self.created - 1)
                    raise
            else:
                connection = self.idle.get(timeout=10)
        try:
            connection.ping(reconnect=False)
            connection.autocommit(bool(autocommit))
            return connection
        except Exception:
            self.discard(connection)
            return self.acquire(autocommit=autocommit)

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
            self.idle.put_nowait(connection)
        except queue.Full:
            self.discard(connection)

    def discard(self, connection) -> None:
        try:
            if connection is not None:
                connection.close()
        finally:
            with self.lock:
                self.created = max(0, self.created - 1)


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
