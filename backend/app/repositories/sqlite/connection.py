"""SQLite connection factory for Corda Brava.

Design notes
------------
WAL mode:  Every connection immediately executes ``PRAGMA journal_mode=WAL``
           so readers do not block writers and vice versa.

check_same_thread=False:
           SQLite's default is to raise if a connection is used from a
           different thread than the one that created it.  We disable that
           check here and instead enforce single-writer semantics via a
           module-level re-entrant lock (_WRITE_LOCK).  WAL only lets
           separate connections read concurrently; threads sharing ONE
           connection interleave cursors and see mid-transaction state, so
           reads take the same lock via :func:`read`.

Usage:
    conn = get_connection(db_path)
    # reads: go through read() (takes the lock, fetches inside it)
    rows = read(conn, "SELECT ...", params)
    # writes: acquire the lock
    with write_lock():
        conn.execute("INSERT INTO ...")
        conn.commit()
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

# RLock: a read nested inside a locked write region must not deadlock.
_WRITE_LOCK: threading.RLock = threading.RLock()

# Cache one open connection per db_path string to avoid opening/closing
# on every repo call.  For production (one path) this is effectively a
# module-level singleton.
_CONNECTIONS: dict[str, sqlite3.Connection] = {}


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    """Return (and cache) a WAL-mode sqlite3 connection for *db_path*.

    The connection is opened with ``check_same_thread=False``; callers must
    use :func:`write_lock` around mutating statements and :func:`read` for queries.
    """
    key = str(db_path)
    if key not in _CONNECTIONS:
        conn = sqlite3.connect(key, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.commit()
        _CONNECTIONS[key] = conn
    return _CONNECTIONS[key]


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


def read(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    """Run a query under the shared lock and fetch all rows before releasing it."""
    with _WRITE_LOCK:
        return conn.execute(sql, params).fetchall()


def close_connection(db_path: str | Path) -> None:
    """Close and remove the cached connection for *db_path* (useful in tests)."""
    key = str(db_path)
    conn = _CONNECTIONS.pop(key, None)
    if conn is not None:
        conn.close()
