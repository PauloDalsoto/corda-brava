"""Pure helpers of the metrics bench: statistics, score matching, parsing and redaction."""

from __future__ import annotations

import pytest

from app.metrics.server import parse_cpu_time
from app.metrics.stats import (
    jitter,
    level_sort_key,
    percentile,
    redact,
    scoreboard_latencies,
    summarize,
)


def test_percentile_interpolates_between_samples():
    assert percentile([10.0, 20.0, 30.0, 40.0], 50) == 25.0
    assert percentile([10.0, 20.0, 30.0, 40.0], 0) == 10.0
    assert percentile([10.0, 20.0, 30.0, 40.0], 100) == 40.0
    assert percentile([7.0], 95) == 7.0


def test_percentile_of_nothing_is_an_error():
    with pytest.raises(ValueError):
        percentile([], 50)


def test_summarize_describes_unordered_values():
    summary = summarize([3.0, 1.0, 2.0, 4.0, 5.0])
    assert (summary.n, summary.min, summary.max) == (5, 1.0, 5.0)
    assert summary.mean == 3.0
    assert summary.p50 == 3.0
    assert summary.p90 == pytest.approx(4.6)
    assert summary.stdev == pytest.approx(1.5811, abs=1e-3)


def test_summarize_single_sample_has_no_deviation():
    summary = summarize([9.0])
    assert summary.n == 1
    assert summary.stdev is None
    assert summary.p99 == 9.0


def test_summarize_nothing():
    assert summarize([]) is None


def test_jitter_is_the_mean_absolute_step():
    assert jitter([10.0, 14.0, 12.0]) == 3.0
    assert jitter([10.0]) is None


def test_scoreboard_matches_each_batch_to_the_first_tick_covering_it():
    batches = [(0, 3), (100, 3), (200, 3)]
    ticks = [(500, 3), (1_000, 6), (1_500, 9)]
    assert scoreboard_latencies(batches, ticks) == [
        (0, 500 / 1e6),
        (100, 900 / 1e6),
        (200, 1_300 / 1e6),
    ]


def test_scoreboard_handles_several_batches_in_one_tick():
    batches = [(0, 2), (10, 2)]
    assert scoreboard_latencies(batches, [(100, 0), (200, 4)]) == [
        (0, 200 / 1e6),
        (10, 190 / 1e6),
    ]


def test_scoreboard_orders_batches_by_send_time_whatever_the_arrival_order():
    batches = [(100, 1), (0, 1)]
    assert scoreboard_latencies(batches, [(500, 1), (900, 2)]) == [
        (0, 500 / 1e6),
        (100, 800 / 1e6),
    ]


def test_scoreboard_reports_batches_the_score_never_covers():
    assert scoreboard_latencies([(0, 2), (10, 2)], [(100, 2)]) == [
        (0, 100 / 1e6),
        (10, None),
    ]


def test_scoreboard_never_credits_a_tick_older_than_the_batch():
    # The score already covers the batch (pulls the client never saw confirmed), but the tick
    # came first; latency must come from the next tick, not be negative.
    assert scoreboard_latencies([(300, 1)], [(100, 5), (700, 5)]) == [(300, 400 / 1e6)]


def test_scoreboard_with_no_ticks():
    assert scoreboard_latencies([(0, 1)], []) == [(0, None)]


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("0:01.23", 1.23),  # BSD
        ("1:02.50", 62.5),
        ("00:00:07", 7.0),  # procps
        ("01:00:00", 3600.0),
        ("2-00:00:01", 172_801.0),
        ("0:01,50", 1.5),  # a comma from a localized ps
    ],
)
def test_parse_cpu_time(text, seconds):
    assert parse_cpu_time(text) == pytest.approx(seconds)


@pytest.mark.parametrize(
    "secret",
    [
        "Endpoint=sb://ns.servicebus.windows.net/;SharedAccessKeyName=Root;SharedAccessKey=abc=",
        "SharedAccessKey=abc123",
        "SharedAccessSignature sr=amqp%3A&sig=abc",
        "/api/matches/X/me?playerId=1&token=eyJhbGci",
    ],
)
def test_redact_removes_credentials(secret):
    cleaned = redact(f"falha: {secret} fim")
    assert "abc" not in cleaned
    assert "eyJhbGci" not in cleaned
    assert cleaned.endswith("fim")


def test_redact_keeps_ordinary_text():
    assert redact("Service Bus send failed for 3 message(s)") == (
        "Service Bus send failed for 3 message(s)"
    )


def test_levels_sort_numerically():
    levels = ["200", "-", "10", "50"]
    assert sorted(levels, key=level_sort_key) == ["10", "50", "200", "-"]
