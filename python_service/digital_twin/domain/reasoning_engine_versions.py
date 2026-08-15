"""Versioned reasoning-engine contracts and promotion rules."""

from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, Mapping, Protocol, Tuple


REASONING_ENGINE_CONTRACT_VERSION = "investment-reasoning-engine-contract-v1"

ENGINE_STATUSES = {
    "registered",
    "provisioning",
    "replaying",
    "shadow",
    "candidate",
    "active",
    "retired",
    "blocked",
}

ENGINE_TRANSITIONS = {
    "registered": {"provisioning", "blocked", "retired"},
    "provisioning": {"replaying", "shadow", "blocked", "retired"},
    "replaying": {"shadow", "blocked", "retired"},
    "shadow": {"candidate", "blocked", "retired"},
    "candidate": {"active", "shadow", "blocked", "retired"},
    "active": {"candidate", "retired", "blocked"},
    "blocked": {"provisioning", "replaying", "shadow", "retired"},
    "retired": {"provisioning"},
}


def engine_status(value: object, fallback: str = "registered") -> str:
    normalized = str(value or fallback).strip().lower()
    return normalized if normalized in ENGINE_STATUSES else fallback


def engine_transition_allowed(current: object, target: object) -> bool:
    current_status = engine_status(current)
    target_status = engine_status(target)
    return current_status == target_status or target_status in ENGINE_TRANSITIONS.get(current_status, set())


@dataclass(frozen=True)
class EngineReleaseBundle:
    tbox_release_id: str
    rulebox_release_id: str
    prompt_release_id: str
    feature_set_version: str
    source_contract_versions: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["source_contract_versions"] = list(self.source_contract_versions)
        return payload


@dataclass(frozen=True)
class ReasoningEngineDescriptor:
    engine_family: str
    engine_version: str
    deployment_id: str
    status: str
    graph_store_binding: str
    time_series_backend_id: str
    release_bundle: EngineReleaseBundle
    capabilities: Dict[str, bool] = field(default_factory=dict)

    @property
    def engine_id(self) -> str:
        return self.engine_family + ":" + self.engine_version

    def to_dict(self) -> Dict[str, object]:
        return {
            "engineId": self.engine_id,
            "engineFamily": self.engine_family,
            "engineVersion": self.engine_version,
            "deploymentId": self.deployment_id,
            "status": engine_status(self.status),
            "graphStoreBinding": self.graph_store_binding,
            "timeSeriesBackendId": self.time_series_backend_id,
            "releaseBundle": self.release_bundle.to_dict(),
            "capabilities": dict(self.capabilities),
            "contractVersion": REASONING_ENGINE_CONTRACT_VERSION,
        }


@dataclass(frozen=True)
class EngineControlState:
    active_deployment_id: str
    delivery_deployment_id: str
    candidate_deployment_id: str = ""
    version: int = 0

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


class InvestmentReasoningEngine(Protocol):
    def descriptor(self) -> ReasoningEngineDescriptor:
        ...

    def consume(self, source_events: Iterable[Mapping[str, object]]) -> Dict[str, object]:
        ...

    def health(self) -> Dict[str, object]:
        ...

    def explain(self, decision_id: str) -> Dict[str, object]:
        ...


def promotion_blockers(
    descriptor: ReasoningEngineDescriptor,
    health: Mapping[str, object],
    comparison: Mapping[str, object],
) -> Tuple[str, ...]:
    blockers = []
    if engine_status(descriptor.status) != "candidate":
        blockers.append("engine-not-candidate")
    if str(health.get("status") or "").lower() not in {"ready", "healthy"}:
        blockers.append("engine-unhealthy")
    if int(comparison.get("unexplainedDecisionDifferenceCount") or 0) > 0:
        blockers.append("unexplained-decision-differences")
    if float(comparison.get("factParityPct") or 0) < 100:
        blockers.append("fact-parity-incomplete")
    if float(comparison.get("ruleSlotCoveragePct") or 0) < 100:
        blockers.append("rule-slot-coverage-incomplete")
    if int(comparison.get("shadowDeliveryCount") or 0) > 0:
        blockers.append("shadow-delivery-detected")
    return tuple(blockers)
