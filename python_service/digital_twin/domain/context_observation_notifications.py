"""Graph-proven notification contract for reference-only market observations."""

from __future__ import annotations

from typing import Dict, Iterable, List, Mapping

from .notification_ai_context import is_graph_backed_relation_context


CONTEXT_OBSERVATION_NOTIFICATION_VERSION = "typedb-context-observation-notification-v1"
CONTEXT_OBSERVATION_DECISION_MODE = "typedb-context-observation"


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return str(value or "").strip()


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
        "sourceAboxSnapshotId": _text(
            relation.get("sourceAboxSnapshotId") or graph.get("sourceAboxSnapshotId")
        ),
        "inferenceGenerationId": _text(
            relation.get("inferenceGenerationId") or graph.get("inferenceGenerationId")
        ),
    }


def is_typedb_context_observation_notification(value: object) -> bool:
    return bool(typedb_context_observation_contract(value))


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
