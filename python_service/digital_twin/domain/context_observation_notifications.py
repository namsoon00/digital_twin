"""Graph-proven notification contract for reference-only market observations."""

from __future__ import annotations

from typing import Dict, Iterable, List, Mapping

from .notification_ai_context import is_graph_backed_relation_context


CONTEXT_OBSERVATION_NOTIFICATION_VERSION = "typedb-context-observation-notification-v2"
CONTEXT_OBSERVATION_DECISION_MODE = "typedb-context-observation"
CONTEXT_OBSERVATION_DELIVERY_VERSION = "typedb-context-observation-delivery-v2"
REVIEW_OBSERVATION_NOTIFICATION_VERSION = "typedb-review-observation-notification-v1"
REVIEW_OBSERVATION_DECISION_MODE = "typedb-review-observation"
REVIEW_OBSERVATION_DELIVERY_VERSION = "typedb-review-observation-delivery-v2"

DELIVERY_POLICY_BLOCKING_DECISIONS = {
    "baseline",
    "cooldown",
    "in-flight",
    "unchanged-inference",
}


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return str(value or "").strip()


def _delivery_policy_block_reason(payload: Mapping[str, object]) -> str:
    decision = _text(payload.get("cooldownDecision")).lower()
    if payload.get("cooldownSuppressed") is True or decision in DELIVERY_POLICY_BLOCKING_DECISIONS:
        return _text(payload.get("cooldownReason")) or (
            "같은 상태의 재알림 간격이 지나지 않아 웹 이력에만 저장합니다."
        )
    return ""


def _relation_context(value: object) -> Dict[str, object]:
    payload = _mapping(value)
    relation = _mapping(payload.get("ontologyRelationContext"))
    if relation:
        return relation
    metadata = _mapping(payload.get("metadata"))
    relation = _mapping(metadata.get("ontologyRelationContext"))
    if relation:
        return relation
    if payload.get("graphStoreInference") or payload.get("graphStoreUsed"):
        return payload
    return {}


def _rule_rows(relation: Mapping[str, object]) -> Iterable[Dict[str, object]]:
    graph = _mapping(relation.get("graphStoreInference"))
    for group in (
        relation.get("activeRules"),
        relation.get("matchedRules"),
        relation.get("referenceRules"),
        graph.get("relations"),
        graph.get("traces"),
    ):
        for item in group or []:
            if isinstance(item, Mapping):
                yield dict(item)


def _explicit_false(row: Mapping[str, object], basis: Mapping[str, object]) -> bool:
    for container in (basis, row):
        if "requiresHypothesis" in container:
            value = container.get("requiresHypothesis")
        elif "requires_hypothesis" in container:
            value = container.get("requires_hypothesis")
        else:
            continue
        if value is False or value == 0:
            return True
        return _text(value).lower() in {"0", "false", "no", "off", "disabled"}
    return False


def typedb_context_observation_contract(value: object) -> Dict[str, object]:
    """Return a delivery contract only when the selected TypeDB rule proves it.

    Metadata flags alone are intentionally insufficient. The selected rule must
    carry the explicit ontology knowledge basis that makes it reference-only.
    """

    payload = _mapping(value)
    relation = _relation_context(payload)
    if not is_graph_backed_relation_context(relation):
        return {}
    decision = _mapping(relation.get("decision"))
    envelope = _mapping(relation.get("actionEnvelope")) or _mapping(decision.get("actionEnvelope"))
    synthesis = _mapping(payload.get("v2DecisionSynthesis"))
    selected_rule_id = _text(
        decision.get("selectedRuleId")
        or envelope.get("selectedRuleId")
        or synthesis.get("selected_rule_id")
        or synthesis.get("selectedRuleId")
    )
    if not selected_rule_id:
        return {}

    selected_row: Dict[str, object] = {}
    for row in _rule_rows(relation):
        rule_id = _text(row.get("ruleId") or row.get("rule_id") or row.get("sourceRuleId"))
        if rule_id != selected_rule_id:
            continue
        basis = _mapping(row.get("knowledgeBasis") or row.get("knowledge_basis"))
        rule_kind = _text(
            basis.get("ruleKind")
            or basis.get("rule_kind")
            or row.get("ruleKind")
            or row.get("rule_kind")
        ).lower()
        eligibility = _text(
            basis.get("decisionEligibility")
            or basis.get("decision_eligibility")
            or row.get("decisionEligibility")
            or row.get("decision_eligibility")
        ).lower()
        if (
            rule_kind == "context-observation"
            and eligibility == "reference-only"
            and _explicit_false(row, basis)
        ):
            selected_row = row
            break
    if not selected_row:
        return {}

    subject = _mapping(relation.get("subject"))
    facts = _mapping(relation.get("facts"))
    graph = _mapping(relation.get("graphStoreInference"))
    return {
        "schemaVersion": CONTEXT_OBSERVATION_NOTIFICATION_VERSION,
        "status": "eligible",
        "decisionMode": CONTEXT_OBSERVATION_DECISION_MODE,
        "messageClass": "informational-market-state-change",
        "selectedRuleId": selected_rule_id,
        "selectedRuleLabel": _text(
            selected_row.get("label")
            or selected_row.get("ruleLabel")
            or selected_row.get("targetLabel")
            or decision.get("label")
        ),
        "ruleKind": "context-observation",
        "decisionEligibility": "reference-only",
        "requiresHypothesis": False,
        "requiresAiJudgement": False,
        "requiresAiNarrative": True,
        "action": "NO_ACTION",
        "validationState": "reference-only",
        "symbol": _text(subject.get("symbol") or facts.get("symbol")).upper(),
        "market": _text(subject.get("market") or facts.get("market")).upper(),
        "graphSource": _text(relation.get("source")),
        "graphStore": _text(relation.get("graphStore") or graph.get("graphStore")),
        "knowledgeOwner": _text(
            _mapping(selected_row.get("knowledgeBasis")).get("owner")
        ),
        "theoryFamily": _text(
            _mapping(selected_row.get("knowledgeBasis")).get("theoryFamily")
        ),
        "sourceAboxSnapshotId": _text(
            relation.get("sourceAboxSnapshotId") or graph.get("sourceAboxSnapshotId")
        ),
        "inferenceGenerationId": _text(
            relation.get("inferenceGenerationId") or graph.get("inferenceGenerationId")
        ),
    }


def is_typedb_context_observation_notification(value: object) -> bool:
    return bool(typedb_context_observation_contract(value))


def typedb_review_observation_contract(value: object) -> Dict[str, object]:
    """Return an actionless contract for TypeDB hypotheses that cannot act alone.

    A risk or constraint hypothesis can be useful enough to notify, but it may
    not manufacture HOLD/BUY/SELL.  The persisted synthesis action authority is
    the boundary: only ``originate`` may enter the investment-judgement path.
    """

    payload = _mapping(value)
    relation = _relation_context(payload)
    if not is_graph_backed_relation_context(relation):
        return {}
    synthesis = _mapping(payload.get("v2DecisionSynthesis"))
    if not synthesis:
        synthesis = _mapping(_mapping(payload.get("metadata")).get("v2DecisionSynthesis"))
    action_authority = _text(
        synthesis.get("action_authority") or synthesis.get("actionAuthority")
    ).lower()
    if action_authority not in {"modify", "observe"}:
        return {}
    selected_rule_id = _text(
        synthesis.get("selected_rule_id")
        or synthesis.get("selectedRuleId")
        or _mapping(relation.get("decision")).get("selectedRuleId")
    )
    eligible_hypothesis_ids = [
        _text(item)
        for item in (
            synthesis.get("eligible_hypothesis_ids")
            or synthesis.get("eligibleHypothesisIds")
            or []
        )
        if _text(item)
    ]
    if not selected_rule_id or not eligible_hypothesis_ids:
        return {}
    subject = _mapping(relation.get("subject"))
    facts = _mapping(relation.get("facts"))
    return {
        "schemaVersion": REVIEW_OBSERVATION_NOTIFICATION_VERSION,
        "status": "eligible",
        "decisionMode": REVIEW_OBSERVATION_DECISION_MODE,
        "messageClass": "typedb-risk-or-constraint-review",
        "selectedRuleId": selected_rule_id,
        "ruleKind": "review-observation",
        "decisionEligibility": "review-only",
        "requiresHypothesis": True,
        "requiresAiJudgement": False,
        "requiresAiNarrative": True,
        "actionAuthority": action_authority,
        "action": "NO_ACTION",
        "validationState": "review-only",
        "symbol": _text(subject.get("symbol") or facts.get("symbol")).upper(),
        "market": _text(subject.get("market") or facts.get("market")).upper(),
        "eligibleHypothesisIds": eligible_hypothesis_ids,
        "graphSource": _text(relation.get("source")),
        "graphStore": _text(relation.get("graphStore")),
    }


def typedb_narrative_only_contract(value: object) -> Dict[str, object]:
    return typedb_context_observation_contract(value) or typedb_review_observation_contract(value)


def context_observation_evidence_presentation(value: object) -> Dict[str, object]:
    """Build concrete customer facts for the selected reference-only rule."""

    payload = _mapping(value)
    contract = typedb_context_observation_contract(payload)
    if not contract:
        return {}
    selected_rule_id = _text(contract.get("selectedRuleId"))
    if "disclosure" not in selected_rule_id.lower():
        return {}
    brief = _mapping(payload.get("notificationAiDecisionBrief"))
    if not brief:
        audit = _mapping(payload.get("notificationAiExecutionAudit"))
        brief = _mapping(audit.get("decisionBrief"))
    evidence = _mapping(brief.get("evidence"))
    candidates: List[Dict[str, object]] = []
    disclosure = _mapping(evidence.get("disclosure"))
    if disclosure:
        candidates.append(disclosure)
    for item in evidence.get("researchEvidence") or []:
        if isinstance(item, Mapping):
            candidates.append(dict(item))
    for item in payload.get("researchEvidence") or []:
        if isinstance(item, Mapping):
            candidates.append(dict(item))

    def eligible(item: Mapping[str, object]) -> bool:
        kind = _text(item.get("kind") or item.get("eventType")).lower()
        is_disclosure = bool(
            "disclosure" in kind
            or "filing" in kind
            or _text(item.get("officialDocumentState")).lower()
            == "document-verified"
        )
        decision_eligible = bool(
            item.get("investmentJudgmentEligible") is True
            or _text(item.get("evidenceEligibilityState")).lower() == "eligible"
        )
        return bool(
            is_disclosure
            and item.get("documentVerified") is True
            and item.get("analysisReady") is True
            and _text(item.get("documentHash"))
            and decision_eligible
        )

    rows = [item for item in candidates if eligible(item)]
    if not rows:
        return {}
    rows.sort(
        key=lambda item: _text(
            item.get("sourceAsOf")
            or item.get("publishedAt")
            or item.get("receiptDate")
            or item.get("observedAt")
        ),
        reverse=True,
    )
    item = rows[0]
    analysis = _mapping(item.get("disclosureAnalysis"))
    confirmed_facts = [
        _text(entry)
        for entry in analysis.get("confirmedFacts") or []
        if _text(entry)
    ][:3]
    watch_items = [
        _text(entry)
        for entry in analysis.get("watchItems") or []
        if _text(entry)
    ][:3]
    summary = _text(
        analysis.get("impactSummary")
        or analysis.get("summary")
        or item.get("summary")
    )
    return {
        "kind": "disclosure",
        "evidenceId": _text(item.get("evidenceId") or item.get("id")),
        "title": _text(item.get("reportName") or item.get("title") or "공시 원문"),
        "source": _text(item.get("sourcePublisher") or item.get("source") or "공시 원문"),
        "receiptDate": _text(
            item.get("receiptDate")
            or item.get("publishedAt")
            or item.get("sourceAsOf")
        ),
        "sourceRevision": _text(item.get("sourceRevision")),
        "url": _text(item.get("url") or item.get("sourceUrl")),
        "summary": summary,
        "confirmedFacts": confirmed_facts,
        "watchItems": watch_items,
        "documentVerified": True,
        "analysisReady": True,
    }


def context_observation_delivery_decision(value: object) -> Dict[str, object]:
    """Allow a reference-only push only when a material change is auditable.

    A semantic relation such as benchmark beta is useful graph context, but a
    newly materialized relation is not by itself useful enough for a customer
    push.  A concrete source event, verified follow-up transition, verified
    disclosure, or TypeDB notification-intent rule must also be present.
    """

    payload = _mapping(value)
    contract = typedb_context_observation_contract(payload)
    if not contract:
        return {}
    publication = _mapping(payload.get("decisionPublication"))
    outcome = _text(publication.get("outcomeKind")).upper()
    insight = _mapping(payload.get("ontologyInsight"))
    semantic = _mapping(insight.get("semanticComponents"))
    material_source_keys = sorted({
        _text(item)
        for item in (
            semantic.get("materialSourceEventKeys")
            or insight.get("materialSourceEventKeys")
            or payload.get("materialSourceEventKeys")
            or []
        )
        if _text(item)
    })
    continuity = _mapping(payload.get("decisionContinuityPacket"))
    verified_follow_ups = [
        _mapping(item)
        for item in continuity.get("followUpConditions") or []
        if isinstance(item, Mapping)
        and item.get("transitionVerified") is True
        and _text(item.get("transitionAt"))
        and _text(item.get("status")).lower()
        in {"satisfied", "invalidated", "expired"}
    ]
    relation = _relation_context(payload)
    notification_intent_rule_ids = sorted({
        _text(row.get("ruleId") or row.get("rule_id") or row.get("sourceRuleId"))
        for row in _rule_rows(relation)
        if _text(row.get("ruleId") or row.get("rule_id") or row.get("sourceRuleId"))
        and _text(_mapping(row.get("knowledgeBasis") or row.get("knowledge_basis")).get("owner"))
        == "notification-policy"
        and row.get("matched") is not False
    })
    verified_evidence = context_observation_evidence_presentation(payload)
    authorization_sources = []
    if material_source_keys:
        authorization_sources.append("material-source-event")
    if verified_follow_ups:
        authorization_sources.append("verified-follow-up-transition")
    if verified_evidence:
        authorization_sources.append("verified-source-document")
    if notification_intent_rule_ids:
        authorization_sources.append("typedb-notification-intent")

    decision = {
        "version": CONTEXT_OBSERVATION_DELIVERY_VERSION,
        "decision": "suppress",
        "reason": (
            "참고 관계만 새로 성립했고 사용자에게 알릴 새 원문이나 검증된 조건 전환이 없어 "
            "웹 관계 이력에만 저장합니다."
        ),
        "suppressionReason": "context_observation_web_history",
        "pushValueClass": "web-only-context-observation",
        "publicationOutcome": outcome,
        "selectedRuleId": contract.get("selectedRuleId"),
        "authorizationSources": authorization_sources,
        "materialSourceEventCount": len(material_source_keys),
        "verifiedFollowUpTransitionCount": len(verified_follow_ups),
        "notificationIntentRuleIds": notification_intent_rule_ids,
        "verifiedEvidenceId": _text(verified_evidence.get("evidenceId")),
    }
    if outcome != "OBSERVATION":
        decision.update({
            "reason": "검증된 참고 관찰 발행물이 없어 투자 푸시로 보내지 않습니다.",
            "suppressionReason": "missing_context_observation_publication",
        })
        return decision
    delivery_policy_block_reason = _delivery_policy_block_reason(payload)
    if delivery_policy_block_reason:
        decision.update({
            "reason": delivery_policy_block_reason,
            "suppressionReason": "context_observation_delivery_cooldown",
        })
        return decision
    if authorization_sources:
        decision.update({
            "decision": "send",
            "reason": "검증된 참고 관찰에 사용자에게 알릴 구체적인 새 근거가 연결됐습니다.",
            "suppressionReason": "",
            "pushValueClass": "material-context-observation",
        })
    return decision


def review_observation_delivery_decision(value: object) -> Dict[str, object]:
    """Deliver a non-originating hypothesis as review evidence, never an action."""

    payload = _mapping(value)
    contract = typedb_review_observation_contract(payload)
    if not contract:
        return {}
    publication = _mapping(payload.get("decisionPublication"))
    outcome = _text(publication.get("outcomeKind")).upper()
    cooldown_decision = _text(payload.get("cooldownDecision")).lower()
    relation_transition = _mapping(payload.get("decisionTransition")) or _mapping(
        _mapping(payload.get("ontologyRelationDiff")).get("decisionTransition")
    )
    insight = _mapping(payload.get("ontologyInsight"))
    semantic = _mapping(insight.get("semanticComponents"))
    material_source_keys = sorted({
        _text(item)
        for item in (
            semantic.get("materialSourceEventKeys")
            or insight.get("materialSourceEventKeys")
            or payload.get("materialSourceEventKeys")
            or []
        )
        if _text(item)
    })
    authorization_sources = []
    if cooldown_decision in {
        "new-condition",
        "meaningful-change",
        "typedb-profit-loss-change",
        "scheduled-summary",
    }:
        authorization_sources.append("delivery-cadence:" + cooldown_decision)
    if bool(relation_transition.get("material")):
        authorization_sources.append("material-relation-transition")
    if material_source_keys:
        authorization_sources.append("material-source-event")

    decision = {
        "version": REVIEW_OBSERVATION_DELIVERY_VERSION,
        "decision": "suppress",
        "reason": "행동 권한이 없는 관계 검토가 반복되어 웹 이력에만 저장합니다.",
        "suppressionReason": "review_observation_web_history",
        "pushValueClass": "web-only-review-observation",
        "publicationOutcome": outcome,
        "selectedRuleId": contract.get("selectedRuleId"),
        "authorizationSources": authorization_sources,
        "materialSourceEventCount": len(material_source_keys),
        "actionAuthority": contract.get("actionAuthority"),
    }
    if outcome != "REVIEW_ONLY":
        decision.update({
            "reason": "검증된 관계 검토 발행물이 없어 사용자 알림으로 보내지 않습니다.",
            "suppressionReason": "missing_review_observation_publication",
        })
        return decision
    delivery_policy_block_reason = _delivery_policy_block_reason(payload)
    if delivery_policy_block_reason:
        decision.update({
            "reason": delivery_policy_block_reason,
            "suppressionReason": "review_observation_delivery_cooldown",
        })
        return decision
    if authorization_sources:
        decision.update({
            "decision": "send",
            "reason": "TypeDB가 매매 결론 없이 다시 확인할 위험·제약 관계를 검증했습니다.",
            "suppressionReason": "",
            "pushValueClass": "material-review-observation",
        })
    return decision
