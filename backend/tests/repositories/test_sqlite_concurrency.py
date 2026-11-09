"""FastAPI runs sync routes on a thread pool next to the event loop: repositories must cope."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.domain.entities import Match, MatchConfig, Player, TeamConfig
from app.domain.enums import BalanceMode, Team
from app.repositories.sqlite.bootstrap import bootstrap_db
from app.repositories.sqlite.connection import close_connection, get_connection, write_lock
from app.repositories.sqlite.match_repo import SQLiteMatchRepository
from app.repositories.sqlite.player_repo import _INSERT_PLAYER, SQLitePlayerRepository

MATCH_ID = "ABC123"
THREADS = 20
READS_PER_THREAD = 300


def _match() -> Match:
    team = TeamConfig(id="t", name="T", primary_color="#000000", secondary_color="#FFFFFF")
    config = MatchConfig(
        duration_s=120,
        rope_target=100,
        balance_mode=BalanceMode.NORMALIZED,
        inter=team,
        gremio=team,
    )
    return Match(id=MATCH_ID, created_at=datetime(2026, 1, 1, tzinfo=UTC), config=config)


def _player(index: int) -> Player:
    return Player(
        id=f"player-{index}",
        match_id=MATCH_ID,
        device_id=f"device-{index}",
        name=f"Bot {index}",
        team=Team.INTER,
        token="token",
        joined_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "t.db"
    bootstrap_db(path)
    SQLiteMatchRepository(path).create_match(_match())
    yield path
    close_connection(path)


def test_concurrent_reads_always_return_the_requested_row(db: Path) -> None:
    repo = SQLitePlayerRepository(db)
    players = [repo.add_player(_player(i)) for i in range(THREADS)]
    start = threading.Barrier(THREADS)

    def read_own_row(player: Player) -> None:
        start.wait()
        for _ in range(READS_PER_THREAD):
            found = repo.get_player(player.id)
            assert found is not None
            assert found.id == player.id

    with ThreadPoolExecutor(THREADS) as pool:
        for outcome in [pool.submit(read_own_row, player) for player in players]:
            outcome.result()


def test_concurrent_reads_of_the_match_stay_intact(db: Path) -> None:
    repo = SQLiteMatchRepository(db)
    start = threading.Barrier(THREADS)

    def read_match() -> None:
        start.wait()
        for _ in range(READS_PER_THREAD):
            match = repo.get_match(MATCH_ID)
            assert match is not None
            assert match.id == MATCH_ID

    with ThreadPoolExecutor(THREADS) as pool:
        for outcome in [pool.submit(read_match) for _ in range(THREADS)]:
            outcome.result()


def test_another_thread_never_sees_an_unfinished_write(db: Path) -> None:
    repo = SQLitePlayerRepository(db)
    player = _player(1)
    connection = get_connection(db)
    with ThreadPoolExecutor(1) as other_thread:
        with write_lock():
            connection.execute(
                _INSERT_PLAYER,
                {
                    "id": player.id,
                    "match_id": player.match_id,
                    "device_id": player.device_id,
                    "name": player.name,
                    "team": player.team.value,
                    "token": player.token,
                    "joined_at": player.joined_at.isoformat(),
                },
            )
            during = other_thread.submit(repo.get_player, player.id).result()
            connection.commit()
        after = other_thread.submit(repo.get_player, player.id).result()
    assert during is None
    assert after is not None


def test_a_thread_gets_a_new_connection_after_close(db: Path) -> None:
    repo = SQLiteMatchRepository(db)
    with ThreadPoolExecutor(1) as same_thread:
        assert same_thread.submit(repo.get_match, MATCH_ID).result() is not None
        close_connection(db)  # from this thread, reaching the worker's connection too
        assert same_thread.submit(repo.get_match, MATCH_ID).result() is not None
