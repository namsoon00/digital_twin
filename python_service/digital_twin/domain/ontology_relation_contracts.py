from dataclasses import asdict, dataclass, field
from typing import Dict, List

from .message_types import DEFAULT_RELATION_RULE_THRESHOLDS


ONTOLOGY_RULE_ENGINE_VERSION = "ontology-relation-rules-v1"
AI_PROMPT_REGISTRY_VERSION = "ai-prompt-registry-v1"

BTC_SENSITIVE_SYMBOLS = {"MSTR", "STRC", "COIN", "MARA", "RIOT", "CLSK", "HUT", "BITF"}

DEFAULT_RELATION_THRESHOLDS = {
    str(key): float(value)
    for key, value in DEFAULT_RELATION_RULE_THRESHOLDS.items()
}


@dataclass
class RelationRuleDefinition:
    rule_id: str
    label: str
    version: str
    relation_type: str
    signal_type: str
    condition_summary: str
    prompt_hint: str
    required_fields: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class OntologyRuleMatch:
    rule_id: str
    label: str
    version: str
    relation_type: str
    signal_type: str
    matched: bool
    review_level: str
    review_label: str
    data_state: str
    evidence_role: str
    evidence: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    reference_only: bool = False
    prompt_hint: str = ""
    evidence_state: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["reviewLevel"] = payload.pop("review_level")
        payload["reviewLabel"] = payload.pop("review_label")
        payload["dataState"] = payload.pop("data_state")
        payload["evidenceRole"] = payload.pop("evidence_role")
        payload["evidenceState"] = payload.pop("evidence_state")
        return payload


@dataclass
class OntologyPromptTemplate:
    prompt_id: str
    label: str
    version: str
    purpose: str
    system_prompt: str
    user_prompt: str
    output_schema: Dict[str, object] = field(default_factory=dict)
    guardrails: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["promptId"] = payload.pop("prompt_id")
        payload["systemPrompt"] = payload.pop("system_prompt")
        payload["userPrompt"] = payload.pop("user_prompt")
        return payload


@dataclass(frozen=True)
class DecisionStageDefinition:
    stage_key: str
    action_group: str
    action_level: str
    label: str
    tone: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "stageKey": self.stage_key,
            "actionGroup": self.action_group,
            "actionLevel": self.action_level,
            "label": self.label,
            "tone": self.tone,
        }
