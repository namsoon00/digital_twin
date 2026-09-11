from digital_twin.shared_kernel.event_payloads import compact_fact_revisions_for_event
from digital_twin.modules.reasoning.domain.event_types import MAX_REASONING_SOURCE_FACTS_PER_EVENT, MAX_DERIVED_DOCUMENT_SOURCE_FACTS_PER_EVENT, ONTOLOGY_REASONING_REQUESTED, ONTOLOGY_REASONING_COMPLETED, ONTOLOGY_REASONING_QUEUE_HEALTH_CHANGED, INVESTMENT_ALERT_COVERAGE_CHANGED
from digital_twin.modules.news_intelligence.contracts import compact_evidence_delta_event_payloads
from digital_twin.modules.news_intelligence.contracts import compact_materiality_assessment_event_payloads
from digital_twin.modules.reasoning.domain.reasoning_source_facts import compact_reasoning_source_fact_payload, reasoning_source_fact
from digital_twin.shared_kernel.events import _event_text
from digital_twin.shared_kernel.events import _event_text_list
from typing import Dict, Iterable, List, Mapping


def compact_ontology_reasoning_request_payload_for_storage(payload: Mapping[str, object]) -> Dict[str, object]:
    source = dict(payload or {})
    compact: Dict[str, object] = {}
    for key, limit in {
        "trigger": 96,
        "sourceEventId": 191,
        "sourceEventName": 191,
        "sourceAggregateId": 191,
        "reason": 1200,
        "sourceObservedAt": 40,
        "dispatchMode": 96,
        "importanceGate": 96,
        "materialityRole": 96,
        "researchRunId": 191,
        "accountId": 191,
        "rebalanceReviewWindow": 80,
        "subjectKind": 40,
        "subjectId": 191,
        "subjectRevision": 191,
    }.items():
        text = _event_text(source.get(key), limit)
        if text:
            compact[key] = text
    for key in ("changedCount", "observedCount"):
        try:
            compact[key] = max(0, int(source.get(key) or 0))
        except (TypeError, ValueError):
            pass
    for key, limit, item_limit in (
        ("symbols", 200, 64),
        ("affectedSymbols", 200, 64),
        ("observationFollowupSymbols", 200, 64),
        ("factTypes", 20, 96),
        ("changedEvidenceIds", 200, 191),
    ):
        values = _event_text_list(source.get(key), limit=limit, item_limit=item_limit)
        if values:
            compact[key] = values
    fact_types_by_symbol = source.get("factTypesBySymbol")
    if isinstance(fact_types_by_symbol, Mapping):
        compact_fact_types = {}
        for symbol, values in fact_types_by_symbol.items():
            clean_symbol = _event_text(symbol, 64).upper()
            clean_types = _event_text_list(values, limit=20, item_limit=96)
            if clean_symbol and clean_types:
                compact_fact_types[clean_symbol] = clean_types
            if len(compact_fact_types) >= 200:
                break
        if compact_fact_types:
            compact["factTypesBySymbol"] = compact_fact_types
    assessments = compact_materiality_assessment_event_payloads(source.get("materialityAssessments"), limit=100)
    if assessments:
        compact["materialityAssessments"] = assessments
    deltas = compact_evidence_delta_event_payloads(source.get("evidenceDeltas"), limit=200)
    if deltas:
        compact["evidenceDeltas"] = deltas
    revisions = compact_fact_revisions_for_event(source.get("factRevisionsBySymbol"), limit=200)
    if revisions:
        compact["factRevisionsBySymbol"] = revisions
    changed_fields = source.get("changedFieldsBySymbol")
    if isinstance(changed_fields, Mapping):
        compact_fields = {}
        for symbol, fields in changed_fields.items():
            clean_symbol = _event_text(symbol, 64).upper()
            clean_fields = _event_text_list(fields, limit=80, item_limit=96)
            if clean_symbol and clean_fields:
                compact_fields[clean_symbol] = clean_fields
            if len(compact_fields) >= 200:
                break
        if compact_fields:
            compact["changedFieldsBySymbol"] = compact_fields
    subject_changed_fields = _event_text_list(
        source.get("subjectChangedFields"),
        limit=80,
        item_limit=96,
    )
    if subject_changed_fields:
        compact["subjectChangedFields"] = subject_changed_fields
    source_facts = []
    for raw in source.get("sourceFacts") or []:
        if not isinstance(raw, Mapping):
            continue
        fact_payload = compact_reasoning_source_fact_payload(raw.get("payload") or {})
        source_facts.append({
            "version": _event_text(raw.get("version"), 64),
            "factId": _event_text(raw.get("factId"), 191),
            "factType": _event_text(raw.get("factType"), 96),
            "aggregateId": _event_text(raw.get("aggregateId"), 191),
            "subjectIds": _event_text_list(raw.get("subjectIds"), limit=100, item_limit=64),
            "revision": _event_text(raw.get("revision"), 64),
            "sourceEventId": _event_text(raw.get("sourceEventId"), 191),
            "sourceEventName": _event_text(raw.get("sourceEventName"), 191),
            "observedAt": _event_text(raw.get("observedAt"), 40),
            "ingestedAt": _event_text(raw.get("ingestedAt"), 40),
            "validFrom": _event_text(raw.get("validFrom"), 40),
            "validTo": _event_text(raw.get("validTo"), 40),
            "qualityState": _event_text(raw.get("qualityState"), 64),
            "payload": fact_payload if isinstance(fact_payload, Mapping) else {},
        })
        if len(source_facts) >= MAX_REASONING_SOURCE_FACTS_PER_EVENT:
            break
    if source_facts:
        compact["sourceFacts"] = source_facts
    repair_requests = source.get("scopeRepairRequestsBySymbol")
    if isinstance(repair_requests, Mapping):
        compact_repairs = {}
        for symbol, raw in repair_requests.items():
            clean_symbol = _event_text(symbol, 64).upper()
            value = dict(raw or {}) if isinstance(raw, Mapping) else {}
            scope_ids = _event_text_list(value.get("scopeIds"), limit=40, item_limit=191)
            request_id = _event_text(value.get("requestId"), 191)
            if clean_symbol and scope_ids and request_id:
                compact_repairs[clean_symbol] = {
                    "requestId": request_id,
                    "scopeIds": scope_ids,
                }
            if len(compact_repairs) >= 80:
                break
        if compact_repairs:
            compact["scopeRepairRequestsBySymbol"] = compact_repairs
    # These are strict generation contracts. They are already bounded by their
    # own domain types, so retain them intact rather than silently weakening a
    # research acknowledgement.
    for key in ("reasoningHandoff", "hypothesisResearchBrief", "verifiedSourceSnapshot"):
        value = source.get(key)
        if isinstance(value, Mapping):
            compact[key] = dict(value)
    contract = source.get("factChangeContract")
    if isinstance(contract, Mapping):
        compact["factChangeContract"] = {
            "version": _event_text(contract.get("version"), 64),
            "status": _event_text(contract.get("status"), 64),
            "factTypes": _event_text_list(contract.get("factTypes"), limit=20, item_limit=96),
            "scopeFamilies": _event_text_list(contract.get("scopeFamilies"), limit=30, item_limit=64),
            "scopeFamiliesBySymbol": {
                _event_text(symbol, 64).upper(): _event_text_list(values, limit=30, item_limit=64)
                for symbol, values in dict(contract.get("scopeFamiliesBySymbol") or {}).items()
                if _event_text(symbol, 64)
            },
            "dependencyKeys": _event_text_list(
                contract.get("dependencyKeys"), limit=160, item_limit=128
            ),
            "dependencyKeysBySymbol": {
                _event_text(symbol, 64).upper(): _event_text_list(values, limit=160, item_limit=128)
                for symbol, values in dict(contract.get("dependencyKeysBySymbol") or {}).items()
                if _event_text(symbol, 64)
            },
            "dependencyKeysComplete": bool(contract.get("dependencyKeysComplete")),
            "dependencyKeysCompleteBySymbol": {
                _event_text(symbol, 64).upper(): bool(value)
                for symbol, value in dict(contract.get("dependencyKeysCompleteBySymbol") or {}).items()
                if _event_text(symbol, 64)
            },
            "unclassifiedFactTypes": _event_text_list(
                contract.get("unclassifiedFactTypes"), limit=20, item_limit=96
            ),
            "unclassifiedFactTypesBySymbol": {
                _event_text(symbol, 64).upper(): _event_text_list(values, limit=20, item_limit=96)
                for symbol, values in dict(contract.get("unclassifiedFactTypesBySymbol") or {}).items()
                if _event_text(symbol, 64)
            },
        }
    return compact


def compact_snapshot_event_metadata(metadata: Dict[str, object]) -> Dict[str, object]:
    compact = dict(metadata or {})
    compact.pop("previousMonitorState", None)
    compact.pop("monitorStateHistory", None)
    ontology = compact.get("ontology")
    if isinstance(ontology, dict):
        compact["ontology"] = compact_ontology_event_metadata(ontology)
    return compact


def compact_ontology_event_metadata(ontology: Dict[str, object]) -> Dict[str, object]:
    compact = {}
    for key in ["activeGraphStore", "previousStateAvailable"]:
        if key in ontology:
            compact[key] = ontology.get(key)
    for key in ["projection", "typedb", "neo4j", "typeDb"]:
        value = ontology.get(key)
        if isinstance(value, dict):
            compact[key] = compact_ontology_projection_metadata(value)
    state = ontology.get("inferenceMissingState")
    if isinstance(state, dict):
        compact["inferenceMissingState"] = {
            key: state.get(key)
            for key in ["reasonCode", "reason", "status", "graphStore", "createdAt", "updatedAt"]
            if state.get(key) not in (None, "", [], {})
        }
    return compact


def compact_ontology_projection_metadata(value: Dict[str, object]) -> Dict[str, object]:
    allowed = [
        "status",
        "saved",
        "graphStore",
        "activeGraphStore",
        "projectionMode",
        "reason",
        "entityCount",
        "relationCount",
        "aboxEntityCount",
        "aboxRelationCount",
        "qualitySampleId",
        "qualityState",
    ]
    compact = {
        key: value.get(key)
        for key in allowed
        if value.get(key) not in (None, "", [], {})
    }
    validation = value.get("aboxValidation")
    if isinstance(validation, dict):
        compact["aboxValidation"] = {
            key: validation.get(key)
            for key in ["status", "errorCount", "warningCount"]
            if validation.get(key) not in (None, "", [], {})
        }
    rulebox = value.get("ruleboxExecution")
    if isinstance(rulebox, dict):
        compact["ruleboxExecution"] = {
            key: rulebox.get(key)
            for key in ["status", "reason", "graphStore", "matchedCount", "relationCount", "traceCount", "nativeTypeDbReasoningUsed"]
            if rulebox.get(key) not in (None, "", [], {})
        }
    inference = value.get("inferenceBox")
    if isinstance(inference, dict):
        compact["inferenceBox"] = {
            key: inference.get(key)
            for key in ["status", "reason", "graphStore", "relationCount", "traceCount", "nativeTypeDbReasoningUsed", "typedbReadStatus"]
            if inference.get(key) not in (None, "", [], {})
        }
    return compact
