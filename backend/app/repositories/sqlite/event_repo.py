"""SQLite implementation of EventRepository.

Row ↔ domain-model mapping
---------------------------
All 8 columns of the pull_events table map to a PullEvent entity.

Deduplication (add_event on duplicate batch_id)
------------------------------------------------
Uses ``INSERT OR IGNORE`` — if (match_id, batch_id) already exists the
statement succeeds silently and no row is inserted.  Callers use
event_exists() to detect whether the event was new.  This keeps the hot-path
ingest endpoint simple: attempt insert, then query if needed.

list_events returns rows ordered by server_ts ascending so consumers can
replay events in order.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from app.domain.entities import PullEvent
from app.domain.enums import Team
from app.repositories.sqlite.connection import get_connection, read, write_lock

_INSERT_EVENT = """
INSERT OR IGNORE INTO pull_events
    (id, match_id, player_id, team, count, client_ts, server_ts, batch_id)
VALUES
    (:id, :match_id, :player_id, :team, :count, :client_ts, :server_ts, :batch_id)
"""

_EXISTS_EVENT = """
SELECT 1 FROM pull_events WHERE match_id = ? AND batch_id = ? LIMIT 1
"""

_LIST_EVENTS = """
SELECT * FROM pull_events WHERE match_id = ? ORDER BY server_ts ASC
"""


def _row_to_event(row: sqlite3.Row) -> PullEvent:
    return PullEvent(
        id=row["id"],
        match_id=row["match_id"],
        player_id=row["player_id"],
        team=Team(row["team"]),
        count=row["count"],
        client_ts=row["client_ts"],
        server_ts=datetime.fromisoformat(row["server_ts"]),
        batch_id=row["batch_id"],
    )


def _event_to_params(event: PullEvent) -> dict[str, object]:
    return {
        "id": event.id,
        "match_id": event.match_id,
        "player_id": event.player_id,
        "team": event.team.value,
        "count": event.count,
        "client_ts": event.client_ts,
        "server_ts": event.server_ts.isoformat(),
        "batch_id": event.batch_id,
    }


class SQLiteEventRepository:
    """SQLite-backed EventRepository implementation.

    Thread-safety: all statements (reads too) serialised on the shared connection.

    Duplicate (match_id, batch_id): silently ignored (INSERT OR IGNORE).
    Call event_exists() when dedup detection is required.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._conn = get_connection(db_path)

    def add_event(self, event: PullEvent) -> None:
        """Persist a PullEvent.  Duplicate (match_id, batch_id) is silently ignored."""
        params = _event_to_params(event)
        with write_lock():
            self._conn.execute(_INSERT_EVENT, params)
            self._conn.commit()

    def event_exists(self, match_id: str, batch_id: str) -> bool:
        """Return True if (match_id, batch_id) is already stored."""
        return bool(read(self._conn, _EXISTS_EVENT, (match_id, batch_id)))

    def list_events(self, match_id: str) -> list[PullEvent]:
        """Return all PullEvents for the match, ordered by server_ts ascending."""
        rows = read(self._conn, _LIST_EVENTS, (match_id,))
        return [_row_to_event(r) for r in rows]
