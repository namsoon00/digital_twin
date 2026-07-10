import hashlib
import json
from functools import lru_cache
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
    tbox_class_materialization_policy,
    tbox_materialization_box,
    tbox_relation_def,
    tbox_relation_materialization_policy,
)


ONTOLOGY_TBOX_VERSION = "investment-tbox-v1"


@lru_cache(maxsize=1)
def tbox_fingerprint() -> str:
    payload = {
        "boundedContexts": bounded_contexts_payload(),
        "classes": class_definitions_payload(),
        "relationTypes": relation_definitions_payload(),
        "reasoningRules": rule_definitions_payload(),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@lru_cache(maxsize=1)
def _default_tbox_metadata_tuple():
    return {
        "source": "code",
        "version": ONTOLOGY_TBOX_VERSION,
        "fingerprint": tbox_fingerprint(),
        "entityCount": 4 + len(BOUNDED_CONTEXTS) + len(TBOX_CLASSES) + len(TBOX_RELATION_TYPES),
        "relationCount": len(BOUNDED_CONTEXTS)
        + len(TBOX_CLASSES)
        + len([name for name in TBOX_CLASSES if tbox_class_def(name) and tbox_class_def(name).parent])
        + len(TBOX_RELATION_TYPES)
        + 4,
    }


def default_tbox_metadata() -> Dict[str, object]:
    return dict(_default_tbox_metadata_tuple())


def normalize_tbox_metadata(payload: Dict[str, object] = None) -> Dict[str, object]:
    fallback = default_tbox_metadata()
    source = dict(payload or {})
    version = str(source.get("version") or source.get("tboxVersion") or fallback["version"]).strip()
    fingerprint = str(source.get("fingerprint") or source.get("tboxFingerprint") or fallback["fingerprint"]).strip()
    return {
        "source": str(source.get("source") or fallback["source"]),
        "version": version or fallback["version"],
        "fingerprint": fingerprint or fallback["fingerprint"],
        "entityCount": int(source.get("entityCount") or fallback["entityCount"]),
        "relationCount": int(source.get("relationCount") or fallback["relationCount"]),
        "status": str(source.get("status") or "ok"),
    }


def ontology_tbox() -> Dict[str, object]:
    metadata = default_tbox_metadata()
    return {
        "box": "TBox",
        "version": metadata["version"],
        "fingerprint": metadata["fingerprint"],
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
    box_counts: Dict[str, int] = {}
    relation_box_counts: Dict[str, int] = {}
    for item in graph.entities:
        box = str((item.properties or {}).get("ontologyBox") or "ABox")
        box_counts[box] = box_counts.get(box, 0) + 1
    for item in graph.relations:
        box = str((item.properties or {}).get("ontologyBox") or "ABox")
        relation_box_counts[box] = relation_box_counts.get(box, 0) + 1
    return {
        "box": "ABox",
        "description": "Runtime portfolio assertions plus RuleBox and InferenceBox projections.",
        "portfolioId": graph.portfolio_id,
        "entityCount": len([item for item in graph.entities if item.properties.get("ontologyBox") != "TBox"]),
        "relationCount": len([item for item in graph.relations if item.properties.get("ontologyBox") != "TBox"]),
        "entityBoxCounts": box_counts,
        "relationBoxCounts": relation_box_counts,
        "ruleBoxEntityCount": box_counts.get("RuleBox", 0),
        "inferenceBoxEntityCount": box_counts.get("InferenceBox", 0),
        "inferenceBoxRelationCount": relation_box_counts.get("InferenceBox", 0),
        "evidenceCount": len(graph.evidence),
        "beliefCount": len(graph.beliefs),
        "opinionCount": len(graph.opinions),
    }


def tbox_entities() -> List[OntologyEntity]:
    entities = [
        OntologyEntity(entity_id("ontology-box", "TBox"), "TBox", "ontology-box", {
            "ontologyBox": "TBox",
            "version": ONTOLOGY_TBOX_VERSION,
            "tboxVersion": ONTOLOGY_TBOX_VERSION,
            "tboxFingerprint": tbox_fingerprint(),
            "description": "Schema layer for investment ontology concepts.",
        }),
        OntologyEntity(entity_id("ontology-box", "ABox"), "ABox", "ontology-box", {
            "ontologyBox": "TBox",
            "version": ONTOLOGY_TBOX_VERSION,
            "tboxVersion": ONTOLOGY_TBOX_VERSION,
            "tboxFingerprint": tbox_fingerprint(),
            "description": "Assertion layer for runtime portfolio facts.",
        }),
        OntologyEntity(entity_id("ontology-box", "RuleBox"), "RuleBox", "ontology-box", {
            "ontologyBox": "TBox",
            "version": ONTOLOGY_TBOX_VERSION,
            "tboxVersion": ONTOLOGY_TBOX_VERSION,
            "tboxFingerprint": tbox_fingerprint(),
            "description": "Executable graph rule layer for conditions, relation templates, and prompt hints.",
        }),
        OntologyEntity(entity_id("ontology-box", "InferenceBox"), "InferenceBox", "ontology-box", {
            "ontologyBox": "TBox",
            "version": ONTOLOGY_TBOX_VERSION,
            "tboxVersion": ONTOLOGY_TBOX_VERSION,
            "tboxFingerprint": tbox_fingerprint(),
            "description": "Derived assertion layer for inference traces and rule-produced relations.",
        }),
    ]
    for context in BOUNDED_CONTEXTS:
        entities.append(OntologyEntity(entity_id("bounded-context", context.key), context.label, "bounded-context", {
            "ontologyBox": "TBox",
            "box": "TBox",
            "version": ONTOLOGY_TBOX_VERSION,
            "tboxVersion": ONTOLOGY_TBOX_VERSION,
            "tboxFingerprint": tbox_fingerprint(),
            "boundedContext": context.key,
            "label": context.label,
            "description": context.description,
        }))
    for name in TBOX_CLASSES:
        definition = tbox_class_def(name)
        policy = tbox_class_materialization_policy(name)
        entities.append(OntologyEntity(entity_id("tbox-class", name), name, "tbox-class", {
            "ontologyBox": "TBox",
            "box": "TBox",
            "version": ONTOLOGY_TBOX_VERSION,
            "tboxVersion": ONTOLOGY_TBOX_VERSION,
            "tboxFingerprint": tbox_fingerprint(),
            "className": name,
            "boundedContext": definition.bounded_context if definition else "",
            "label": definition.label if definition else name,
            "parentClass": definition.parent if definition else "",
            "description": definition.description if definition else "",
            "materializationPolicy": policy,
            "materializationBox": tbox_materialization_box(policy),
        }))
    for name in TBOX_RELATION_TYPES:
        definition = tbox_relation_def(name)
        policy = tbox_relation_materialization_policy(name)
        entities.append(OntologyEntity(entity_id("tbox-relation", name), name, "tbox-relation", {
            "ontologyBox": "TBox",
            "box": "TBox",
            "version": ONTOLOGY_TBOX_VERSION,
            "tboxVersion": ONTOLOGY_TBOX_VERSION,
            "tboxFingerprint": tbox_fingerprint(),
            "relationType": name,
            "boundedContext": definition.bounded_context if definition else "",
            "sourceContext": definition.source_context if definition else "",
            "targetContext": definition.target_context if definition else "",
            "description": definition.description if definition else "",
            "materializationPolicy": policy,
            "materializationBox": tbox_materialization_box(policy),
        }))
    return entities


def tbox_relations() -> List[OntologyRelation]:
    relations: List[OntologyRelation] = []
    tbox_id = entity_id("ontology-box", "TBox")
    abox_id = entity_id("ontology-box", "ABox")
    rulebox_id = entity_id("ontology-box", "RuleBox")
    inferencebox_id = entity_id("ontology-box", "InferenceBox")
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
    relations.append(OntologyRelation(tbox_id, rulebox_id, "CONSTRAINS_RULES", properties={"ontologyBox": "TBox"}))
    relations.append(OntologyRelation(rulebox_id, inferencebox_id, "DERIVES_ASSERTIONS", properties={"ontologyBox": "TBox"}))
    relations.append(OntologyRelation(inferencebox_id, abox_id, "CONSTRAINS_ASSERTIONS", properties={"ontologyBox": "TBox"}))
    for item in relations:
        item.properties.setdefault("version", ONTOLOGY_TBOX_VERSION)
        item.properties.setdefault("tboxVersion", ONTOLOGY_TBOX_VERSION)
        item.properties.setdefault("tboxFingerprint", tbox_fingerprint())
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


def abox_lifecycle_metadata(
    portfolio_id: str,
    runtime_context: Dict[str, object] = None,
    active_tbox: Dict[str, object] = None,
) -> Dict[str, object]:
    runtime_context = runtime_context or {}
    account = runtime_context.get("account") if isinstance(runtime_context.get("account"), dict) else {}
    account_id = str(account.get("accountId") or account.get("id") or portfolio_id or "account").strip() or "account"
    as_of = str(
        runtime_context.get("asOf")
        or runtime_context.get("referenceDate")
        or runtime_context.get("snapshotAt")
        or ""
    ).strip()
    snapshot_id = str(runtime_context.get("snapshotId") or "").strip()
    if not snapshot_id:
        snapshot_seed = "|".join([account_id, as_of or "unknown"])
        snapshot_id = "abox-snapshot:" + hashlib.sha256(snapshot_seed.encode("utf-8")).hexdigest()[:16]
    tbox = normalize_tbox_metadata(active_tbox or runtime_context.get("activeTBox") or runtime_context.get("tbox"))
    return {
        "accountId": account_id,
        "aboxSnapshotId": snapshot_id,
        "snapshotId": snapshot_id,
        "asOf": as_of,
        "isCurrent": True,
        "tboxVersion": tbox["version"],
        "activeTboxVersion": tbox["version"],
        "tboxFingerprint": tbox["fingerprint"],
        "activeTboxSource": tbox["source"],
        "activeTboxEntityCount": tbox["entityCount"],
        "activeTboxRelationCount": tbox["relationCount"],
    }


def apply_abox_lifecycle(graph: PortfolioOntology, metadata: Dict[str, object]) -> PortfolioOntology:
    if not metadata:
        return graph
    for item in graph.entities:
        properties = item.properties or {}
        if str(properties.get("ontologyBox") or "ABox") == "ABox":
            properties.update(metadata)
            item.properties = properties
    for item in graph.relations:
        properties = item.properties or {}
        if str(properties.get("ontologyBox") or "ABox") == "ABox":
            properties.update(metadata)
            item.properties = properties
    for item in graph.evidence:
        value = item.value or {}
        if str(value.get("ontologyBox") or "ABox") == "ABox":
            value.update(metadata)
            item.value = value
    return graph


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
