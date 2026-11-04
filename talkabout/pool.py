"""Process-local PostgreSQL connection reuse.

Each request previously opened a brand-new connection (TCP + SCRAM handshake) and
closed it immediately; one authenticated request could open four, and a single SSE
refresh cycle about two dozen. This module keeps a small bounded set of connections
alive and hands them out one at a time.

Design notes (independent implementation, no third-party pool code):

* A checkout behaves like ``with psycopg.connect(...) as conn``: commit on success,
  rollback on failure, and the connection returns to the pool instead of the server.
* A connection that is closed, broken, or left outside ``IDLE`` is discarded, never
  reused. Session state that outlives a transaction (``LISTEN``, advisory locks held
  for the session) must use the non-pooled path -- see ``PostgresStore.listener``.
* The pool is bounded: at most ``max_size`` live connections. Waiters block up to
  ``acquire_timeout`` and then fail closed instead of opening unbounded sockets.
"""
from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager


class PoolExhausted(RuntimeError):
    """No connection became available inside the acquire timeout."""


class PoolClosed(RuntimeError):
    """The pool was closed while a caller was waiting for a connection."""


def _safe_rollback(connection):
    try:
        connection.rollback()
    except Exception:
        return False
    return True


def _is_broken(connection):
    if getattr(connection, "closed", False):
        return True
    if getattr(connection, "broken", False):
        return True
    try:
        status = connection.info.transaction_status
    except Exception:
        return True
    # psycopg.pq.TransactionStatus.IDLE == 0. Anything else (ACTIVE, INTRANS,
    # INERROR, UNKNOWN) must not be handed to the next caller.
    return status != 0


class ConnectionPool:
    def __init__(self, factory, max_size=16, acquire_timeout=5.0):
        self._factory = factory
        self._max_size = max(1, int(max_size))
        self._acquire_timeout = max(0.1, float(acquire_timeout))
        self._free = []
        self._checked_out = 0
        self._closed = False
        self._lock = threading.Lock()
        self._available = threading.Condition(self._lock)
        self._stats = {"created": 0, "reused": 0, "discarded": 0, "waited": 0}

    @classmethod
    def from_environment(cls, factory):
        size = os.environ.get("TALKABOUT_DB_POOL_SIZE", "16")
        try:
            size = int(size)
        except ValueError:
            size = 16
        timeout = os.environ.get("TALKABOUT_DB_POOL_TIMEOUT", "5")
        try:
            timeout = float(timeout)
        except ValueError:
            timeout = 5.0
        return cls(factory, max_size=size, acquire_timeout=timeout)

    def stats(self):
        with self._lock:
            return {**self._stats, "free": len(self._free), "checked_out": self._checked_out,
                    "max_size": self._max_size}

    def _acquire(self):
        deadline = time.monotonic() + self._acquire_timeout
        with self._available:
            while True:
                if self._closed:
                    raise PoolClosed("connection_pool_closed")
                if self._free:
                    connection = self._free.pop()
                    self._checked_out += 1
                    self._stats["reused"] += 1
                    if _is_broken(connection):
                        self._checked_out -= 1
                        self._stats["discarded"] += 1
                        try:
                            connection.close()
                        except Exception:
                            pass
                        continue
                    return connection
                if self._checked_out < self._max_size:
                    self._checked_out += 1
                    try:
                        connection = self._factory()
                    except BaseException:
                        self._checked_out -= 1
                        self._available.notify()
                        raise
                    self._stats["created"] += 1
                    return connection
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PoolExhausted("connection_pool_exhausted")
                self._stats["waited"] += 1
                self._available.wait(remaining)

    def _release(self, connection):
        broken = _is_broken(connection)
        with self._available:
            self._checked_out -= 1
            if broken or self._closed:
                self._stats["discarded"] += 1
            else:
                self._free.append(connection)
            self._available.notify()
        if broken or self._closed:
            try:
                connection.close()
            except Exception:
                pass

    @contextmanager
    def connection(self, autocommit=False):
        connection = self._acquire()
        try:
            if bool(getattr(connection, "autocommit", False)) != bool(autocommit):
                connection.autocommit = bool(autocommit)
            try:
                yield connection
                if not autocommit:
                    connection.commit()
            except BaseException:
                _safe_rollback(connection)
                raise
        finally:
            self._release(connection)

    def close(self):
        with self._available:
            self._closed = True
            free, self._free = self._free, []
            self._available.notify_all()
        for connection in free:
            try:
                connection.close()
            except Exception:
                pass
