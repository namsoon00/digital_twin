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

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


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
