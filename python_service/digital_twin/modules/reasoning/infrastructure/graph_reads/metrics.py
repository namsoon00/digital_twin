"""graph_reads: metrics through explicit injected capabilities."""

from typing import Dict
import hashlib
import re
from .metrics_ports import GraphReadsMetricsStore


def reset_query_metrics(_store: GraphReadsMetricsStore) -> None:
    with _store._query_metrics_lock:
        _store._query_metrics = []


def record_query_metric(
    _store: GraphReadsMetricsStore,
    label: str,
    query: str,
    row_count: int,
    duration_ms: float,
    status: str = "ok",
) -> None:
    if not _store.query_metrics_enabled():
        return
    normalized_query = re.sub(r"\s+", " ", str(query or "")).strip()
    with _store._query_metrics_lock:
        _store._query_metrics.append(
            {
                "label": str(label or "typedb.read")[:80],
                "status": str(status or "ok"),
                "rowCount": int(row_count or 0),
                "durationMs": round(float(duration_ms or 0.0), 2),
                "queryHash": (
                    hashlib.sha256(normalized_query.encode("utf-8")).hexdigest()[:12]
                    if normalized_query
                    else ""
                ),
                "queryPreview": normalized_query[:180],
            }
        )
        if len(_store._query_metrics) > 120:
            _store._query_metrics = _store._query_metrics[-120:]


def query_metrics_snapshot(_store: GraphReadsMetricsStore) -> Dict[str, object]:
    with _store._query_metrics_lock:
        rows = list(_store._query_metrics or [])
    total_ms = sum(float(item.get("durationMs") or 0) for item in rows)
    slow = sorted(rows, key=lambda item: float(item.get("durationMs") or 0), reverse=True)[:8]
    return {
        "enabled": _store.query_metrics_enabled(),
        "queryCount": len(rows),
        "totalDurationMs": round(total_ms, 2),
        "slowQueries": slow,
    }
