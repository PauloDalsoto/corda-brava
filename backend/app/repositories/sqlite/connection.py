"""SQLite connection factory for Corda Brava.

Design notes
------------
One connection per thread:
           FastAPI runs sync routes on a thread pool and async routes, plus the consumer loops,
           on the event loop.  A single ``sqlite3.Connection`` shared by those threads is not
           safe even for reads: statements get rebound and reset under each other, so a query
           can return no row, another row, a half-read row or raise ``InterfaceError``.  It
           would also expose one thread's uncommitted writes to the others.

WAL mode:  Every connection immediately executes ``PRAGMA journal_mode=WAL``
           so readers do not block writers and vice versa.

Writers:   Single-writer semantics are enforced via a module-level
           threading.Lock (_WRITE_LOCK).  Readers run concurrently in WAL
           mode; all write operations must acquire the lock before executing.

check_same_thread=False:
           Kept only so ``close_connection`` and the garbage collector can
           close a connection from a thread other than the one that opened it.

Usage:
    conn = get_connection(db_path)
    # reads: no lock needed
    rows = conn.execute("SELECT ...").fetchall()
    # writes: acquire the lock
    with write_lock():
        conn.execute("INSERT INTO ...")
        conn.commit()

Never keep a connection across calls: ask ``get_connection`` from the thread that will use it
(``SQLiteRepository._conn`` does that).
"""

from __future__ import annotations

import sqlite3
import threading
import weakref
from contextlib import contextmanager
from pathlib import Path

_WRITE_LOCK: threading.Lock = threading.Lock()


class _Connection(sqlite3.Connection):
    """Knows when it was closed and, unlike the base class, can be weakly referenced."""

    closed = False

    def close(self) -> None:
        self.closed = True
        super().close()


# This thread's connections, by db_path string.
_THREAD = threading.local()

# Every open connection by db_path string, so ``close_connection`` can reach other threads'.
# Weak references: a connection goes away with its thread (the pool retires idle workers).
_OPEN: dict[str, weakref.WeakSet[_Connection]] = {}
_OPEN_LOCK = threading.Lock()


def _open(key: str) -> _Connection:
    conn = sqlite3.connect(key, check_same_thread=False, factory=_Connection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    with _OPEN_LOCK:
        _OPEN.setdefault(key, weakref.WeakSet()).add(conn)
    return conn


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    """Return the calling thread's WAL-mode sqlite3 connection for *db_path*.

    Callers must use :func:`write_lock` around any mutating statements.
    """
    key = str(db_path)
    if not hasattr(_THREAD, "connections"):
        _THREAD.connections = {}
    conn = _THREAD.connections.get(key)
    if conn is None or conn.closed:
        conn = _THREAD.connections[key] = _open(key)
    return conn


class SQLiteRepository:
    """Base for the repositories: ``_conn`` is always the calling thread's connection."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = db_path

    @property
    def _conn(self) -> sqlite3.Connection:
        return get_connection(self._db_path)


@contextmanager
def write_lock() -> object:
    """Context manager that acquires the module-level write lock.

    Example::

        with write_lock():
            conn.execute("INSERT INTO matches ...")
            conn.commit()
    """
    with _WRITE_LOCK:
        yield


def close_connection(db_path: str | Path) -> None:
    """Close every thread's connection for *db_path* (useful in tests)."""
    with _OPEN_LOCK:
        connections = list(_OPEN.pop(str(db_path), ()))
    for conn in connections:
        conn.close()
