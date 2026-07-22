import re
from dataclasses import asdict, dataclass, field as dataclass_field
from typing import Dict, List


GRAPH_REASONER_VERSION = "typedb-rulebox-graph-reasoner-v1"
WATCHLIST_TARGET_ROLE = "watchlist"
WATCHLIST_ACTION_POLICY = "ENTRY_ONLY"
WATCHLIST_ALLOWED_ACTIONS = ["BUY", "HOLD", "AVOID"]
WATCHLIST_BLOCKED_ACTIONS = ["ADD", "TRIM", "SELL"]
HOLDING_TARGET_ROLE = "holding"


def string_list(value: object) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item or "").strip()]
    if value is None or value == "":
        return []
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item or "").strip()]
    return [item.strip() for item in str(value).replace("\n", ",").split(",") if item.strip()]


def stable_rulebox_component_id(value: object, prefix: str, index: int) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.:-]+", "-", str(value or "").strip()).strip("-")
    if not normalized:
        normalized = prefix + "-" + str(index + 1)
    return normalized[:96]


def unique_rulebox_component_id(value: object, prefix: str, index: int, seen: set) -> str:
    base = stable_rulebox_component_id(value, prefix, index)
    candidate = base
    suffix = 2
    while candidate in seen:
        candidate = (base[:88] + "-" + str(suffix))[:96]
        suffix += 1
    seen.add(candidate)
    return candidate


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
    role: str = "required"

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
            role=str(payload.get("role") or payload.get("conditionRole") or "required"),
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
    # Empty means "inherit the derivation polarity".  Defaulting this to
    # context silently flattened risk/support rules into neutral evidence.
    evidence_role: str = ""
    belief_label: str = ""
    ai_influence_label: str = ""
    action_group: str = ""
    action_level: str = ""
    decision_stage: str = ""
    # These are RuleBox/TBox-owned presentation semantics.  Runtime readers
    # must use the values materialized with the inference relation instead of
    # mapping a stage key through a separate Python policy table.
    decision_label: str = ""
    decision_tone: str = ""
    target_role: str = ""
    action_policy: str = ""
    allowed_actions: List[str] = dataclass_field(default_factory=list)
    blocked_actions: List[str] = dataclass_field(default_factory=list)

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
            evidence_role=str(payload.get("evidence_role") or payload.get("evidenceRole") or payload.get("polarity") or "context"),
            belief_label=str(payload.get("belief_label") or payload.get("beliefLabel") or ""),
            ai_influence_label=str(payload.get("ai_influence_label") or payload.get("aiInfluenceLabel") or ""),
            action_group=str(payload.get("action_group") or payload.get("actionGroup") or ""),
            action_level=str(payload.get("action_level") or payload.get("actionLevel") or ""),
            decision_stage=str(payload.get("decision_stage") or payload.get("decisionStage") or ""),
            decision_label=str(payload.get("decision_label") or payload.get("decisionLabel") or ""),
            decision_tone=str(payload.get("decision_tone") or payload.get("decisionTone") or ""),
            target_role=str(payload.get("target_role") or payload.get("targetRole") or ""),
            action_policy=str(payload.get("action_policy") or payload.get("actionPolicy") or ""),
            allowed_actions=string_list(payload.get("allowed_actions") or payload.get("allowedActions")),
            blocked_actions=string_list(payload.get("blocked_actions") or payload.get("blockedActions")),
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
    # Optional governance key for safely grouping equivalent rule variants
    # into one current-situation hypothesis family. Empty keeps the rule on
    # the conservative structural-signature path.
    hypothesis_family_key: str = ""
    any_condition_min_count: int = 1
    enabled: bool = True

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["conditionCount"] = len(self.conditions)
        payload["derivationCount"] = len(self.derivations)
        return payload

    @staticmethod
    def from_dict(payload: Dict[str, object]):
        payload = dict(payload or {})
        rule_id = str(payload.get("rule_id") or payload.get("ruleId") or "").strip()
        if not rule_id:
            raise ValueError("RuleBox rule_id is required.")
        seen_condition_ids = set()
        conditions = [
            GraphRuleCondition.from_dict({
                **item,
                "condition_id": unique_rulebox_component_id(
                    item.get("condition_id") or item.get("conditionId"),
                    "condition",
                    index,
                    seen_condition_ids,
                ),
            })
            for index, item in enumerate(payload.get("conditions") or [])
            if isinstance(item, dict)
        ]
        derivations = [
            GraphRuleDerivation.from_dict(item)
            for item in (payload.get("derivations") or [])
            if isinstance(item, dict)
        ]
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
            hypothesis_family_key=str(
                payload.get("hypothesis_family_key")
                or payload.get("hypothesisFamilyKey")
                or ""
            ).strip(),
            any_condition_min_count=max(1, int(payload.get("any_condition_min_count") or payload.get("anyConditionMinCount") or 1)),
            enabled=bool(payload.get("enabled")) if "enabled" in payload else True,
        )
