"""SQLite implementation of MatchRepository.

Row ↔ domain-model mapping
---------------------------
All 19 columns of the matches table map to a Match + nested MatchConfig +
two TeamConfig objects.  Datetimes use ISO-8601 text (UTC); enum values use
their .value strings.  No sqlite3 types escape this module.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from app.domain.entities import Match, MatchConfig, PullEvent, TeamConfig
from app.domain.enums import BalanceMode, EndReason, MatchResult, MatchStatus
from app.repositories.sqlite.connection import SQLiteRepository, write_lock
from app.repositories.sqlite.event_repo import _INSERT_EVENT, _event_to_params

_INSERT_MATCH = """
INSERT INTO matches (
    id, status, created_at,
    duration_s, rope_target, balance_mode,
    inter_id, inter_name, inter_primary, inter_secondary,
    gremio_id, gremio_name, gremio_primary, gremio_secondary,
    rope_position, started_at, ends_at, result, end_reason
) VALUES (
    :id, :status, :created_at,
    :duration_s, :rope_target, :balance_mode,
    :inter_id, :inter_name, :inter_primary, :inter_secondary,
    :gremio_id, :gremio_name, :gremio_primary, :gremio_secondary,
    :rope_position, :started_at, :ends_at, :result, :end_reason
)
"""

_UPDATE_MATCH = """
UPDATE matches SET
    status        = :status,
    rope_position = :rope_position,
    started_at    = :started_at,
    ends_at       = :ends_at,
    result        = :result,
    end_reason    = :end_reason
WHERE id = :id
"""

_SELECT_MATCH = "SELECT * FROM matches WHERE id = ?"


def _dt_to_text(dt: datetime | None) -> str | None:
    """Serialise a timezone-aware datetime to ISO-8601 text, or None."""
    if dt is None:
        return None
    return dt.isoformat()


def _text_to_dt(text: str | None) -> datetime | None:
    """Deserialise an ISO-8601 text column back to a timezone-aware datetime."""
    if text is None:
        return None
    return datetime.fromisoformat(text)


def _row_to_match(row: sqlite3.Row) -> Match:
    inter = TeamConfig(
        id=row["inter_id"],
        name=row["inter_name"],
        primary_color=row["inter_primary"],
        secondary_color=row["inter_secondary"],
    )
    gremio = TeamConfig(
        id=row["gremio_id"],
        name=row["gremio_name"],
        primary_color=row["gremio_primary"],
        secondary_color=row["gremio_secondary"],
    )
    config = MatchConfig(
        duration_s=row["duration_s"],
        rope_target=row["rope_target"],
        balance_mode=BalanceMode(row["balance_mode"]),
        inter=inter,
        gremio=gremio,
    )
    return Match(
        id=row["id"],
        status=MatchStatus(row["status"]),
        created_at=_text_to_dt(row["created_at"]),  # type: ignore[arg-type]
        config=config,
        rope_position=row["rope_position"],
        started_at=_text_to_dt(row["started_at"]),
        ends_at=_text_to_dt(row["ends_at"]),
        result=MatchResult(row["result"]) if row["result"] else None,
        end_reason=EndReason(row["end_reason"]) if row["end_reason"] else None,
    )


def _match_to_params(match: Match) -> dict[str, object]:
    return {
        "id": match.id,
        "status": match.status.value,
        "created_at": _dt_to_text(match.created_at),
        "duration_s": match.config.duration_s,
        "rope_target": match.config.rope_target,
        "balance_mode": match.config.balance_mode.value,
        "inter_id": match.config.inter.id,
        "inter_name": match.config.inter.name,
        "inter_primary": match.config.inter.primary_color,
        "inter_secondary": match.config.inter.secondary_color,
        "gremio_id": match.config.gremio.id,
        "gremio_name": match.config.gremio.name,
        "gremio_primary": match.config.gremio.primary_color,
        "gremio_secondary": match.config.gremio.secondary_color,
        "rope_position": match.rope_position,
        "started_at": _dt_to_text(match.started_at),
        "ends_at": _dt_to_text(match.ends_at),
        "result": match.result.value if match.result else None,
        "end_reason": match.end_reason.value if match.end_reason else None,
    }


def _update_params(match: Match) -> dict[str, object]:
    return {
        "id": match.id,
        "status": match.status.value,
        "rope_position": match.rope_position,
        "started_at": _dt_to_text(match.started_at),
        "ends_at": _dt_to_text(match.ends_at),
        "result": match.result.value if match.result else None,
        "end_reason": match.end_reason.value if match.end_reason else None,
    }


class SQLiteMatchRepository(SQLiteRepository):
    """SQLite-backed MatchRepository implementation.

    Thread-safety: all writes serialised by the module write_lock.
    Reads use the calling thread's own connection (WAL allows concurrent reads).
    """

    def create_match(self, match: Match) -> Match:
        """INSERT a new Match row and return the model unchanged."""
        params = _match_to_params(match)
        with write_lock():
            self._conn.execute(_INSERT_MATCH, params)
            self._conn.commit()
        return match

    def get_match(self, match_id: str) -> Match | None:
        """SELECT and deserialise a Match, or return None."""
        row = self._conn.execute(_SELECT_MATCH, (match_id,)).fetchone()
        if row is None:
            return None
        return _row_to_match(row)

    def list_running(self) -> list[Match]:
        rows = self._conn.execute("SELECT * FROM matches WHERE status = 'running'").fetchall()
        return [_row_to_match(r) for r in rows]

    def update_match(self, match: Match) -> Match:
        """UPDATE the mutable runtime columns of an existing Match."""
        params = _update_params(match)
        with write_lock():
            self._conn.execute(_UPDATE_MATCH, params)
            self._conn.commit()
        return match

    def apply_tick(self, match: Match, events: list[PullEvent]) -> Match:
        """Persist *events* and the match update in ONE transaction (all or nothing)."""
        with write_lock():
            try:
                for e in events:
                    self._conn.execute(_INSERT_EVENT, _event_to_params(e))
                self._conn.execute(_UPDATE_MATCH, _update_params(match))
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return match
