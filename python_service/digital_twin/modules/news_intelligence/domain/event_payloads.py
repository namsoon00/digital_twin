from digital_twin.shared_kernel.event_payloads import compact_fact_revisions_for_event
from digital_twin.shared_kernel.events import _event_signature_digest
from digital_twin.shared_kernel.events import _event_text
from digital_twin.shared_kernel.events import _event_text_list
from typing import Dict, Iterable, List, Mapping


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
        for key in (
            "eventType", "polarity", "readScope", "relationScope",
            "sourceTrustState", "validationState", "priceChangePct",
            "previousVolumeRatio", "volumeRatio", "volumeRatioThreshold",
            "previousTradeStrength", "tradeStrength",
            "tradeStrengthLowerThreshold", "tradeStrengthUpperThreshold",
            "previousBidAskImbalance", "bidAskImbalance",
            "bidAskImbalanceThreshold", "orderbookBidVolume",
            "orderbookAskVolume", "ma20Distance", "ma60Distance",
            "ma20DistanceChange", "ma60DistanceChange",
            "foreignFlowPressurePct", "institutionFlowPressurePct",
        ):
            text = _event_text(facts.get(key), 96)
            if text:
                compact_facts[key] = text
        confirmed_transitions = facts.get("confirmedSignalTransitions")
        if isinstance(confirmed_transitions, (list, tuple)):
            compact_facts["confirmedSignalTransitions"] = [
                {
                    key: transition.get(key)
                    for key in (
                        "signalId", "condition", "fromState", "toState",
                        "observedValue", "confirmationCount",
                        "requiredConfirmations", "immediate",
                    )
                    if transition.get(key) not in (None, "", [], {})
                }
                for transition in confirmed_transitions[:8]
                if isinstance(transition, Mapping)
            ]
        crypto_transitions = facts.get("cryptoTransitions")
        if isinstance(crypto_transitions, (list, tuple)):
            compact_facts["cryptoTransitions"] = [
                {
                    key: transition.get(key)
                    for key in (
                        "symbol", "coinId", "name", "horizon", "direction",
                        "severity", "changePct", "thresholdPct", "previousBand",
                        "currentBand", "transition", "observedAt", "signature",
                    )
                    if transition.get(key) not in (None, "", [], {})
                }
                for transition in crypto_transitions[:4]
                if isinstance(transition, Mapping)
            ]
        target = _event_text(facts.get("cryptoTransitionTarget"), 96)
        if target:
            compact_facts["cryptoTransitionTarget"] = target
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
        "articleSourceRevision": 191,
        "articleEnrichmentRevision": 191,
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
            "status": 64,
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
        for key in ("bodyAvailable", "bodyQualityPassed"):
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

    summary_quality = pick("articleSummaryQuality")
    if isinstance(summary_quality, Mapping):
        compact_summary_quality = {}
        state = _event_text(summary_quality.get("state"), 64)
        if state:
            compact_summary_quality["state"] = state
        issues = _event_text_list(summary_quality.get("issues"), limit=8, item_limit=240)
        if issues:
            compact_summary_quality["issues"] = issues
        if compact_summary_quality:
            compact["articleSummaryQuality"] = compact_summary_quality

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

    for source_key, boolean_keys, text_keys, list_keys in (
        (
            "newsEligibility",
            ("archiveEligible", "displayEligible", "alertEligible", "reasoningEligible"),
            ("reviewState",),
            ("reviewReasonCodes", "alertReasonCodes", "reasoningReasonCodes"),
        ),
        (
            "promptEvidenceAdmission",
            ("displayEligible", "alertEligible", "referenceEligible", "decisionEligible", "promptEligible"),
            ("version", "usage", "freshnessState", "sourceAsOf", "checkedAt"),
            ("reasonCodes",),
        ),
    ):
        state = pick(source_key)
        if not isinstance(state, Mapping):
            continue
        bounded_state = {}
        for key in boolean_keys:
            if key in state:
                bounded_state[key] = bool(state.get(key))
        for key in text_keys:
            text = _event_text(state.get(key), 96 if key != "sourceAsOf" else 80)
            if text:
                bounded_state[key] = text
        for key in list_keys:
            values = _event_text_list(state.get(key), limit=10, item_limit=240)
            if values:
                bounded_state[key] = values
        if bounded_state:
            compact[source_key] = bounded_state

    for key, limit in {
        "officialDocumentState": 64,
        "officialDocumentType": 96,
        "officialDocumentQuality": 64,
        "receiptNo": 64,
        "receiptDate": 40,
        "reportName": 800,
        "sourceRevision": 191,
        "sourceAsOf": 80,
        "documentHash": 96,
    }.items():
        text = _event_text(pick(key), limit)
        if text:
            compact[key] = text
    for key in ("metadataVerified", "documentVerified", "analysisReady"):
        value = pick(key)
        if value is not None:
            compact[key] = bool(value)
    disclosure_analysis = pick("disclosureAnalysis")
    if isinstance(disclosure_analysis, Mapping):
        compact_disclosure = {}
        for key, limit in {
            "status": 64,
            "version": 96,
            "source": 120,
            "summary": 1400,
            "impactSummary": 1400,
            "uncertaintySummary": 1200,
        }.items():
            text = _event_text(disclosure_analysis.get(key), limit)
            if text:
                compact_disclosure[key] = text
        lines = _event_text_list(disclosure_analysis.get("lines"), limit=6, item_limit=600)
        if lines:
            compact_disclosure["lines"] = lines
        for key, count, item_limit in (
            ("confirmedFacts", 4, 600),
            ("materialNumbers", 12, 120),
            ("documentDates", 8, 80),
            ("watchItems", 4, 400),
        ):
            values = _event_text_list(disclosure_analysis.get(key), limit=count, item_limit=item_limit)
            if values:
                compact_disclosure[key] = values
        if compact_disclosure:
            compact["disclosureAnalysis"] = compact_disclosure
    return {key: value for key, value in compact.items() if value not in (None, "", [], {})}


def compact_research_evidence_event_payload_for_storage(payload: Mapping[str, object]) -> Dict[str, object]:
    source = dict(payload or {})
    compact: Dict[str, object] = {}
    for key in (
        "source", "status", "targetCount", "fetchedCount", "savedCount", "changedCount",
        "materialChangedCount", "alertEligibleCount", "lifecycleChangedCount",
    ):
        if key in source:
            compact[key] = source.get(key)
    for key in ("symbols", "changedSymbols", "materialChangedSymbols", "alertEligibleSymbols", "inferenceChangedSymbols", "providers"):
        values = _event_text_list(source.get(key), limit=100, item_limit=96)
        if values:
            compact[key] = values
    for key in ("changedItems", "materialChangedItems", "alertEligibleItems"):
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
        if items or key == "alertEligibleItems":
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
    for key, limit in {
        "eventContract": 96,
        "eligibilityPolicy": 96,
    }.items():
        text = _event_text(source.get(key), limit)
        if text:
            compact[key] = text
    if "allowHistoricalAlert" in source:
        compact["allowHistoricalAlert"] = bool(source.get("allowHistoricalAlert"))
    return compact
