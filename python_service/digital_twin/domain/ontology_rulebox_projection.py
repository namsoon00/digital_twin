from typing import Dict, Iterable

from .ontology_contracts import OntologyEntity, OntologyRelation, PortfolioOntology, entity_id
from .ontology_rulebox_contracts import GRAPH_REASONER_VERSION, GraphInferenceRule
from .ontology_rules import ONTOLOGY_RULE_ENGINE_VERSION, RelationRuleDefinition
from .ontology_schema import abox_relation_properties


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
        for condition in rule.conditions:
            condition_id = entity_id("rule-condition", rule.rule_id + ":" + condition.condition_id)
            graph.entities.append(OntologyEntity(condition_id, condition.description, "rule-condition", rulebox_properties({
                "tboxClass": "RuleCondition",
                "tboxClasses": ["RuleCondition", "ValidationRule"],
                "ruleId": rule.rule_id,
                "conditionId": condition.condition_id,
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
        for index, derivation in enumerate(rule.derivations):
            template_id = entity_id("relation-template", rule.rule_id + ":" + str(index))
            graph.entities.append(OntologyEntity(template_id, derivation.target_label, "relation-template", rulebox_properties({
                "tboxClass": "RelationTemplate",
                "tboxClasses": ["RelationTemplate", "DerivedAssertion"],
                "ruleId": rule.rule_id,
                "relationType": derivation.relation_type,
                "derivationIndex": index,
                "derivation": derivation.to_dict(),
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
