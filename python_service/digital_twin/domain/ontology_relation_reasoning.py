"""Ontology relation prompt and read-model helpers.

Runtime investment judgement must use active TypeDB InferenceBox results.
This module formats prompt/context payloads only; it must not materialize
InferenceBox output or bypass the graph-store boundary for user-facing
investment judgement.
"""

from typing import Dict, Iterable, List, Optional

from .market_data import clamp, number
from .ontology_prompt_registry import (
    DEFAULT_PROMPT_TEMPLATES,
    default_ai_prompt_policy_text,
    default_ai_prompt_templates_text,
    default_ontology_relation_reasoning_text,
)
from .ontology_relation_contracts import (
    AI_PROMPT_REGISTRY_VERSION,
    DEFAULT_RELATION_THRESHOLDS,
    ONTOLOGY_RULE_ENGINE_VERSION,
    DecisionStageDefinition,
    OntologyPromptTemplate,
    OntologyRuleMatch,
    RelationRuleDefinition,
)
from .ontology_relation_facts import (
    _bp_text,
    _fx_context_line_from_facts,
    _has_numeric_fact,
    _number_text,
    _rate_context_line_from_facts,
    moving_average_distance_text,
    position_signal_facts,
    research_evidence_facts,
)
from .ontology_relation_catalog import (
    DECISION_LABEL_ALIASES,
    DECISION_STAGE_DEFINITIONS,
    DEFAULT_RELATION_RULES,
)
from .portfolio import PortfolioSummary, Position
from .ontology_relation_decisions import decision_stage_by_key
from .ontology_relation_execution_plan import execution_plan_from_relation_context
from .ontology_relation_prompt_context import build_ai_prompt_context
from .ontology_relation_settings import (
    _thresholds,
    parse_ai_prompt_templates_text,
    parse_relation_rule_definitions_text,
    prompt_template,
    prompt_template_for_message_type,
    prompt_templates_from_settings,
    relation_rule_definitions_from_settings,
    relation_thresholds_from_settings,
)


def relation_rule_context_summary_lines(context: Dict[str, object]) -> List[str]:
    if not isinstance(context, dict) or not context:
        return []
    lines: List[str] = []
    state = context.get("decisionState") if isinstance(context.get("decisionState"), dict) else {}
    review_label = str(state.get("reviewLevelLabel") or context.get("reviewLevelLabel") or "").strip()
    data_label = str(state.get("dataStateLabel") or context.get("dataStateLabel") or "").strip()
    if review_label:
        lines.append("확인 단계 " + review_label)
    if data_label:
        lines.append("자료 상태 " + data_label)
    fact_rows: List[Dict[str, object]] = []
    for candidate in [
        context.get("facts"),
        (context.get("executionPlan") or {}).get("sourceFacts") if isinstance(context.get("executionPlan"), dict) else {},
        (context.get("promptContext") or {}).get("facts") if isinstance(context.get("promptContext"), dict) else {},
    ]:
        if isinstance(candidate, dict) and candidate:
            fact_rows.append(candidate)
    for facts in fact_rows:
        for fact_line in [_rate_context_line_from_facts(facts), _fx_context_line_from_facts(facts)]:
            if fact_line and fact_line not in lines:
                lines.append(fact_line)
    active_rules = context.get("activeRules") or context.get("matchedRules") or []
    names = []
    for item in active_rules:
        if not isinstance(item, dict) or item.get("referenceOnly") or item.get("reference_only"):
            continue
        label = str(item.get("label") or item.get("rule_id") or item.get("ruleId") or "").strip()
        if label:
            names.append(label)
    if names:
        lines.append("성립 규칙 " + " · ".join(names[:3]))
    missing = context.get("missingData") or []
    missing_names = []
    for item in missing:
        if isinstance(item, dict):
            text = str(item.get("label") or item.get("key") or "").strip()
        else:
            text = str(item or "").strip()
        if text:
            missing_names.append(text)
    if missing_names:
        lines.append("부족 데이터 " + ", ".join(missing_names[:5]))
    return lines
