"""The Markdown reports must explain every metric and never carry credentials."""

from __future__ import annotations

import json

import pytest

from app.metrics.catalog import METRICS
from app.metrics.report import load_run, write_comparison, write_run_report
from app.metrics.stats import NO_LEVEL, Recorder

LEVELS = ("10", "50")
SERVICE_BUS_METRICS = [name for name, info in METRICS.items() if info.group == "Service Bus direto"]
APP_METRICS = [name for name in METRICS if name not in SERVICE_BUS_METRICS]
QUEUE = {
    "name": "metrics-app",
    "lock_duration_s": 10.0,
    "max_delivery_count": 10,
    "message_ttl_s": 3600.0,
    "duplicate_detection": True,
    "duplicate_window_s": 600.0,
}


def _outcome(players: int, **consistency) -> dict:
    return {
        "players": players,
        "players_joined": players,
        "players_stopped": 0,
        "match_duration_s": 120,
        "ended_received": True,
        "result": "DRAW",
        "end_reason": "timeout",
        "final_rope": 0.0,
        "ticks": 160,
        "load": {
            "window_s": 115,
            "requests": 100,
            "requests_per_s": 30.0,
            "pull_posts": 80,
            "pull_posts_per_s": 20.0,
            "pulls_applied_per_s": 60.0,
        },
        "consistency": {
            "pulls_generated": 100,
            "pulls_accepted": 100,
            "pulls_rejected": 0,
            "pulls_discarded": 0,
            "pulls_not_confirmed": 0,
            "pulls_applied_ws": 100,
            "pulls_applied_view": 100,
            "pulls_applied_analytics": 100,
            "pulls_missing": 0,
            "pulls_extra": 0,
            "batches_accepted": 33,
            "flush_attempts": 34,
            "flushes_skipped": 0,
            "resends": 0,
            "duplicates_sent": 4,
            "duplicates_accepted": 4,
            **consistency,
        },
        "e2e": {"resolved": 33, "unresolved": 0},
        "queue_end": {"active": 0, "dead_letter": 0, "size_bytes": 0},
        "backend_log": {},
    }


def _write_run(root, label: str, *, extra_metric: str | None = None, **consistency):
    run_dir = root / label
    run_dir.mkdir()
    recorder = Recorder()
    for level in LEVELS:
        for name in APP_METRICS:
            for value in (10.0, 20.0, 30.0):
                recorder.add("app", level, name, value)
        recorder.count("app", level, "http_status:pulls:202", 80)
    if extra_metric:
        recorder.add("app", LEVELS[0], extra_metric, 1.0)
    for name in SERVICE_BUS_METRICS:
        recorder.add("servicebus", NO_LEVEL, name, 5.0)
    results = {
        "app": {
            "levels": {level: _outcome(int(level), **consistency) for level in LEVELS},
            "incomplete": [],
            "queue": QUEUE,
        },
        "servicebus": {
            "queue": {**QUEUE, "name": "metrics-sb"},
            "sent": 10,
            "received": 10,
            "e2e": {
                "expected": 10,
                "sent": 10,
                "received": 10,
                "lost": 0,
                "duplicates": 0,
                "out_of_order": 0,
            },
        },
    }
    recorder.write(run_dir, results)
    manifest = {
        "run_id": "r1",
        "label": label,
        "profile": "quick",
        "tests": ["app", "servicebus"],
        "status": "complete",
        "failed_tests": [],
        "started_at_utc": "2026-11-08T10:00:00+00:00",
        "finished_at_utc": "2026-11-08T10:10:00+00:00",
        "host": {
            "fingerprint": "abc",
            "platform": "macOS",
            "machine": "arm64",
            "cpu_count": 8,
            "python": "3.12.3",
        },
        "versions": {"azure-servicebus": "7.15.0"},
        "namespace": {"host": f"{label}.servicebus.windows.net", "tier": "Standard"},
        "parameters": {
            "game": {"TICK_INTERVAL_MS": "500"},
            "app": {
                "player_levels": [10, 50],
                "match_duration_s": 120,
                "flush_interval_s": 0.5,
                "me_poll_interval_s": 1.0,
                "taps_per_flush": 3,
                "max_pulls_per_batch": 12,
                "stop_tapping_before_end_s": 5.0,
                "duplicate_every_s": 10.0,
                "join_concurrency": 20,
                "end_grace_s": 30.0,
            },
            "servicebus": {
                "rtt_probes": 30,
                "single_sends": 200,
                "single_sends_max_s": 15.0,
                "batch_sends": 30,
                "batch_size": 100,
                "payload_bytes": 256,
                "drain_max_s": 30.0,
                "e2e_rate_msg_s": 100,
                "e2e_duration_s": 30.0,
                "e2e_tick_s": 0.1,
                "e2e_drain_max_s": 10.0,
            },
        },
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return run_dir


@pytest.fixture(autouse=True)
def no_charts(monkeypatch):
    """Charts are exercised by the real runs; these tests only read the Markdown."""
    monkeypatch.setattr("app.metrics.report._pyplot", lambda: None)


def test_run_report_explains_every_metric(tmp_path):
    report = write_run_report(_write_run(tmp_path, "brazil-south")).read_text(encoding="utf-8")
    for heading in ("## 1. Ambiente", "## 2. Como", "## 3. Como cada", "## 4. Res", "## 5. Lim"):
        assert heading in report
    for name in METRICS:
        assert f"#### `{name}`" in report
    assert "Sem descrição" not in report
    assert "Nenhuma ocorrência adicional" in report


def test_run_report_flags_a_metric_missing_from_the_catalog(tmp_path):
    run_dir = _write_run(tmp_path, "regiao", extra_metric="metrica_nova_ms")
    report = write_run_report(run_dir).read_text(encoding="utf-8")
    assert "Sem descrição" in report
    assert "`metrica_nova_ms`" in report


def test_run_report_lists_what_went_wrong(tmp_path):
    run_dir = _write_run(tmp_path, "regiao", pulls_missing=7, pulls_extra=2)
    report = write_run_report(run_dir).read_text(encoding="utf-8")
    assert "7 puxadas aceitas não chegaram ao placar" in report
    assert "2 puxadas a mais" in report


def test_run_report_calls_out_error_responses(tmp_path):
    run_dir = _write_run(tmp_path, "regiao")
    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["counters"].append(
        {"test": "app", "level": "10", "name": "http_status:me:403", "value": 3}
    )
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    report = write_run_report(run_dir).read_text(encoding="utf-8")
    assert "3 × 403" in report


def test_comparison_shows_both_runs_and_their_differences(tmp_path):
    first = load_run(_write_run(tmp_path, "brazil-south"))
    second = load_run(_write_run(tmp_path, "east-us"))
    second.manifest["host"]["fingerprint"] = "outra"
    out = tmp_path / "comparacao"
    report = write_comparison(first, second, out).read_text(encoding="utf-8")
    assert "brazil-south" in report
    assert "east-us" in report
    assert "**diferente**" in report  # the different machine is called out
    for name in METRICS:
        assert f"#### `{name}`" in report
    rows = (out / "comparacao.csv").read_text(encoding="utf-8").splitlines()
    assert rows[0].startswith("test,level,metric,n_brazil-south")
    assert len(rows) == 1 + len(APP_METRICS) * len(LEVELS) + len(SERVICE_BUS_METRICS)


def test_reports_carry_no_credentials(tmp_path):
    run_dir = _write_run(tmp_path, "regiao")
    report = write_run_report(run_dir).read_text(encoding="utf-8")
    assert "SharedAccessKey" not in report
    assert "playerToken" not in report


def test_a_409_is_not_reported_as_a_backend_failure(tmp_path):
    run_dir = _write_run(tmp_path, "regiao")
    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["counters"].append(
        {"test": "app", "level": "10", "name": "http_status:pulls:409", "value": 5}
    )
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    report = write_run_report(run_dir).read_text(encoding="utf-8")
    assert "× 409" not in report


def _edit_level(run_dir, level: str, **changes) -> None:
    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    outcome = summary["results"]["app"]["levels"][level]
    for section, values in changes.items():
        outcome[section].update(values)
    summary_path.write_text(json.dumps(summary), encoding="utf-8")


def test_a_consumer_that_falls_behind_is_explained_once(tmp_path):
    run_dir = _write_run(tmp_path, "regiao")
    _edit_level(
        run_dir,
        "50",
        consistency={"pulls_missing": 1_500, "batches_accepted": 2_000},
        e2e={"resolved": 1_500, "unresolved": 500},
        queue_end={"active": 500},
    )
    report = write_run_report(run_dir).read_text(encoding="utf-8")
    assert "o consumidor não acompanhou a entrada" in report
    assert "não foram perdidas pelo broker" in report
    assert "está subestimada" in report
    assert "puxadas aceitas não chegaram ao placar" not in report
    assert "mensagens ficaram na fila ao fim" not in report


def test_a_few_leftover_messages_are_not_called_saturation(tmp_path):
    run_dir = _write_run(tmp_path, "regiao")
    _edit_level(
        run_dir,
        "50",
        consistency={"pulls_missing": 9},
        e2e={"unresolved": 3},
        queue_end={"active": 3},
    )
    report = write_run_report(run_dir).read_text(encoding="utf-8")
    assert "o consumidor não acompanhou a entrada" not in report
    assert "9 puxadas aceitas não chegaram ao placar" in report
    assert "3 mensagens ficaram na fila ao fim" in report


def _edit_service_bus(run_dir, **changes) -> None:
    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    service_bus = summary["results"]["servicebus"]
    service_bus["e2e"].update(changes.pop("e2e", {}))
    service_bus.update(changes)
    summary_path.write_text(json.dumps(summary), encoding="utf-8")


def test_service_bus_shortfalls_are_not_called_losses(tmp_path):
    run_dir = _write_run(tmp_path, "regiao")
    _edit_service_bus(run_dir, sent=3_000, received=1_000, e2e={"lost": 4, "left_in_queue": 4})
    report = write_run_report(run_dir).read_text(encoding="utf-8")
    assert "com 1000 de 3000 mensagens recebidas" in report
    assert "4 das 10 mensagens enviadas não foram recebidas até o fim da drenagem" in report
    assert "4 mensagens continuavam na fila" in report


def test_a_suspended_machine_invalidates_the_run_in_the_report(tmp_path):
    run_dir = _write_run(tmp_path, "regiao")
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["suspended_s"] = 600
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    report = write_run_report(run_dir).read_text(encoding="utf-8")
    assert "ficou suspensa por cerca de 10 min" in report


def test_a_503_is_reported_as_a_failed_broker_send(tmp_path):
    run_dir = _write_run(tmp_path, "regiao")
    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["counters"].append(
        {"test": "app", "level": "10", "name": "http_status:pulls:503", "value": 7}
    )
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    report = write_run_report(run_dir).read_text(encoding="utf-8")
    assert "7 envios em `POST /pulls` receberam 503" in report
    assert "× 503" not in report
