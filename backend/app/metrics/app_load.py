"""Main test: simulated players and host driving the app's real HTTP and WebSocket API.

Each simulated phone follows the frontend's behaviour (``usePullBuffer.ts``, ``useMePolling.ts``):
it polls ``/me`` every second, starts pulling only once ``/me`` says the match is running, flushes
what it accumulated every 500 ms, never overlaps two flushes, and resends a failed batch with the
same ``batchId``.  The only addition is a deliberate resend of a confirmed batch now and then,
which the route answers without touching the broker and so gives a baseline for the API's cost.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import random
import secrets
import time
import uuid
from collections import Counter
from collections.abc import Coroutine, Iterable
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from app.metrics.server import BackendServer, sample_loop_lag, sample_processes
from app.metrics.servicebus import QueueProbe, drop_queue, recreate_queue
from app.metrics.stats import Recorder, say, scoreboard_latencies

TEST = "app"
QUEUE_NAME = "metrics-app"
HTTP_TIMEOUT_S = 30.0
SOCKET_OPEN_TIMEOUT_S = 30.0
TASKS_FINISH_TIMEOUT_S = 15.0
QUEUE_SAMPLE_INTERVAL_S = 2.0
QUEUE_SETTLE_S = 3.0  # broker counters lag a little behind the last settle
LOBBY_POLL_DEADLINE_S = 3_600.0
RETRY_ATTEMPTS = 5  # the app answers some valid requests with 403/404/500 under concurrency
RETRY_PAUSE_S = 0.2
# httpcore's pool does work quadratic in its connections on every request; hundreds of phones on
# one client would make the generator, not the app, the slowest part of the system.
PLAYERS_PER_CLIENT = 10
TEAMS = ("inter", "gremio")


@dataclass(frozen=True)
class AppProfile:
    player_levels: tuple[int, ...]
    match_duration_s: int
    flush_interval_s: float = 0.5  # flushIntervalMs in PlayerPlay.tsx
    me_poll_interval_s: float = 1.0  # ME_POLL_MS in useMePolling.ts
    taps_per_flush: int = 3  # 6 taps per second: far below the 25-per-tick anti-cheat cap
    # The frontend allows 200.  Twelve keeps even two batches landing in one tick under the
    # consumer's 25-per-tick anti-cheat cap, which would otherwise look like lost pulls.
    max_pulls_per_batch: int = 12
    stop_tapping_before_end_s: float = 5.0  # lets in-flight pulls land before the final count
    duplicate_every_s: float = 10.0
    join_concurrency: int = 20  # phones scanning the QR code do not all arrive at once
    end_grace_s: float = 30.0


APP_PROFILES = {
    "quick": AppProfile(player_levels=(10, 50, 200), match_duration_s=120),
    "smoke": AppProfile(player_levels=(6,), match_duration_s=30),
}


class SimulationError(RuntimeError):
    """A step every later step depends on has failed; the match cannot go on."""


@dataclass
class _Batch:
    batch_id: str
    count: int
    first_attempt_ns: int


@dataclass
class _Player:
    team: str
    name: str
    device_id: str
    player_id: str = ""
    token: str = ""
    pending: int = 0
    unacked: _Batch | None = None
    inflight: bool = False
    stopped: bool = False
    next_duplicate_at: float = 0.0
    client: httpx.AsyncClient | None = None
    playing: asyncio.Event = field(default_factory=asyncio.Event)


async def _sleep_until(moment: float) -> None:
    """Sleep until ``moment``; always yields, so a late task cannot starve the others."""
    await asyncio.sleep(max(0.0, moment - time.perf_counter()))


async def _finish(tasks: Iterable[asyncio.Task[None]], timeout: float) -> None:
    """Wait for ``tasks`` up to ``timeout``, then cancel whatever is left."""
    pending = list(tasks)
    if not pending:
        return
    _, unfinished = await asyncio.wait(pending, timeout=timeout)
    for task in unfinished:
        task.cancel()
    await asyncio.gather(*unfinished, return_exceptions=True)


class MatchSimulation:
    """One complete match at one load level."""

    def __init__(
        self,
        *,
        server: BackendServer,
        password: str,
        players: int,
        profile: AppProfile,
        recorder: Recorder,
        probe: QueueProbe | None,
    ) -> None:
        self._server = server
        self._password = password
        self._profile = profile
        self._recorder = recorder
        self._probe = probe
        self._level = str(players)
        self._rng = random.Random(players)  # same phases on every run at this level
        self._players = [
            _Player(team=TEAMS[i % 2], name=f"Bot {i}", device_id=str(uuid.uuid4()))
            for i in range(players)
        ]
        self._client: httpx.AsyncClient | None = None
        self._match_id = ""
        self._host_headers: dict[str, str] = {}
        self._background: set[asyncio.Task[None]] = set()

        self._snapshot_seen = asyncio.Event()
        self._ended = asyncio.Event()
        self._ended_message: dict[str, Any] = {}
        self._ticks: list[tuple[int, dict[str, int]]] = []

        self._started_at: float | None = None
        self._stop_tapping_at = float("inf")
        self._deadline = float("inf")
        self._window_requests: Counter[str] = Counter()
        self._accepted: dict[str, list[tuple[int, int]]] = {team: [] for team in TEAMS}
        self._pulls_generated = 0
        self._pulls_rejected = 0
        self._flush_attempts = 0
        self._flushes_skipped = 0
        self._resends = 0
        self._duplicates_sent = 0
        self._duplicates_accepted = 0
        self._players_requested = players
        self._players_failed_to_join = 0
        self._players_stopped_early = 0
        self._pulls_discarded = 0  # pending when a player was told to stop

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _add(self, metric: str, value: float, at_ns: int | None = None) -> None:
        self._recorder.add(TEST, self._level, metric, value, at_ns)

    def _count(self, name: str, amount: int = 1) -> None:
        self._recorder.count(TEST, self._level, name, amount)

    def _error(self, where: str, problem: BaseException | str) -> None:
        self._recorder.error(TEST, self._level, where, problem)

    def _spawn(self, coroutine: Coroutine[Any, Any, None], where: str) -> asyncio.Task[None]:
        """Run a coroutine in the background; a crash is recorded, never lost."""

        async def guarded() -> None:
            try:
                await coroutine
            except Exception as exc:  # noqa: BLE001 - one phone must not stop the match
                self._error(where, exc)

        task = asyncio.create_task(guarded())
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    async def _request(
        self,
        route: str,
        method: str,
        url: str,
        *,
        client: httpx.AsyncClient | None = None,
        **kwargs: Any,  # noqa: ANN401 - passed straight to httpx
    ) -> httpx.Response | None:
        """Timed request.  ``None`` means no response arrived (timeout or network failure)."""
        moment = time.perf_counter()
        if self._started_at is not None and moment < self._stop_tapping_at:
            self._window_requests[route] += 1
        started = time.perf_counter_ns()
        try:
            response = await (client or self._client).request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            self._error(f"http:{route}", exc)
            self._count(f"http_failure:{route}:timeout")
            return None
        except httpx.TransportError as exc:
            self._error(f"http:{route}", exc)
            self._count(f"http_failure:{route}:network")
            return None
        self._add(f"http_{route}_ms", (time.perf_counter_ns() - started) / 1e6, started)
        self._count(f"http_status:{route}:{response.status_code}")
        return response

    # ── Host ──────────────────────────────────────────────────────────────────

    async def _retrying(
        self,
        route: str,
        method: str,
        url: str,
        *,
        conflict_means_done: bool = False,
        **kwargs: Any,  # noqa: ANN401 - passed straight to httpx
    ) -> httpx.Response | None:
        """Repeat an idempotent step until it works, as a person pressing the button again would.

        Every attempt is timed and counted, so failures stay visible in the results.
        """
        for attempt in range(RETRY_ATTEMPTS):
            response = await self._request(route, method, url, **kwargs)
            if response is not None:
                if response.is_success:
                    return response
                if conflict_means_done and attempt and response.status_code == 409:
                    return response  # an earlier attempt took effect after all
            if attempt + 1 < RETRY_ATTEMPTS:
                await asyncio.sleep(RETRY_PAUSE_S)
        return None

    async def _setup_request(
        self,
        route: str,
        method: str,
        url: str,
        **kwargs: Any,  # noqa: ANN401 - passed straight to httpx
    ) -> httpx.Response:
        """A step the rest of the match depends on: giving up on it ends this level."""
        response = await self._retrying(route, method, url, **kwargs)
        if response is None:
            raise SimulationError(f"{route} falhou em {RETRY_ATTEMPTS} tentativas")
        return response

    async def _open_match(self) -> str:
        login = await self._setup_request(
            "login", "POST", "/api/host/login", json={"password": self._password}
        )
        token = login.json()["token"]
        self._host_headers = {"Authorization": f"Bearer {token}"}
        created = await self._setup_request(
            "create",
            "POST",
            "/api/host/matches",
            json={"durationS": self._profile.match_duration_s},
            headers=self._host_headers,
        )
        self._match_id = created.json()["matchId"]
        return token

    async def _watch_host_socket(self, token: str) -> None:
        """The big screen: one WebSocket, read until the match ends."""
        url = f"ws://127.0.0.1:{self._server.port}/api/host/matches/{self._match_id}/ws"
        started = time.perf_counter_ns()
        try:
            async with connect(
                f"{url}?token={token}",
                max_size=None,
                open_timeout=SOCKET_OPEN_TIMEOUT_S,
                ping_interval=None,
            ) as socket:
                self._add("ws_connect_ms", (time.perf_counter_ns() - started) / 1e6, started)
                async for raw in socket:
                    arrived = time.perf_counter_ns()
                    message = json.loads(raw)
                    kind = message.get("type")
                    if kind == "snapshot":
                        self._add("ws_snapshot_ms", (arrived - started) / 1e6, started)
                        self._snapshot_seen.set()
                    elif kind == "tick":
                        scores = {team: message["scores"][team]["pulls"] for team in TEAMS}
                        self._ticks.append((arrived, scores))
                    elif kind == "ended":
                        self._ended_message = message
                        self._ended.set()
                        return
        except (OSError, TimeoutError, WebSocketException) as exc:
            self._error("ws", exc)

    # ── Players ───────────────────────────────────────────────────────────────

    async def _join(self, player: _Player, gate: asyncio.Semaphore) -> asyncio.Task[None] | None:
        """Enter the match; a player who still cannot get in is left out, not fatal to the level."""
        async with gate:
            url = f"/api/matches/{self._match_id}"
            await self._retrying("public", "GET", f"{url}/public", client=player.client)
            joined = await self._retrying(
                "join",
                "POST",
                f"{url}/players",
                client=player.client,
                json={"name": player.name, "team": player.team, "deviceId": player.device_id},
            )
        if joined is None:
            self._players_failed_to_join += 1
            self._error("join", "gave_up")
            return None
        body = joined.json()
        player.player_id, player.token = body["playerId"], body["playerToken"]
        return self._spawn(self._poll_me(player), "poll_me")

    async def _poll_me(self, player: _Player) -> None:
        """Poll ``/me`` on a fixed grid; a slow answer skips grid points, it never overlaps."""
        url = f"/api/matches/{self._match_id}/me"
        params = {"playerId": player.player_id, "token": player.token}
        interval = self._profile.me_poll_interval_s
        next_at = time.perf_counter()
        last_chance = next_at + LOBBY_POLL_DEADLINE_S
        while time.perf_counter() < min(last_chance, self._deadline):
            response = await self._request("me", "GET", url, client=player.client, params=params)
            if response is not None and response.status_code == 200:
                screen = response.json()["screen"]
                if screen == "play":
                    player.playing.set()
                elif screen == "end":
                    return
            now = time.perf_counter()
            while next_at <= now:
                next_at += interval
            await _sleep_until(next_at)

    async def _pull_loop(self, player: _Player, phase_s: float) -> None:
        """Accumulate taps and flush on a timer, with the frontend's no-overlap guard."""
        await player.playing.wait()
        next_at = time.perf_counter() + phase_s
        while True:
            await _sleep_until(next_at)
            now = time.perf_counter()
            next_at = max(next_at + self._profile.flush_interval_s, now)  # late ticks not replayed
            tapping = now < self._stop_tapping_at
            if tapping:
                player.pending += self._profile.taps_per_flush
                self._pulls_generated += self._profile.taps_per_flush
            if player.stopped or now >= self._deadline:
                return
            if not tapping and not player.pending and player.unacked is None:
                if not player.inflight:
                    return
            if player.inflight:
                self._flushes_skipped += 1
                continue
            self._flush_attempts += 1
            player.inflight = True  # set here: the task only starts on a later loop iteration
            self._spawn(self._flush(player), "flush")

    async def _post_pulls(
        self, route: str, player: _Player, batch: _Batch
    ) -> httpx.Response | None:
        return await self._request(
            route,
            "POST",
            f"/api/matches/{self._match_id}/pulls",
            client=player.client,
            json={
                "playerId": player.player_id,
                "playerToken": player.token,
                "count": batch.count,
                "clientTs": time.time() * 1000,
                "batchId": batch.batch_id,
            },
        )

    async def _flush(self, player: _Player) -> None:
        try:
            await self._send_next_batch(player)
        finally:
            player.inflight = False

    async def _send_next_batch(self, player: _Player) -> None:
        if player.unacked is None:
            count = min(player.pending, self._profile.max_pulls_per_batch)
            if count == 0:
                return
            player.pending -= count
            player.unacked = _Batch(str(uuid.uuid4()), count, time.perf_counter_ns())
        else:
            self._resends += 1
        batch = player.unacked
        response = await self._post_pulls("pulls", player, batch)
        if response is None:
            return  # keep the batch: the next flush resends it with the same batchId
        status = response.status_code
        if response.is_success:
            player.unacked = None
            self._accepted[player.team].append((batch.first_attempt_ns, batch.count))
            self._maybe_send_duplicate(player, batch)
        elif 400 <= status < 500 and status not in (408, 429):
            player.unacked = None  # a poison batch, dropped as the frontend does
            self._pulls_rejected += batch.count
            if status in (401, 403, 409):
                self._players_stopped_early += time.perf_counter() < self._stop_tapping_at
                player.stopped = True
                self._pulls_discarded += player.pending
                player.pending = 0

    def _maybe_send_duplicate(self, player: _Player, batch: _Batch) -> None:
        now = time.perf_counter()
        if now < player.next_duplicate_at or now >= self._stop_tapping_at:
            return
        player.next_duplicate_at = now + self._profile.duplicate_every_s
        self._spawn(self._send_duplicate(player, batch), "duplicate")

    async def _send_duplicate(self, player: _Player, batch: _Batch) -> None:
        self._duplicates_sent += 1
        response = await self._post_pulls("pulls_duplicate", player, batch)
        if (
            response is not None
            and response.is_success
            and response.json().get("accepted") == batch.count
        ):
            self._duplicates_accepted += 1

    # ── Monitors ──────────────────────────────────────────────────────────────

    async def _watch_queue(self) -> None:
        while True:
            try:
                state = await asyncio.to_thread(self._probe.read)
            except Exception as exc:  # noqa: BLE001 - a missed sample must not stop the match
                self._error("queue_probe", exc)
            else:
                at = time.perf_counter_ns()
                self._add("queue_active_messages", state["active"], at)
                self._add("queue_dead_letter_messages", state["dead_letter"], at)
                self._add("queue_size_bytes", state["size_bytes"], at)
            await asyncio.sleep(QUEUE_SAMPLE_INTERVAL_S)

    def _start_monitors(self) -> list[asyncio.Task[None]]:
        processes = {"backend": self._server.pid, "generator": os.getpid()}
        coroutines: list[Coroutine[Any, Any, None]] = [
            sample_processes(self._recorder, TEST, self._level, processes),
            sample_loop_lag(self._recorder, TEST, self._level),
        ]
        if self._probe is not None:
            coroutines.append(self._watch_queue())
        return [asyncio.create_task(coroutine) for coroutine in coroutines]

    # ── The match ─────────────────────────────────────────────────────────────

    async def _play(self) -> dict[str, Any]:
        token = await self._open_match()
        socket_task = self._spawn(self._watch_host_socket(token), "ws")
        try:
            await asyncio.wait_for(self._snapshot_seen.wait(), SOCKET_OPEN_TIMEOUT_S)
        except TimeoutError:
            raise SimulationError("o WebSocket do telão não enviou o snapshot") from None

        say(f"[app] {self._level} jogadores: entrando na partida")
        gate = asyncio.Semaphore(self._profile.join_concurrency)
        joined = await asyncio.gather(*(self._join(player, gate) for player in self._players))
        pollers = [task for task in joined if task is not None]
        self._players = [player for player in self._players if player.player_id]
        if not self._players:
            raise SimulationError("nenhum jogador conseguiu entrar")

        monitors = self._start_monitors()
        try:
            say(f"[app] {self._level} jogadores: partida de {self._profile.match_duration_s} s")
            started = await self._retrying(
                "start",
                "POST",
                f"/api/host/matches/{self._match_id}/start",
                conflict_means_done=True,
                headers=self._host_headers,
            )
            if started is None:
                raise SimulationError(f"start falhou em {RETRY_ATTEMPTS} tentativas")
            pullers = self._start_pulling()
            ended = await self._wait_for_end()
            await _finish([*pullers, *pollers], TASKS_FINISH_TIMEOUT_S)
        finally:
            for monitor in monitors:
                monitor.cancel()
            await asyncio.gather(*monitors, return_exceptions=True)
        await _finish([socket_task, *self._background], TASKS_FINISH_TIMEOUT_S)
        return await self._collect(ended)

    def _start_pulling(self) -> list[asyncio.Task[None]]:
        profile = self._profile
        self._started_at = time.perf_counter()
        self._stop_tapping_at = (
            self._started_at + profile.match_duration_s - profile.stop_tapping_before_end_s
        )
        self._deadline = self._started_at + profile.match_duration_s + profile.end_grace_s
        tasks = []
        for player in self._players:
            first_duplicate = self._rng.uniform(1.0, profile.duplicate_every_s)
            player.next_duplicate_at = self._started_at + first_duplicate
            phase_s = self._rng.uniform(0.0, profile.flush_interval_s)
            tasks.append(self._spawn(self._pull_loop(player, phase_s), "pull_loop"))
        return tasks

    async def _wait_for_end(self) -> bool:
        try:
            await asyncio.wait_for(self._ended.wait(), self._deadline - time.perf_counter())
        except TimeoutError:
            self._error("match", "ended_not_received")
            return False
        return True

    def _record_derived_samples(self) -> dict[str, int]:
        resolved, unresolved = 0, 0
        for team in TEAMS:
            ticks = [(arrived, scores[team]) for arrived, scores in self._ticks]
            for started, latency in scoreboard_latencies(self._accepted[team], ticks):
                if latency is None:
                    unresolved += 1
                else:
                    resolved += 1
                    self._add("e2e_pull_ms", latency, started)
        for (before, _), (after, _) in pairwise(self._ticks):
            self._add("tick_interval_ms", (after - before) / 1e6, after)
        return {"resolved": resolved, "unresolved": unresolved}

    async def _collect(self, ended: bool) -> dict[str, Any]:
        """Read the final state from every source and put the match's outcome together."""
        match_url = f"/api/host/matches/{self._match_id}"
        view = await self._retrying("match_view", "GET", match_url, headers=self._host_headers)
        analytics = await self._retrying(
            "analytics", "GET", f"{match_url}/analytics", headers=self._host_headers
        )
        view_body = view.json() if view is not None else {}
        analytics_body = analytics.json() if analytics is not None else {}

        queue_end: dict[str, int] | None = None
        if self._probe is not None:
            await asyncio.sleep(QUEUE_SETTLE_S)
            try:
                queue_end = await asyncio.to_thread(self._probe.read)
            except Exception as exc:  # noqa: BLE001 - the rest of the outcome is still valid
                self._error("queue_probe", exc)

        e2e = self._record_derived_samples()
        accepted = sum(count for batches in self._accepted.values() for _, count in batches)
        last_scores = self._ticks[-1][1] if self._ticks else {}
        applied = sum(last_scores.values()) if last_scores else None
        window_s = self._profile.match_duration_s - self._profile.stop_tapping_before_end_s
        pulls_posts = self._window_requests["pulls"]
        return {
            "players": self._players_requested,
            "players_joined": len(self._players),
            "players_stopped": self._players_stopped_early,
            "match_duration_s": self._profile.match_duration_s,
            "ended_received": ended,
            "result": self._ended_message.get("result"),
            "end_reason": self._ended_message.get("endReason"),
            "final_rope": self._ended_message.get("finalRope"),
            "ticks": len(self._ticks),
            "load": {
                "window_s": window_s,
                "requests": sum(self._window_requests.values()),
                "requests_per_s": sum(self._window_requests.values()) / window_s,
                "pull_posts": pulls_posts,
                "pull_posts_per_s": pulls_posts / window_s,
                "pulls_applied_per_s": (applied or 0) / window_s,
            },
            "consistency": {
                "pulls_generated": self._pulls_generated,
                "pulls_accepted": accepted,
                "pulls_rejected": self._pulls_rejected,
                "pulls_discarded": self._pulls_discarded,
                "pulls_not_confirmed": (
                    self._pulls_generated - accepted - self._pulls_rejected - self._pulls_discarded
                ),
                "pulls_applied_ws": applied,
                "pulls_applied_view": _view_total(view_body),
                "pulls_applied_analytics": analytics_body.get("totalPulls", {}).get("overall"),
                "pulls_missing": None if applied is None else max(accepted - applied, 0),
                "pulls_extra": None if applied is None else max(applied - accepted, 0),
                "batches_accepted": sum(len(batches) for batches in self._accepted.values()),
                "flush_attempts": self._flush_attempts,
                "flushes_skipped": self._flushes_skipped,
                "resends": self._resends,
                "duplicates_sent": self._duplicates_sent,
                "duplicates_accepted": self._duplicates_accepted,
            },
            "e2e": e2e,
            "queue_end": queue_end,
            "backend_log": dict(self._server.log_counts),
        }

    def _new_client(self) -> httpx.AsyncClient:
        connections = PLAYERS_PER_CLIENT * 2 + 10
        return httpx.AsyncClient(
            base_url=self._server.base_url,
            limits=httpx.Limits(max_connections=connections, max_keepalive_connections=connections),
            timeout=httpx.Timeout(HTTP_TIMEOUT_S),
            trust_env=False,  # loopback traffic must not go through a configured proxy
        )

    async def run(self) -> dict[str, Any]:
        groups = math.ceil(len(self._players) / PLAYERS_PER_CLIENT)
        player_clients = [self._new_client() for _ in range(groups)]
        for index, player in enumerate(self._players):
            player.client = player_clients[index % groups]
        self._client = self._new_client()  # the host's own connections
        try:
            return await self._play()
        finally:
            for task in list(self._background):
                task.cancel()
            await asyncio.gather(*self._background, return_exceptions=True)
            await asyncio.gather(
                self._client.aclose(), *(client.aclose() for client in player_clients)
            )


def _view_total(view: dict[str, Any]) -> int | None:
    scores = view.get("scores")
    return None if scores is None else sum(scores[team]["pulls"] for team in TEAMS)


async def run_app_test(
    *,
    connection: str | None,
    profile: AppProfile,
    recorder: Recorder,
    port: int,
    lock_s: int,
    log_path: Path,
    keep_queue: bool = False,
) -> dict[str, Any]:
    """Start a private backend and play one match per load level.

    A level that fails is recorded and the next one still runs.  ``connection=None`` uses the
    app's in-memory queue, without Azure.
    """
    output: dict[str, Any] = {"levels": {}, "incomplete": [], "queue": None}
    password = secrets.token_urlsafe(24)
    probe: QueueProbe | None = None
    if connection is not None:
        say(f"[app] criando a fila {QUEUE_NAME}")
        output["queue"] = await asyncio.to_thread(recreate_queue, connection, QUEUE_NAME, lock_s)
        probe = QueueProbe(connection, QUEUE_NAME)
    server = BackendServer(
        connection=connection,
        queue_name=QUEUE_NAME,
        port=port,
        password=password,
        log_path=log_path,
    )
    try:
        say(f"[app] subindo o backend de teste na porta {port}")
        await server.start()
        for players in profile.player_levels:
            level = str(players)
            before = server.log_counts.copy()
            simulation = MatchSimulation(
                server=server,
                password=password,
                players=players,
                profile=profile,
                recorder=recorder,
                probe=probe,
            )
            try:
                outcome = await simulation.run()
            except (SimulationError, KeyError, ValueError, httpx.HTTPError) as exc:
                recorder.error(TEST, level, "level", exc)
                output["incomplete"].append(level)
                say(f"[app] {level} jogadores: nível incompleto ({type(exc).__name__}: {exc})")
                continue
            logged = server.log_counts - before
            outcome["backend_log"] = dict(logged)
            if not outcome["ended_received"]:
                output["incomplete"].append(level)
            output["levels"][level] = outcome
    finally:
        await server.stop()
        if probe is not None:
            probe.close()
        if connection is not None and not keep_queue:
            await asyncio.to_thread(drop_queue, connection, QUEUE_NAME)
    return output
