"""Graph-proven notification contract for reference-only market observations."""

from __future__ import annotations

from typing import Dict, Iterable, Mapping

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
