"""Profiles for TypeQL, without database execution."""

from typing import Dict, Iterable, List

from digital_twin.modules.model_registry.contracts import is_model_signal_interpretation_rule, model_signal_bridge_source_scope
from digital_twin.modules.reasoning.infrastructure.typeql.constants import (
    TYPEDB_FUNCTION_OPERATORS,
    TYPEDB_FUNCTION_RELATION_FILTERS,
    TYPEDB_FUNCTION_SUBJECT_FIELDS,
    TYPEDB_FUNCTION_TARGET_FILTERS,
    TYPEDB_NATIVE_REASONING_LAYER,
    TYPEDB_NATIVE_REASONING_PROFILE_VERSION,
    TYPEDB_NATIVE_RULE_ENGINE_VERSION,
)
from digital_twin.modules.reasoning.infrastructure.typeql.literals import typedb_string
from digital_twin.modules.reasoning.infrastructure.typeql.match_queries import typedb_native_match_query
from digital_twin.modules.reasoning.infrastructure.typeql.rule_shape import (
    typedb_native_rule_id,
    typedb_rule_is_enabled,
)


def typedb_native_reasoning_profile(rules: Iterable[object]) -> Dict[str, object]:
    rule_payloads = [
        item.to_dict() if hasattr(item, "to_dict") else dict(item)
        for item in (rules or [])
        if (isinstance(item, dict) or hasattr(item, "to_dict"))
        and typedb_rule_is_enabled(item)
    ]
    rule_profiles = [typedb_native_rule_profile(rule) for rule in rule_payloads]
    ready = [item for item in rule_profiles if item.get("status") == "ready"]
    partial = [item for item in rule_profiles if item.get("status") == "partial"]
    blocked = [item for item in rule_profiles if item.get("status") == "blocked"]
    unsupported = sum(int(item.get("unsupportedConditionCount") or 0) for item in rule_profiles)
    supported = sum(int(item.get("supportedConditionCount") or 0) for item in rule_profiles)
    shared_model_signal_policy_count = sum(
        bool(item.get("sharedModelSignalBridge")) for item in ready
    )
    shared_model_signal_scopes = {
        str(item.get("bridgeSourceScope") or "").strip()
        for item in ready
        if item.get("sharedModelSignalBridge")
        and str(item.get("bridgeSourceScope") or "").strip()
    }
    status = "ready" if rule_profiles and len(ready) == len(rule_profiles) else ("partial" if ready or partial else "blocked")
    blockers = [
        blocker
        for item in rule_profiles
        for blocker in (item.get("blockers") or [])
        if isinstance(blocker, dict)
    ]
    return {
        "version": TYPEDB_NATIVE_REASONING_PROFILE_VERSION,
        "graphStore": "typedb",
        "reasoningModel": "typedb-native-rule-materialization",
        "engineVersion": TYPEDB_NATIVE_RULE_ENGINE_VERSION,
        "status": status,
        "ruleCount": len(rule_profiles),
        "nativeRuleCount": len(rule_profiles),
        "readyRuleCount": len(ready),
        "directTypeqlRuleCount": len(ready),
        "sharedModelSignalPolicyCount": shared_model_signal_policy_count,
        "sharedModelSignalBridgeCount": len(shared_model_signal_scopes),
        "partialRuleCount": len(partial),
        "blockedRuleCount": len(blocked),
        "supportedConditionCount": supported,
        "unsupportedConditionCount": unsupported,
        "materializationRequired": True,
        "materializationTarget": "InferenceBox",
        "materializationStrategy": "typedb-abox-native-rule-to-inferencebox",
        "reason": "TypeDB ABox facts are evaluated by direct TypeQL rules and materialized into the TypeDB InferenceBox.",
        "readyRules": [item.get("ruleId") for item in ready][:24],
        "readyNativeRules": [item.get("nativeRuleId") for item in ready][:24],
        "partialRules": [item.get("ruleId") for item in partial][:24],
        "partialNativeRules": [item.get("nativeRuleId") for item in partial][:24],
        "blockedRules": [item.get("ruleId") for item in blocked][:24],
        "blockedNativeRules": [item.get("nativeRuleId") for item in blocked][:24],
        "blockers": blockers[:24],
        "rules": rule_profiles[:80],
    }


def typedb_native_rule_profile(rule: Dict[str, object]) -> Dict[str, object]:
    conditions = [item for item in (rule.get("conditions") or []) if isinstance(item, dict)]
    derivations = [item for item in (rule.get("derivations") or []) if isinstance(item, dict)]
    condition_profiles = [typedb_native_condition_profile(item) for item in conditions]
    blockers = [
        blocker
        for item in condition_profiles
        for blocker in (item.get("blockers") or [])
        if isinstance(blocker, dict)
    ]
    supported = [item for item in condition_profiles if item.get("status") == "ready"]
    partial = [item for item in condition_profiles if item.get("status") == "partial"]
    if blockers and not supported and not partial:
        status = "blocked"
    elif blockers:
        status = "partial"
    else:
        status = "ready"
    source_rule_id = str(rule.get("rule_id") or rule.get("ruleId") or "")
    native_rule_id = typedb_native_rule_id(source_rule_id)
    direct_query = (
        typedb_native_match_query(
            rule,
            [],
            scoped_manifest_only=True,
            include_any_conditions=True,
            compact_result_rows=True,
        )
        if status in {"ready", "partial"}
        else {}
    )
    return {
        "ruleId": source_rule_id,
        "sourceRuleId": source_rule_id,
        "nativeRuleId": native_rule_id,
        "executionStrategy": "direct-typeql",
        "sharedModelSignalBridge": is_model_signal_interpretation_rule(rule),
        "bridgeSourceScope": (
            model_signal_bridge_source_scope(rule)
            if is_model_signal_interpretation_rule(rule)
            else ""
        ),
        "label": str(rule.get("label") or ""),
        "status": status,
        "conditionCount": len(conditions),
        "derivationCount": len(derivations),
        "supportedConditionCount": len(supported),
        "unsupportedConditionCount": len(blockers),
        "blockers": blockers,
        "reasoningLayer": TYPEDB_NATIVE_REASONING_LAYER,
        "conditions": condition_profiles,
        "typeqlRuleBlueprint": str(direct_query.get("query") or ""),
    }


def typedb_native_condition_profile(condition: Dict[str, object]) -> Dict[str, object]:
    condition_id = str(condition.get("condition_id") or condition.get("conditionId") or "")
    kind = str(condition.get("kind") or "")
    blockers: List[Dict[str, object]] = []
    operator = str(condition.get("operator") or "==")
    if operator not in TYPEDB_FUNCTION_OPERATORS:
        blockers.append(condition_blocker(condition_id, "unsupported-operator", "TypeDB function profile does not support operator " + operator))
    if kind == "subject_property":
        field = str(condition.get("field") or "")
        if field not in TYPEDB_FUNCTION_SUBJECT_FIELDS:
            blockers.append(condition_blocker(condition_id, "json-bound-subject-field", field + " is still JSON-bound or not promoted to a TypeDB attribute."))
    elif kind == "relation":
        if not str(condition.get("relation_type") or condition.get("relationType") or ""):
            blockers.append(condition_blocker(condition_id, "missing-relation-type", "Relation condition needs an explicit relation_type."))
        blockers.extend(filter_blockers(condition_id, condition.get("target_property_filters") or condition.get("targetPropertyFilters") or {}, TYPEDB_FUNCTION_TARGET_FILTERS, "target"))
        blockers.extend(filter_blockers(condition_id, condition.get("relation_property_filters") or condition.get("relationPropertyFilters") or {}, TYPEDB_FUNCTION_RELATION_FILTERS, "relation"))
    else:
        blockers.append(condition_blocker(condition_id, "unsupported-condition-kind", kind + " is not mapped to a TypeDB function pattern."))
    return {
        "conditionId": condition_id,
        "kind": kind,
        "status": "ready" if not blockers else "partial",
        "blockers": blockers,
    }


def filter_blockers(condition_id: str, filters: Dict[str, object], supported: set, scope: str) -> List[Dict[str, object]]:
    blockers = []
    for key in sorted((filters or {}).keys()):
        if key not in supported:
            blockers.append(condition_blocker(condition_id, "json-bound-" + scope + "-filter", key + " is not promoted to a TypeDB attribute."))
    return blockers


def condition_blocker(condition_id: str, code: str, reason: str) -> Dict[str, object]:
    return {
        "conditionId": condition_id,
        "code": code,
        "reason": reason,
    }


def typedb_function_blueprint(rule: Dict[str, object]) -> str:
    rule_id = str(rule.get("rule_id") or rule.get("ruleId") or "rule").replace(".", "_").replace("-", "_")
    source_kind = str(rule.get("source_kind") or rule.get("sourceKind") or "stock")
    return (
        "fun orbit_inference_" + rule_id + "() -> { ontology-node, ontology-node }:\n"
        "  match\n"
        "    $source isa ontology-node, has ontology-kind " + typedb_string(source_kind) + ";\n"
        "    # Semantic rule conditions are represented by promoted ontology-* attributes where available.\n"
        "  return { $source, $source };"
    )
