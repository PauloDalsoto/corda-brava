"""Command line: ``python -m app.metrics run|compare|collect`` (see this folder's README)."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from app.core.settings import BACKEND_ENV_FILE, ROOT_ENV_FILE
from app.metrics.app_load import APP_PROFILES, run_app_test
from app.metrics.report import (
    SUSPENSION_WARN_S,
    load_run,
    write_comparison,
    write_run_report,
)
from app.metrics.server import game_settings, raise_open_file_limit
from app.metrics.servicebus import SERVICEBUS_PROFILES, namespace_info, run_servicebus_test
from app.metrics.stats import Recorder, say

METRICS_DIR = Path(__file__).resolve().parent
RESULTS_DIR = METRICS_DIR / "results"
DEFAULT_CONNECTION_ENV = "AZURE_SERVICEBUS_CONNECTION_STRING"
DEFAULT_PORT = 18_000
PACKAGES = ("azure-servicebus", "fastapi", "httpx", "uvicorn", "websockets")
SAFE_LABEL = re.compile(r"^[A-Za-z0-9._-]+$")


def _load_connection(variable: str) -> str:
    """Read a connection string from the environment, ``backend/.env`` or the repo-root ``.env``."""
    value = os.environ.get(variable)
    for env_file in (BACKEND_ENV_FILE, ROOT_ENV_FILE):
        value = value or dotenv_values(env_file).get(variable)
    if not value:
        raise SystemExit(f"A variável {variable} não está no ambiente nem em {ROOT_ENV_FILE}.")
    if "EntityPath=" in value:
        raise SystemExit(
            f"{variable} precisa ser a connection string do namespace, sem EntityPath."
        )
    return value


def _keep_awake() -> None:
    """Stop macOS from idle-sleeping during the run; closing the lid still suspends it."""
    if shutil.which("caffeinate"):
        subprocess.Popen(["caffeinate", "-ims", "-w", str(os.getpid())])


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _host_info() -> dict[str, Any]:
    return {
        "fingerprint": hashlib.sha256(socket.gethostname().encode()).hexdigest()[:12],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
    }


def _versions() -> dict[str, str]:
    return {name: importlib.metadata.version(name) for name in PACKAGES}


async def _run_tests(
    args: argparse.Namespace,
    connection: str,
    run_dir: Path,
    recorder: Recorder,
    manifest: dict[str, Any],
    results: dict[str, Any],
) -> None:
    """Run the selected tests; a failing test is recorded and the next one still runs."""
    lock_s = int(game_settings()["QUEUE_LEASE_TIMEOUT_S"])
    for test in manifest["tests"]:
        try:
            if test == "app":
                results["app"] = await run_app_test(
                    connection=connection,
                    profile=APP_PROFILES[args.profile],
                    recorder=recorder,
                    port=args.port,
                    lock_s=lock_s,
                    log_path=run_dir / "backend_warnings.log",
                    keep_queue=args.keep_queues,
                )
            else:
                results["servicebus"] = await run_servicebus_test(
                    connection=connection,
                    profile=SERVICEBUS_PROFILES[args.profile],
                    recorder=recorder,
                    lock_s=lock_s,
                    keep_queue=args.keep_queues,
                )
        except Exception as exc:  # noqa: BLE001 - the other test and the report must still happen
            recorder.error(test, "-", "test", exc)
            manifest["failed_tests"].append(test)
            say(f"[{test}] teste falhou: {type(exc).__name__}: {exc}")


def _final_status(manifest: dict[str, Any], results: dict[str, Any]) -> str:
    if manifest["failed_tests"] or results.get("app", {}).get("incomplete"):
        return "partial"
    return "complete"


async def _run(args: argparse.Namespace) -> int:
    if not SAFE_LABEL.match(args.label):
        raise SystemExit("--label aceita apenas letras, números, ponto, hífen e sublinhado.")
    connection = _load_connection(args.connection_env)
    raise_open_file_limit()
    _keep_awake()
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    root = Path(args.results_dir).resolve() if args.results_dir else RESULTS_DIR
    run_dir = root / args.label / run_id
    run_dir.mkdir(parents=True)
    say(f"Resultados em {run_dir}")

    tests = [args.only] if args.only else ["app", "servicebus"]
    manifest: dict[str, Any] = {
        "run_id": run_id,
        "label": args.label,
        "profile": args.profile,
        "tests": tests,
        "status": "running",
        "failed_tests": [],
        "started_at_utc": _utc_now(),
        "host": _host_info(),
        "versions": _versions(),
        "namespace": await asyncio.to_thread(namespace_info, connection),
        "parameters": {
            "game": game_settings(),
            "app": asdict(APP_PROFILES[args.profile]),
            "servicebus": asdict(SERVICEBUS_PROFILES[args.profile]),
        },
    }
    recorder = Recorder()
    wall_started, clock_started = time.time(), time.monotonic()  # the monotonic clock stops asleep
    results: dict[str, Any] = {}
    exit_code = 0
    try:
        await _run_tests(args, connection, run_dir, recorder, manifest, results)
        manifest["status"] = _final_status(manifest, results)
    except asyncio.CancelledError:
        manifest["status"] = "interrupted"
        exit_code = 130
    suspended_s = (time.time() - wall_started) - (time.monotonic() - clock_started)
    manifest["suspended_s"] = round(max(suspended_s, 0.0))
    if manifest["suspended_s"] > SUSPENSION_WARN_S and manifest["status"] == "complete":
        manifest["status"] = "partial"
    manifest["finished_at_utc"] = _utc_now()
    recorder.write(run_dir, results)
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report = write_run_report(run_dir)
    say(f"Execução {manifest['status']}. Relatório: {report}")
    return exit_code


def _resolve_existing(text: str) -> Path:
    """Accept a path as given or relative to this folder, as in ``results/<label>``."""
    for candidate in (Path(text), METRICS_DIR / text):
        if candidate.exists():
            return candidate.resolve()
    raise SystemExit(f"Caminho não encontrado: {text}")


def _resolve_run(text: str) -> Path:
    """A run directory, or the most recent run inside a label directory."""
    path = _resolve_existing(text)
    if (path / "manifest.json").exists():
        return path
    runs = sorted(child for child in path.iterdir() if (child / "manifest.json").exists())
    if not runs:
        raise SystemExit(f"Nenhuma execução encontrada em {path}")
    return runs[-1]


def _compare(args: argparse.Namespace) -> int:
    first, second = load_run(_resolve_run(args.first)), load_run(_resolve_run(args.second))
    out = (
        Path(args.out).resolve()
        if args.out
        else RESULTS_DIR / "comparacoes" / f"{first.label}_vs_{second.label}"
    )
    report = write_comparison(first, second, out)
    say(f"Comparação: {report}")
    return 0


def _collect(args: argparse.Namespace) -> int:
    from app.metrics.monitor import collect

    run_dir = _resolve_run(args.run)
    csv_path = collect(run_dir, args.resource_id)
    report = write_run_report(run_dir)
    say(f"Azure Monitor: {csv_path}. Relatório atualizado: {report}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.metrics", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="roda os testes e gera o relatório")
    run.add_argument("--label", required=True, help="nome da região ou recurso testado")
    run.add_argument(
        "--connection-env",
        default=DEFAULT_CONNECTION_ENV,
        help="variável com a connection string do namespace (ambiente ou .env)",
    )
    run.add_argument("--profile", choices=sorted(APP_PROFILES), default="quick")
    run.add_argument("--only", choices=("app", "servicebus"), help="roda só uma das partes")
    run.add_argument("--port", type=int, default=DEFAULT_PORT, help="porta do backend de teste")
    run.add_argument("--keep-queues", action="store_true", help="não apaga as filas de teste")
    run.add_argument("--results-dir", help="pasta de saída no lugar de app/metrics/results")

    compare = commands.add_parser("compare", help="compara duas execuções")
    compare.add_argument("first", help="pasta da execução, ou do rótulo para usar a mais recente")
    compare.add_argument("second", help="pasta da execução, ou do rótulo para usar a mais recente")
    compare.add_argument("--out", help="pasta de saída da comparação")

    collect = commands.add_parser("collect", help="busca as métricas do Azure Monitor")
    collect.add_argument("--run", required=True, help="pasta da execução ou do rótulo")
    collect.add_argument("--resource-id", required=True, help="resource ID do namespace")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "compare":
        return _compare(args)
    if args.command == "collect":
        return _collect(args)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
