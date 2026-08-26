"""Bounded in-process HTTP performance telemetry for the web console."""

from collections import defaultdict, deque
from datetime import datetime, timezone
import math
import re
import threading
from typing import Deque, Dict, Iterable, List


API_PERFORMANCE_VERSION = "api-performance-v1"
DEFAULT_SAMPLE_LIMIT = 240


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def route_template(path: object) -> str:
    """Remove high-cardinality identifiers while preserving the API shape."""

    value = str(path or "/").split("?", 1)[0] or "/"
    if not value.startswith("/api/"):
        return value
    segments = value.split("/")
    stable = {
        "access", "account", "accounts", "activity", "ai-review", "analysis",
        "approve", "baseline", "bootstrap", "candidates", "catalog", "classes",
        "comparisons", "dashboard", "decisions", "delete", "delivery", "detail",
        "diagnostics", "discover", "episodes", "events", "evidence", "experiments",
        "external-data", "flow-lens", "health", "history", "hypotheses", "inferences",
        "instruments", "investment-analysis", "investment-brain", "investment-calendar",
        "investment-cases", "investment-flow", "investment-model", "investment-reasoning",
        "investment-strategy-proposals", "investment-validation", "language", "lifecycles",
        "lineage", "market", "notifications", "notification-jobs", "ontology", "operations",
        "performance", "policy", "portfolio", "positions", "profile", "reasoning",
        "reasoning-engine", "rebalance", "refresh", "replay", "research-evidence", "rules",
        "research-runs", "rulebox", "run", "schedules", "sections", "service-accounts", "settings", "share",
        "status", "suggest", "summary", "sync", "templates", "time-series-platform",
        "timeline", "trace", "validate", "version", "watchlist", "workspace",
        "hypothesis-lifecycles", "hypothesis-templates", "hypothesis-policy-versions",
    }
    normalized = []
    for index, segment in enumerate(segments):
        if index < 3 or not segment or segment in stable:
            normalized.append(segment)
            continue
        decoded = segment.lower()
        identifier_like = (
            len(segment) >= 12
            or bool(re.fullmatch(r"\d{6,}", segment))
            or bool(re.fullmatch(r"[0-9a-f]{8,}", decoded))
            or ":" in segment
            or "." in segment
        )
        normalized.append("{id}" if identifier_like else segment)
    return "/".join(normalized)


def _percentile(values: Iterable[float], percentile: float) -> float:
    ordered = sorted(float(value or 0.0) for value in values)
    if not ordered:
        return 0.0
    rank = max(0, min(len(ordered) - 1, math.ceil((percentile / 100.0) * len(ordered)) - 1))
    return round(ordered[rank], 2)


class ApiPerformanceRegistry:
    """Keep a bounded sample per route; telemetry must never become storage work."""

    def __init__(self, sample_limit: int = DEFAULT_SAMPLE_LIMIT):
        self.sample_limit = max(10, int(sample_limit or DEFAULT_SAMPLE_LIMIT))
        self._lock = threading.Lock()
        self._samples: Dict[str, Deque[Dict[str, object]]] = defaultdict(
            lambda: deque(maxlen=self.sample_limit)
        )

    def record(
        self,
        method: object,
        path: object,
        status: int,
        duration_ms: float,
        raw_bytes: int,
        wire_bytes: int,
        compressed: bool,
    ) -> None:
        route = route_template(path)
        key = str(method or "GET").upper() + " " + route
        sample = {
            "occurredAt": _utc_now_iso(),
            "status": int(status or 0),
            "durationMs": round(max(0.0, float(duration_ms or 0.0)), 2),
            "rawBytes": max(0, int(raw_bytes or 0)),
            "wireBytes": max(0, int(wire_bytes or 0)),
            "compressed": bool(compressed),
        }
        with self._lock:
            self._samples[key].append(sample)

    def snapshot(self) -> Dict[str, object]:
        with self._lock:
            copied = {key: list(values) for key, values in self._samples.items()}
        routes: List[Dict[str, object]] = []
        for key, rows in copied.items():
            method, route = key.split(" ", 1)
            durations = [float(row.get("durationMs") or 0.0) for row in rows]
            raw_sizes = [int(row.get("rawBytes") or 0) for row in rows]
            wire_sizes = [int(row.get("wireBytes") or 0) for row in rows]
            errors = sum(int(row.get("status") or 0) >= 400 for row in rows)
            latest = rows[-1] if rows else {}
            routes.append({
                "method": method,
                "route": route,
                "sampleCount": len(rows),
                "errorCount": errors,
                "p50Ms": _percentile(durations, 50),
                "p95Ms": _percentile(durations, 95),
                "maxMs": round(max(durations, default=0.0), 2),
                "averageRawBytes": round(sum(raw_sizes) / len(raw_sizes)) if raw_sizes else 0,
                "averageWireBytes": round(sum(wire_sizes) / len(wire_sizes)) if wire_sizes else 0,
                "maxRawBytes": max(raw_sizes, default=0),
                "lastStatus": int(latest.get("status") or 0),
                "lastObservedAt": str(latest.get("occurredAt") or ""),
            })
        routes.sort(key=lambda item: (float(item.get("p95Ms") or 0), int(item.get("maxRawBytes") or 0)), reverse=True)
        return {
            "version": API_PERFORMANCE_VERSION,
            "generatedAt": _utc_now_iso(),
            "sampleLimitPerRoute": self.sample_limit,
            "routeCount": len(routes),
            "sampleCount": sum(int(item.get("sampleCount") or 0) for item in routes),
            "budgets": {
                "listP95Ms": 500,
                "detailP95Ms": 1000,
                "listPreferredRawBytes": 50000,
                "listMaximumRawBytes": 100000,
            },
            "routes": routes,
        }

    def clear(self) -> None:
        with self._lock:
            self._samples.clear()
