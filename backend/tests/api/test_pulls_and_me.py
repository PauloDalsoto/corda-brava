"""Pull ingest + /me tests. Status is set via the repo so no consumer drains the queue."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_container
from app.api.player import NOT_RUNNING_DETAIL
from app.core.app_factory import create_app
from app.core.settings import Settings
from app.domain.enums import EndReason, MatchResult, MatchStatus
from app.repositories.sqlite.connection import close_connection


@pytest.fixture()
def ctx(tmp_path: Path):
    db = tmp_path / "t.db"
    settings = Settings(DATABASE_URL=f"sqlite:///{db.as_posix()}", SECRET_KEY="s" * 32)
    app = create_app(settings)
    client = TestClient(app)
    c = get_container(app)
    match = c.service.create_match(duration_s=60)
    j = client.post(
        f"/api/matches/{match.id}/players",
        json={"name": "Ana", "team": "gremio", "deviceId": "d1"},
    ).json()
    yield client, c, match.id, j
    close_connection(db)


def _set(c, mid, status, result=None):
    m = c.matches.get_match(mid)
    m.status = status
    if result:
        m.result, m.end_reason = result, EndReason.TIMEOUT
    c.matches.update_match(m)


def _pull(client, mid, j, **over):
    body = {
        "playerId": j["playerId"],
        "playerToken": j["playerToken"],
        "count": 5,
        "clientTs": 1.0,
        "batchId": "b1",
    } | over
    return client.post(f"/api/matches/{mid}/pulls", json=body)


async def _drain(c):
    return await c.queue.receive_batch(100, 0)


@pytest.mark.asyncio
async def test_pull_running_enqueues_one(ctx) -> None:
    client, c, mid, j = ctx
    _set(c, mid, MatchStatus.RUNNING)
    assert _pull(client, mid, j).status_code == 202
    msgs = await _drain(c)
    assert len(msgs) == 1
    ev = msgs[0].payload
    assert (ev.match_id, ev.player_id, ev.team.value, ev.count, ev.batch_id) == (
        mid,
        j["playerId"],
        "gremio",
        5,
        "b1",
    )


@pytest.mark.asyncio
async def test_same_batch_not_requeued(ctx) -> None:
    client, c, mid, j = ctx
    _set(c, mid, MatchStatus.RUNNING)
    assert _pull(client, mid, j).status_code == 202
    assert _pull(client, mid, j).status_code == 202
    assert len(await _drain(c)) == 1


@pytest.mark.asyncio
async def test_failed_enqueue_is_503_and_the_resend_is_not_swallowed(ctx, monkeypatch) -> None:
    client, c, mid, j = ctx
    _set(c, mid, MatchStatus.RUNNING)
    real_enqueue, calls = c.queue.enqueue, []

    async def flaky(event) -> None:
        calls.append(event.batch_id)
        if len(calls) == 1:
            raise RuntimeError("broker unreachable")
        await real_enqueue(event)

    monkeypatch.setattr(c.queue, "enqueue", flaky)
    assert _pull(client, mid, j).status_code == 503
    assert _pull(client, mid, j).status_code == 202  # same batchId: must be enqueued, not "seen"
    assert calls == ["b1", "b1"]
    assert len(await _drain(c)) == 1


async def _concurrent_resends(client, c, mid, j, enqueue) -> list[int]:
    """Send the same batch twice while the first enqueue is still in flight."""
    started, release = asyncio.Event(), asyncio.Event()

    async def slow(event) -> None:  # a broker round trip in flight while the resend arrives
        started.set()
        await release.wait()
        await enqueue(event)

    c.queue.enqueue = slow
    body = {
        "playerId": j["playerId"],
        "playerToken": j["playerToken"],
        "count": 5,
        "clientTs": 1.0,
        "batchId": "b1",
    }
    transport = httpx.ASGITransport(app=client.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        url = f"/api/matches/{mid}/pulls"
        first = asyncio.create_task(ac.post(url, json=body))
        await started.wait()
        second = asyncio.create_task(ac.post(url, json=body))
        for _ in range(100):  # let the resend reach the route and join the in-flight enqueue
            await asyncio.sleep(0)
        release.set()
        responses = await asyncio.gather(first, second)
    return [r.status_code for r in responses]


@pytest.mark.asyncio
async def test_a_resend_during_the_enqueue_is_not_enqueued_twice(ctx) -> None:
    client, c, mid, j = ctx
    _set(c, mid, MatchStatus.RUNNING)
    real_enqueue, calls = c.queue.enqueue, []

    async def counting(event) -> None:
        calls.append(event.batch_id)
        await real_enqueue(event)

    assert await _concurrent_resends(client, c, mid, j, counting) == [202, 202]
    assert calls == ["b1"]
    assert len(await _drain(c)) == 1
    assert c.inflight_batches == {}


@pytest.mark.asyncio
async def test_a_resend_during_a_failing_enqueue_also_gets_503_and_can_retry(ctx) -> None:
    client, c, mid, j = ctx
    _set(c, mid, MatchStatus.RUNNING)
    real_enqueue = c.queue.enqueue

    async def down(event) -> None:
        raise RuntimeError("broker unreachable")

    assert await _concurrent_resends(client, c, mid, j, down) == [503, 503]
    c.queue.enqueue = real_enqueue
    assert _pull(client, mid, j).status_code == 202  # not marked seen: the retry goes through
    assert len(await _drain(c)) == 1


@pytest.mark.asyncio
async def test_count_clamped(ctx) -> None:
    client, c, mid, j = ctx
    _set(c, mid, MatchStatus.RUNNING)
    assert _pull(client, mid, j, count=10_000).status_code == 202
    assert (await _drain(c))[0].payload.count == c.settings.MAX_PULLS_PER_BATCH


def test_bad_count_422(ctx) -> None:
    client, c, mid, j = ctx
    _set(c, mid, MatchStatus.RUNNING)
    assert _pull(client, mid, j, count=0).status_code == 422


@pytest.mark.parametrize("status", [MatchStatus.LOBBY, MatchStatus.FINISHED])
def test_pull_not_running_409(ctx, status) -> None:
    client, c, mid, j = ctx
    _set(c, mid, status, MatchResult.DRAW if status is MatchStatus.FINISHED else None)
    r = _pull(client, mid, j)
    assert r.status_code == 409 and "partida" in r.json()["detail"]


def test_pull_after_ends_at_is_409_and_not_enqueued(ctx) -> None:
    client, c, mid, j = ctx
    _set(c, mid, MatchStatus.RUNNING)
    m = c.matches.get_match(mid)
    m.ends_at = datetime.now(UTC) - timedelta(seconds=1)  # timer expired, consumer not yet finished
    c.matches.update_match(m)
    r = _pull(client, mid, j)
    assert r.status_code == 409 and r.json()["detail"] == NOT_RUNNING_DETAIL
    m.ends_at = datetime.now(UTC) + timedelta(seconds=30)
    c.matches.update_match(m)
    assert _pull(client, mid, j).status_code == 202


def test_bad_token_401_and_mismatch_403(ctx) -> None:
    client, c, mid, j = ctx
    _set(c, mid, MatchStatus.RUNNING)
    assert _pull(client, mid, j, playerToken="junk").status_code == 401
    assert _pull(client, mid, j, playerId="someone-else").status_code == 403
    other = c.service.create_match(duration_s=60)
    assert _pull(client, other.id, j).status_code == 403


def test_me_screens(ctx) -> None:
    client, c, mid, j = ctx
    q = {"playerId": j["playerId"], "token": j["playerToken"]}
    get = lambda: client.get(f"/api/matches/{mid}/me", params=q)  # noqa: E731
    assert get().json() == {"screen": "wait", "team": "gremio", "matchStatus": "LOBBY"}
    _set(c, mid, MatchStatus.RUNNING)
    assert get().json()["screen"] == "play"
    _set(c, mid, MatchStatus.FINISHED, MatchResult.GREMIO)
    assert get().json() == {
        "screen": "end",
        "team": "gremio",
        "matchStatus": "FINISHED",
        "result": "GREMIO",
    }


def test_me_bad_token_401(ctx) -> None:
    client, _, mid, j = ctx
    r = client.get(f"/api/matches/{mid}/me", params={"playerId": j["playerId"], "token": "x"})
    assert r.status_code == 401
