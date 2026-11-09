"""A private backend process for the load test, plus process-level resource sampling."""

from __future__ import annotations

import asyncio
import os
import re
import resource
import secrets
import socket
import sys
import tempfile
import time
from collections import Counter, deque
from pathlib import Path

import httpx

from app.core.settings import BACKEND_ENV_FILE, Settings
from app.metrics.stats import Recorder, redact

BACKEND_DIR = BACKEND_ENV_FILE.parent
HOST = "127.0.0.1"
START_TIMEOUT_S = 60.0
STOP_TIMEOUT_S = 15.0
HEALTH_POLL_S = 0.25
OPEN_FILES_TARGET = 10_240  # 200 players need far more sockets than macOS's default of 256
MAX_LOG_LINES_KEPT = 500
MAX_LOG_LINE_BYTES = 1 << 20
PROCESS_SAMPLE_INTERVAL_S = 5.0  # Linux ``ps`` reports whole CPU seconds
LOOP_LAG_INTERVAL_S = 0.1

# Everything that shapes the game, pinned to the app's defaults so that a stray backend/.env or
# shell variable cannot make two regions run under different rules.
_GAME_SETTINGS = (
    "TICK_INTERVAL_MS",
    "DEFAULT_MATCH_DURATION_S",
    "MATCH_DURATION_MIN_S",
    "MATCH_DURATION_MAX_S",
    "ROPE_TARGET",
    "PULL_UNIT",
    "MAX_PULLS_PER_BATCH",
    "MAX_PULLS_PER_PLAYER_PER_S",
    "BATCH_DEDUP_TTL_S",
    "LATE_JOIN_POLICY",
    "TEAM_BALANCE_MODE",
    "QUEUE_LEASE_TIMEOUT_S",
)

_LOG_LEVEL = re.compile(r"\|\s*(WARNING|ERROR|CRITICAL)\s*\||^(WARNING|ERROR|CRITICAL):")
_LOG_EXCEPTION = re.compile(
    r"^([A-Za-z_][\w.]*(?:Error|Exception))(?::|$)"
)  # a traceback's last line


def game_settings() -> dict[str, str]:
    """The game rules and timings the private backend runs with."""
    return {name: str(Settings.model_fields[name].default) for name in _GAME_SETTINGS}


def raise_open_file_limit() -> None:
    """Lift the soft descriptor limit; the backend child inherits it."""
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    target = OPEN_FILES_TARGET if hard == resource.RLIM_INFINITY else min(OPEN_FILES_TARGET, hard)
    if soft == resource.RLIM_INFINITY or soft >= target:
        return
    resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        return probe.connect_ex((HOST, port)) == 0


class BackendServer:
    """Runs the real app with uvicorn, without frontend or Docker, on its own queue and database.

    ``connection=None`` selects the in-memory queue, which needs no Azure account.
    """

    def __init__(
        self,
        *,
        connection: str | None,
        queue_name: str,
        port: int,
        password: str,
        log_path: Path,
    ) -> None:
        self._connection = connection
        self._queue_name = queue_name
        self.port = port
        self._password = password
        self._log_path = log_path
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._data_dir: tempfile.TemporaryDirectory[str] | None = None
        self._recent: deque[str] = deque(maxlen=30)
        self.log_counts: Counter[str] = Counter()

    @property
    def base_url(self) -> str:
        return f"http://{HOST}:{self.port}"

    @property
    def pid(self) -> int:
        if self._process is None:
            raise RuntimeError("the backend is not running")
        return self._process.pid

    def _environment(self, database: Path) -> dict[str, str]:
        queue = (
            {
                "QUEUE_BACKEND": "azure_service_bus",
                "AZURE_SERVICEBUS_CONNECTION_STRING": self._connection,
                "AZURE_SERVICEBUS_QUEUE_NAME": self._queue_name,
            }
            if self._connection
            else {"QUEUE_BACKEND": "memory"}
        )
        return {
            **os.environ,
            **game_settings(),
            **queue,
            "DATABASE_URL": f"sqlite:///{database}",
            "HOST_PASSWORD": self._password,
            "SECRET_KEY": secrets.token_hex(32),
            "PYTHONUNBUFFERED": "1",
        }

    async def start(self) -> None:
        """Start uvicorn and wait for ``/health``; the error carries the backend's last lines."""
        if _port_in_use(self.port):
            raise RuntimeError(f"A porta {self.port} já está em uso. Use --port com outra porta.")
        self._data_dir = tempfile.TemporaryDirectory(prefix="corda-metrics-")
        database = Path(self._data_dir.name) / "corda_brava.db"
        self._process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "uvicorn",
            "app.core.app_factory:create_app",
            "--factory",
            "--host",
            HOST,
            "--port",
            str(self.port),
            "--log-level",  # below INFO uvicorn would log the WebSocket URL, with its token
            "warning",
            "--no-access-log",
            "--timeout-graceful-shutdown",
            "5",
            cwd=BACKEND_DIR,
            env=self._environment(database),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            limit=MAX_LOG_LINE_BYTES,
        )
        self._reader = asyncio.create_task(self._read_log(self._process.stdout))
        await self._wait_until_healthy()

    async def _wait_until_healthy(self) -> None:
        deadline = time.monotonic() + START_TIMEOUT_S
        async with httpx.AsyncClient(base_url=self.base_url, timeout=2.0) as client:
            while time.monotonic() < deadline:
                if self._process is None or self._process.returncode is not None:
                    break
                try:
                    if (await client.get("/health")).status_code == 200:
                        return
                except httpx.TransportError:
                    pass
                await asyncio.sleep(HEALTH_POLL_S)
        recent = "\n".join(self._recent)
        raise RuntimeError(
            f"O backend de teste não respondeu em /health. Últimas linhas:\n{recent}"
        )

    async def _read_log(self, stream: asyncio.StreamReader) -> None:
        """Keep the pipe drained, count warnings and errors, and store only those, redacted."""
        kept = 0
        with self._log_path.open("w", encoding="utf-8") as file:
            while True:
                try:
                    raw = await stream.readline()
                except ValueError:  # one line above the limit
                    continue
                if not raw:
                    return
                line = redact(raw.decode("utf-8", "replace").rstrip())
                self._recent.append(line)
                level, exception = _LOG_LEVEL.search(line), _LOG_EXCEPTION.match(line)
                if level is None and exception is None:
                    continue
                if level is not None:
                    self.log_counts[level.group(1) or level.group(2)] += 1
                else:
                    self.log_counts[exception.group(1)] += 1
                if kept < MAX_LOG_LINES_KEPT:
                    file.write(line + "\n")
                    file.flush()
                    kept += 1

    async def stop(self) -> None:
        process, self._process = self._process, None
        if process is not None:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), STOP_TIMEOUT_S)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            if self._reader is not None:
                await self._reader
        if self._data_dir is not None:
            self._data_dir.cleanup()
            self._data_dir = None


def parse_cpu_time(text: str) -> float:
    """Seconds from ``ps`` output: ``[dd-][hh:]mm:ss[.cc]`` (BSD and procps formats)."""
    days, _, clock = text.strip().rpartition("-")
    seconds = 0.0
    for part in clock.replace(",", ".").split(":"):
        seconds = seconds * 60 + float(part)
    return seconds + (int(days) * 86_400 if days else 0)


async def read_process_usage(pid: int) -> tuple[float, float] | None:
    """``(cpu_seconds, rss_mb)`` of a process, or ``None`` if it cannot be read."""
    process = await asyncio.create_subprocess_exec(
        "ps",
        "-o",
        "time=",
        "-o",
        "rss=",
        "-p",
        str(pid),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env={**os.environ, "LC_ALL": "C"},
    )
    output, _ = await process.communicate()
    fields = output.decode().split()
    if process.returncode != 0 or len(fields) != 2:
        return None
    return parse_cpu_time(fields[0]), int(fields[1]) / 1024


async def sample_processes(
    recorder: Recorder, test: str, level: str, processes: dict[str, int]
) -> None:
    """Record CPU (as a share of one core) and memory for each process until cancelled."""
    previous: dict[str, tuple[float, float]] = {}
    while True:
        for name, pid in processes.items():
            usage = await read_process_usage(pid)
            if usage is None:
                continue
            cpu_s, rss_mb = usage
            now = time.perf_counter()
            recorder.add(test, level, f"{name}_rss_mb", rss_mb)
            if name in previous:
                last_cpu_s, last_at = previous[name]
                recorder.add(
                    test, level, f"{name}_cpu_pct", 100 * (cpu_s - last_cpu_s) / (now - last_at)
                )
            previous[name] = (cpu_s, now)
        await asyncio.sleep(PROCESS_SAMPLE_INTERVAL_S)


async def sample_loop_lag(recorder: Recorder, test: str, level: str) -> None:
    """Record how late this process's event loop wakes up: the generator's own saturation."""
    while True:
        before = time.perf_counter()
        await asyncio.sleep(LOOP_LAG_INTERVAL_S)
        overshoot_s = time.perf_counter() - before - LOOP_LAG_INTERVAL_S
        recorder.add(test, level, "generator_loop_lag_ms", overshoot_s * 1000)
