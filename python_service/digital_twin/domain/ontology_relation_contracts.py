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
    strength_score: float
    strength_label: str
    confidence: float
    evidence: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    reference_only: bool = False
    prompt_hint: str = ""
    score_breakdown: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["strengthScore"] = round(float(self.strength_score or 0), 1)
        payload["strengthLabel"] = self.strength_label
        payload["scoreBreakdown"] = dict(self.score_breakdown or {})
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
class ScoreBandDefinition:
    key: str
    label: str
    min_score: float
    action_level: str
    meaning: str
    next_stage_at: float = 0.0

    def contains(self, score: float) -> bool:
        return float(score or 0) >= self.min_score

    def to_dict(self) -> Dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "minScore": self.min_score,
            "actionLevel": self.action_level,
            "meaning": self.meaning,
            "nextStageAt": self.next_stage_at,
        }


@dataclass(frozen=True)
class DecisionStageDefinition:
    stage_key: str
    action_group: str
    action_level: str
    label: str
    tone: str
    min_score: float = 0.0
    next_stage_at: float = 0.0

    def to_dict(self) -> Dict[str, object]:
        return {
            "stageKey": self.stage_key,
            "actionGroup": self.action_group,
            "actionLevel": self.action_level,
            "label": self.label,
            "tone": self.tone,
            "minScore": self.min_score,
            "nextStageAt": self.next_stage_at,
        }
