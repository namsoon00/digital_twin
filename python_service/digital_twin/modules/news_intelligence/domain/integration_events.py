from digital_twin.modules.news_intelligence.domain.event_payloads import compact_evidence_delta_event_payload, compact_evidence_delta_event_payloads, compact_materiality_assessment_event_payload, compact_materiality_assessment_event_payloads, compact_research_evidence_event_payload_for_storage, compact_research_item_for_event_storage
from digital_twin.modules.news_intelligence.domain.event_types import RESEARCH_EVIDENCE_COLLECTED, RESEARCH_EVIDENCE_LIFECYCLE_CHANGED, NEWS_ARTICLE_ANALYZED, HYPOTHESIS_RESEARCH_COMPLETED
from digital_twin.shared_kernel.event_payloads import compact_fact_revisions_for_event
from digital_twin.modules.reasoning.contracts import ontology_reasoning_requested_event
from digital_twin.shared_kernel.events import DomainEvent
from digital_twin.shared_kernel.events import _event_signature_digest
from digital_twin.shared_kernel.events import _event_text
from digital_twin.shared_kernel.events import _event_text_list
from typing import Dict, Iterable, List, Mapping






















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
            "alertEligibleCount": int(payload.get("alertEligibleCount") or len(payload.get("alertEligibleItems") or []) or 0),
            "alertEligibleSymbols": list(payload.get("alertEligibleSymbols") or [])[:100],
            "changedItems": list(payload.get("changedItems") or [])[:100],
            "materialChangedItems": list(payload.get("materialChangedItems") or [])[:100],
            "alertEligibleItems": list(payload.get("alertEligibleItems") or [])[:100],
            "materialityAssessments": list(payload.get("materialityAssessments") or [])[:100],
            "evidenceDeltas": list(payload.get("evidenceDeltas") or [])[:200],
            "inferenceChangedSymbols": list(payload.get("inferenceChangedSymbols") or [])[:100],
            "factRevisionsBySymbol": dict(payload.get("factRevisionsBySymbol") or {}),
            "lifecycleChangedCount": int(payload.get("lifecycleChangedCount") or 0),
            "providers": list(payload.get("providers") or [])[:20],
            "eventContract": "research-evidence-change-v2",
            "eligibilityPolicy": "display-alert-reasoning-independent-v1",
            "allowHistoricalAlert": bool(payload.get("allowHistoricalAlert", False)),
        },
    )


def news_article_analyzed_event(payload: Dict[str, object]) -> DomainEvent:
    """Publish only alert-eligible news across the bounded-context boundary."""
    items = []
    candidates = payload.get("alertEligibleItems")
    if not isinstance(candidates, list):
        candidates = payload.get("materialChangedItems") or payload.get("changedItems") or []
    for item in list(candidates or []):
        if not isinstance(item, dict) or str(item.get("kind") or "").lower() != "news":
            continue
        raw = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        eligibility = raw.get("newsEligibility") if isinstance(raw.get("newsEligibility"), dict) else {}
        if eligibility.get("alertEligible") is True:
            items.append(item)
    symbols = sorted({str(item.get("symbol") or "").upper().strip() for item in items if str(item.get("symbol") or "").strip()})
    return DomainEvent(
        name=NEWS_ARTICLE_ANALYZED,
        aggregate_id="news-article:" + (",".join(symbols) or "none")[:170],
        payload={
            "source": str(payload.get("source") or "news-intelligence"),
            "status": str(payload.get("status") or ""),
            "symbols": symbols[:100],
            "changedSymbols": symbols[:100],
            "materialChangedSymbols": symbols[:100],
            "materialChangedCount": len(items),
            "materialChangedItems": items[:100],
            "changedItems": items[:100],
            "providers": list(payload.get("providers") or [])[:20],
            "eligibilityPolicy": "alertEligible",
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
            fact_types_by_symbol={symbol: ["ResearchEvidence", "EvidenceLifecycle"] for symbol in symbols},
            changed_fields_by_symbol={symbol: ["external.researchEvidence"] for symbol in symbols},
            reason="유효 리서치 근거가 만료 또는 철회되어 TypeDB ABox와 네이티브 규칙 추론을 갱신합니다.",
            fact_revisions_by_symbol=dict(values.get("factRevisionsBySymbol") or {}),
            evidence_deltas=list(values.get("evidenceDeltas") or []),
        ))
    return events
