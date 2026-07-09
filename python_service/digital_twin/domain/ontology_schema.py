from typing import Dict, List

from .ontology_contracts import OntologyEntity, OntologyRelation, PortfolioOntology, entity_id
from .ontology_tbox import (
    BOUNDED_CONTEXTS,
    TBOX_CLASSES,
    TBOX_REASONING_RULES,
    TBOX_RELATION_TYPES,
    bounded_contexts_payload,
    class_definitions_payload,
    relation_definitions_payload,
    rule_definitions_payload,
    tbox_class_def,
    tbox_relation_def,
)


def ontology_tbox() -> Dict[str, object]:
    return {
        "box": "TBox",
        "description": "Investment ontology schema: bounded contexts, classes, relation types, and reasoning rules.",
        "boundedContexts": bounded_contexts_payload(),
        "classes": list(TBOX_CLASSES),
        "classDefinitions": class_definitions_payload(),
        "relationTypes": list(TBOX_RELATION_TYPES),
        "relationDefinitions": relation_definitions_payload(),
        "reasoningRules": list(TBOX_REASONING_RULES),
        "reasoningRuleDefinitions": rule_definitions_payload(),
    }


def ontology_abox(graph: PortfolioOntology) -> Dict[str, object]:
    return {
        "box": "ABox",
        "description": "Runtime portfolio assertions: holdings, evidence, beliefs, and opinions.",
        "portfolioId": graph.portfolio_id,
        "entityCount": len([item for item in graph.entities if item.properties.get("ontologyBox") != "TBox"]),
        "relationCount": len([item for item in graph.relations if item.properties.get("ontologyBox") != "TBox"]),
        "evidenceCount": len(graph.evidence),
        "beliefCount": len(graph.beliefs),
        "opinionCount": len(graph.opinions),
    }


def tbox_entities() -> List[OntologyEntity]:
    entities = [
        OntologyEntity(entity_id("ontology-box", "TBox"), "TBox", "ontology-box", {
            "ontologyBox": "TBox",
            "description": "Schema layer for investment ontology concepts.",
        }),
        OntologyEntity(entity_id("ontology-box", "ABox"), "ABox", "ontology-box", {
            "ontologyBox": "TBox",
            "description": "Assertion layer for runtime portfolio facts.",
        }),
    ]
    for context in BOUNDED_CONTEXTS:
        entities.append(OntologyEntity(entity_id("bounded-context", context.key), context.label, "bounded-context", {
            "ontologyBox": "TBox",
            "box": "TBox",
            "boundedContext": context.key,
            "label": context.label,
            "description": context.description,
        }))
    for name in TBOX_CLASSES:
        definition = tbox_class_def(name)
        entities.append(OntologyEntity(entity_id("tbox-class", name), name, "tbox-class", {
            "ontologyBox": "TBox",
            "box": "TBox",
            "className": name,
            "boundedContext": definition.bounded_context if definition else "",
            "label": definition.label if definition else name,
            "parentClass": definition.parent if definition else "",
            "description": definition.description if definition else "",
        }))
    for name in TBOX_RELATION_TYPES:
        definition = tbox_relation_def(name)
        entities.append(OntologyEntity(entity_id("tbox-relation", name), name, "tbox-relation", {
            "ontologyBox": "TBox",
            "box": "TBox",
            "relationType": name,
            "boundedContext": definition.bounded_context if definition else "",
            "sourceContext": definition.source_context if definition else "",
            "targetContext": definition.target_context if definition else "",
            "description": definition.description if definition else "",
        }))
    return entities


def tbox_relations() -> List[OntologyRelation]:
    relations: List[OntologyRelation] = []
    tbox_id = entity_id("ontology-box", "TBox")
    abox_id = entity_id("ontology-box", "ABox")
    for context in BOUNDED_CONTEXTS:
        context_id = entity_id("bounded-context", context.key)
        relations.append(OntologyRelation(tbox_id, context_id, "DEFINES_BOUNDED_CONTEXT", properties={
            "ontologyBox": "TBox",
            "boundedContext": context.key,
        }))
    for name in TBOX_CLASSES:
        definition = tbox_class_def(name)
        class_id = entity_id("tbox-class", name)
        owner_id = entity_id("bounded-context", definition.bounded_context) if definition else tbox_id
        relations.append(OntologyRelation(owner_id, class_id, "DEFINES_CLASS", properties={
            "ontologyBox": "TBox",
            "boundedContext": definition.bounded_context if definition else "",
        }))
        if definition and definition.parent:
            relations.append(OntologyRelation(class_id, entity_id("tbox-class", definition.parent), "IS_A", properties={
                "ontologyBox": "TBox",
                "boundedContext": definition.bounded_context,
            }))
    for name in TBOX_RELATION_TYPES:
        definition = tbox_relation_def(name)
        owner_id = entity_id("bounded-context", definition.bounded_context) if definition else tbox_id
        relations.append(OntologyRelation(owner_id, entity_id("tbox-relation", name), "DEFINES_RELATION", properties={
            "ontologyBox": "TBox",
            "boundedContext": definition.bounded_context if definition else "",
            "sourceContext": definition.source_context if definition else "",
            "targetContext": definition.target_context if definition else "",
        }))
    relations.append(OntologyRelation(tbox_id, abox_id, "CONSTRAINS_ASSERTIONS", properties={"ontologyBox": "TBox"}))
    return relations


def abox_properties(properties: Dict[str, object] = None) -> Dict[str, object]:
    payload = dict(properties or {})
    payload.setdefault("ontologyBox", "ABox")
    payload.setdefault("box", "ABox")
    if not payload.get("boundedContext"):
        class_names = []
        if payload.get("tboxClass"):
            class_names.append(str(payload.get("tboxClass")))
        class_names.extend(str(value) for value in payload.get("tboxClasses") or [] if value)
        for class_name in class_names:
            definition = tbox_class_def(class_name)
            if definition:
                payload["boundedContext"] = definition.bounded_context
                break
    return payload


def abox_relation_properties(relation_type: str, properties: Dict[str, object] = None) -> Dict[str, object]:
    payload = abox_properties(properties or {})
    definition = tbox_relation_def(relation_type)
    if definition and not payload.get("boundedContext"):
        payload["boundedContext"] = definition.bounded_context
    if definition:
        payload.setdefault("sourceContext", definition.source_context)
        payload.setdefault("targetContext", definition.target_context)
    return payload


def add_entity(graph: PortfolioOntology, kind: str, value: str, label: str, properties: Dict[str, object] = None) -> str:
    item_id = entity_id(kind, value)
    graph.entities.append(OntologyEntity(item_id, label or str(value or item_id), kind, abox_properties(properties or {})))
    return item_id


def add_relation(
    graph: PortfolioOntology,
    source: str,
    target: str,
    relation_type: str,
    weight: float = 1.0,
    evidence_ids: List[str] = None,
    properties: Dict[str, object] = None,
) -> None:
    graph.relations.append(OntologyRelation(
        source,
        target,
        relation_type,
        weight=weight,
        evidence_ids=list(evidence_ids or []),
        properties=abox_relation_properties(relation_type, properties or {}),
    ))
