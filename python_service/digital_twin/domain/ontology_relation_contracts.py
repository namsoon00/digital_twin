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
    decision_effect: str = ""
    evidence: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    reference_only: bool = False
    prompt_hint: str = ""
    evidence_state: Dict[str, object] = field(default_factory=dict)
    # Keep TypeDB RuleBox decision metadata with the match. The execution
    # guide must describe native allowed/blocked relations, not reclassify
    # them from raw facts in Python.
    decision_stage: str = ""
    action_group: str = ""
    action_level: str = ""
    decision_label: str = ""
    decision_tone: str = ""
    target_role: str = ""
    action_policy: str = ""
    allowed_actions: List[str] = field(default_factory=list)
    blocked_actions: List[str] = field(default_factory=list)
    primary_action: str = ""
    primary_action_label: str = ""
    candidate_action: str = ""
    candidate_action_label: str = ""
    blocked_action_labels: List[str] = field(default_factory=list)
    strengthen_conditions: List[str] = field(default_factory=list)
    weaken_conditions: List[str] = field(default_factory=list)
    next_checks: List[str] = field(default_factory=list)
    notification_category: str = ""
    notification_severity: str = ""
    rule_source_kind: str = ""
    rule_scope_families: List[str] = field(default_factory=list)
    assessment_scope: str = ""
    rule_domain_module: str = ""
    rule_lifecycle_class: str = ""
    rule_trigger_families: List[str] = field(default_factory=list)
    rule_required_facts: List[str] = field(default_factory=list)
    rule_context_requirements: List[Dict[str, object]] = field(default_factory=list)
    rule_invalidation_contract: Dict[str, object] = field(default_factory=dict)
    rule_derived_outputs: List[Dict[str, object]] = field(default_factory=list)
    context_completeness_policy: Dict[str, object] = field(default_factory=dict)
    rule_dependency_contract_version: str = ""
    rule_output_contract: Dict[str, object] = field(default_factory=dict)
    knowledge_basis: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["reviewLevel"] = payload.pop("review_level")
        payload["reviewLabel"] = payload.pop("review_label")
        payload["dataState"] = payload.pop("data_state")
        payload["evidenceRole"] = payload.pop("evidence_role")
        payload["evidenceState"] = payload.pop("evidence_state")
        payload["decisionStage"] = payload.pop("decision_stage")
        payload["actionGroup"] = payload.pop("action_group")
        payload["actionLevel"] = payload.pop("action_level")
        payload["decisionLabel"] = payload.pop("decision_label")
        payload["decisionTone"] = payload.pop("decision_tone")
        payload["targetRole"] = payload.pop("target_role")
        payload["actionPolicy"] = payload.pop("action_policy")
        payload["allowedActions"] = payload.pop("allowed_actions")
        payload["blockedActions"] = payload.pop("blocked_actions")
        payload["primaryAction"] = payload.pop("primary_action")
        payload["primaryActionLabel"] = payload.pop("primary_action_label")
        payload["candidateAction"] = payload.pop("candidate_action")
        payload["candidateActionLabel"] = payload.pop("candidate_action_label")
        payload["blockedActionLabels"] = payload.pop("blocked_action_labels")
        payload["strengthenConditions"] = payload.pop("strengthen_conditions")
        payload["weakenConditions"] = payload.pop("weaken_conditions")
        payload["nextChecks"] = payload.pop("next_checks")
        payload["notificationCategory"] = payload.pop("notification_category")
        payload["notificationSeverity"] = payload.pop("notification_severity")
        payload["ruleSourceKind"] = payload.pop("rule_source_kind")
        payload["ruleScopeFamilies"] = payload.pop("rule_scope_families")
        payload["assessmentScope"] = payload.pop("assessment_scope")
        payload["ruleDomainModule"] = payload.pop("rule_domain_module")
        payload["ruleLifecycleClass"] = payload.pop("rule_lifecycle_class")
        payload["ruleTriggerFamilies"] = payload.pop("rule_trigger_families")
        payload["ruleRequiredFacts"] = payload.pop("rule_required_facts")
        payload["ruleContextRequirements"] = payload.pop("rule_context_requirements")
        payload["ruleInvalidationContract"] = payload.pop("rule_invalidation_contract")
        payload["ruleDerivedOutputs"] = payload.pop("rule_derived_outputs")
        payload["contextCompletenessPolicy"] = payload.pop("context_completeness_policy")
        payload["ruleDependencyContractVersion"] = payload.pop("rule_dependency_contract_version")
        payload["ruleOutputContract"] = payload.pop("rule_output_contract")
        payload["knowledgeBasis"] = payload.pop("knowledge_basis")
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
