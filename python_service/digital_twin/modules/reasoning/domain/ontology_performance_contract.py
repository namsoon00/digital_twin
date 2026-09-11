"""Deterministic completion criteria for one ontology reasoning turn."""

from __future__ import annotations

from typing import Dict, Mapping


ONTOLOGY_PERFORMANCE_CONTRACT_VERSION = "ontology-performance-contract-v1"

DEFAULT_STAGE_BUDGETS_MS = {
    "graphAssemblyMs": 15_000,
    "graphBuildMs": 20_000,
    "projectionMs": 30_000,
    "aboxPersistenceMs": 30_000,
    "nativeInferenceMs": 45_000,
    "resultSlotWriteMs": 5_000,
    "totalMs": 90_000,
}


def _integer(value: object, fallback: int) -> int:
    try:
        return max(1, int(float(value)))
    except (TypeError, ValueError):
        return fallback


def _observed_integer(value: object) -> int:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def ontology_performance_assessment(
    runtime_stages: Mapping[str, object],
    configured_budgets: Mapping[str, object] = None,
) -> Dict[str, object]:
    """Assess observed latency without changing execution or inference."""

    stages = {
        str(key): _observed_integer(value)
        for key, value in dict(runtime_stages or {}).items()
        if isinstance(value, (int, float)) and float(value) >= 0
    }
    configured = dict(configured_budgets or {})
    budgets = {
        stage: _integer(configured.get(stage), default)
        for stage, default in DEFAULT_STAGE_BUDGETS_MS.items()
    }
    rows = []
    for stage, budget_ms in budgets.items():
        if stage not in stages:
            continue
        duration_ms = stages[stage]
        rows.append({
            "stage": stage,
            "durationMs": duration_ms,
            "budgetMs": budget_ms,
            "ratio": round(duration_ms / max(1, budget_ms), 3),
            "withinBudget": duration_ms <= budget_ms,
        })
    rows.sort(key=lambda item: (-float(item["ratio"]), str(item["stage"])))
    violations = [item for item in rows if not item["withinBudget"]]
    worst_ratio = max((float(item["ratio"]) for item in rows), default=0.0)
    status = (
        "critical" if worst_ratio >= 2.0
        else "degraded" if violations
        else "within-budget"
    )
    return {
        "version": ONTOLOGY_PERFORMANCE_CONTRACT_VERSION,
        "status": status,
        "withinBudget": not violations,
        "bottleneckStage": str(rows[0]["stage"]) if rows else "",
        "bottleneckRatio": worst_ratio,
        "observedStageCount": len(rows),
        "violations": violations,
        "stages": rows,
    }
