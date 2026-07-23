from typing import Dict, Iterable

from .ontology_change_impact import rule_condition_dependency_profile
from .ontology_contracts import OntologyEntity, OntologyRelation, PortfolioOntology, entity_id
from .ontology_rulebox_contracts import GRAPH_REASONER_VERSION, GraphInferenceRule
from .ontology_relation_reasoning import ONTOLOGY_RULE_ENGINE_VERSION, RelationRuleDefinition
from .ontology_schema import abox_relation_properties
from .ontology_threshold_policy import rulebox_threshold_policy_payloads


def add_rulebox_concepts(graph: PortfolioOntology, rules: Iterable[GraphInferenceRule]) -> None:
    registry_id = entity_id("rule-registry", GRAPH_REASONER_VERSION)
    graph.entities.append(OntologyEntity(registry_id, "Graph Reasoner RuleBox", "rule-registry", rulebox_properties({
        "tboxClass": "RuleRegistry",
        "tboxClasses": ["RuleRegistry", "GraphReasoner"],
        "version": GRAPH_REASONER_VERSION,
        "engine": "graph-reasoner",
    })))
    graph.relations.append(OntologyRelation(
        entity_id("ontology-box", "RuleBox"),
        registry_id,
        "DEFINES_RULE",
        weight=1.0,
        properties=rulebox_relation_properties("DEFINES_RULE", {"source": GRAPH_REASONER_VERSION}),
    ))
    add_threshold_policy_concepts(graph, registry_id)
    for rule in rules:
        rule_id = entity_id("rule", rule.rule_id)
        graph.entities.append(OntologyEntity(rule_id, rule.label, "rule", rulebox_properties({
            "tboxClass": "GraphInferenceRule",
            "tboxClasses": ["ReasoningRule", "GraphInferenceRule"],
            "ruleId": rule.rule_id,
            "version": rule.version,
            "enabled": rule.enabled,
            "sourceKind": rule.source_kind,
            "actionGroup": rule.action_group,
            "actionLevel": rule.action_level,
            "promptHint": rule.prompt_hint,
            "hypothesisFamilyKey": rule.hypothesis_family_key,
            "hypothesisLifecycle": rule.resolved_hypothesis_lifecycle().to_dict(),
            "anyConditionMinCount": rule.any_condition_min_count,
            "conditionCount": len(rule.conditions),
            "derivationCount": len(rule.derivations),
        })))
        graph.relations.append(OntologyRelation(
            registry_id,
            rule_id,
            "DEFINES_RULE",
            weight=1.0,
            properties=rulebox_relation_properties("DEFINES_RULE", {"ruleId": rule.rule_id}),
        ))
        for condition_index, condition in enumerate(rule.conditions):
            condition_id = entity_id("rule-condition", rule.rule_id + ":" + condition.condition_id)
            graph.entities.append(OntologyEntity(condition_id, condition.description, "rule-condition", rulebox_properties({
                "tboxClass": "RuleCondition",
                "tboxClasses": ["RuleCondition", "ValidationRule"],
                "ruleId": rule.rule_id,
                "conditionId": condition.condition_id,
                "conditionIndex": condition_index,
                "condition": condition.to_dict(),
            })))
            graph.relations.append(OntologyRelation(
                rule_id,
                condition_id,
                "HAS_CONDITION",
                weight=1.0,
                properties=rulebox_relation_properties("HAS_CONDITION", {
                    "ruleId": rule.rule_id,
                    "conditionId": condition.condition_id,
                }),
            ))
            dependency = rule_condition_dependency_profile(condition)
            dependency_id = entity_id("rule-dependency", rule.rule_id + ":" + condition.condition_id)
            graph.entities.append(OntologyEntity(dependency_id, condition.description, "rule-dependency", rulebox_properties({
                "tboxClass": "RuleDependency",
                "tboxClasses": ["RuleDependency", "RuleCondition"],
                "ruleId": rule.rule_id,
                "conditionId": condition.condition_id,
                "scopeFamilies": list(dependency.get("scopeFamilies") or []),
                "conditionKind": dependency.get("conditionKind"),
                "field": dependency.get("field"),
                "relationType": dependency.get("relationType"),
                "targetKind": dependency.get("targetKind"),
                "role": dependency.get("role"),
                "conservative": bool(dependency.get("conservative")),
            })))
            graph.relations.append(OntologyRelation(
                rule_id,
                dependency_id,
                "HAS_RULE_DEPENDENCY",
                weight=1.0,
                properties=rulebox_relation_properties("HAS_RULE_DEPENDENCY", {
                    "ruleId": rule.rule_id,
                    "conditionId": condition.condition_id,
                    "scopeFamilies": list(dependency.get("scopeFamilies") or []),
                    "conservative": bool(dependency.get("conservative")),
                }),
            ))
        for index, derivation in enumerate(rule.derivations):
            template_id = entity_id("relation-template", rule.rule_id + ":" + str(index))
            derivation_payload = derivation.to_dict()
            derivation_payload["action_group"] = derivation.action_group or rule.action_group
            derivation_payload["action_level"] = derivation.action_level or rule.action_level
            graph.entities.append(OntologyEntity(template_id, derivation.target_label, "relation-template", rulebox_properties({
                "tboxClass": "RelationTemplate",
                "tboxClasses": ["RelationTemplate", "DerivedAssertion", "RuleDecisionPolicy", "RulePriorityPolicy"],
                "ruleId": rule.rule_id,
                "relationType": derivation.relation_type,
                "derivationIndex": index,
                "derivation": derivation_payload,
            })))
            graph.relations.append(OntologyRelation(
                rule_id,
                template_id,
                "DERIVES_RELATION",
                weight=1.0,
                properties=rulebox_relation_properties("DERIVES_RELATION", {
                    "ruleId": rule.rule_id,
                    "relationType": derivation.relation_type,
                }),
            ))


def add_threshold_policy_concepts(graph: PortfolioOntology, registry_id: str) -> None:
    policy_registry_id = entity_id("threshold-policy-registry", GRAPH_REASONER_VERSION)
    graph.entities.append(OntologyEntity(policy_registry_id, "Ontology Threshold Policy Registry", "threshold-policy-registry", rulebox_properties({
        "tboxClass": "RuleRegistry",
        "tboxClasses": ["RuleRegistry", "RuleDecisionPolicy"],
        "version": GRAPH_REASONER_VERSION,
        "engine": "threshold-policy",
    })))
    graph.relations.append(OntologyRelation(
        registry_id,
        policy_registry_id,
        "DEFINES_POLICY",
        weight=1.0,
        properties=rulebox_relation_properties("DEFINES_POLICY", {"source": GRAPH_REASONER_VERSION}),
    ))
    for index, payload in enumerate(rulebox_threshold_policy_payloads()):
        policy_id = str(payload.get("policyId") or "threshold-policy-" + str(index + 1))
        entity = entity_id("threshold-policy", policy_id)
        graph.entities.append(OntologyEntity(entity, str(payload.get("label") or policy_id), "threshold-policy", rulebox_properties({
            "tboxClass": payload.get("tboxClass"),
            "tboxClasses": payload.get("tboxClasses") or [],
            "policyId": policy_id,
            "policyVersion": payload.get("version"),
            "policySource": payload.get("source"),
            "thresholdCount": payload.get("thresholdCount"),
            "thresholds": payload.get("thresholds") or {},
        })))
        graph.relations.append(OntologyRelation(
            policy_registry_id,
            entity,
            "DEFINES_POLICY",
            weight=1.0,
            properties=rulebox_relation_properties("DEFINES_POLICY", {
                "policyId": policy_id,
                "source": GRAPH_REASONER_VERSION,
            }),
        ))


def add_relation_rulebox_concepts(graph: PortfolioOntology, rules: Iterable[RelationRuleDefinition]) -> None:
    registry_id = entity_id("relation-rule-registry", ONTOLOGY_RULE_ENGINE_VERSION)
    graph.entities.append(OntologyEntity(registry_id, "Relation RuleBox", "relation-rule-registry", rulebox_properties({
        "tboxClass": "RuleRegistry",
        "tboxClasses": ["RuleRegistry", "RelationRuleRegistry"],
        "version": ONTOLOGY_RULE_ENGINE_VERSION,
        "engine": "ontology-relation-rules",
    })))
    graph.relations.append(OntologyRelation(
        entity_id("ontology-box", "RuleBox"),
        registry_id,
        "DEFINES_RULE",
        weight=1.0,
        properties=rulebox_relation_properties("DEFINES_RULE", {"source": ONTOLOGY_RULE_ENGINE_VERSION}),
    ))
    for rule in rules or []:
        rule_id = entity_id("relation-rule", rule.rule_id)
        graph.entities.append(OntologyEntity(rule_id, rule.label, "relation-rule", rulebox_properties({
            "tboxClass": "RelationReasoningRule",
            "tboxClasses": ["ReasoningRule", "RelationReasoningRule"],
            "ruleId": rule.rule_id,
            "version": rule.version,
            "engine": ONTOLOGY_RULE_ENGINE_VERSION,
            "relationType": rule.relation_type,
            "signalType": rule.signal_type,
            "conditionSummary": rule.condition_summary,
            "promptHint": rule.prompt_hint,
            "requiredFields": list(rule.required_fields or []),
        })))
        graph.relations.append(OntologyRelation(
            registry_id,
            rule_id,
            "DEFINES_RULE",
            weight=1.0,
            properties=rulebox_relation_properties("DEFINES_RULE", {"ruleId": rule.rule_id, "source": ONTOLOGY_RULE_ENGINE_VERSION}),
        ))
        condition_id = entity_id("relation-rule-condition", rule.rule_id)
        graph.entities.append(OntologyEntity(condition_id, rule.condition_summary or rule.label, "relation-rule-condition", rulebox_properties({
            "tboxClass": "RuleCondition",
            "tboxClasses": ["RuleCondition", "RelationRuleCondition"],
            "ruleId": rule.rule_id,
            "conditionSummary": rule.condition_summary,
            "requiredFields": list(rule.required_fields or []),
        })))
        graph.relations.append(OntologyRelation(
            rule_id,
            condition_id,
            "HAS_CONDITION",
            weight=1.0,
            properties=rulebox_relation_properties("HAS_CONDITION", {"ruleId": rule.rule_id, "source": ONTOLOGY_RULE_ENGINE_VERSION}),
        ))
        dependency = rule_condition_dependency_profile({
            "conditionId": rule.rule_id,
            "kind": "relation",
            "relationType": rule.relation_type,
            "relationPropertyFilters": {"field": list(rule.required_fields or [])},
        })
        dependency_id = entity_id("relation-rule-dependency", rule.rule_id)
        graph.entities.append(OntologyEntity(dependency_id, rule.condition_summary or rule.label, "rule-dependency", rulebox_properties({
            "tboxClass": "RuleDependency",
            "tboxClasses": ["RuleDependency", "RelationRuleCondition"],
            "ruleId": rule.rule_id,
            "conditionId": rule.rule_id,
            "scopeFamilies": list(dependency.get("scopeFamilies") or []),
            "conditionKind": dependency.get("conditionKind"),
            "field": dependency.get("field"),
            "relationType": dependency.get("relationType"),
            "targetKind": dependency.get("targetKind"),
            "role": dependency.get("role"),
            "conservative": bool(dependency.get("conservative")),
        })))
        graph.relations.append(OntologyRelation(
            rule_id,
            dependency_id,
            "HAS_RULE_DEPENDENCY",
            weight=1.0,
            properties=rulebox_relation_properties("HAS_RULE_DEPENDENCY", {
                "ruleId": rule.rule_id,
                "conditionId": rule.rule_id,
                "scopeFamilies": list(dependency.get("scopeFamilies") or []),
                "conservative": bool(dependency.get("conservative")),
                "source": ONTOLOGY_RULE_ENGINE_VERSION,
            }),
        ))
        template_id = entity_id("relation-rule-template", rule.rule_id)
        graph.entities.append(OntologyEntity(template_id, rule.relation_type, "relation-rule-template", rulebox_properties({
            "tboxClass": "RelationTemplate",
            "tboxClasses": ["RelationTemplate", "RelationRuleTemplate"],
            "ruleId": rule.rule_id,
            "relationType": rule.relation_type,
            "signalType": rule.signal_type,
            "promptHint": rule.prompt_hint,
        })))
        graph.relations.append(OntologyRelation(
            rule_id,
            template_id,
            "DERIVES_RELATION",
            weight=1.0,
            properties=rulebox_relation_properties("DERIVES_RELATION", {
                "ruleId": rule.rule_id,
                "relationType": rule.relation_type,
                "source": ONTOLOGY_RULE_ENGINE_VERSION,
            }),
        ))


def rulebox_properties(properties: Dict[str, object]) -> Dict[str, object]:
    payload = dict(properties or {})
    payload.setdefault("ontologyBox", "RuleBox")
    payload.setdefault("box", "RuleBox")
    payload.setdefault("boundedContext", "reasoning-insight")
    payload.setdefault("engineVersion", GRAPH_REASONER_VERSION)
    return payload


def rulebox_relation_properties(relation_type: str, properties: Dict[str, object] = None) -> Dict[str, object]:
    payload = abox_relation_properties(relation_type, properties or {})
    payload.update({"ontologyBox": "RuleBox", "box": "RuleBox", "engineVersion": GRAPH_REASONER_VERSION})
    return payload
