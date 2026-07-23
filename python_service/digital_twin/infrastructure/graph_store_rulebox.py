import json
import re
from typing import Dict, Iterable, List

from ..domain.ontology_contracts import OntologyEntity, OntologyRelation, PortfolioOntology, entity_id
from ..domain.investment_ubiquitous_language import add_investment_language_governance_concepts
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
    condition_relation_filter_bool,
    condition_relation_filter_number,
    condition_relation_filter_values,
    condition_target_filter_bool,
    condition_target_filter_number,
    condition_target_filter_values,
    condition_target_level_types,
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
    missing_policy = [
        rule.rule_id
        for rule in rules
        if any(not str(derivation.decision_stage or "").strip() for derivation in rule.derivations)
    ]
    if missing_policy:
        raise ValueError("Every TypeDB rule derivation requires decision_stage: " + ", ".join(missing_policy[:10]))
    return rules

def rulebox_graph_from_rules(
    rules: Iterable[GraphInferenceRule],
    include_tbox: bool = True,
    language_registry: Dict[str, object] = None,
    rulebox_version: Dict[str, object] = None,
) -> PortfolioOntology:
    graph = PortfolioOntology("typedb-rulebox-admin")
    if include_tbox:
        graph.entities.extend(tbox_entities())
        graph.relations.extend(tbox_relations())
    add_rulebox_concepts(graph, rules)
    if isinstance(rulebox_version, dict) and str(rulebox_version.get("id") or "").strip():
        add_rulebox_version_concept(graph, rulebox_version)
    if language_registry is not None:
        add_investment_language_governance_concepts(graph, language_registry)
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
        "languageRegistryVersion": str((language_registry or {}).get("version") or ""),
    }
    return graph


def add_rulebox_version_concept(graph: PortfolioOntology, version: Dict[str, object]) -> None:
    """Persist an immutable RuleBox payload for audit and restoration."""

    version_key = str(version.get("id") or "").strip()
    if not version_key:
        return
    registry_id = entity_id("rule-registry", GRAPH_REASONER_VERSION)
    version_id = entity_id("rulebox-version", version_key)
    graph.entities.append(OntologyEntity(
        version_id,
        str(version.get("label") or version_key),
        "rulebox-version",
        {
            "ontologyBox": "RuleBoxGovernance",
            "boundedContext": "reasoning-insight",
            "tboxClass": "RuleBoxVersion",
            "versionId": version_key,
            "versionLabel": version.get("versionLabel"),
            "rulesHash": version.get("rulesHash"),
            "shortHash": version.get("shortHash"),
            "ruleCount": version.get("ruleCount"),
            "conditionCount": version.get("conditionCount"),
            "derivationCount": version.get("derivationCount"),
            "createdAt": version.get("createdAt"),
            "changeReason": version.get("changeReason"),
            "author": version.get("author"),
            "status": version.get("status") or "saved",
            "engineVersion": version.get("engineVersion") or GRAPH_REASONER_VERSION,
            "rulesJson": version.get("rulesJson") or "[]",
        },
    ))
    graph.relations.append(OntologyRelation(
        registry_id,
        version_id,
        "HAS_RULEBOX_VERSION",
        weight=1.0,
        properties={
            "ontologyBox": "RuleBoxGovernance",
            "boundedContext": "reasoning-insight",
            "versionId": version_key,
            "source": "rulebox-governance",
        },
    ))

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
    versions = sorted(
        [rulebox_version_from_row(row) for row in (rowsets.get("versions") or [])],
        key=lambda item: str(item.get("createdAt") or ""),
        reverse=True,
    )
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
        "id": str(row.get("versionId") or row.get("id") or ""),
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
        "rulesJson": str(row.get("rulesJson") or ""),
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
    for row in sorted(condition_rows, key=rule_component_sort_key("conditionIndex", "conditionId")):
        rule_id = str(row.get("ruleId") or "")
        if rule_id:
            conditions_by_rule.setdefault(rule_id, []).append(condition_payload_from_row(row))
    for row in sorted(derivation_rows, key=rule_component_sort_key("derivationIndex", "derivationTargetKey")):
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
            "hypothesis_family_key": str(
                row.get("hypothesisFamilyKey")
                or props.get("hypothesisFamilyKey")
                or props.get("hypothesis_family_key")
                or ""
            ),
            "hypothesis_lifecycle": (
                props.get("hypothesisLifecycle")
                or props.get("hypothesis_lifecycle")
                or {}
            ),
            "any_condition_min_count": int(row.get("anyConditionMinCount") or props.get("anyConditionMinCount") or 1),
            "enabled": bool(row.get("enabled")) if row.get("enabled") is not None else bool(props.get("enabled", True)),
        }
        try:
            rules.append(GraphInferenceRule.from_dict(payload))
        except ValueError:
            continue
    return rules

def rule_component_sort_key(index_field: str, fallback_field: str):
    def sort_key(row: Dict[str, object]):
        index = number_or_none(row.get(index_field))
        if index is None:
            index = 999999
        return (str(row.get("ruleId") or ""), int(index), str(row.get(fallback_field) or row.get("id") or ""))

    return sort_key

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
        materiality_states = row.get("targetMaterialityStates") if isinstance(row.get("targetMaterialityStates"), list) else []
        if materiality_states:
            target_filters["materialityState"] = materiality_states
        for row_key, filter_key in [
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
        evidence_roles = row.get("relationEvidenceRoles") if isinstance(row.get("relationEvidenceRoles"), list) else []
        if evidence_roles:
            relation_filters["evidenceRole"] = evidence_roles
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
        "kind": str(condition.get("kind") or row.get("conditionKind") or row.get("kind") or ""),
        "description": str(condition.get("description") or row.get("description") or ""),
        "field": str(condition.get("field") or row.get("field") or ""),
        "operator": str(condition.get("operator") or row.get("operator") or "=="),
        "role": str(condition.get("role") or row.get("role") or "required"),
        "value": condition.get("value") if "value" in condition else (row.get("valueNumber") if row.get("valueNumber") is not None else row.get("valueString")),
        "relation_type": str(condition.get("relation_type") or row.get("relationType") or ""),
        "direction": str(condition.get("direction") or row.get("direction") or "out"),
        "target_kind": str(condition.get("target_kind") or row.get("targetKind") or ""),
        "target_property_filters": target_filters,
        "relation_property_filters": relation_filters,
    }

def derivation_payload_from_row(row: Dict[str, object]) -> Dict[str, object]:
    props = json_object(row.get("propertiesJson"))
    derivation = props.get("derivation") if isinstance(props.get("derivation"), dict) else {}
    payload = {
        "relation_type": str(row.get("relationType") or derivation.get("relation_type") or ""),
        "target_kind": str(row.get("targetKind") or derivation.get("target_kind") or ""),
        "target_key": str(row.get("targetKey") or derivation.get("target_key") or ""),
        "target_label": str(row.get("targetLabel") or derivation.get("target_label") or row.get("label") or ""),
        "tbox_class": str(row.get("derivationTboxClass") or derivation.get("tbox_class") or row.get("tboxClass") or ""),
        "tbox_classes": list_of_strings(row.get("derivationTboxClasses") or derivation.get("tbox_classes") or row.get("tboxClasses") or []),
        "polarity": str(row.get("polarity") or derivation.get("polarity") or "context"),
        # The entity-level role describes the RuleBox template itself and commonly
        # defaults to context. Preserve the role explicitly assigned to this
        # derivation so a support/risk relation survives a TypeDB round trip.
        "evidence_role": str(
            derivation.get("evidence_role")
            or derivation.get("evidenceRole")
            or row.get("derivationEvidenceRole")
            or derivation.get("polarity")
            or row.get("evidenceRole")
            or row.get("polarity")
            or "context"
        ),
        "belief_label": str(row.get("beliefLabel") or derivation.get("belief_label") or ""),
        "ai_influence_label": str(row.get("aiInfluenceLabel") or derivation.get("ai_influence_label") or ""),
        "action_group": str(row.get("actionGroup") or derivation.get("action_group") or ""),
        "action_level": str(row.get("actionLevel") or derivation.get("action_level") or ""),
        "decision_stage": str(row.get("decisionStage") or row.get("derivationDecisionStage") or derivation.get("decision_stage") or derivation.get("decisionStage") or ""),
        "decision_label": str(row.get("decisionLabel") or row.get("derivationDecisionLabel") or derivation.get("decision_label") or derivation.get("decisionLabel") or ""),
        "decision_tone": str(row.get("decisionTone") or row.get("derivationDecisionTone") or derivation.get("decision_tone") or derivation.get("decisionTone") or ""),
        "target_role": str(row.get("targetRole") or row.get("derivationTargetRole") or derivation.get("target_role") or derivation.get("targetRole") or ""),
        "action_policy": str(row.get("actionPolicy") or row.get("derivationActionPolicy") or derivation.get("action_policy") or derivation.get("actionPolicy") or ""),
        "allowed_actions": list_of_strings(row.get("allowedActions") or row.get("derivationAllowedActions") or derivation.get("allowed_actions") or derivation.get("allowedActions")),
        "blocked_actions": list_of_strings(row.get("blockedActions") or row.get("derivationBlockedActions") or derivation.get("blocked_actions") or derivation.get("blockedActions")),
    }
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
