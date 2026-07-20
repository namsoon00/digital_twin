from typing import Dict, Iterable, List, Optional

from .ontology_decision_state import REVIEW_LEVEL_LABELS, review_level_for
from .ontology_relation_catalog import DEFAULT_RELATION_RULES
from .ontology_relation_contracts import OntologyRuleMatch, RelationRuleDefinition
from .ontology_relation_decisions import decision_stage_by_key, resolve_decision_stage


def _rule(rule_id: str, definitions: Optional[List[RelationRuleDefinition]] = None) -> RelationRuleDefinition:
    for item in definitions or DEFAULT_RELATION_RULES:
        if item.rule_id == rule_id:
            return item
    for item in DEFAULT_RELATION_RULES:
        if item.rule_id == rule_id:
            return item
    return DEFAULT_RELATION_RULES[-1]


def _match(
    rule_id: str,
    evidence: Iterable[str],
    missing: Iterable[str] = (),
    matched: bool = True,
    reference_only: bool = False,
    action_level: str = "review",
    evidence_role: str = "context",
    definitions: Optional[List[RelationRuleDefinition]] = None,
) -> OntologyRuleMatch:
    definition = _rule(rule_id, definitions)
    data_state = "partial" if list(missing or []) else "sufficient"
    level = "blocked" if reference_only else review_level_for(action_level, data_state)
    return OntologyRuleMatch(
        rule_id=definition.rule_id,
        label=definition.label,
        version=definition.version,
        relation_type=definition.relation_type,
        signal_type=definition.signal_type,
        matched=matched,
        review_level=level,
        review_label=REVIEW_LEVEL_LABELS[level],
        data_state=data_state,
        evidence_role=evidence_role,
        evidence=[str(item) for item in evidence if str(item or "").strip()],
        missing=[str(item) for item in missing if str(item or "").strip()],
        reference_only=reference_only,
        prompt_hint=definition.prompt_hint,
        evidence_state={"usableForJudgement": not reference_only},
    )


def decision_from_matches(facts: Dict[str, object], matches: List[OntologyRuleMatch]) -> Dict[str, object]:
    active = [item for item in matches if item.matched and not item.reference_only]
    if not active:
        stage = decision_stage_by_key("RELATION_WATCH")
        return {
            "label": stage.label,
            "tone": stage.tone,
            "basis": "ontologyRelationRules",
            "selectedRuleId": "",
            "decisionStage": stage.stage_key,
            "actionGroup": stage.action_group,
            "actionLevel": stage.action_level,
            "reviewLevel": review_level_for(stage.action_level),
        }
    order = {item.rule_id: index for index, item in enumerate(active)}
    selected = min(active, key=lambda item: (order.get(item.rule_id, len(order)), item.rule_id))
    stage = resolve_decision_stage(selected.rule_id, facts, selected.review_level)
    return {
        "label": stage.label,
        "tone": stage.tone,
        "basis": "ontologyRelationRules",
        "selectedRuleId": selected.rule_id,
        "decisionStage": stage.stage_key,
        "actionGroup": stage.action_group,
        "actionLevel": stage.action_level,
        "reviewLevel": selected.review_level,
    }
