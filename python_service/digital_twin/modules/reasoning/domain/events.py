from digital_twin.modules.reasoning.domain.event_payloads import compact_ontology_event_metadata, compact_ontology_projection_metadata, compact_ontology_reasoning_request_payload_for_storage, compact_snapshot_event_metadata
from digital_twin.shared_kernel.event_payloads import compact_fact_revisions_for_event
from digital_twin.modules.reasoning.domain.event_types import MAX_REASONING_SOURCE_FACTS_PER_EVENT, MAX_DERIVED_DOCUMENT_SOURCE_FACTS_PER_EVENT, ONTOLOGY_REASONING_REQUESTED, ONTOLOGY_REASONING_COMPLETED, ONTOLOGY_REASONING_QUEUE_HEALTH_CHANGED, INVESTMENT_ALERT_COVERAGE_CHANGED
from dataclasses import asdict, dataclass, field
from digital_twin.modules.market_data.contracts import EXTERNAL_FACT_CHANGED
from digital_twin.modules.news_intelligence.contracts import compact_evidence_delta_event_payloads
from digital_twin.modules.news_intelligence.contracts import compact_materiality_assessment_event_payloads
from digital_twin.modules.news_intelligence.contracts import compact_research_item_for_event_storage
from digital_twin.modules.reasoning.domain.fact_changes import fact_change_contract, follow_up_field_fact_types
from digital_twin.modules.reasoning.domain.reasoning_source_facts import compact_reasoning_source_fact_payload, reasoning_source_fact
from digital_twin.shared_kernel.events import DomainEvent
from digital_twin.shared_kernel.events import _event_text
from digital_twin.shared_kernel.events import _event_text_list
from typing import Dict, Iterable, List, Mapping
























def _research_source_fact_type(item: Mapping[str, object]) -> str:
    kind = str(item.get("kind") or "").strip().lower()
    if "news" in kind:
        return "NewsArticle"
    if "disclosure" in kind or "filing" in kind:
        return "DisclosureFiling"
    if "financial" in kind or "earning" in kind:
        return "FinancialFact"
    return "ResearchEvidence"


def _reasoning_source_facts_from_event(
    source_event: DomainEvent,
    symbols: Iterable[str],
    fact_types_by_symbol: Mapping[str, Iterable[str]],
    revisions_by_symbol: Mapping[str, object],
) -> List[Dict[str, object]]:
    """Derive bounded immutable facts from a research/external source event."""

    source_payload = source_event.payload if isinstance(source_event.payload, Mapping) else {}
    target_symbols = {
        str(symbol or "").upper().strip()
        for symbol in symbols or []
        if str(symbol or "").strip()
    }
    raw_items = (
        source_payload.get("materialChangedItems")
        or source_payload.get("changedItems")
        or []
    )
    facts: List[Dict[str, object]] = []
    seen = set()
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, Mapping):
            continue
        item = compact_research_item_for_event_storage(raw)
        symbol = str(item.get("symbol") or "").upper().strip()
        if not symbol or (target_symbols and symbol not in target_symbols):
            continue
        aggregate_id = str(item.get("evidenceId") or "").strip()
        if not aggregate_id:
            aggregate_id = (str(source_event.aggregate_id or "research") + ":" + symbol + ":" + str(index))[:191]
        fact_type = _research_source_fact_type(item)
        key = (fact_type, aggregate_id)
        if key in seen:
            continue
        seen.add(key)
        governance = item.get("evidenceGovernance") if isinstance(item.get("evidenceGovernance"), Mapping) else {}
        admission = item.get("promptEvidenceAdmission") if isinstance(item.get("promptEvidenceAdmission"), Mapping) else {}
        eligible = bool(
            governance.get("investmentJudgmentEligible")
            or admission.get("decisionEligible")
        )
        observed_at = str(
            item.get("observedAt")
            or item.get("sourceAsOf")
            or item.get("publishedAt")
            or source_event.occurred_at
            or ""
        )
        fact = reasoning_source_fact(
            fact_type=fact_type,
            aggregate_id=aggregate_id,
            subject_ids=[symbol],
            source_event=source_event,
            payload=item,
            revision=str(
                item.get("articleEnrichmentRevision")
                or item.get("sourceRevision")
                or item.get("documentHash")
                or revisions_by_symbol.get(symbol)
                or ""
            ),
            observed_at=observed_at,
            valid_from=str(item.get("publishedAt") or observed_at),
            quality_state=(
                "verified-source-boundary" if eligible else "conditional-source-boundary"
            ),
        )
        facts.append(fact.request_payload())
        if len(facts) >= MAX_DERIVED_DOCUMENT_SOURCE_FACTS_PER_EVENT:
            break

    if facts or source_event.name != EXTERNAL_FACT_CHANGED:
        return facts

    # External dataset events intentionally carry metadata, not the canonical
    # document. Persist that exact revision boundary so delayed workers can
    # explain which provider fact caused the projection refresh.
    for symbol in sorted(target_symbols):
        requested_types = list(fact_types_by_symbol.get(symbol) or []) or ["ExternalFact"]
        for fact_type in requested_types:
            fact = reasoning_source_fact(
                fact_type=str(fact_type or "ExternalFact"),
                aggregate_id=str(source_event.aggregate_id or ("external:" + symbol)),
                subject_ids=[symbol],
                source_event=source_event,
                payload=source_payload,
                revision=str(revisions_by_symbol.get(symbol) or source_payload.get("sourceRevision") or ""),
                observed_at=str(source_payload.get("sourceAsOf") or source_event.occurred_at or ""),
            )
            facts.append(fact.request_payload())
            if len(facts) >= MAX_DERIVED_DOCUMENT_SOURCE_FACTS_PER_EVENT:
                return facts
    return facts


def ontology_reasoning_requested_event(
    source_event: DomainEvent,
    trigger: str,
    symbols: Iterable[str] = None,
    changed_count: int = 0,
    observed_count: int = 0,
    fact_types: Iterable[str] = None,
    reason: str = "",
    materiality_assessments=None,
    fact_revisions_by_symbol: Dict[str, object] = None,
    changed_fields_by_symbol: Dict[str, Iterable[str]] = None,
    evidence_deltas: Iterable[Dict[str, object]] = None,
    snapshot_barrier: Mapping[str, object] = None,
    observation_followup_symbols: Iterable[str] = None,
    importance_gate: str = "fact-revision-first",
    materiality_role: str = "advisory-priority-only",
    fact_types_by_symbol: Dict[str, Iterable[str]] = None,
    scope_repair_requests_by_symbol: Dict[str, object] = None,
    subject_kind: str = "",
    subject_id: str = "",
    affected_symbols: Iterable[str] = None,
    subject_revision: str = "",
    subject_changed_fields: Iterable[str] = None,
    account_id: str = "",
    rebalance_review_window: str = "",
    source_facts: Iterable[Mapping[str, object]] = None,
) -> DomainEvent:
    clean_symbols = sorted(set(str(symbol or "").upper().strip() for symbol in (symbols or []) if str(symbol or "").strip()))
    clean_observation_followups = sorted({
        str(symbol or "").upper().strip()
        for symbol in (observation_followup_symbols or [])
        if str(symbol or "").strip()
    }.intersection(clean_symbols))
    clean_fact_types = sorted(set(str(item or "").strip() for item in (fact_types or []) if str(item or "").strip()))
    clean_subject_kind = str(subject_kind or "").upper().strip()[:40]
    clean_subject_id = str(subject_id or "").strip()[:191]
    clean_affected_symbols = sorted({
        str(symbol or "").upper().strip()
        for symbol in (affected_symbols or [])
        if str(symbol or "").strip()
    })[:200]
    clean_subject_revision = str(subject_revision or "").strip()[:191]
    clean_subject_changed_fields = [
        str(field or "").strip()
        for field in (subject_changed_fields or [])
        if str(field or "").strip()
    ][:80]
    source_payload = source_event.payload or {}
    handoff = source_payload.get("reasoningHandoff") if isinstance(source_payload.get("reasoningHandoff"), dict) else {}
    research_brief = source_payload.get("hypothesisResearchBrief") if isinstance(source_payload.get("hypothesisResearchBrief"), dict) else {}
    raw_revisions = fact_revisions_by_symbol if isinstance(fact_revisions_by_symbol, dict) else source_payload.get("factRevisionsBySymbol")
    raw_revisions = raw_revisions if isinstance(raw_revisions, dict) else {}
    raw_changed_fields = changed_fields_by_symbol if isinstance(changed_fields_by_symbol, dict) else source_payload.get("changedFieldsBySymbol")
    raw_changed_fields = raw_changed_fields if isinstance(raw_changed_fields, dict) else {}
    raw_fact_types_by_symbol = fact_types_by_symbol if isinstance(fact_types_by_symbol, Mapping) else source_payload.get("factTypesBySymbol")
    raw_fact_types_by_symbol = raw_fact_types_by_symbol if isinstance(raw_fact_types_by_symbol, Mapping) else {}
    revisions = {}
    changed_fields = {}
    symbol_fact_types = {}
    for symbol in clean_symbols:
        revision = str(raw_revisions.get(symbol) or raw_revisions.get(symbol.upper()) or "").strip()
        if revision:
            revisions[symbol] = revision[:160]
        fields = raw_changed_fields.get(symbol)
        if fields is None:
            fields = raw_changed_fields.get(symbol.upper())
        if isinstance(fields, (list, tuple, set)):
            changed_fields[symbol] = [str(field or "").strip() for field in fields if str(field or "").strip()][:80]
        values = raw_fact_types_by_symbol.get(symbol)
        if values is None:
            values = raw_fact_types_by_symbol.get(symbol.upper())
        if isinstance(values, (list, tuple, set)):
            clean_values = sorted({
                str(value or "").strip()
                for value in values
                if str(value or "").strip()
            })
            if clean_values:
                symbol_fact_types[symbol] = clean_values[:20]
    change_contract = fact_change_contract(
        clean_fact_types,
        symbol_fact_types,
        changed_fields,
    )
    source_observed_at = next((
        str(source_payload.get(key) or "").strip()
        for key in ["sourceObservedAt", "sourceAsOf", "observedAt", "generatedAt", "collectedAt"]
        if str(source_payload.get(key) or "").strip()
    ), str(source_event.occurred_at or ""))
    changed_evidence_ids = [
        str(item or "").strip()
        for item in (source_payload.get("changedEvidenceIds") or source_payload.get("verifiedClaims") or [])
        if str(item or "").strip()
    ]
    raw_deltas = evidence_deltas if evidence_deltas is not None else source_payload.get("evidenceDeltas")
    deltas = compact_evidence_delta_event_payloads(raw_deltas, limit=200)
    bounded_source_facts = (
        [dict(item) for item in source_facts or [] if isinstance(item, Mapping)]
        if source_facts is not None
        else _reasoning_source_facts_from_event(
            source_event,
            clean_symbols,
            symbol_fact_types,
            revisions,
        )
    )
    return DomainEvent(
        name=ONTOLOGY_REASONING_REQUESTED,
        aggregate_id="ontology:" + (
            clean_subject_id
            or ",".join(clean_symbols)
            or str(trigger or "all")
        )[:180],
        correlation_id=source_event.correlation_id or source_event.event_id,
        payload=compact_ontology_reasoning_request_payload_for_storage({
            "trigger": str(trigger or "data-update"),
            "sourceEventId": source_event.event_id,
            "sourceEventName": source_event.name,
            "sourceAggregateId": source_event.aggregate_id,
            "symbols": clean_symbols[:200],
            "subjectKind": clean_subject_kind,
            "subjectId": clean_subject_id,
            "affectedSymbols": clean_affected_symbols,
            "subjectRevision": clean_subject_revision,
            "subjectChangedFields": clean_subject_changed_fields,
            # This is delivery/scheduling provenance for a deterministic raw
            # price observation, never a TypeDB rule condition.
            "observationFollowupSymbols": clean_observation_followups[:200],
            "changedCount": int(changed_count or 0),
            "observedCount": int(observed_count or 0),
            "factTypes": clean_fact_types[:20],
            # Per-symbol provenance prevents a broad monitor snapshot from
            # making an unrelated research or flow family look changed for
            # every mailbox slot. It is scheduling metadata only.
            "factTypesBySymbol": symbol_fact_types,
            # The contract is routing provenance only. It prevents an unknown
            # provider class name from silently widening a target projection.
            "factChangeContract": change_contract,
            "reason": str(reason or ""),
            # Scheduling uses the original vendor/collection observation time,
            # never the delayed worker publish time, to reject stale data
            # before an expensive TypeDB cycle.
            "sourceObservedAt": source_observed_at,
            "dispatchMode": "data-update-driven",
            # This controls whether a raw fact creates a TypeDB work request;
            # it is not an investment rule or a Python buy/sell decision.
            "importanceGate": str(importance_gate or "fact-revision-first"),
            "materialityRole": str(materiality_role or "advisory-priority-only"),
            "materialityAssessments": compact_materiality_assessment_event_payloads(
                materiality_assessments if materiality_assessments is not None else [],
                limit=100,
            ),
            # Fact revisions are scheduling provenance only. They let the
            # durable mailbox keep the existing pending slot when a provider
            # emits the exact same market fact again.
            "factRevisionsBySymbol": compact_fact_revisions_for_event(revisions, limit=200),
            "changedFieldsBySymbol": changed_fields,
            "researchRunId": str(source_payload.get("runId") or ""),
            "accountId": str(account_id or source_payload.get("accountId") or ""),
            "rebalanceReviewWindow": str(
                rebalance_review_window
                or source_payload.get("rebalanceReviewWindow")
                or ""
            )[:80],
            "changedEvidenceIds": changed_evidence_ids[:200],
            "evidenceDeltas": deltas[:200],
            "reasoningHandoff": handoff,
            "hypothesisResearchBrief": research_brief,
            # A monitor snapshot barrier is operational provenance. It lets
            # the worker distinguish a replayable persisted boundary from a
            # raw provider tick without turning that distinction into an
            # investment rule condition.
            "verifiedSourceSnapshot": dict(snapshot_barrier or {}) if isinstance(snapshot_barrier, Mapping) else {},
            "scopeRepairRequestsBySymbol": (
                dict(scope_repair_requests_by_symbol or {})
                if isinstance(scope_repair_requests_by_symbol, Mapping)
                else {}
            ),
            # Immutable, bounded source facts make delayed/replayed workers
            # reconstruct the exact event that caused this request.
            "sourceFacts": bounded_source_facts[:MAX_REASONING_SOURCE_FACTS_PER_EVENT],
        }),
    )


def ontology_reasoning_completed_event(
    trigger_event_ids: Iterable[str],
    account_ids: Iterable[str],
    symbols: Iterable[str],
    alert_count: int,
    status: str = "ok",
    reason: str = "",
    research_generation_refreshes: Dict[str, object] = None,
    projection_outcomes: Iterable[Dict[str, object]] = None,
) -> DomainEvent:
    clean_trigger_ids = [str(item or "").strip() for item in (trigger_event_ids or []) if str(item or "").strip()]
    clean_accounts = sorted(set(str(item or "").strip() for item in (account_ids or []) if str(item or "").strip()))
    clean_symbols = sorted(set(str(item or "").upper().strip() for item in (symbols or []) if str(item or "").strip()))
    return DomainEvent(
        name=ONTOLOGY_REASONING_COMPLETED,
        aggregate_id="ontology:" + (",".join(clean_accounts) or "all")[:180],
        payload={
            "triggerEventIds": clean_trigger_ids[:200],
            "accountIds": clean_accounts[:100],
            "symbols": clean_symbols[:200],
            "alertCount": int(alert_count or 0),
            "status": str(status or "ok"),
            "reason": str(reason or ""),
            "dispatchMode": "data-update-driven",
            "researchGenerationRefreshes": dict(research_generation_refreshes or {}),
            "projectionOutcomes": [
                dict(item)
                for item in (projection_outcomes or [])
                if isinstance(item, dict)
            ][:100],
        },
    )


def ontology_reasoning_queue_health_changed_event(health: Dict[str, object]) -> DomainEvent:
    payload = dict(health or {})
    return DomainEvent(
        name=ONTOLOGY_REASONING_QUEUE_HEALTH_CHANGED,
        aggregate_id="ontology-reasoning-queue",
        payload=payload,
    )


def investment_alert_coverage_changed_event(health: Dict[str, object]) -> DomainEvent:
    payload = dict(health or {})
    return DomainEvent(
        name=INVESTMENT_ALERT_COVERAGE_CHANGED,
        aggregate_id=str(payload.get("deploymentId") or "investment-alert-coverage"),
        payload=payload,
    )
