"""Database bootstrap — creates tables, indexes, and unique constraints.

Run once at startup (or in tests via a tmp_path db).  Safe to call multiple
times: all statements use ``CREATE TABLE IF NOT EXISTS`` / ``CREATE INDEX
IF NOT EXISTS``.

Schema decisions
----------------
datetimes:  Stored as ISO-8601 text (UTC, ``+00:00`` suffix) so they survive
            round-trips as timezone-aware Python ``datetime`` objects without
            any epoch arithmetic.  Example: ``"2024-06-01T12:00:00+00:00"``.

enums:      Stored as their ``.value`` strings (lowercase) matching StrEnum.

MatchConfig: Stored inline on the matches table rather than a separate config
             table — the config is immutable after creation and always read
             with the match, so embedding avoids a join.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.repositories.sqlite.connection import get_connection, write_lock

_CREATE_MATCHES = """
CREATE TABLE IF NOT EXISTS matches (
    id              TEXT PRIMARY KEY,
    status          TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    -- MatchConfig inline
    duration_s      INTEGER NOT NULL,
    rope_target     INTEGER NOT NULL,
    balance_mode    TEXT NOT NULL,
    -- TeamConfig: inter
    inter_id        TEXT NOT NULL,
    inter_name      TEXT NOT NULL,
    inter_primary   TEXT NOT NULL,
    inter_secondary TEXT NOT NULL,
    -- TeamConfig: gremio
    gremio_id        TEXT NOT NULL,
    gremio_name      TEXT NOT NULL,
    gremio_primary   TEXT NOT NULL,
    gremio_secondary TEXT NOT NULL,
    -- runtime
    rope_position   REAL NOT NULL DEFAULT 0.0,
    started_at      TEXT,
    ends_at         TEXT,
    result          TEXT,
    end_reason      TEXT
)
"""

_CREATE_PLAYERS = """
CREATE TABLE IF NOT EXISTS players (
    id          TEXT PRIMARY KEY,
    match_id    TEXT NOT NULL REFERENCES matches(id),
    device_id   TEXT NOT NULL,
    name        TEXT NOT NULL,
    team        TEXT NOT NULL,
    token       TEXT NOT NULL,
    joined_at   TEXT NOT NULL,
    UNIQUE (match_id, device_id)
)
"""

_CREATE_PULL_EVENTS = """
CREATE TABLE IF NOT EXISTS pull_events (
    id          TEXT PRIMARY KEY,
    match_id    TEXT NOT NULL REFERENCES matches(id),
    player_id   TEXT NOT NULL REFERENCES players(id),
    team        TEXT NOT NULL,
    count       INTEGER NOT NULL,
    client_ts   REAL NOT NULL,
    server_ts   TEXT NOT NULL,
    batch_id    TEXT NOT NULL,
    UNIQUE (match_id, batch_id)
)
"""

_CREATE_IDX_PULL_EVENTS_TS = """
CREATE INDEX IF NOT EXISTS idx_pull_events_match_ts
    ON pull_events (match_id, server_ts)
"""


def bootstrap_db(db_path: str | Path) -> sqlite3.Connection:
    """Create all tables and indexes for *db_path*.

    Returns the calling thread's connection.
    """
    conn = get_connection(db_path)
    with write_lock():
        conn.execute(_CREATE_MATCHES)
        conn.execute(_CREATE_PLAYERS)
        conn.execute(_CREATE_PULL_EVENTS)
        conn.execute(_CREATE_IDX_PULL_EVENTS_TS)
        conn.commit()
    return conn
