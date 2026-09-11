from digital_twin.platform.domain.event_types import SETTINGS_UPDATED, APP_PROFILE_UPDATED, APP_MEMORY_RECORDED, APP_MEMORY_UPDATED, APP_MEMORY_REMOVED, APP_ITEM_UPDATED, APP_ITEM_REMOVED, CHAT_MESSAGE_APPENDED, DATA_PIPELINE_HEALTH_CHANGED, OPERATIONAL_STORAGE_CAPACITY_CHANGED, SYSTEM_ERROR_REPORTED
from digital_twin.modules.news_intelligence.domain.event_types import HYPOTHESIS_RESEARCH_COMPLETED
from digital_twin.modules.news_intelligence.domain.event_types import RESEARCH_EVIDENCE_COLLECTED
from digital_twin.modules.news_intelligence.domain.event_types import RESEARCH_EVIDENCE_LIFECYCLE_CHANGED
from digital_twin.modules.news_intelligence.domain.event_payloads import compact_evidence_delta_event_payloads
from digital_twin.modules.news_intelligence.domain.event_payloads import compact_research_evidence_event_payload_for_storage
from digital_twin.modules.reasoning.domain.event_types import ONTOLOGY_REASONING_REQUESTED
from digital_twin.shared_kernel.event_payloads import compact_fact_revisions_for_event
from digital_twin.modules.reasoning.domain.event_payloads import compact_ontology_reasoning_request_payload_for_storage
from digital_twin.shared_kernel.events import DomainEvent
from typing import Dict, Iterable, List, Mapping
























def system_error_reported_event(
    component: str,
    error_type: str,
    message: str,
    fingerprint: str,
    occurrence_count: int = 1,
) -> DomainEvent:
    return DomainEvent(
        name=SYSTEM_ERROR_REPORTED,
        aggregate_id="system-error:" + str(fingerprint or "unknown")[:40],
        payload={
            "component": str(component or "system"),
            "errorType": str(error_type or "Exception"),
            "message": str(message or "알 수 없는 오류"),
            "fingerprint": str(fingerprint or ""),
            "occurrenceCount": max(1, int(occurrence_count or 1)),
        },
    )


def operational_storage_capacity_changed_event(payload: Dict[str, object]) -> DomainEvent:
    """Emit an operations-only storage state transition without raw payloads."""

    values = dict(payload or {})
    state = str(values.get("state") or "unknown").strip() or "unknown"
    return DomainEvent(
        name=OPERATIONAL_STORAGE_CAPACITY_CHANGED,
        aggregate_id="operations-storage-capacity",
        payload=values,
        correlation_id="storage-capacity:" + state,
    )


def domain_event_storage_payload(event_name: object, payload: Mapping[str, object]) -> Dict[str, object]:
    """Return the bounded durable representation for an event payload."""
    name = str(event_name or "").strip()
    source = dict(payload or {}) if isinstance(payload, Mapping) else {}
    if name == RESEARCH_EVIDENCE_COLLECTED:
        return compact_research_evidence_event_payload_for_storage(source)
    if name == ONTOLOGY_REASONING_REQUESTED:
        return compact_ontology_reasoning_request_payload_for_storage(source)
    if name in {RESEARCH_EVIDENCE_LIFECYCLE_CHANGED, HYPOTHESIS_RESEARCH_COMPLETED}:
        compact = dict(source)
        compact["evidenceDeltas"] = compact_evidence_delta_event_payloads(source.get("evidenceDeltas"), limit=200)
        compact["factRevisionsBySymbol"] = compact_fact_revisions_for_event(source.get("factRevisionsBySymbol"), limit=200)
        return compact
    return source


def data_pipeline_health_changed_event(payload: Dict[str, object]) -> DomainEvent:
    pipeline = str(payload.get("pipeline") or "unknown")
    return DomainEvent(
        name=DATA_PIPELINE_HEALTH_CHANGED,
        aggregate_id="data-pipeline:" + pipeline,
        payload={
            "pipeline": pipeline,
            "state": str(payload.get("state") or "unknown"),
            "previousState": str(payload.get("previousState") or ""),
            "reasonCode": str(payload.get("reasonCode") or ""),
            "reason": str(payload.get("reason") or ""),
            "checkedAt": str(payload.get("checkedAt") or ""),
            "stateSince": str(payload.get("stateSince") or ""),
            "lastNonZeroAt": str(payload.get("lastNonZeroAt") or ""),
            "consecutiveZeroRuns": int(payload.get("consecutiveZeroRuns") or 0),
            "targetCount": int(payload.get("targetCount") or 0),
            "fetchedCount": int(payload.get("fetchedCount") or 0),
            "savedCount": int(payload.get("savedCount") or 0),
            "providerFailureCount": int(payload.get("providerFailureCount") or 0),
            "providerCandidateCount": int(payload.get("providerCandidateCount") or 0),
            "providers": list(payload.get("providers") or [])[:20],
            "stateChanged": bool(payload.get("stateChanged")),
            "alertRequired": bool(payload.get("alertRequired")),
            "observedState": str(payload.get("observedState") or payload.get("state") or ""),
            "observedReasonCode": str(payload.get("observedReasonCode") or payload.get("reasonCode") or ""),
            "transitionCandidateState": str(payload.get("transitionCandidateState") or ""),
            "transitionCandidateCount": int(payload.get("transitionCandidateCount") or 0),
            "transitionConfirmed": bool(payload.get("transitionConfirmed")),
        },
    )
