"""Regression: many threads sharing one SQLite connection must not corrupt reads."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.repositories.sqlite.bootstrap import bootstrap_db
from app.repositories.sqlite.connection import close_connection
from app.repositories.sqlite.match_repo import SQLiteMatchRepository
from app.repositories.sqlite.player_repo import SQLitePlayerRepository
from tests.repositories.test_sqlite_repos import _make_event, _make_match, _make_player


def test_concurrent_reads_during_writes(tmp_path: Path) -> None:
    db = tmp_path / "conc.db"
    bootstrap_db(db)
    matches, players = SQLiteMatchRepository(db), SQLitePlayerRepository(db)
    try:
        match = _make_match("CONC01")
        matches.create_match(match)
        pls = [_make_player("CONC01", f"dev-{i}") for i in range(10)]
        for p in pls:
            players.add_player(p)

        stop = threading.Event()

        def writer() -> None:
            i = 0
            while not stop.is_set():
                i += 1
                match.rope_position = (i % 5) - 2
                matches.apply_tick(match, [_make_event("CONC01", pls[0].id, f"b{i}", i)])

        def reader(n: int) -> None:
            for _ in range(2000 // 4):
                m = matches.get_match("CONC01")
                assert m is not None and m.config.inter.name == "Inter"
                assert len(players.list_players("CONC01")) == 10
                p = players.get_player(pls[n % 10].id)
                assert p is not None and p.team is not None and p.joined_at

        w = threading.Thread(target=writer)
        w.start()
        try:
            with ThreadPoolExecutor(32) as ex:
                for f in [ex.submit(reader, n) for n in range(32)]:
                    f.result()
        finally:
            stop.set()
            w.join()
    finally:
        close_connection(db)
