from typing import Dict, List, Optional

from .ontology_prompt_registry import DEFAULT_PROMPT_TEMPLATES
from .ontology_relation_contracts import (
    AI_PROMPT_REGISTRY_VERSION,
    DEFAULT_RELATION_THRESHOLDS,
    OntologyPromptTemplate,
    RelationRuleDefinition,
)
from .ontology_rule_catalog import DEFAULT_RELATION_RULES
from .parsing import parse_assignments


def parse_relation_rule_definitions_text(text: str) -> List[RelationRuleDefinition]:
    defaults = {item.rule_id: item for item in DEFAULT_RELATION_RULES}
    definitions: List[RelationRuleDefinition] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        rule_id = parts[0] if parts else ""
        if not rule_id:
            continue
        default = defaults.get(rule_id)
        label = parts[1] if len(parts) > 1 and parts[1] else (default.label if default else rule_id)
        condition = parts[2] if len(parts) > 2 and parts[2] else (default.condition_summary if default else "")
        relation_type = parts[3] if len(parts) > 3 and parts[3] else (default.relation_type if default else "CUSTOM_RELATION")
        signal_type = parts[4] if len(parts) > 4 and parts[4] else (default.signal_type if default else "custom")
        prompt_hint = " | ".join(parts[5:]).strip() if len(parts) > 5 else (default.prompt_hint if default else "")
        definitions.append(RelationRuleDefinition(
            rule_id=rule_id,
            label=label,
            version=default.version if default else "custom",
            relation_type=relation_type,
            signal_type=signal_type,
            condition_summary=condition,
            prompt_hint=prompt_hint,
            required_fields=list(default.required_fields if default else []),
        ))
    return definitions or list(DEFAULT_RELATION_RULES)


def relation_rule_definitions_from_settings(settings: Optional[Dict[str, object]] = None) -> List[RelationRuleDefinition]:
    settings = settings or {}
    text = str(settings.get("ontologyRelationRules") or "").strip()
    return parse_relation_rule_definitions_text(text) if text else list(DEFAULT_RELATION_RULES)


def _thresholds(settings: Optional[Dict[str, object]]) -> Dict[str, float]:
    settings = settings or {}
    legacy = parse_assignments(str(settings.get("alertThresholds") or ""), DEFAULT_RELATION_THRESHOLDS)
    configured = str(settings.get("relationRuleThresholds") or "").strip()
    if not configured:
        return legacy
    return parse_assignments(configured, legacy)


def relation_thresholds_from_settings(settings: Optional[Dict[str, object]] = None) -> Dict[str, float]:
    return _thresholds(settings)


def parse_ai_prompt_templates_text(text: str) -> List[OntologyPromptTemplate]:
    defaults = {item.prompt_id: item for item in DEFAULT_PROMPT_TEMPLATES}
    templates: List[OntologyPromptTemplate] = []
    current: Dict[str, str] = {}

    def flush_current() -> None:
        prompt_id = str(current.get("prompt_id") or "").strip()
        if not prompt_id:
            return
        default = defaults.get(prompt_id, DEFAULT_PROMPT_TEMPLATES[0])
        guardrails_text = str(current.get("guardrails") or "").strip()
        guardrails = [
            item.strip()
            for item in guardrails_text.replace("\n", " / ").split(" / ")
            if item.strip()
        ] or list(default.guardrails)
        templates.append(OntologyPromptTemplate(
            prompt_id=prompt_id,
            label=str(current.get("label") or default.label or prompt_id).strip(),
            version=str(current.get("version") or default.version or AI_PROMPT_REGISTRY_VERSION).strip(),
            purpose=str(current.get("purpose") or default.purpose or "").strip(),
            system_prompt=str(current.get("system") or default.system_prompt or "").strip(),
            user_prompt=str(current.get("user") or default.user_prompt or "").strip(),
            output_schema=dict(default.output_schema or {}),
            guardrails=guardrails,
        ))

    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            flush_current()
            current = {"prompt_id": line[1:-1].strip()}
            continue
        if "=" not in line or not current:
            continue
        key, value = line.split("=", 1)
        current[key.strip()] = value.strip()
    flush_current()
    return templates or list(DEFAULT_PROMPT_TEMPLATES)


def prompt_templates_from_settings(settings: Optional[Dict[str, object]] = None) -> List[OntologyPromptTemplate]:
    settings = settings or {}
    text = str(settings.get("aiPromptTemplates") or "").strip()
    if not text:
        return list(DEFAULT_PROMPT_TEMPLATES)
    configured = parse_ai_prompt_templates_text(text)
    merged: Dict[str, OntologyPromptTemplate] = {item.prompt_id: item for item in DEFAULT_PROMPT_TEMPLATES}
    extra_order: List[str] = []
    for item in configured:
        if item.prompt_id not in merged:
            extra_order.append(item.prompt_id)
        merged[item.prompt_id] = item
    return [merged[item.prompt_id] for item in DEFAULT_PROMPT_TEMPLATES if item.prompt_id in merged] + [merged[key] for key in extra_order]


def prompt_template(prompt_id: str, settings: Optional[Dict[str, object]] = None) -> OntologyPromptTemplate:
    templates = prompt_templates_from_settings(settings)
    requested = str(prompt_id or "").strip()
    for item in templates:
        if item.prompt_id == requested:
            return item
    for item in templates:
        if item.prompt_id == "default":
            return item
    return templates[0]


def prompt_template_for_message_type(message_type: str, settings: Optional[Dict[str, object]] = None) -> OntologyPromptTemplate:
    return prompt_template(str(message_type or "").strip() or "default", settings)
