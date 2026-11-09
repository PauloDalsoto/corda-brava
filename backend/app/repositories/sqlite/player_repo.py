"""SQLite implementation of PlayerRepository.

Row ↔ domain-model mapping
---------------------------
All 7 columns of the players table map to a Player entity.
The UNIQUE (match_id, device_id) constraint is enforced by the DB schema;
violations are caught here and re-raised as UniqueConstraintError so callers
do not import sqlite3.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from app.domain.entities import Player
from app.domain.enums import Team
from app.repositories.interfaces import UniqueConstraintError
from app.repositories.sqlite.connection import SQLiteRepository, write_lock

_INSERT_PLAYER = """
INSERT INTO players (id, match_id, device_id, name, team, token, joined_at)
VALUES (:id, :match_id, :device_id, :name, :team, :token, :joined_at)
"""

_SELECT_PLAYER_BY_ID = "SELECT * FROM players WHERE id = ?"
_SELECT_PLAYER_BY_DEVICE = "SELECT * FROM players WHERE match_id = ? AND device_id = ?"
_LIST_PLAYERS = "SELECT * FROM players WHERE match_id = ?"


def _row_to_player(row: sqlite3.Row) -> Player:
    return Player(
        id=row["id"],
        match_id=row["match_id"],
        device_id=row["device_id"],
        name=row["name"],
        team=Team(row["team"]),
        token=row["token"],
        joined_at=datetime.fromisoformat(row["joined_at"]),
    )


def _player_to_params(player: Player) -> dict[str, object]:
    return {
        "id": player.id,
        "match_id": player.match_id,
        "device_id": player.device_id,
        "name": player.name,
        "team": player.team.value,
        "token": player.token,
        "joined_at": player.joined_at.isoformat(),
    }


class SQLitePlayerRepository(SQLiteRepository):
    """SQLite-backed PlayerRepository implementation.

    Thread-safety: writes serialised by write_lock; reads run freely (WAL).

    Duplicate (match_id, device_id) raises UniqueConstraintError.
    The service layer (Task 8) must call get_player_by_device first for
    idempotent-rejoin behaviour.
    """

    def add_player(self, player: Player) -> Player:
        """INSERT a new Player row.

        Raises:
            UniqueConstraintError: if (match_id, device_id) already exists.
        """
        params = _player_to_params(player)
        try:
            with write_lock():
                self._conn.execute(_INSERT_PLAYER, params)
                self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise UniqueConstraintError(
                f"Player with device_id={player.device_id!r} already exists "
                f"in match {player.match_id!r}"
            ) from exc
        return player

    def get_player(self, player_id: str) -> Player | None:
        """Return the Player by primary key, or None."""
        row = self._conn.execute(_SELECT_PLAYER_BY_ID, (player_id,)).fetchone()
        if row is None:
            return None
        return _row_to_player(row)

    def get_player_by_device(self, match_id: str, device_id: str) -> Player | None:
        """Return the Player for (match_id, device_id), or None."""
        row = self._conn.execute(_SELECT_PLAYER_BY_DEVICE, (match_id, device_id)).fetchone()
        if row is None:
            return None
        return _row_to_player(row)

    def list_players(self, match_id: str) -> list[Player]:
        """Return all Players for the given match."""
        rows = self._conn.execute(_LIST_PLAYERS, (match_id,)).fetchall()
        return [_row_to_player(r) for r in rows]
