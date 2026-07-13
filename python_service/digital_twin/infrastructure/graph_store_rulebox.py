import json
import re
from typing import Dict, Iterable, List

from ..domain.ontology_contracts import PortfolioOntology
from ..domain.ontology_decision_policy import decision_stage_from_action, relation_stage_priority
from ..domain.ontology_rulebox_catalog import default_graph_inference_rules
from ..domain.ontology_rulebox_contracts import GRAPH_REASONER_VERSION, GraphInferenceRule
from ..domain.ontology_rulebox_governance import (
    normalize_rule_change_candidate,
    rulebox_governance_candidates,
    rulebox_version_payload,
)
from ..domain.ontology_rulebox_projection import add_rulebox_concepts
from ..domain.ontology_schema import default_tbox_metadata, normalize_tbox_metadata, tbox_entities, tbox_relations
from .graph_store_payloads import (
    bool_or_none,
    condition_relation_filter_bool,
    condition_relation_filter_number,
    condition_relation_filter_values,
    condition_target_filter_bool,
    condition_target_filter_number,
    condition_target_filter_values,
    condition_target_level_types,
    derivation_decision_stage,
    derivation_stage_priority,
    group_relation_rows,
    list_of_strings,
    number_or_none,
    safe_relation_type,
)


def rulebox_rules_to_payload(rules: Iterable[GraphInferenceRule]) -> List[Dict[str, object]]:
    return [rule.to_dict() for rule in rules]

def rulebox_rules_from_payload(payload: Dict[str, object]) -> List[GraphInferenceRule]:
    payload = payload or {}
    raw_rules = payload.get("rules")
    if payload.get("rulesJson"):
        raw_rules = json.loads(str(payload.get("rulesJson") or "[]"))
    if raw_rules is None:
        if payload.get("useBootstrapDefaults") or payload.get("resetToDefaults"):
            raw_rules = rulebox_rules_to_payload(default_graph_inference_rules())
        else:
            raise ValueError("RuleBox rules are required. Use seed_ontology or useBootstrapDefaults to write bootstrap defaults.")
    if not isinstance(raw_rules, list):
        raise ValueError("RuleBox rules must be a list.")
    rules = [GraphInferenceRule.from_dict(item) for item in raw_rules if isinstance(item, dict)]
    if not rules:
        raise ValueError("RuleBox rules are empty.")
    return rules

def rulebox_graph_from_rules(rules: Iterable[GraphInferenceRule]) -> PortfolioOntology:
    graph = PortfolioOntology("typedb-rulebox-admin")
    graph.entities.extend(tbox_entities())
    graph.relations.extend(tbox_relations())
    add_rulebox_concepts(graph, rules)
    tbox = default_tbox_metadata()
    for item in graph.entities:
        if str((item.properties or {}).get("ontologyBox") or "") == "RuleBox":
            item.properties["tboxVersion"] = tbox["version"]
            item.properties["activeTboxVersion"] = tbox["version"]
            item.properties["tboxFingerprint"] = tbox["fingerprint"]
    for item in graph.relations:
        if str((item.properties or {}).get("ontologyBox") or "") == "RuleBox":
            item.properties["tboxVersion"] = tbox["version"]
            item.properties["activeTboxVersion"] = tbox["version"]
            item.properties["tboxFingerprint"] = tbox["fingerprint"]
    graph.worldview = {
        "model": "typedb-rulebox-source-of-truth",
        "engineVersion": GRAPH_REASONER_VERSION,
        "adminEditable": True,
        "activeTBox": tbox,
    }
    return graph

def rulebox_store_snapshot_unavailable(status: str, reason: str = "", source: str = "typedb") -> Dict[str, object]:
    bootstrap_rules = rulebox_rules_to_payload(default_graph_inference_rules())
    return {
        "configured": True,
        "saved": False,
        "status": status,
        "source": source,
        "reason": reason,
        "engineVersion": GRAPH_REASONER_VERSION,
        "rules": [],
        "ruleCount": 0,
        "conditionCount": 0,
        "derivationCount": 0,
        "relationTypes": [],
        "defaultsFallbackUsed": False,
        "bootstrapAvailable": True,
        "bootstrapRuleCount": len(bootstrap_rules),
        "bootstrapRules": bootstrap_rules,
        "versions": [],
        "versionCount": 0,
        "changeCandidates": rulebox_governance_candidates([], []),
    }

def rulebox_snapshot_from_rows(rowsets: Dict[str, List[Dict[str, object]]], source: str) -> Dict[str, object]:
    rules = build_rulebox_rules_from_rows(
        rowsets.get("rules") or [],
        rowsets.get("conditions") or [],
        rowsets.get("derivations") or [],
    )
    if not rules:
        return rulebox_store_snapshot_unavailable(
            "empty",
            "TypeDB RuleBox rows are empty. Seed or save RuleBox rules before running graph reasoning.",
            source=source,
        )
    relation_types = sorted(set(
        safe_relation_type(row.get("relationType") or "")
        for row in (rowsets.get("relationTypes") or [])
        if row.get("relationType")
    ))
    payload = rulebox_rules_to_payload(rules)
    versions = [rulebox_version_from_row(row) for row in (rowsets.get("versions") or [])]
    candidates = [rule_change_candidate_from_row(row) for row in (rowsets.get("candidates") or [])]
    return {
        "configured": True,
        "saved": True,
        "status": "ok",
        "source": source,
        "engineVersion": GRAPH_REASONER_VERSION,
        "rules": payload,
        "ruleCount": len(payload),
        "conditionCount": sum(len(item.get("conditions") or []) for item in payload),
        "derivationCount": sum(len(item.get("derivations") or []) for item in payload),
        "relationTypes": relation_types,
        "defaultsFallbackUsed": False,
        "versions": versions,
        "versionCount": len(versions),
        "changeCandidates": rulebox_governance_candidates(payload, versions, candidates),
    }

def rulebox_version_from_row(row: Dict[str, object]) -> Dict[str, object]:
    return {
        "id": str(row.get("id") or ""),
        "label": str(row.get("label") or ""),
        "versionLabel": str(row.get("versionLabel") or row.get("shortHash") or ""),
        "rulesHash": str(row.get("rulesHash") or ""),
        "shortHash": str(row.get("shortHash") or ""),
        "ruleCount": int(number_or_none(row.get("ruleCount")) or 0),
        "conditionCount": int(number_or_none(row.get("conditionCount")) or 0),
        "derivationCount": int(number_or_none(row.get("derivationCount")) or 0),
        "status": str(row.get("status") or ""),
        "changeReason": str(row.get("changeReason") or ""),
        "author": str(row.get("author") or ""),
        "engineVersion": str(row.get("engineVersion") or ""),
        "createdAt": str(row.get("createdAt") or ""),
    }

def rule_change_candidate_from_row(row: Dict[str, object]) -> Dict[str, object]:
    proposed = json_object(row.get("proposedRuleJson"))
    return {
        "id": str(row.get("id") or ""),
        "title": str(row.get("title") or row.get("label") or ""),
        "status": str(row.get("status") or ""),
        "priority": int(number_or_none(row.get("priority")) or 0),
        "source": str(row.get("source") or ""),
        "rationale": str(row.get("rationale") or ""),
        "expectedEffect": str(row.get("expectedEffect") or ""),
        "risk": str(row.get("risk") or ""),
        "action": str(row.get("action") or ""),
        "requiresData": list_of_strings(row.get("requiresData")),
        "proposedRule": proposed if proposed else None,
        "validationWarnings": list_of_strings(row.get("validationWarnings")),
        "promptVersion": str(row.get("promptVersion") or ""),
        "createdAt": str(row.get("createdAt") or ""),
        "updatedAt": str(row.get("updatedAt") or ""),
        "symbols": list_of_strings(row.get("symbols")),
    }

def build_rulebox_rules_from_rows(
    rule_rows: List[Dict[str, object]],
    condition_rows: List[Dict[str, object]],
    derivation_rows: List[Dict[str, object]],
) -> List[GraphInferenceRule]:
    conditions_by_rule: Dict[str, List[Dict[str, object]]] = {}
    derivations_by_rule: Dict[str, List[Dict[str, object]]] = {}
    for row in condition_rows:
        rule_id = str(row.get("ruleId") or "")
        if rule_id:
            conditions_by_rule.setdefault(rule_id, []).append(condition_payload_from_row(row))
    for row in derivation_rows:
        rule_id = str(row.get("ruleId") or "")
        if rule_id:
            derivations_by_rule.setdefault(rule_id, []).append(derivation_payload_from_row(row))
    rules = []
    for row in rule_rows:
        props = json_object(row.get("propertiesJson"))
        rule_id = str(row.get("ruleId") or props.get("ruleId") or "")
        if not rule_id:
            continue
        payload = {
            "rule_id": rule_id,
            "label": str(row.get("label") or props.get("label") or rule_id),
            "version": str(row.get("version") or props.get("version") or GRAPH_REASONER_VERSION),
            "source_kind": str(row.get("sourceKind") or props.get("sourceKind") or "typedb"),
            "conditions": conditions_by_rule.get(rule_id) or [],
            "derivations": derivations_by_rule.get(rule_id) or [],
            "action_group": str(row.get("actionGroup") or props.get("actionGroup") or ""),
            "action_level": str(row.get("actionLevel") or props.get("actionLevel") or ""),
            "prompt_hint": str(row.get("promptHint") or props.get("promptHint") or ""),
            "any_condition_min_count": int(row.get("anyConditionMinCount") or props.get("anyConditionMinCount") or 1),
            "enabled": bool(row.get("enabled")) if row.get("enabled") is not None else bool(props.get("enabled", True)),
        }
        try:
            rules.append(GraphInferenceRule.from_dict(payload))
        except ValueError:
            continue
    return rules

def condition_payload_from_row(row: Dict[str, object]) -> Dict[str, object]:
    props = json_object(row.get("propertiesJson"))
    condition = props.get("condition") if isinstance(props.get("condition"), dict) else {}
    target_level_types = row.get("targetLevelTypes")
    if not isinstance(target_level_types, list):
        target_level_types = []
    target_filters = condition.get("target_property_filters") if isinstance(condition.get("target_property_filters"), dict) else {}
    if not target_filters:
        target_filters = {}
        if target_level_types:
            target_filters["levelType"] = target_level_types
        for row_key, filter_key in [
            ("targetFields", "field"),
            ("targetTboxClasses", "tboxClasses"),
            ("targetGroups", "group"),
            ("targetScopes", "scope"),
            ("targetDataScopes", "dataScope"),
            ("targetDomainScopes", "domainScope"),
            ("targetRelationScopes", "relationScope"),
            ("targetEventTypes", "eventType"),
            ("targetPolarities", "polarity"),
        ]:
            values = row.get(row_key) if isinstance(row.get(row_key), list) else []
            if values:
                target_filters[filter_key] = values
        if row.get("targetMaterialityPassed") is not None:
            target_filters["materialityPassed"] = bool(row.get("targetMaterialityPassed"))
        for row_key, filter_key in [
            ("targetMinMaterialityScore", "minMaterialityScore"),
            ("targetMinValue", "minValue"),
            ("targetMaxValue", "maxValue"),
        ]:
            if row.get(row_key) is not None:
                target_filters[filter_key] = row.get(row_key)
    relation_filters = condition.get("relation_property_filters") if isinstance(condition.get("relation_property_filters"), dict) else {}
    if not relation_filters:
        relation_filters = {}
        polarities = row.get("relationPolarities") if isinstance(row.get("relationPolarities"), list) else []
        transition_types = row.get("relationTransitionTypes") if isinstance(row.get("relationTransitionTypes"), list) else []
        fields = row.get("relationFields") if isinstance(row.get("relationFields"), list) else []
        signal_groups = row.get("relationSignalGroups") if isinstance(row.get("relationSignalGroups"), list) else []
        if polarities:
            relation_filters["polarity"] = polarities
        if transition_types:
            relation_filters["transitionType"] = transition_types
        if fields:
            relation_filters["field"] = fields
        if signal_groups:
            relation_filters["signalGroup"] = signal_groups
        if row.get("relationMaterialityPassed") is not None:
            relation_filters["materialityPassed"] = bool(row.get("relationMaterialityPassed"))
        for row_key, filter_key in [
            ("relationMinRiskImpact", "minRiskImpact"),
            ("relationMinSupportImpact", "minSupportImpact"),
        ]:
            if row.get(row_key) is not None:
                relation_filters[filter_key] = row.get(row_key)
    return {
        "condition_id": str(row.get("conditionId") or condition.get("condition_id") or ""),
        "kind": str(row.get("kind") or condition.get("kind") or ""),
        "description": str(row.get("description") or condition.get("description") or ""),
        "field": str(row.get("field") or condition.get("field") or ""),
        "operator": str(row.get("operator") or condition.get("operator") or "=="),
        "role": str(row.get("role") or condition.get("role") or "required"),
        "value": condition.get("value") if "value" in condition else (row.get("valueNumber") if row.get("valueNumber") is not None else row.get("valueString")),
        "relation_type": str(row.get("relationType") or condition.get("relation_type") or ""),
        "direction": str(row.get("direction") or condition.get("direction") or "out"),
        "target_kind": str(row.get("targetKind") or condition.get("target_kind") or ""),
        "target_property_filters": target_filters,
        "relation_property_filters": relation_filters,
        "min_weight": float(row.get("minWeight") or condition.get("min_weight") or 0),
    }

def derivation_payload_from_row(row: Dict[str, object]) -> Dict[str, object]:
    props = json_object(row.get("propertiesJson"))
    derivation = props.get("derivation") if isinstance(props.get("derivation"), dict) else {}
    payload = {
        "relation_type": str(row.get("relationType") or derivation.get("relation_type") or ""),
        "target_kind": str(row.get("targetKind") or derivation.get("target_kind") or ""),
        "target_key": str(row.get("targetKey") or derivation.get("target_key") or ""),
        "target_label": str(row.get("targetLabel") or derivation.get("target_label") or row.get("label") or ""),
        "tbox_class": str(row.get("tboxClass") or derivation.get("tbox_class") or ""),
        "tbox_classes": list_of_strings(row.get("tboxClasses") or derivation.get("tbox_classes") or []),
        "polarity": str(row.get("polarity") or derivation.get("polarity") or "context"),
        "risk_impact": float(row.get("riskImpact") or derivation.get("risk_impact") or 0),
        "support_impact": float(row.get("supportImpact") or derivation.get("support_impact") or 0),
        "weight": float(row.get("weight") or derivation.get("weight") or 0.72),
        "belief_label": str(row.get("beliefLabel") or derivation.get("belief_label") or ""),
        "ai_influence_label": str(row.get("aiInfluenceLabel") or derivation.get("ai_influence_label") or ""),
        "action_group": str(row.get("actionGroup") or derivation.get("action_group") or ""),
        "action_level": str(row.get("actionLevel") or derivation.get("action_level") or ""),
        "decision_stage": str(row.get("decisionStage") or row.get("derivationDecisionStage") or derivation.get("decision_stage") or derivation.get("decisionStage") or ""),
        "stage_priority": float(row.get("stagePriority") or row.get("derivationStagePriority") or derivation.get("stage_priority") or derivation.get("stagePriority") or 0),
    }
    if not payload["decision_stage"]:
        payload["decision_stage"] = decision_stage_from_action(payload["action_group"], payload["action_level"])
    if not payload["stage_priority"]:
        payload["stage_priority"] = float(relation_stage_priority({
            "decisionStage": payload["decision_stage"],
            "actionGroup": payload["action_group"],
            "actionLevel": payload["action_level"],
            "riskImpact": payload["risk_impact"],
            "supportImpact": payload["support_impact"],
        }))
    return payload

def json_object(value: object) -> Dict[str, object]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}
