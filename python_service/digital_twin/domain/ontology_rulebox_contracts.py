from dataclasses import asdict, dataclass, field as dataclass_field
from typing import Dict, List


GRAPH_REASONER_VERSION = "neo4j-rulebox-graph-reasoner-v1"


@dataclass(frozen=True)
class GraphRuleCondition:
    condition_id: str
    kind: str
    description: str
    field: str = ""
    operator: str = "=="
    value: object = None
    relation_type: str = ""
    direction: str = "out"
    target_kind: str = ""
    target_property_filters: Dict[str, object] = dataclass_field(default_factory=dict)
    relation_property_filters: Dict[str, object] = dataclass_field(default_factory=dict)
    min_weight: float = 0.0

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)

    @staticmethod
    def from_dict(payload: Dict[str, object]):
        payload = dict(payload or {})
        return GraphRuleCondition(
            condition_id=str(payload.get("condition_id") or payload.get("conditionId") or ""),
            kind=str(payload.get("kind") or ""),
            description=str(payload.get("description") or ""),
            field=str(payload.get("field") or ""),
            operator=str(payload.get("operator") or "=="),
            value=payload.get("value"),
            relation_type=str(payload.get("relation_type") or payload.get("relationType") or ""),
            direction=str(payload.get("direction") or "out"),
            target_kind=str(payload.get("target_kind") or payload.get("targetKind") or ""),
            target_property_filters=dict(payload.get("target_property_filters") or payload.get("targetPropertyFilters") or {}),
            relation_property_filters=dict(payload.get("relation_property_filters") or payload.get("relationPropertyFilters") or {}),
            min_weight=float(payload.get("min_weight") or payload.get("minWeight") or 0),
        )


@dataclass(frozen=True)
class GraphRuleDerivation:
    relation_type: str
    target_kind: str
    target_key: str
    target_label: str
    tbox_class: str
    tbox_classes: List[str] = dataclass_field(default_factory=list)
    polarity: str = "context"
    risk_impact: float = 0.0
    support_impact: float = 0.0
    weight: float = 0.72
    belief_label: str = ""
    ai_influence_label: str = ""
    action_group: str = ""
    action_level: str = ""
    decision_stage: str = ""
    stage_priority: float = 0.0

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)

    @staticmethod
    def from_dict(payload: Dict[str, object]):
        payload = dict(payload or {})
        return GraphRuleDerivation(
            relation_type=str(payload.get("relation_type") or payload.get("relationType") or ""),
            target_kind=str(payload.get("target_kind") or payload.get("targetKind") or ""),
            target_key=str(payload.get("target_key") or payload.get("targetKey") or ""),
            target_label=str(payload.get("target_label") or payload.get("targetLabel") or ""),
            tbox_class=str(payload.get("tbox_class") or payload.get("tboxClass") or ""),
            tbox_classes=[str(item) for item in (payload.get("tbox_classes") or payload.get("tboxClasses") or [])],
            polarity=str(payload.get("polarity") or "context"),
            risk_impact=float(payload.get("risk_impact") or payload.get("riskImpact") or 0),
            support_impact=float(payload.get("support_impact") or payload.get("supportImpact") or 0),
            weight=float(payload.get("weight") or 0.72),
            belief_label=str(payload.get("belief_label") or payload.get("beliefLabel") or ""),
            ai_influence_label=str(payload.get("ai_influence_label") or payload.get("aiInfluenceLabel") or ""),
            action_group=str(payload.get("action_group") or payload.get("actionGroup") or ""),
            action_level=str(payload.get("action_level") or payload.get("actionLevel") or ""),
            decision_stage=str(payload.get("decision_stage") or payload.get("decisionStage") or ""),
            stage_priority=float(payload.get("stage_priority") or payload.get("stagePriority") or 0),
        )


@dataclass(frozen=True)
class GraphInferenceRule:
    rule_id: str
    label: str
    version: str
    source_kind: str
    conditions: List[GraphRuleCondition]
    derivations: List[GraphRuleDerivation]
    action_group: str
    action_level: str
    prompt_hint: str
    enabled: bool = True

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["conditionCount"] = len(self.conditions)
        payload["derivationCount"] = len(self.derivations)
        return payload

    @staticmethod
    def from_dict(payload: Dict[str, object]):
        payload = dict(payload or {})
        conditions = [
            GraphRuleCondition.from_dict(item)
            for item in (payload.get("conditions") or [])
            if isinstance(item, dict)
        ]
        derivations = [
            GraphRuleDerivation.from_dict(item)
            for item in (payload.get("derivations") or [])
            if isinstance(item, dict)
        ]
        rule_id = str(payload.get("rule_id") or payload.get("ruleId") or "").strip()
        if not rule_id:
            raise ValueError("RuleBox rule_id is required.")
        if not conditions:
            raise ValueError("RuleBox rule must contain at least one condition.")
        if not derivations:
            raise ValueError("RuleBox rule must contain at least one derivation.")
        return GraphInferenceRule(
            rule_id=rule_id,
            label=str(payload.get("label") or rule_id),
            version=str(payload.get("version") or GRAPH_REASONER_VERSION),
            source_kind=str(payload.get("source_kind") or payload.get("sourceKind") or "admin"),
            conditions=conditions,
            derivations=derivations,
            action_group=str(payload.get("action_group") or payload.get("actionGroup") or ""),
            action_level=str(payload.get("action_level") or payload.get("actionLevel") or ""),
            prompt_hint=str(payload.get("prompt_hint") or payload.get("promptHint") or ""),
            enabled=bool(payload.get("enabled")) if "enabled" in payload else True,
        )
