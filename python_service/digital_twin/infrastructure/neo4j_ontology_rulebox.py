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
from .settings import utc_now
from .neo4j_ontology_payloads import (
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
    graph = PortfolioOntology("neo4j-rulebox-admin")
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
        "model": "neo4j-rulebox-source-of-truth",
        "engineVersion": GRAPH_REASONER_VERSION,
        "adminEditable": True,
        "activeTBox": tbox,
    }
    return graph

def rulebox_store_snapshot_unavailable(status: str, reason: str = "", source: str = "neo4j") -> Dict[str, object]:
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

def rulebox_snapshot_statements() -> List[Dict[str, object]]:
    return [
        {
            "statement": (
                "MATCH (rule:OntologyEntity {kind: 'rule', ontologyBox: 'RuleBox'}) "
                "RETURN rule.id AS id, rule.ruleId AS ruleId, rule.label AS label, rule.version AS version, "
                "rule.sourceKind AS sourceKind, rule.enabled AS enabled, rule.actionGroup AS actionGroup, "
                "rule.actionLevel AS actionLevel, rule.promptHint AS promptHint, rule.propertiesJson AS propertiesJson, "
                "rule.updatedAt AS updatedAt ORDER BY rule.ruleId"
            ),
            "parameters": {},
        },
        {
            "statement": (
                "MATCH (rule:OntologyEntity {kind: 'rule', ontologyBox: 'RuleBox'})-[:HAS_CONDITION]->"
                "(condition:OntologyEntity {kind: 'rule-condition', ontologyBox: 'RuleBox'}) "
                "RETURN rule.ruleId AS ruleId, condition.id AS id, condition.conditionId AS conditionId, "
                "condition.label AS description, condition.conditionKind AS kind, condition.conditionField AS field, "
                "condition.conditionOperator AS operator, condition.conditionValueString AS valueString, "
                "condition.conditionValueNumber AS valueNumber, condition.conditionRelationType AS relationType, "
                "condition.conditionDirection AS direction, condition.conditionTargetKind AS targetKind, "
                "condition.conditionTargetLevelTypes AS targetLevelTypes, condition.conditionRelationPolarities AS relationPolarities, "
                "condition.conditionRelationTransitionTypes AS relationTransitionTypes, condition.conditionMinWeight AS minWeight, "
                "condition.conditionTargetFields AS targetFields, condition.conditionTargetTboxClasses AS targetTboxClasses, "
                "condition.conditionTargetGroups AS targetGroups, condition.conditionTargetRelationScopes AS targetRelationScopes, "
                "condition.conditionTargetEventTypes AS targetEventTypes, condition.conditionTargetPolarities AS targetPolarities, "
                "condition.conditionTargetMaterialityPassed AS targetMaterialityPassed, "
                "condition.conditionTargetMinMaterialityScore AS targetMinMaterialityScore, "
                "condition.conditionTargetMinValue AS targetMinValue, condition.conditionTargetMaxValue AS targetMaxValue, "
                "condition.conditionRelationFields AS relationFields, condition.conditionRelationSignalGroups AS relationSignalGroups, "
                "condition.conditionRelationMaterialityPassed AS relationMaterialityPassed, "
                "condition.conditionRelationMinRiskImpact AS relationMinRiskImpact, "
                "condition.conditionRelationMinSupportImpact AS relationMinSupportImpact, "
                "condition.propertiesJson AS propertiesJson ORDER BY rule.ruleId, condition.conditionId"
            ),
            "parameters": {},
        },
        {
            "statement": (
                "MATCH (rule:OntologyEntity {kind: 'rule', ontologyBox: 'RuleBox'})-[:DERIVES_RELATION]->"
                "(template:OntologyEntity {kind: 'relation-template', ontologyBox: 'RuleBox'}) "
                "RETURN rule.ruleId AS ruleId, template.id AS id, template.label AS label, "
                "template.derivationIndex AS derivationIndex, template.derivationRelationType AS relationType, "
                "template.derivationTargetKind AS targetKind, template.derivationTargetKey AS targetKey, "
                "template.derivationTargetLabel AS targetLabel, template.derivationTboxClass AS tboxClass, "
                "template.derivationTboxClasses AS tboxClasses, template.derivationPolarity AS polarity, "
                "template.derivationRiskImpact AS riskImpact, template.derivationSupportImpact AS supportImpact, "
                "template.derivationWeight AS weight, template.derivationBeliefLabel AS beliefLabel, "
                "template.derivationAiInfluenceLabel AS aiInfluenceLabel, template.derivationActionGroup AS actionGroup, "
                "template.derivationActionLevel AS actionLevel, template.derivationDecisionStage AS decisionStage, "
                "template.derivationStagePriority AS stagePriority, template.propertiesJson AS propertiesJson "
                "ORDER BY rule.ruleId, template.derivationIndex"
            ),
            "parameters": {},
        },
        rulebox_relation_types_statement(),
        {
            "statement": (
                "MATCH (version:OntologyEntity {kind: 'rulebox-version', ontologyBox: 'RuleBoxGovernance'}) "
                "RETURN version.id AS id, version.label AS label, version.versionLabel AS versionLabel, "
                "version.rulesHash AS rulesHash, version.shortHash AS shortHash, version.ruleCount AS ruleCount, "
                "version.conditionCount AS conditionCount, version.derivationCount AS derivationCount, "
                "version.status AS status, version.changeReason AS changeReason, version.author AS author, "
                "version.engineVersion AS engineVersion, version.createdAt AS createdAt "
                "ORDER BY version.createdAt DESC LIMIT 12"
            ),
            "parameters": {},
        },
        {
            "statement": (
                "MATCH (candidate:OntologyEntity {kind: 'rule-change-candidate', ontologyBox: 'RuleBoxGovernance'}) "
                "RETURN candidate.id AS id, candidate.label AS label, candidate.title AS title, "
                "candidate.status AS status, candidate.priority AS priority, candidate.source AS source, "
                "candidate.rationale AS rationale, candidate.expectedEffect AS expectedEffect, candidate.risk AS risk, "
                "candidate.action AS action, candidate.requiresData AS requiresData, candidate.proposedRuleJson AS proposedRuleJson, "
                "candidate.validationWarnings AS validationWarnings, candidate.promptVersion AS promptVersion, "
                "candidate.createdAt AS createdAt, candidate.updatedAt AS updatedAt, candidate.symbols AS symbols "
                "ORDER BY coalesce(candidate.updatedAt, candidate.createdAt, '') DESC LIMIT 20"
            ),
            "parameters": {},
        },
    ]

def rulebox_version_statements(version: Dict[str, object]) -> List[Dict[str, object]]:
    return [
        {
            "statement": (
                "MERGE (registry:OntologyEntity {id: 'rulebox-governance:graph-reasoner'}) "
                "SET registry.label = 'RuleBox Governance', registry.kind = 'rulebox-governance', "
                "registry.ontologyBox = 'RuleBoxGovernance', registry.boundedContext = 'reasoning-insight', "
                "registry.tboxClass = 'RuleBoxGovernance', registry.engineVersion = $engineVersion, registry.updatedAt = $createdAt "
                "MERGE (version:OntologyEntity {id: $id}) "
                "SET version.label = $label, version.kind = 'rulebox-version', version.ontologyBox = 'RuleBoxGovernance', "
                "version.boundedContext = 'reasoning-insight', version.tboxClass = 'RuleBoxVersion', version.versionLabel = $versionLabel, "
                "version.rulesHash = $rulesHash, version.shortHash = $shortHash, version.ruleCount = $ruleCount, "
                "version.conditionCount = $conditionCount, version.derivationCount = $derivationCount, "
                "version.status = $status, version.changeReason = $changeReason, version.author = $author, "
                "version.engineVersion = $engineVersion, version.rulesJson = $rulesJson, "
                "version.clearInference = $clearInference, version.createdAt = $createdAt, version.updatedAt = $createdAt "
                "MERGE (registry)-[rel:HAS_RULEBOX_VERSION]->(version) "
                "SET rel.weight = 1.0, rel.ontologyBox = 'RuleBoxGovernance', rel.updatedAt = $createdAt"
            ),
            "parameters": {
                "id": str(version.get("id") or ""),
                "label": str(version.get("label") or ""),
                "versionLabel": str(version.get("versionLabel") or ""),
                "rulesHash": str(version.get("rulesHash") or ""),
                "shortHash": str(version.get("shortHash") or ""),
                "ruleCount": int(version.get("ruleCount") or 0),
                "conditionCount": int(version.get("conditionCount") or 0),
                "derivationCount": int(version.get("derivationCount") or 0),
                "status": str(version.get("status") or "saved"),
                "changeReason": str(version.get("changeReason") or ""),
                "author": str(version.get("author") or "local-admin"),
                "engineVersion": str(version.get("engineVersion") or GRAPH_REASONER_VERSION),
                "rulesJson": str(version.get("rulesJson") or "[]"),
                "clearInference": bool(version.get("clearInference")),
                "createdAt": str(version.get("createdAt") or utc_now()),
            },
        }
    ]

def rule_change_candidate_statements(candidates: List[Dict[str, object]], context: Dict[str, object] = None) -> List[Dict[str, object]]:
    updated_at = utc_now()
    rows = []
    symbols = [str(item or "").upper().strip() for item in ((context or {}).get("symbols") or []) if str(item or "").strip()]
    for candidate in candidates or []:
        proposed = candidate.get("proposedRule") if isinstance(candidate.get("proposedRule"), dict) else None
        rows.append({
            "id": str(candidate.get("id") or ""),
            "label": str(candidate.get("title") or candidate.get("id") or ""),
            "title": str(candidate.get("title") or ""),
            "status": str(candidate.get("status") or "candidate"),
            "priority": int(candidate.get("priority") or 0),
            "source": str(candidate.get("source") or "ai-rule-candidate"),
            "rationale": str(candidate.get("rationale") or ""),
            "expectedEffect": str(candidate.get("expectedEffect") or ""),
            "risk": str(candidate.get("risk") or ""),
            "action": str(candidate.get("action") or ""),
            "requiresData": [str(item) for item in (candidate.get("requiresData") or []) if str(item or "").strip()],
            "proposedRuleJson": json.dumps(proposed, ensure_ascii=False, sort_keys=True) if proposed else "",
            "validationWarnings": [str(item) for item in (candidate.get("validationWarnings") or []) if str(item or "").strip()],
            "promptVersion": str(candidate.get("promptVersion") or "rule-change-candidate-ai-v1"),
            "symbols": symbols,
            "updatedAt": updated_at,
        })
    return [
        {
            "statement": (
                "MERGE (registry:OntologyEntity {id: 'rulebox-governance:graph-reasoner'}) "
                "SET registry.label = 'RuleBox Governance', registry.kind = 'rulebox-governance', "
                "registry.ontologyBox = 'RuleBoxGovernance', registry.boundedContext = 'reasoning-insight', "
                "registry.tboxClass = 'RuleBoxGovernance', registry.updatedAt = $updatedAt "
                "WITH registry "
                "UNWIND $rows AS row "
                "MERGE (candidate:OntologyEntity {id: row.id}) "
                "ON CREATE SET candidate.createdAt = row.updatedAt "
                "SET candidate.label = row.label, candidate.title = row.title, candidate.kind = 'rule-change-candidate', "
                "candidate.ontologyBox = 'RuleBoxGovernance', candidate.boundedContext = 'reasoning-insight', "
                "candidate.tboxClass = 'RuleChangeCandidate', candidate.status = row.status, candidate.priority = row.priority, "
                "candidate.source = row.source, candidate.rationale = row.rationale, candidate.expectedEffect = row.expectedEffect, "
                "candidate.risk = row.risk, candidate.action = row.action, candidate.requiresData = row.requiresData, "
                "candidate.proposedRuleJson = row.proposedRuleJson, candidate.validationWarnings = row.validationWarnings, "
                "candidate.promptVersion = row.promptVersion, candidate.symbols = row.symbols, candidate.updatedAt = row.updatedAt "
                "MERGE (registry)-[rel:HAS_RULE_CHANGE_CANDIDATE]->(candidate) "
                "SET rel.weight = 1.0, rel.ontologyBox = 'RuleBoxGovernance', rel.updatedAt = row.updatedAt"
            ),
            "parameters": {"rows": rows, "updatedAt": updated_at},
        }
    ]

def rulebox_relation_types_statement() -> Dict[str, object]:
    return {
        "statement": (
            "MATCH (template:OntologyEntity {kind: 'relation-template', ontologyBox: 'RuleBox'}) "
            "RETURN DISTINCT template.derivationRelationType AS relationType ORDER BY relationType"
        ),
        "parameters": {},
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
            "Neo4j RuleBox nodes are empty. Seed or save RuleBox rules before running graph reasoning.",
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
            "source_kind": str(row.get("sourceKind") or props.get("sourceKind") or "neo4j"),
            "conditions": conditions_by_rule.get(rule_id) or [],
            "derivations": derivations_by_rule.get(rule_id) or [],
            "action_group": str(row.get("actionGroup") or props.get("actionGroup") or ""),
            "action_level": str(row.get("actionLevel") or props.get("actionLevel") or ""),
            "prompt_hint": str(row.get("promptHint") or props.get("promptHint") or ""),
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

def native_reasoning_statements_for_relation_types(relation_types: Iterable[str]) -> List[Dict[str, object]]:
    cleaned = sorted(set(safe_relation_type(relation_type) for relation_type in relation_types if safe_relation_type(relation_type)))
    return [native_reasoning_statement_for_relation_type(relation_type) for relation_type in cleaned]

def native_reasoning_statement_for_relation_type(relation_type: str) -> Dict[str, object]:
    safe_type = safe_relation_type(relation_type)
    statement = (
        "MATCH (rule:OntologyEntity {kind: 'rule', ontologyBox: 'RuleBox'}) "
        "WHERE coalesce(rule.enabled, false) = true "
        "MATCH (rule)-[:HAS_CONDITION]->(condition:OntologyEntity {kind: 'rule-condition', ontologyBox: 'RuleBox'}) "
        "WITH rule, collect(condition) AS conditions "
        "MATCH (rule)-[:DERIVES_RELATION]->(template:OntologyEntity {kind: 'relation-template', ontologyBox: 'RuleBox'}) "
        "WHERE template.derivationRelationType = $relationType "
        "MATCH (stock:OntologyEntity {kind: 'stock'}) "
        "WHERE stock.ontologyBox = 'ABox' "
        "AND coalesce(stock.isCurrent, false) = true "
        "AND coalesce(stock.aboxSnapshotId, '') <> '' "
        "AND all(condition IN conditions WHERE "
        "CASE condition.conditionKind "
        "WHEN 'subject_property' THEN "
        "CASE condition.conditionOperator "
        "WHEN '==' THEN toLower(toString(CASE condition.conditionField WHEN 'source' THEN stock.sourceValue ELSE stock[condition.conditionField] END)) = toLower(condition.conditionValueString) "
        "WHEN 'eq' THEN toLower(toString(CASE condition.conditionField WHEN 'source' THEN stock.sourceValue ELSE stock[condition.conditionField] END)) = toLower(condition.conditionValueString) "
        "WHEN '!=' THEN toLower(toString(CASE condition.conditionField WHEN 'source' THEN stock.sourceValue ELSE stock[condition.conditionField] END)) <> toLower(condition.conditionValueString) "
        "WHEN 'ne' THEN toLower(toString(CASE condition.conditionField WHEN 'source' THEN stock.sourceValue ELSE stock[condition.conditionField] END)) <> toLower(condition.conditionValueString) "
        "WHEN '<=' THEN coalesce(toFloat(CASE condition.conditionField WHEN 'source' THEN stock.sourceValue ELSE stock[condition.conditionField] END), 999999999.0) <= condition.conditionValueNumber "
        "WHEN 'lte' THEN coalesce(toFloat(CASE condition.conditionField WHEN 'source' THEN stock.sourceValue ELSE stock[condition.conditionField] END), 999999999.0) <= condition.conditionValueNumber "
        "WHEN '>=' THEN coalesce(toFloat(CASE condition.conditionField WHEN 'source' THEN stock.sourceValue ELSE stock[condition.conditionField] END), -999999999.0) >= condition.conditionValueNumber "
        "WHEN 'gte' THEN coalesce(toFloat(CASE condition.conditionField WHEN 'source' THEN stock.sourceValue ELSE stock[condition.conditionField] END), -999999999.0) >= condition.conditionValueNumber "
        "WHEN '<' THEN coalesce(toFloat(CASE condition.conditionField WHEN 'source' THEN stock.sourceValue ELSE stock[condition.conditionField] END), 999999999.0) < condition.conditionValueNumber "
        "WHEN 'lt' THEN coalesce(toFloat(CASE condition.conditionField WHEN 'source' THEN stock.sourceValue ELSE stock[condition.conditionField] END), 999999999.0) < condition.conditionValueNumber "
        "WHEN '>' THEN coalesce(toFloat(CASE condition.conditionField WHEN 'source' THEN stock.sourceValue ELSE stock[condition.conditionField] END), -999999999.0) > condition.conditionValueNumber "
        "WHEN 'gt' THEN coalesce(toFloat(CASE condition.conditionField WHEN 'source' THEN stock.sourceValue ELSE stock[condition.conditionField] END), -999999999.0) > condition.conditionValueNumber "
        "ELSE false END "
        "WHEN 'relation' THEN EXISTS { "
        "MATCH (stock)-[rel]->(target:OntologyEntity) "
        "WHERE type(rel) = condition.conditionRelationType "
        "AND coalesce(rel.isCurrent, false) = true "
        "AND (target.ontologyBox <> 'ABox' OR coalesce(target.isCurrent, false) = true) "
        "AND (target.ontologyBox <> 'ABox' OR target.aboxSnapshotId = stock.aboxSnapshotId) "
        "AND coalesce(rel.weight, 0.0) >= coalesce(condition.conditionMinWeight, 0.0) "
        "AND (condition.conditionTargetKind = '' OR target.kind = condition.conditionTargetKind) "
        "AND (size(coalesce(condition.conditionTargetLevelTypes, [])) = 0 OR target.levelType IN condition.conditionTargetLevelTypes) "
        "AND (size(coalesce(condition.conditionTargetFields, [])) = 0 OR target.field IN condition.conditionTargetFields) "
        "AND (size(coalesce(condition.conditionTargetTboxClasses, [])) = 0 OR target.tboxClass IN condition.conditionTargetTboxClasses OR any(cls IN coalesce(target.tboxClasses, []) WHERE cls IN condition.conditionTargetTboxClasses)) "
        "AND (size(coalesce(condition.conditionTargetGroups, [])) = 0 OR target.group IN condition.conditionTargetGroups) "
        "AND (size(coalesce(condition.conditionTargetRelationScopes, [])) = 0 OR target.relationScope IN condition.conditionTargetRelationScopes) "
        "AND (size(coalesce(condition.conditionTargetEventTypes, [])) = 0 OR target.eventType IN condition.conditionTargetEventTypes) "
        "AND (size(coalesce(condition.conditionTargetPolarities, [])) = 0 OR target.polarity IN condition.conditionTargetPolarities) "
        "AND (condition.conditionTargetMaterialityPassed IS NULL OR target.materialityPassed = condition.conditionTargetMaterialityPassed) "
        "AND (condition.conditionTargetMinMaterialityScore IS NULL OR coalesce(target.materialityScore, 0.0) >= condition.conditionTargetMinMaterialityScore) "
        "AND (condition.conditionTargetMinValue IS NULL OR coalesce(target.valueNumber, 0.0) >= condition.conditionTargetMinValue) "
        "AND (condition.conditionTargetMaxValue IS NULL OR coalesce(target.valueNumber, 0.0) <= condition.conditionTargetMaxValue) "
        "AND (size(coalesce(condition.conditionRelationPolarities, [])) = 0 OR rel.polarity IN condition.conditionRelationPolarities) "
        "AND (size(coalesce(condition.conditionRelationTransitionTypes, [])) = 0 OR rel.transitionType IN condition.conditionRelationTransitionTypes) "
        "AND (size(coalesce(condition.conditionRelationFields, [])) = 0 OR rel.field IN condition.conditionRelationFields) "
        "AND (size(coalesce(condition.conditionRelationSignalGroups, [])) = 0 OR rel.signalGroup IN condition.conditionRelationSignalGroups) "
        "AND (condition.conditionRelationMaterialityPassed IS NULL OR rel.materialityPassed = condition.conditionRelationMaterialityPassed) "
        "AND (condition.conditionRelationMinRiskImpact IS NULL OR coalesce(rel.riskImpact, 0.0) >= condition.conditionRelationMinRiskImpact) "
        "AND (condition.conditionRelationMinSupportImpact IS NULL OR coalesce(rel.supportImpact, 0.0) >= condition.conditionRelationMinSupportImpact) "
        "} "
        "ELSE false END) "
        "WITH rule, template, stock, conditions, "
        "CASE WHEN 0.62 + size(conditions) * 0.08 > 0.94 THEN 0.94 ELSE 0.62 + size(conditions) * 0.08 END AS confidence "
        "WITH rule, template, stock, conditions, confidence, "
        "replace(replace(template.derivationTargetKey, '{symbol}', stock.symbol), '{displayName}', stock.label) AS targetValue, "
        "replace(replace(template.derivationTargetLabel, '{symbol}', stock.symbol), '{displayName}', stock.label) AS targetLabel "
        "WITH rule, template, stock, conditions, confidence, targetLabel, "
        "template.derivationTargetKind + ':' + targetValue AS targetId, "
        "'inference-trace:' + stock.symbol + ':' + rule.ruleId AS traceId, "
        "'evidence:inference:' + stock.symbol + ':' + rule.ruleId AS evidenceId, "
        "'belief:inference:' + stock.symbol + ':' + rule.ruleId + ':' + toString(coalesce(template.derivationIndex, 0)) AS beliefId "
        "MERGE (target:OntologyEntity {id: targetId}) "
        "SET target.label = targetLabel, target.kind = template.derivationTargetKind, target.ontologyBox = 'InferenceBox', "
        "target.symbol = stock.symbol, target.ruleId = rule.ruleId, target.tboxClass = template.derivationTboxClass, "
        "target.accountId = stock.accountId, target.aboxSnapshotId = stock.aboxSnapshotId, target.snapshotId = stock.snapshotId, "
        "target.asOf = stock.asOf, target.isCurrent = true, target.tboxVersion = stock.tboxVersion, "
        "target.activeTboxVersion = stock.activeTboxVersion, target.tboxFingerprint = stock.tboxFingerprint, "
        "target.actionGroup = template.derivationActionGroup, target.actionLevel = template.derivationActionLevel, "
        "target.decisionStage = template.derivationDecisionStage, target.stagePriority = template.derivationStagePriority, "
        "target.boundedContext = 'reasoning-insight', target.nativeNeo4jReasoned = true, target.updatedAt = $updatedAt "
        "MERGE (trace:OntologyEntity {id: traceId}) "
        "SET trace.label = stock.label + ' · ' + rule.label, trace.kind = 'inference-trace', trace.ontologyBox = 'InferenceBox', "
        "trace.symbol = stock.symbol, trace.ruleId = rule.ruleId, trace.tboxClass = 'InferenceTrace', "
        "trace.accountId = stock.accountId, trace.aboxSnapshotId = stock.aboxSnapshotId, trace.snapshotId = stock.snapshotId, "
        "trace.asOf = stock.asOf, trace.isCurrent = true, trace.tboxVersion = stock.tboxVersion, "
        "trace.boundedContext = 'reasoning-insight', trace.confidence = confidence, trace.nativeNeo4jReasoned = true, "
        "trace.matchedConditionIds = [c IN conditions | c.conditionId], trace.updatedAt = $updatedAt "
        "MERGE (evidence:OntologyEvidence {id: evidenceId}) "
        "SET evidence.subject = stock.id, evidence.kind = 'inference-trace', evidence.source = 'neo4j-native-rulebox', "
        "evidence.summary = stock.label + ' · ' + rule.label, evidence.ontologyBox = 'InferenceBox', "
        "evidence.accountId = stock.accountId, evidence.aboxSnapshotId = stock.aboxSnapshotId, evidence.snapshotId = stock.snapshotId, "
        "evidence.asOf = stock.asOf, evidence.isCurrent = true, evidence.tboxVersion = stock.tboxVersion, "
        "evidence.confidence = confidence, evidence.nativeNeo4jReasoned = true, evidence.updatedAt = $updatedAt "
        "MERGE (stock)-[:HAS_EVIDENCE]->(evidence) "
        "MERGE (rule)-[triggered:TRIGGERED_INFERENCE]->(trace) "
        "SET triggered.weight = confidence, triggered.ontologyBox = 'InferenceBox', triggered.ruleId = rule.ruleId, "
        "triggered.accountId = stock.accountId, triggered.aboxSnapshotId = stock.aboxSnapshotId, triggered.snapshotId = stock.snapshotId, "
        "triggered.asOf = stock.asOf, triggered.isCurrent = true, triggered.tboxVersion = stock.tboxVersion, "
        "triggered.nativeNeo4jReasoned = true, triggered.updatedAt = $updatedAt "
        "MERGE (stock)-[hasTrace:HAS_INFERENCE_TRACE]->(trace) "
        "SET hasTrace.weight = confidence, hasTrace.ontologyBox = 'InferenceBox', hasTrace.ruleId = rule.ruleId, "
        "hasTrace.accountId = stock.accountId, hasTrace.aboxSnapshotId = stock.aboxSnapshotId, hasTrace.snapshotId = stock.snapshotId, "
        "hasTrace.asOf = stock.asOf, hasTrace.isCurrent = true, hasTrace.tboxVersion = stock.tboxVersion, "
        "hasTrace.nativeNeo4jReasoned = true, hasTrace.updatedAt = $updatedAt "
        "MERGE (stock)-[inferred:" + safe_type + "]->(target) "
        "SET inferred.weight = coalesce(template.derivationWeight, 0.72), inferred.ontologyBox = 'InferenceBox', inferred.ruleId = rule.ruleId, "
        "inferred.accountId = stock.accountId, inferred.aboxSnapshotId = stock.aboxSnapshotId, inferred.snapshotId = stock.snapshotId, "
        "inferred.asOf = stock.asOf, inferred.isCurrent = true, inferred.tboxVersion = stock.tboxVersion, "
        "inferred.polarity = template.derivationPolarity, inferred.riskImpact = template.derivationRiskImpact, "
        "inferred.supportImpact = template.derivationSupportImpact, inferred.actionGroup = template.derivationActionGroup, "
        "inferred.actionLevel = template.derivationActionLevel, inferred.decisionStage = template.derivationDecisionStage, "
        "inferred.stagePriority = template.derivationStagePriority, inferred.aiInfluenceLabel = template.derivationAiInfluenceLabel, "
        "inferred.inferenceTraceId = traceId, inferred.evidenceIds = [evidenceId], inferred.nativeNeo4jReasoned = true, inferred.updatedAt = $updatedAt "
        "MERGE (target)-[explained:EXPLAINED_BY_TRACE]->(trace) "
        "SET explained.weight = confidence, explained.ontologyBox = 'InferenceBox', explained.ruleId = rule.ruleId, "
        "explained.accountId = stock.accountId, explained.aboxSnapshotId = stock.aboxSnapshotId, explained.snapshotId = stock.snapshotId, "
        "explained.asOf = stock.asOf, explained.isCurrent = true, explained.tboxVersion = stock.tboxVersion, "
        "explained.nativeNeo4jReasoned = true, explained.updatedAt = $updatedAt "
        "FOREACH (_ IN CASE WHEN template.derivationBeliefLabel <> '' THEN [1] ELSE [] END | "
        "MERGE (belief:OntologyBelief {id: beliefId}) "
        "SET belief.label = template.derivationBeliefLabel, belief.polarity = CASE WHEN template.derivationPolarity IN ['risk', 'support'] THEN template.derivationPolarity ELSE 'context' END, "
        "belief.confidence = confidence, belief.ontologyBox = 'InferenceBox', belief.evidenceIds = [evidenceId], "
        "belief.accountId = stock.accountId, belief.aboxSnapshotId = stock.aboxSnapshotId, belief.snapshotId = stock.snapshotId, "
        "belief.asOf = stock.asOf, belief.isCurrent = true, belief.tboxVersion = stock.tboxVersion, "
        "belief.nativeNeo4jReasoned = true, belief.updatedAt = $updatedAt "
        "MERGE (stock)-[:HAS_BELIEF]->(belief) "
        ")"
    )
    return {"statement": statement, "parameters": {"relationType": safe_type, "updatedAt": utc_now()}}
