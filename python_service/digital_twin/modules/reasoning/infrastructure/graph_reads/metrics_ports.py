"""Explicit capabilities for graph_reads/metrics; no repository or application imports."""

from dataclasses import dataclass
from typing import Any, Callable, Protocol


class GraphReadsMetricsStore(Protocol):
    _query_metrics: Any

    _query_metrics_lock: Any

    def query_metrics_enabled(self) -> bool: ...
