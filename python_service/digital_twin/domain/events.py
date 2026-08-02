import hashlib
import uuid
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Mapping

from .accounts import AccountConfig
from .portfolio import AccountSnapshot, AlertEvent, utc_now_iso


DOMAIN_EVENT_SCHEMA_VERSION = "domain-event-v1"


ACCOUNT_SAVED = "account.saved"
ACCOUNT_REMOVED = "account.removed"
MONITORING_SNAPSHOT_COLLECTED = "monitoring.snapshot_collected"
MONITORING_ALERTS_DETECTED = "monitoring.alerts_detected"
MONITORING_CYCLE_COMPLETED = "monitoring.cycle_completed"
SETTINGS_UPDATED = "settings.updated"
NOTIFICATION_TEMPLATE_UPDATED = "notification_template.updated"
NOTIFICATION_RULE_UPDATED = "notification_rule.updated"
NOTIFICATION_TEST_REQUESTED = "notification.test_requested"
NOTIFICATION_JOB_QUEUED = "notification.job_queued"
APP_PROFILE_UPDATED = "app.profile_updated"
APP_MEMORY_RECORDED = "app.memory_recorded"
APP_MEMORY_UPDATED = "app.memory_updated"
APP_MEMORY_REMOVED = "app.memory_removed"
APP_ITEM_UPDATED = "app.item_updated"
APP_ITEM_REMOVED = "app.item_removed"
CHAT_MESSAGE_APPENDED = "chat.message_appended"
SYMBOL_UNIVERSE_REFRESHED = "symbol_universe.refreshed"
MARKET_DATA_COLLECTED = "market_data.collected"
RESEARCH_EVIDENCE_COLLECTED = "research_evidence.collected"
RESEARCH_EVIDENCE_LIFECYCLE_CHANGED = "research_evidence.lifecycle_changed"
DATA_PIPELINE_HEALTH_CHANGED = "data_pipeline.health_changed"
HYPOTHESIS_RESEARCH_COMPLETED = "investment_hypothesis.research_completed"
HYPOTHESIS_PROPOSED = "investment_hypothesis.proposed"
HYPOTHESIS_REVIEWED = "investment_hypothesis.reviewed"
HYPOTHESIS_LIFECYCLE_TRANSITIONED = "investment_hypothesis.lifecycle_transitioned"
ONTOLOGY_REASONING_REQUESTED = "ontology.reasoning_requested"
ONTOLOGY_REASONING_COMPLETED = "ontology.reasoning_completed"
ONTOLOGY_REASONING_QUEUE_HEALTH_CHANGED = "ontology.reasoning_queue_health_changed"
OPERATIONAL_STORAGE_CAPACITY_CHANGED = "operations.storage_capacity_changed"
INVESTMENT_CALENDAR_EVENT_SAVED = "investment_calendar.event_saved"
INVESTMENT_CALENDAR_EVENT_REMOVED = "investment_calendar.event_removed"
INVESTMENT_CALENDAR_REMINDER_DUE = "investment_calendar.reminder_due"
INVESTMENT_STRATEGY_PROPOSED = "investment_strategy.proposed"
INVESTMENT_STRATEGY_VALIDATED = "investment_strategy.validated"
INVESTMENT_STRATEGY_APPROVED = "investment_strategy.approved"
INVESTMENT_STRATEGY_DEPLOYED = "investment_strategy.deployed"
INVESTMENT_STRATEGY_PERFORMANCE_RECORDED = "investment_strategy.performance_recorded"
SYSTEM_ERROR_REPORTED = "system.error_reported"


@dataclass(frozen=True)
class DomainEvent:
    name: str
    aggregate_id: str
    schema_version: str = DOMAIN_EVENT_SCHEMA_VERSION
    payload: Dict[str, object] = field(default_factory=dict)
    occurred_at: str = field(default_factory=utc_now_iso)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    correlation_id: str = ""

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, object]):
        return cls(
            name=str(payload.get("name") or ""),
            aggregate_id=str(payload.get("aggregate_id") or payload.get("aggregateId") or ""),
            schema_version=str(
                payload.get("schema_version")
                or payload.get("schemaVersion")
                or DOMAIN_EVENT_SCHEMA_VERSION
            ),
            payload=dict(payload.get("payload") or {}),
            occurred_at=str(payload.get("occurred_at") or payload.get("occurredAt") or utc_now_iso()),
            event_id=str(payload.get("event_id") or payload.get("eventId") or uuid.uuid4().hex),
            correlation_id=str(payload.get("correlation_id") or payload.get("correlationId") or ""),
        )


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


def account_saved_event(account: AccountConfig) -> DomainEvent:
    return DomainEvent(
        name=ACCOUNT_SAVED,
        aggregate_id=account.account_id,
        payload={"account": account.masked()},
    )


def account_removed_event(account_id: str) -> DomainEvent:
    return DomainEvent(
        name=ACCOUNT_REMOVED,
        aggregate_id=account_id,
        payload={"accountId": account_id},
    )


def _event_text(value: object, limit: int = 600) -> str:
    """Keep the durable event transport small without changing source facts."""
    return str(value or "").strip()[:max(0, int(limit or 0))]


def _event_text_list(values: object, limit: int = 100, item_limit: int = 240) -> List[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        return []
    result = []
    for value in values:
        clean = _event_text(value, item_limit)
        if clean:
            result.append(clean)
        if len(result) >= max(0, int(limit or 0)):
            break
    return result


def _event_signature_digest(value: object) -> str:
    raw = str(value or "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw else ""


def compact_evidence_delta_event_payload(value: object) -> Dict[str, object]:
    """Make an evidence-set delta replayable without duplicating its source body.

    The full signatures are derived from canonical evidence and can be tens of
    kilobytes because they intentionally include source facts.  Mailbox and
    event-log consumers only need their identity, transition, and a stable
    provenance digest; TypeDB reloads the canonical facts from the evidence
    store before it reasons.
    """
    if hasattr(value, "to_dict"):
        try:
            value = value.to_dict()
        except Exception:  # noqa: BLE001 - a malformed audit item is omitted, never fatal.
            value = {}
    if not isinstance(value, Mapping):
        return {}
    source = dict(value)
    compact: Dict[str, object] = {}
    for key, limit in {
        "evidenceId": 191,
        "symbol": 64,
        "transition": 64,
        "previousLifecycleState": 64,
        "lifecycleState": 64,
        "occurredAt": 40,
        "reason": 500,
        "eligibleEvidenceSetRevision": 191,
        "storyKey": 191,
    }.items():
        text = _event_text(source.get(key), limit)
        if text:
            compact[key] = text
    for key in ("previousEligible", "eligible", "changesInferenceEligibleSet"):
        if key in source:
            compact[key] = bool(source.get(key))
    families = _event_text_list(source.get("factFamilies"), limit=12, item_limit=96)
    if families:
        compact["factFamilies"] = families
    for source_key, digest_key, size_key in (
        ("previousSignature", "previousSignatureDigest", "previousSignatureBytes"),
        ("signature", "signatureDigest", "signatureBytes"),
        ("previousInferenceSignature", "previousInferenceSignatureDigest", "previousInferenceSignatureBytes"),
        ("inferenceSignature", "inferenceSignatureDigest", "inferenceSignatureBytes"),
    ):
        raw = str(source.get(source_key) or "")
        digest = _event_signature_digest(raw) or _event_text(source.get(digest_key), 64)
        if digest:
            compact[digest_key] = digest
            try:
                compact[size_key] = max(0, int(source.get(size_key) or len(raw.encode("utf-8"))))
            except (TypeError, ValueError):
                compact[size_key] = len(raw.encode("utf-8"))
    return compact


def compact_evidence_delta_event_payloads(values: object, limit: int = 200) -> List[Dict[str, object]]:
    if not isinstance(values, (list, tuple, set)):
        return []
    compact = []
    for value in values:
        item = compact_evidence_delta_event_payload(value)
        if item:
            compact.append(item)
        if len(compact) >= max(0, int(limit or 0)):
            break
    return compact


def compact_materiality_assessment_event_payload(value: object) -> Dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    source = dict(value)
    compact: Dict[str, object] = {}
    for key, limit in {
        "subject": 96,
        "reviewLevel": 40,
        "changeState": 64,
        "dataState": 64,
        "evidenceRole": 64,
        "trigger": 96,
        "reason": 600,
    }.items():
        text = _event_text(source.get(key), limit)
        if text:
            compact[key] = text
    if "passed" in source:
        compact["passed"] = bool(source.get("passed"))
    changed_fields = _event_text_list(source.get("changedFields"), limit=30, item_limit=96)
    if changed_fields:
        compact["changedFields"] = changed_fields
    conditions = _event_text_list(source.get("matchedConditions"), limit=20, item_limit=120)
    if conditions:
        compact["matchedConditions"] = conditions
    facts = source.get("facts")
    if isinstance(facts, Mapping):
        compact_facts = {}
        for key in ("eventType", "polarity", "readScope", "relationScope", "sourceTrustState", "validationState"):
            text = _event_text(facts.get(key), 96)
            if text:
                compact_facts[key] = text
        if compact_facts:
            compact["facts"] = compact_facts
    return compact


def compact_materiality_assessment_event_payloads(values: object, limit: int = 100) -> List[Dict[str, object]]:
    if isinstance(values, Mapping):
        values = list(values.values())
    if not isinstance(values, (list, tuple, set)):
        return []
    compact = []
    for value in values:
        item = compact_materiality_assessment_event_payload(value)
        if item:
            compact.append(item)
        if len(compact) >= max(0, int(limit or 0)):
            break
    return compact


def compact_fact_revisions_for_event(values: object, limit: int = 200) -> Dict[str, str]:
    if not isinstance(values, Mapping):
        return {}
    compact = {}
    for key, value in values.items():
        symbol = _event_text(key, 64).upper()
        revision = _event_text(value, 191)
        if symbol and revision:
            compact[symbol] = revision
        if len(compact) >= max(0, int(limit or 0)):
            break
    return compact


def compact_research_item_for_event_storage(value: object) -> Dict[str, object]:
    """Persist a replayable research summary, never duplicate article bodies.

    The canonical research-evidence store owns raw article text, claim ledgers,
    and model payloads.  This event projection keeps the identifiers and the
    compact presentation/provenance fields required by event replay.
    """
    if not isinstance(value, Mapping):
        return {}
    source = dict(value)
    raw_payload = source.get("payload") if isinstance(source.get("payload"), Mapping) else {}

    def pick(key: str):
        item = source.get(key)
        return item if item not in (None, "", [], {}) else raw_payload.get(key)

    compact: Dict[str, object] = {}
    for key, limit in {
        "evidenceId": 191,
        "symbol": 64,
        "kind": 64,
        "source": 160,
        "sourceKind": 96,
        "sourceOrigin": 120,
        "sourcePlatform": 120,
        "sourcePublisher": 240,
        "sourceTrustState": 64,
        "title": 800,
        "url": 1200,
        "publishedAt": 40,
        "observedAt": 40,
        "summary": 1400,
        "articleSummaryKo": 1400,
        "analysisSummary": 1400,
        "stockImpact": 64,
        "stockImpactLabel": 96,
        "stockImpactPolarity": 64,
        "stockImpactReasonKo": 1200,
        "eventType": 96,
        "evidenceRole": 64,
        "relationScope": 64,
        "relevanceState": 64,
        "dataState": 64,
        "materialityState": 64,
        "lifecycleState": 64,
        "lifecycleChangedAt": 40,
        "validationState": 64,
        "articleReadStatus": 64,
    }.items():
        text = _event_text(pick(key), limit)
        if text:
            compact[key] = text
    if not compact.get("evidenceId"):
        compact["evidenceId"] = _event_text(pick("id"), 191)

    for key in ("analysisConflict",):
        if key in source:
            compact[key] = bool(source.get(key))

    analysis = pick("aiAnalysis")
    if isinstance(analysis, Mapping):
        compact_analysis = {}
        for key, limit in {
            "impactLabelKo": 160,
            "impactPolarity": 64,
            "impactReasonKo": 1000,
            "summary": 1200,
            "briefKo": 1200,
            "rationaleKo": 1000,
            "translatedTitleKo": 800,
            "originalTitle": 800,
            "sourceLanguage": 32,
            "eventType": 96,
            "dataState": 64,
            "materialityState": 64,
            "relevanceState": 64,
            "relationScope": 64,
            "readScope": 64,
            "actionBoundaryKo": 800,
            "portfolioImplicationKo": 1000,
        }.items():
            text = _event_text(analysis.get(key), limit)
            if text:
                compact_analysis[key] = text
        if "needsReview" in analysis:
            compact_analysis["needsReview"] = bool(analysis.get("needsReview"))
        if compact_analysis:
            compact["aiAnalysis"] = compact_analysis

    article_facts = pick("articleFacts")
    if isinstance(article_facts, Mapping):
        compact_facts = {}
        for key, limit in {
            "eventType": 96,
            "eventTypeLabel": 160,
            "bodyQualityState": 64,
            "bodyQualityReason": 500,
            "publishedAt": 40,
            "readStatus": 64,
            "sourcePublisher": 240,
            "sourceLanguage": 32,
        }.items():
            text = _event_text(article_facts.get(key), limit)
            if text:
                compact_facts[key] = text
        for key in ("bodyAvailable",):
            if key in article_facts:
                compact_facts[key] = bool(article_facts.get(key))
        if "bodyCharCount" in article_facts:
            try:
                compact_facts["bodyCharCount"] = max(0, int(article_facts.get("bodyCharCount") or 0))
            except (TypeError, ValueError):
                pass
        sentences = _event_text_list(article_facts.get("keySentences"), limit=5, item_limit=500)
        if sentences:
            compact_facts["keySentences"] = sentences
        if compact_facts:
            compact["articleFacts"] = compact_facts

    quality_gate = pick("qualityGate")
    if isinstance(quality_gate, Mapping):
        compact_gate = {}
        for key, limit in {"decision": 64, "stage": 64, "reason": 500, "bodyQualityState": 64}.items():
            text = _event_text(quality_gate.get(key), limit)
            if text:
                compact_gate[key] = text
        for key in ("passed",):
            if key in quality_gate:
                compact_gate[key] = bool(quality_gate.get(key))
        if "bodyCharCount" in quality_gate:
            try:
                compact_gate["bodyCharCount"] = max(0, int(quality_gate.get("bodyCharCount") or 0))
            except (TypeError, ValueError):
                pass
        if compact_gate:
            compact["qualityGate"] = compact_gate

    governance = pick("evidenceGovernance")
    if isinstance(governance, Mapping):
        compact_governance = {}
        for key, limit in {
            "claimState": 64,
            "validationState": 64,
            "verificationStatus": 64,
            "sourceTrustState": 64,
            "sourcePublisher": 240,
            "canonicalUrl": 1200,
            "entityResolutionStatus": 96,
        }.items():
            text = _event_text(governance.get(key), limit)
            if text:
                compact_governance[key] = text
        for key in ("investmentJudgmentEligible",):
            if key in governance:
                compact_governance[key] = bool(governance.get(key))
        if "independentSourceCount" in governance:
            try:
                compact_governance["independentSourceCount"] = max(0, int(governance.get("independentSourceCount") or 0))
            except (TypeError, ValueError):
                pass
        reasons = _event_text_list(governance.get("reasons"), limit=8, item_limit=400)
        if reasons:
            compact_governance["reasons"] = reasons
        if compact_governance:
            compact["evidenceGovernance"] = compact_governance
    return {key: value for key, value in compact.items() if value not in (None, "", [], {})}


def compact_research_evidence_event_payload_for_storage(payload: Mapping[str, object]) -> Dict[str, object]:
    source = dict(payload or {})
    compact: Dict[str, object] = {}
    for key in (
        "source", "status", "targetCount", "fetchedCount", "savedCount", "changedCount",
        "materialChangedCount", "lifecycleChangedCount",
    ):
        if key in source:
            compact[key] = source.get(key)
    for key in ("symbols", "changedSymbols", "materialChangedSymbols", "inferenceChangedSymbols", "providers"):
        values = _event_text_list(source.get(key), limit=100, item_limit=96)
        if values:
            compact[key] = values
    for key in ("changedItems", "materialChangedItems"):
        values = source.get(key)
        if not isinstance(values, (list, tuple, set)):
            continue
        items = []
        for value in values:
            item = compact_research_item_for_event_storage(value)
            if item:
                items.append(item)
            if len(items) >= 100:
                break
        if items:
            compact[key] = items
    assessments = compact_materiality_assessment_event_payloads(source.get("materialityAssessments"), limit=100)
    if assessments:
        compact["materialityAssessments"] = assessments
    deltas = compact_evidence_delta_event_payloads(source.get("evidenceDeltas"), limit=200)
    if deltas:
        compact["evidenceDeltas"] = deltas
    revisions = compact_fact_revisions_for_event(source.get("factRevisionsBySymbol"), limit=100)
    if revisions:
        compact["factRevisionsBySymbol"] = revisions
    return compact


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
        ("observationFollowupSymbols", 200, 64),
        ("factTypes", 20, 96),
        ("changedEvidenceIds", 200, 191),
    ):
        values = _event_text_list(source.get(key), limit=limit, item_limit=item_limit)
        if values:
            compact[key] = values
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
            clean_fields = _event_text_list(fields, limit=30, item_limit=96)
            if clean_symbol and clean_fields:
                compact_fields[clean_symbol] = clean_fields
            if len(compact_fields) >= 200:
                break
        if compact_fields:
            compact["changedFieldsBySymbol"] = compact_fields
    # These are strict generation contracts. They are already bounded by their
    # own domain types, so retain them intact rather than silently weakening a
    # research acknowledgement.
    for key in ("reasoningHandoff", "hypothesisResearchBrief", "verifiedSourceSnapshot"):
        value = source.get(key)
        if isinstance(value, Mapping):
            compact[key] = dict(value)
    return compact


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


def snapshot_collected_event(snapshot: AccountSnapshot) -> DomainEvent:
    return DomainEvent(
        name=MONITORING_SNAPSHOT_COLLECTED,
        aggregate_id=snapshot.account_id,
        payload={
            "accountId": snapshot.account_id,
            "accountLabel": snapshot.account_label,
            "provider": snapshot.provider,
            "mode": snapshot.mode,
            "status": snapshot.status,
            "generatedAt": snapshot.generated_at,
            "positionCount": len([item for item in snapshot.positions if not item.is_cash()]),
            "decisionCount": len(snapshot.decisions),
            "portfolioTotal": snapshot.portfolio.total,
            "portfolioInvested": snapshot.portfolio.invested,
            "portfolioCash": snapshot.portfolio.cash,
            "metadata": compact_snapshot_event_metadata(getattr(snapshot, "metadata", {}) or {}),
        },
    )


def alerts_detected_event(events: Iterable[AlertEvent]) -> DomainEvent:
    items = list(events)
    account_ids = sorted(set(item.account_id for item in items))
    rules = sorted(set(item.rule for item in items))
    symbols = sorted(set(item.symbol for item in items if item.symbol))
    return DomainEvent(
        name=MONITORING_ALERTS_DETECTED,
        aggregate_id=",".join(account_ids) or "all",
        payload={
            "accountIds": account_ids,
            "count": len(items),
            "rules": rules,
            "symbols": symbols,
            "events": [
                {
                    "accountId": item.account_id,
                    "accountLabel": item.account_label,
                    "severity": item.severity,
                    "rule": item.rule,
                    "key": item.key,
                    "title": item.title,
                    "symbol": item.symbol,
                    "lines": item.lines,
                    "criteria": list(getattr(item, "criteria", []) or []),
                    "metadata": dict(getattr(item, "metadata", {}) or {}),
                    "generatedAt": getattr(item, "generated_at", ""),
                }
                for item in items
            ],
        },
    )


def monitoring_cycle_completed_event(
    account_ids: List[str],
    snapshot_count: int,
    alert_count: int,
    dry_run: bool,
    delivered: bool,
) -> DomainEvent:
    return DomainEvent(
        name=MONITORING_CYCLE_COMPLETED,
        aggregate_id=",".join(account_ids) or "all",
        payload={
            "accountIds": account_ids,
            "snapshotCount": snapshot_count,
            "alertCount": alert_count,
            "dryRun": dry_run,
            "delivered": delivered,
        },
    )


def market_data_collected_event(payload: Dict[str, object]) -> DomainEvent:
    provider = str(payload.get("provider") or "market-data")
    markets = ",".join(str(market) for market in payload.get("markets") or []) or "all"
    symbols = [str(symbol or "").upper().strip() for symbol in (payload.get("changedSymbols") or payload.get("symbols") or []) if str(symbol or "").strip()]
    material_symbols = [str(symbol or "").upper().strip() for symbol in (payload.get("materialChangedSymbols") or []) if str(symbol or "").strip()]
    raw_changed_fields = payload.get("changedFieldsBySymbol") if isinstance(payload.get("changedFieldsBySymbol"), dict) else {}
    raw_revisions = payload.get("factRevisionsBySymbol") if isinstance(payload.get("factRevisionsBySymbol"), dict) else {}
    changed_fields = {}
    revisions = {}
    for symbol in symbols[:200]:
        fields = raw_changed_fields.get(symbol)
        if fields is None:
            fields = raw_changed_fields.get(symbol.upper())
        if isinstance(fields, (list, tuple, set)):
            changed_fields[symbol] = [str(field or "").strip() for field in fields if str(field or "").strip()][:30]
        revision = str(raw_revisions.get(symbol) or raw_revisions.get(symbol.upper()) or "").strip()
        if revision:
            revisions[symbol] = revision[:160]
    return DomainEvent(
        name=MARKET_DATA_COLLECTED,
        aggregate_id=provider + ":" + markets,
        payload={
            "provider": provider,
            "markets": list(payload.get("markets") or []),
            "symbols": symbols[:200],
            "selectedCount": int(payload.get("selectedCount") or 0),
            "priceCount": int(payload.get("priceCount") or 0),
            "candleCount": int(payload.get("candleCount") or 0),
            "savedCount": int(payload.get("savedCount") or 0),
            "changedCount": int(payload.get("changedCount") or 0),
            "changedSymbols": symbols[:200],
            "changedFieldsBySymbol": changed_fields,
            "factRevisionsBySymbol": revisions,
            "materialChangedCount": int(payload.get("materialChangedCount") or len(material_symbols) or 0),
            "materialChangedSymbols": material_symbols[:200],
            "materialityAssessments": dict(payload.get("materialityAssessments") or {}),
            "status": str(payload.get("status") or ""),
            "dataQuality": str(payload.get("dataQuality") or "actual"),
            "quoteQualityCounts": dict(payload.get("quoteQualityCounts") or {}),
            "marketSessionCounts": dict(payload.get("marketSessionCounts") or {}),
        },
    )


def research_evidence_collected_event(payload: Dict[str, object]) -> DomainEvent:
    symbols = list(payload.get("symbols") or [])
    material_symbols = list(payload.get("materialChangedSymbols") or [])
    return DomainEvent(
        name=RESEARCH_EVIDENCE_COLLECTED,
        aggregate_id="news:" + (",".join(str(symbol) for symbol in symbols) or "all")[:180],
        payload={
            "source": str(payload.get("source") or "news-collection"),
            "status": str(payload.get("status") or ""),
            "targetCount": int(payload.get("targetCount") or 0),
            "fetchedCount": int(payload.get("fetchedCount") or 0),
            "savedCount": int(payload.get("savedCount") or 0),
            "changedCount": int(payload.get("changedCount") or payload.get("savedCount") or 0),
            "symbols": symbols[:100],
            "changedSymbols": list(payload.get("changedSymbols") or symbols)[:100],
            "materialChangedCount": int(payload.get("materialChangedCount") or len(material_symbols) or 0),
            "materialChangedSymbols": material_symbols[:100],
            "changedItems": list(payload.get("changedItems") or [])[:100],
            "materialChangedItems": list(payload.get("materialChangedItems") or [])[:100],
            "materialityAssessments": list(payload.get("materialityAssessments") or [])[:100],
            "evidenceDeltas": list(payload.get("evidenceDeltas") or [])[:200],
            "inferenceChangedSymbols": list(payload.get("inferenceChangedSymbols") or [])[:100],
            "factRevisionsBySymbol": dict(payload.get("factRevisionsBySymbol") or {}),
            "lifecycleChangedCount": int(payload.get("lifecycleChangedCount") or 0),
            "providers": list(payload.get("providers") or [])[:20],
        },
    )


def research_evidence_lifecycle_changed_event(payload: Dict[str, object]) -> DomainEvent:
    """Record expiry/retraction as an auditable source fact change.

    The event is deliberately separate from collection because a previously
    eligible fact leaving the world can invalidate an inference even when no
    provider returned a new article in this run.
    """
    raw_inference_symbols = (
        payload.get("inferenceChangedSymbols")
        if "inferenceChangedSymbols" in payload
        else payload.get("changedSymbols")
    )
    inference_symbols = [
        str(symbol or "").upper().strip()
        for symbol in (raw_inference_symbols or [])
        if str(symbol or "").strip()
    ]
    changed_symbols = [
        str(symbol or "").upper().strip()
        for symbol in (payload.get("changedSymbols") or inference_symbols)
        if str(symbol or "").strip()
    ]
    audit_symbols = sorted(set(changed_symbols or inference_symbols))
    return DomainEvent(
        name=RESEARCH_EVIDENCE_LIFECYCLE_CHANGED,
        aggregate_id="research-evidence-lifecycle:" + (",".join(audit_symbols) or "all")[:160],
        payload={
            "source": "research-evidence-lifecycle",
            "status": str(payload.get("status") or "ok"),
            "symbols": audit_symbols[:100],
            "changedSymbols": audit_symbols[:100],
            "inferenceChangedSymbols": sorted(set(inference_symbols))[:100],
            "expiredCount": int(payload.get("expiredCount") or 0),
            "retractedCount": int(payload.get("retractedCount") or 0),
            "lifecycleChangedCount": int(payload.get("lifecycleChangedCount") or 0),
            "evidenceDeltas": list(payload.get("evidenceDeltas") or [])[:200],
            "factRevisionsBySymbol": dict(payload.get("factRevisionsBySymbol") or {}),
            "reason": str(payload.get("reason") or ""),
        },
    )


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


def hypothesis_research_completed_event(payload: Dict[str, object]) -> DomainEvent:
    symbol = str(payload.get("symbol") or "").upper().strip()
    handoff = payload.get("reasoningHandoff") if isinstance(payload.get("reasoningHandoff"), dict) else {}
    research_brief = payload.get("hypothesisResearchBrief") if isinstance(payload.get("hypothesisResearchBrief"), dict) else {}
    changed_evidence_ids = [
        str(item or "").strip()
        for item in (payload.get("changedEvidenceIds") or payload.get("verifiedClaims") or [])
        if str(item or "").strip()
    ]
    inference_symbols = [
        str(item or "").upper().strip()
        for item in (payload.get("inferenceChangedSymbols") or [])
        if str(item or "").strip()
    ]
    return DomainEvent(
        name=HYPOTHESIS_RESEARCH_COMPLETED,
        aggregate_id="hypothesis-research:" + (symbol or str(payload.get("runId") or "unknown")),
        payload={
            "runId": str(payload.get("runId") or ""),
            "questionId": str(payload.get("questionId") or ""),
            "accountId": str(payload.get("accountId") or ""),
            "symbol": symbol,
            "symbols": [symbol] if symbol else [],
            "status": str(payload.get("status") or ""),
            "changedCount": int(payload.get("changedEvidenceCount") or 0),
            "changedEvidenceIds": changed_evidence_ids[:200],
            "verifiedClaimCount": len(payload.get("verifiedClaims") or []),
            "rejectedClaimCount": len(payload.get("rejectedClaims") or []),
            "factTypes": ["ResearchEvidence", "VerifiedClaim", "VerificationRun"],
            "inferenceChangedSymbols": sorted(set(inference_symbols))[:100],
            "evidenceDeltas": [
                dict(item)
                for item in (payload.get("evidenceDeltas") or [])
                if isinstance(item, dict)
            ][:200],
            "factRevisionsBySymbol": dict(payload.get("factRevisionsBySymbol") or {}),
            "source": "investment-brain-on-demand-research",
            "reasoningHandoff": handoff,
            "hypothesisResearchBrief": research_brief,
        },
    )


def hypothesis_proposed_event(payload: Dict[str, object]) -> DomainEvent:
    return DomainEvent(
        name=HYPOTHESIS_PROPOSED,
        aggregate_id=str(payload.get("proposalId") or "hypothesis-proposal"),
        payload=dict(payload or {}),
    )


def hypothesis_reviewed_event(payload: Dict[str, object]) -> DomainEvent:
    return DomainEvent(
        name=HYPOTHESIS_REVIEWED,
        aggregate_id=str(payload.get("proposalId") or "hypothesis-proposal"),
        payload=dict(payload or {}),
    )


def hypothesis_lifecycle_transitioned_event(payload: Dict[str, object]) -> DomainEvent:
    """Publish audit-only lifecycle changes without creating an alert signal."""

    lifecycle_key = str(payload.get("lifecycleKey") or payload.get("lifecycle_key") or "unknown")
    return DomainEvent(
        name=HYPOTHESIS_LIFECYCLE_TRANSITIONED,
        aggregate_id="hypothesis-lifecycle:" + lifecycle_key[:160],
        payload={
            "lifecycleKey": lifecycle_key,
            "lifecycleId": str(payload.get("lifecycleId") or payload.get("lifecycle_id") or ""),
            "scope": str(payload.get("scope") or ""),
            "symbol": str(payload.get("symbol") or "").upper(),
            "accountId": str(payload.get("accountId") or payload.get("account_id") or ""),
            "previousState": str(payload.get("previousState") or payload.get("previous_state") or ""),
            "currentState": str(payload.get("currentState") or payload.get("current_state") or ""),
            "inferenceGenerationId": str(payload.get("inferenceGenerationId") or payload.get("inference_generation_id") or ""),
            "previousGenerationId": str(payload.get("previousGenerationId") or payload.get("previous_generation_id") or ""),
            "occurredAt": str(payload.get("occurredAt") or payload.get("occurred_at") or ""),
            "reason": str(payload.get("reason") or ""),
            "materialChange": bool(payload.get("materialChange") if "materialChange" in payload else payload.get("material_change")),
            "evidenceDelta": dict(payload.get("evidenceDelta") or payload.get("evidence_delta") or {}),
            "source": "typedb-hypothesis-lifecycle",
        },
    )


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
) -> DomainEvent:
    clean_symbols = sorted(set(str(symbol or "").upper().strip() for symbol in (symbols or []) if str(symbol or "").strip()))
    clean_observation_followups = sorted({
        str(symbol or "").upper().strip()
        for symbol in (observation_followup_symbols or [])
        if str(symbol or "").strip()
    }.intersection(clean_symbols))
    clean_fact_types = sorted(set(str(item or "").strip() for item in (fact_types or []) if str(item or "").strip()))
    source_payload = source_event.payload or {}
    handoff = source_payload.get("reasoningHandoff") if isinstance(source_payload.get("reasoningHandoff"), dict) else {}
    research_brief = source_payload.get("hypothesisResearchBrief") if isinstance(source_payload.get("hypothesisResearchBrief"), dict) else {}
    raw_revisions = fact_revisions_by_symbol if isinstance(fact_revisions_by_symbol, dict) else source_payload.get("factRevisionsBySymbol")
    raw_revisions = raw_revisions if isinstance(raw_revisions, dict) else {}
    raw_changed_fields = changed_fields_by_symbol if isinstance(changed_fields_by_symbol, dict) else source_payload.get("changedFieldsBySymbol")
    raw_changed_fields = raw_changed_fields if isinstance(raw_changed_fields, dict) else {}
    revisions = {}
    changed_fields = {}
    for symbol in clean_symbols:
        revision = str(raw_revisions.get(symbol) or raw_revisions.get(symbol.upper()) or "").strip()
        if revision:
            revisions[symbol] = revision[:160]
        fields = raw_changed_fields.get(symbol)
        if fields is None:
            fields = raw_changed_fields.get(symbol.upper())
        if isinstance(fields, (list, tuple, set)):
            changed_fields[symbol] = [str(field or "").strip() for field in fields if str(field or "").strip()][:30]
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
    return DomainEvent(
        name=ONTOLOGY_REASONING_REQUESTED,
        aggregate_id="ontology:" + (",".join(clean_symbols) or str(trigger or "all"))[:180],
        correlation_id=source_event.correlation_id or source_event.event_id,
        payload=compact_ontology_reasoning_request_payload_for_storage({
            "trigger": str(trigger or "data-update"),
            "sourceEventId": source_event.event_id,
            "sourceEventName": source_event.name,
            "sourceAggregateId": source_event.aggregate_id,
            "symbols": clean_symbols[:200],
            # This is delivery/scheduling provenance for a deterministic raw
            # price observation, never a TypeDB rule condition.
            "observationFollowupSymbols": clean_observation_followups[:200],
            "changedCount": int(changed_count or 0),
            "observedCount": int(observed_count or 0),
            "factTypes": clean_fact_types[:20],
            "reason": str(reason or ""),
            # Scheduling uses the original vendor/collection observation time,
            # never the delayed worker publish time, to reject stale data
            # before an expensive TypeDB cycle.
            "sourceObservedAt": source_observed_at,
            "dispatchMode": "data-update-driven",
            # A canonical source fact revision is the ingress contract. The
            # materiality assessment below remains advisory scheduling
            # provenance, not a Python investment-decision gate.
            "importanceGate": "fact-revision-first",
            "materialityRole": "advisory-priority-only",
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
            "accountId": str(source_payload.get("accountId") or ""),
            "changedEvidenceIds": changed_evidence_ids[:200],
            "evidenceDeltas": deltas[:200],
            "reasoningHandoff": handoff,
            "hypothesisResearchBrief": research_brief,
            # A monitor snapshot barrier is operational provenance. It lets
            # the worker distinguish a replayable persisted boundary from a
            # raw provider tick without turning that distinction into an
            # investment rule condition.
            "verifiedSourceSnapshot": dict(snapshot_barrier or {}) if isinstance(snapshot_barrier, Mapping) else {},
        }),
    )


def research_evidence_lifecycle_events(payload: Dict[str, object]) -> List[DomainEvent]:
    """Create the durable audit event and its ABox refresh handoff together."""
    values = dict(payload or {})
    values["lifecycleChangedCount"] = int(values.get("lifecycleChangedCount") or 0) or (
        int(values.get("expiredCount") or 0) + int(values.get("retractedCount") or 0)
    )
    source_event = research_evidence_lifecycle_changed_event(values)
    symbols = list(source_event.payload.get("inferenceChangedSymbols") or [])
    events = [source_event]
    if symbols:
        events.append(ontology_reasoning_requested_event(
            source_event,
            "research-evidence-lifecycle",
            symbols,
            changed_count=len(symbols),
            observed_count=int(values.get("lifecycleChangedCount") or 0),
            fact_types=["ResearchEvidence", "EvidenceLifecycle"],
            reason="유효 리서치 근거가 만료 또는 철회되어 TypeDB ABox와 네이티브 규칙 추론을 갱신합니다.",
            fact_revisions_by_symbol=dict(values.get("factRevisionsBySymbol") or {}),
            evidence_deltas=list(values.get("evidenceDeltas") or []),
        ))
    return events


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


def investment_strategy_proposed_event(proposal) -> DomainEvent:
    payload = proposal.to_dict() if hasattr(proposal, "to_dict") else dict(proposal or {})
    return DomainEvent(
        name=INVESTMENT_STRATEGY_PROPOSED,
        aggregate_id=str(payload.get("id") or payload.get("proposalId") or ""),
        payload={"proposal": payload},
    )


def investment_strategy_validated_event(proposal) -> DomainEvent:
    payload = proposal.to_dict() if hasattr(proposal, "to_dict") else dict(proposal or {})
    return DomainEvent(
        name=INVESTMENT_STRATEGY_VALIDATED,
        aggregate_id=str(payload.get("id") or payload.get("proposalId") or ""),
        payload={
            "proposalId": str(payload.get("id") or ""),
            "status": str(payload.get("status") or ""),
            "validation": dict(payload.get("validation") or {}),
        },
    )


def investment_strategy_approved_event(proposal) -> DomainEvent:
    payload = proposal.to_dict() if hasattr(proposal, "to_dict") else dict(proposal or {})
    lifecycle = dict(payload.get("lifecycle") or {})
    return DomainEvent(
        name=INVESTMENT_STRATEGY_APPROVED,
        aggregate_id=str(payload.get("id") or payload.get("proposalId") or ""),
        payload={
            "proposalId": str(payload.get("id") or ""),
            "status": str(payload.get("status") or ""),
            "approvedAt": str(payload.get("approvedAt") or ""),
            "approvedBy": str(lifecycle.get("approvedBy") or ""),
            "approvalReason": str(lifecycle.get("approvalReason") or ""),
        },
    )


def investment_strategy_deployed_event(proposal) -> DomainEvent:
    payload = proposal.to_dict() if hasattr(proposal, "to_dict") else dict(proposal or {})
    return DomainEvent(
        name=INVESTMENT_STRATEGY_DEPLOYED,
        aggregate_id=str(payload.get("id") or payload.get("proposalId") or ""),
        payload={
            "proposalId": str(payload.get("id") or ""),
            "status": str(payload.get("status") or ""),
            "deployedAt": str(payload.get("deployedAt") or ""),
            "ruleIds": list(payload.get("ruleIds") or []),
        },
    )


def investment_strategy_performance_recorded_event(proposal, sample: Dict[str, object]) -> DomainEvent:
    payload = proposal.to_dict() if hasattr(proposal, "to_dict") else dict(proposal or {})
    performance = dict(payload.get("performance") or {})
    return DomainEvent(
        name=INVESTMENT_STRATEGY_PERFORMANCE_RECORDED,
        aggregate_id=str(payload.get("id") or payload.get("proposalId") or ""),
        payload={
            "proposalId": str(payload.get("id") or ""),
            "status": str(payload.get("status") or ""),
            "sample": dict(sample or {}),
            "summary": dict(performance.get("summary") or {}),
        },
    )


def investment_calendar_event_saved_event(calendar_event) -> DomainEvent:
    payload = calendar_event.to_dict() if hasattr(calendar_event, "to_dict") else dict(calendar_event or {})
    symbols = list(payload.get("symbols") or [])
    markets = list(payload.get("markets") or [])
    return DomainEvent(
        name=INVESTMENT_CALENDAR_EVENT_SAVED,
        aggregate_id=str(payload.get("eventId") or ""),
        payload={
            "event": payload,
            "eventId": str(payload.get("eventId") or ""),
            "title": str(payload.get("title") or ""),
            "eventType": str(payload.get("eventType") or ""),
            "startsAt": str(payload.get("startsAt") or ""),
            "importance": int(payload.get("importance") or 0),
            "symbols": symbols[:100],
            "markets": markets[:50],
            "changedSymbols": symbols[:100],
            "changedCount": len(symbols),
        },
    )


def investment_calendar_event_removed_event(event_id: str) -> DomainEvent:
    return DomainEvent(
        name=INVESTMENT_CALENDAR_EVENT_REMOVED,
        aggregate_id=str(event_id or ""),
        payload={"eventId": str(event_id or "")},
    )


def investment_calendar_reminder_due_event(reminders: Iterable[object]) -> DomainEvent:
    items = [item.to_dict() if hasattr(item, "to_dict") else dict(item or {}) for item in reminders or []]
    event_ids = sorted(set(str(item.get("eventId") or "") for item in items if str(item.get("eventId") or "")))
    symbols = sorted(set(str(symbol or "").upper().strip() for item in items for symbol in (item.get("symbols") or []) if str(symbol or "").strip()))
    return DomainEvent(
        name=INVESTMENT_CALENDAR_REMINDER_DUE,
        aggregate_id="calendar:" + (",".join(event_ids) or "none")[:180],
        payload={
            "count": len(items),
            "eventIds": event_ids[:100],
            "symbols": symbols[:100],
            "reminders": items[:100],
        },
    )
