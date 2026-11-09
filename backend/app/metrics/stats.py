"""Descriptive statistics and the sample store shared by the two tests."""

from __future__ import annotations

import csv
import gzip
import json
import math
import re
import statistics
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

NO_LEVEL = "-"  # the Service Bus test has no player count
MAX_STORED_ERRORS = 5_000  # the counters keep the true totals

_SECRETS = (
    re.compile(r"Endpoint=sb://[^\s\"']+", re.IGNORECASE),
    re.compile(r"SharedAccessKey(?:Name)?=[^;\s\"']+", re.IGNORECASE),
    re.compile(r"SharedAccessSignature\s+\S+", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z])(?:token|sig)=[^&\s\"']+", re.IGNORECASE),
)


def say(message: str) -> None:
    """Print one progress line for the operator."""
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)


def redact(text: str) -> str:
    """Remove connection strings, signatures and tokens from free text."""
    for pattern in _SECRETS:
        text = pattern.sub("[removido]", text)
    return text


@dataclass(frozen=True)
class Summary:
    n: int
    mean: float
    stdev: float | None  # undefined for a single sample
    min: float
    p50: float
    p90: float
    p95: float
    p99: float
    max: float


def percentile(ordered: Sequence[float], q: float) -> float:
    """Percentile ``q`` (0-100) of already sorted values, by linear interpolation."""
    if not ordered:
        raise ValueError("percentile of an empty sequence")
    rank = (len(ordered) - 1) * q / 100
    low, high = math.floor(rank), math.ceil(rank)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def summarize(values: Iterable[float]) -> Summary | None:
    """Summary of ``values``, or ``None`` when there are none."""
    ordered = sorted(values)
    if not ordered:
        return None
    return Summary(
        n=len(ordered),
        mean=statistics.fmean(ordered),
        stdev=statistics.stdev(ordered) if len(ordered) > 1 else None,
        min=ordered[0],
        p50=percentile(ordered, 50),
        p90=percentile(ordered, 90),
        p95=percentile(ordered, 95),
        p99=percentile(ordered, 99),
        max=ordered[-1],
    )


def jitter(values: Sequence[float]) -> float | None:
    """Mean absolute difference between consecutive samples, in the samples' unit."""
    if len(values) < 2:
        return None
    return statistics.fmean(abs(b - a) for a, b in zip(values, values[1:], strict=False))


def scoreboard_latencies(
    batches: Iterable[tuple[int, int]], ticks: Iterable[tuple[int, int]]
) -> list[tuple[int, float | None]]:
    """Latency of each accepted batch, read from a team's cumulative score.

    The tick broadcast carries totals, not batch ids, so this assumes the queue is FIFO within a
    team: a batch counts as applied at the first tick whose score covers every pull sent up to
    and including it.  A tick older than the batch can never be the one that applied it.

    ``batches`` holds ``(started_ns, pulls)`` and ``ticks`` holds ``(arrived_ns, score)``.
    Returns ``(started_ns, latency_ms)`` in send order, with ``None`` for batches never covered.
    """
    ordered_ticks = sorted(ticks)
    latencies: list[tuple[int, float | None]] = []
    covered, index = 0, 0
    for started_ns, pulls in sorted(batches):
        covered += pulls
        while index < len(ordered_ticks) and (
            ordered_ticks[index][1] < covered or ordered_ticks[index][0] < started_ns
        ):
            index += 1
        if index == len(ordered_ticks):
            latencies.append((started_ns, None))
        else:
            latencies.append((started_ns, (ordered_ticks[index][0] - started_ns) / 1e6))
    return latencies


def level_sort_key(level: str) -> tuple[int, int, str]:
    """Numeric levels first, in numeric order; anything else after them."""
    return (0, int(level), "") if level.isdigit() else (1, 0, level)


class Recorder:
    """Collects raw samples, counters and errors for one run, then writes them out."""

    def __init__(self) -> None:
        self.started_ns = time.perf_counter_ns()
        self.samples: list[tuple[str, str, str, float, float]] = []
        self.counters: Counter[tuple[str, str, str]] = Counter()
        self.errors: list[dict[str, Any]] = []

    def elapsed_s(self, at_ns: int | None = None) -> float:
        at = time.perf_counter_ns() if at_ns is None else at_ns
        return (at - self.started_ns) / 1e9

    def add(
        self, test: str, level: str, metric: str, value: float, at_ns: int | None = None
    ) -> None:
        self.samples.append((test, level, metric, round(self.elapsed_s(at_ns), 3), float(value)))

    def count(self, test: str, level: str, name: str, amount: int = 1) -> None:
        self.counters[(test, level, name)] += amount

    def error(self, test: str, level: str, where: str, problem: BaseException | str) -> None:
        kind = problem if isinstance(problem, str) else type(problem).__name__
        detail = "" if isinstance(problem, str) else redact(str(problem))[:300]
        self.count(test, level, f"error:{where}:{kind}")
        if len(self.errors) < MAX_STORED_ERRORS:
            self.errors.append(
                {
                    "t_s": round(self.elapsed_s(), 3),
                    "test": test,
                    "level": level,
                    "where": where,
                    "kind": kind,
                    "detail": detail,
                }
            )

    def summaries(self) -> list[dict[str, Any]]:
        groups: dict[tuple[str, str, str], list[float]] = defaultdict(list)
        for test, level, metric, _, value in self.samples:
            groups[(test, level, metric)].append(value)
        ordered = sorted(groups.items(), key=lambda g: (g[0][0], level_sort_key(g[0][1]), g[0][2]))
        return [
            {"test": test, "level": level, "metric": metric, **asdict(summarize(values))}
            for (test, level, metric), values in ordered
        ]

    def write(self, run_dir: Path, results: dict[str, Any]) -> None:
        """Write the raw samples, the per-metric summary, the errors and ``summary.json``."""
        with gzip.open(run_dir / "samples.csv.gz", "wt", encoding="utf-8", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["test", "level", "metric", "t_s", "value"])
            writer.writerows(self.samples)

        summaries = self.summaries()
        columns = ["test", "level", "metric", *Summary.__dataclass_fields__]
        with (run_dir / "summary.csv").open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=columns)
            writer.writeheader()
            writer.writerows(summaries)

        with (run_dir / "errors.jsonl").open("w", encoding="utf-8") as file:
            for error in self.errors:
                file.write(json.dumps(error, ensure_ascii=False) + "\n")

        counters = [
            {"test": test, "level": level, "name": name, "value": value}
            for (test, level, name), value in sorted(self.counters.items())
        ]
        document = {"metrics": summaries, "counters": counters, "results": results}
        (run_dir / "summary.json").write_text(
            json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8"
        )
