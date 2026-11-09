"""Optional: server-side metrics of the namespace, from Azure Monitor, for a finished run.

Needs the ``metrics`` extra and an Azure login (``az login`` or a service principal in the
environment).  Azure publishes these numbers with a delay of a few minutes, so this is a separate
command that runs after the test.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.metrics.report import AZURE_MONITOR_CSV, AZURE_MONITOR_STATUS
from app.metrics.stats import say

GRANULARITY = timedelta(minutes=1)
WINDOW_PADDING = timedelta(minutes=1)
ENTITY_DIMENSION = "EntityName"
COLUMNS = (
    "metric",
    "unit",
    "dimensions",
    "timestamp",
    "average",
    "minimum",
    "maximum",
    "total",
    "count",
)


def _rows_of(result: Any) -> list[dict[str, Any]]:  # noqa: ANN401 - SDK result object
    rows = []
    for metric in result.metrics:
        for series in metric.timeseries:
            labels = series.metadata_values or {}
            dimensions = ";".join(f"{k}={v}" for k, v in sorted(labels.items()))
            for point in series.data:
                values = {
                    name: getattr(point, name)
                    for name in ("average", "minimum", "maximum", "total", "count")
                }
                if all(value is None for value in values.values()):
                    continue
                rows.append(
                    {
                        "metric": metric.name,
                        "unit": str(metric.unit),
                        "dimensions": dimensions,
                        "timestamp": point.timestamp.isoformat(),
                        **values,
                    }
                )
    return rows


def _aggregations(definition: Any) -> list[str] | None:  # noqa: ANN401 - SDK model object
    """The aggregations this metric supports; Azure also lists a "None" pseudo-aggregation."""
    supported = [str(a) for a in definition.supported_aggregation_types or [] if str(a) != "None"]
    return supported or None


def collect(run_dir: Path, resource_id: str) -> Path:
    """Fetch every metric the namespace exposes for the run's window into ``azure_monitor.csv``."""
    try:
        from azure.core.exceptions import HttpResponseError
        from azure.identity import DefaultAzureCredential
        from azure.monitor.query import MetricsQueryClient
    except ImportError as exc:
        raise SystemExit("Instale o extra: pip install -e '.[metrics]'") from exc

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    window = (
        datetime.fromisoformat(manifest["started_at_utc"]) - WINDOW_PADDING,
        datetime.fromisoformat(manifest["finished_at_utc"]) + WINDOW_PADDING,
    )
    client = MetricsQueryClient(DefaultAzureCredential())
    rows: list[dict[str, Any]] = []
    empty: list[str] = []
    failed: dict[str, str] = {}
    definitions = list(client.list_metric_definitions(resource_id))
    say(f"[azure monitor] {len(definitions)} métricas disponíveis no recurso")
    for definition in definitions:
        dimensions = definition.dimensions or []
        try:
            result = client.query_resource(
                resource_id,
                [definition.name],
                timespan=window,
                granularity=GRANULARITY,
                aggregations=_aggregations(definition),
                filter=f"{ENTITY_DIMENSION} eq '*'" if ENTITY_DIMENSION in dimensions else None,
            )
        except HttpResponseError as exc:
            failed[definition.name] = type(exc).__name__
            continue
        found = _rows_of(result)
        if found:
            rows += found
        else:
            empty.append(definition.name)

    path = run_dir / AZURE_MONITOR_CSV
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    status = {
        "window_utc": [moment.isoformat() for moment in window],
        "metrics_available": [definition.name for definition in definitions],
        "empty": sorted(empty),
        "failed": failed,
    }
    (run_dir / AZURE_MONITOR_STATUS).write_text(
        json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path
