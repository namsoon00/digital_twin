"""Storage schema for TypeQL, without database execution."""

from functools import lru_cache
from typing import Dict, Set

from digital_twin.modules.model_registry.contracts import default_graph_inference_rules
from digital_twin.modules.reasoning.domain.ontology_schema_capabilities import rule_schema_capability_manifest
from digital_twin.modules.reasoning.domain.ontology_semantics import SEMANTIC_STORAGE_CONTRACT_VERSION, class_for_kind, primary_tbox_class, semantic_class_types, typedb_class_type, typedb_context_fallback_type, typedb_relation_type
from digital_twin.modules.model_registry.contracts import CLASS_DEFS, tbox_class_def
from digital_twin.modules.reasoning.infrastructure.typeql.constants import (
    TYPEDB_COMMON_NODE_ATTRIBUTES,
    TYPEDB_PROMOTED_NUMERIC_ATTRIBUTES,
    TYPEDB_PROMOTED_TEXT_ATTRIBUTES,
)


@lru_cache(maxsize=1)
def typedb_rule_schema_capability_contract() -> Dict[str, object]:
    """Compile the physical TypeDB capability surface from the active rules."""
    manifest = rule_schema_capability_manifest(default_graph_inference_rules())
    contexts = sorted({item.bounded_context for item in CLASS_DEFS if item.bounded_context})
    subject_attributes = {
        typedb_subject_attribute(field)
        for field in manifest.subject_fields
        if typedb_subject_attribute(field)
    }
    context_attributes: Dict[str, Set[str]] = {
        context: set(subject_attributes) - TYPEDB_COMMON_NODE_ATTRIBUTES
        for context in contexts
    }
    for context, fields in manifest.target_fields_by_context.items():
        target = context_attributes.setdefault(context, set(subject_attributes))
        target.update(
            typedb_target_attribute(field)
            for field in fields
            if typedb_target_attribute(field)
        )
    context_attributes = {
        context: set(attributes) - TYPEDB_COMMON_NODE_ATTRIBUTES
        for context, attributes in context_attributes.items()
    }
    all_rule_attributes = set(subject_attributes)
    for attributes in context_attributes.values():
        all_rule_attributes.update(attributes)
    physical_classes: Set[str] = set()
    physical_relations: Set[str] = set()
    for rule in default_graph_inference_rules():
        if getattr(rule, "enabled", True) is False:
            continue
        source_class = class_for_kind(getattr(rule, "source_kind", ""))
        if source_class:
            physical_classes.add(source_class)
        for condition in getattr(rule, "conditions", []) or []:
            relation_type = str(getattr(condition, "relation_type", "") or "").upper().strip()
            if relation_type:
                physical_relations.add(relation_type)
            target_class = class_for_kind(getattr(condition, "target_kind", ""))
            if target_class:
                physical_classes.add(target_class)
            tbox_filter = dict(getattr(condition, "target_property_filters", {}) or {}).get("tboxClass")
            for value in tbox_filter if isinstance(tbox_filter, (list, tuple, set)) else [tbox_filter]:
                if str(value or "").strip() in semantic_class_types():
                    physical_classes.add(str(value).strip())
        for derivation in getattr(rule, "derivations", []) or []:
            relation_type = str(getattr(derivation, "relation_type", "") or "").upper().strip()
            if relation_type:
                physical_relations.add(relation_type)
            tbox_class = str(getattr(derivation, "tbox_class", "") or "").strip()
            if tbox_class in semantic_class_types():
                physical_classes.add(tbox_class)

    pending_classes = list(physical_classes)
    while pending_classes:
        definition = tbox_class_def(pending_classes.pop())
        parent = str(definition.parent or "").strip() if definition else ""
        if parent and parent in semantic_class_types() and parent not in physical_classes:
            physical_classes.add(parent)
            pending_classes.append(parent)
    return {
        **manifest.to_dict(),
        "storageContractVersion": SEMANTIC_STORAGE_CONTRACT_VERSION,
        "commonAttributes": sorted(TYPEDB_COMMON_NODE_ATTRIBUTES),
        "subjectAttributes": sorted(subject_attributes),
        "contextAttributes": {
            context: sorted(attributes)
            for context, attributes in sorted(context_attributes.items())
        },
        "directQueryAttributes": sorted(all_rule_attributes | TYPEDB_COMMON_NODE_ATTRIBUTES),
        "physicalClassNames": sorted(physical_classes),
        "physicalClassTypes": sorted(typedb_class_type(item) for item in physical_classes),
        "physicalRelationNames": sorted(physical_relations),
        "physicalRelationTypes": sorted(typedb_relation_type(item) for item in physical_relations),
    }


def typedb_entity_storage_type(
    properties: Dict[str, object] = None,
    kind: object = "",
    fallback: str = "ontology-entity",
) -> str:
    class_name = primary_tbox_class(properties) or class_for_kind(kind)
    definition = tbox_class_def(class_name) if class_name else None
    if definition is None:
        return str(fallback or "ontology-entity")
    physical_classes = set(
        typedb_rule_schema_capability_contract().get("physicalClassNames") or []
    )
    if class_name in physical_classes:
        return typedb_class_type(class_name)
    return typedb_context_fallback_type(definition.bounded_context)


def typedb_relation_storage_type(
    relation_type: object,
    fallback: str = "ontology-assertion",
) -> str:
    normalized = str(relation_type or "").upper().strip()
    physical_relations = set(
        typedb_rule_schema_capability_contract().get("physicalRelationNames") or []
    )
    return typedb_relation_type(normalized) if normalized in physical_relations else fallback


def typedb_subject_attribute(field: str) -> str:
    promoted_attribute = TYPEDB_PROMOTED_NUMERIC_ATTRIBUTES.get(field) or TYPEDB_PROMOTED_TEXT_ATTRIBUTES.get(field)
    if promoted_attribute:
        return promoted_attribute
    return {
        "source": "ontology-source-value",
        "symbol": "ontology-symbol",
        "kind": "ontology-kind",
        "ontologyBox": "ontology-box",
        "tboxClass": "ontology-tbox-class",
        "profitLossRate": "ontology-profit-loss-rate",
        "value": "ontology-value-number",
        "valueNumber": "ontology-value-number",
    }.get(field, "")


def typedb_target_attribute(field: str) -> str:
    if field in {"minValue", "maxValue"}:
        field = "value"
    promoted_attribute = TYPEDB_PROMOTED_NUMERIC_ATTRIBUTES.get(field) or TYPEDB_PROMOTED_TEXT_ATTRIBUTES.get(field)
    if promoted_attribute:
        return promoted_attribute
    return {
        "field": "ontology-field",
        "levelType": "ontology-level-type",
        "dataScope": "ontology-data-scope",
        "domainScope": "ontology-domain-scope",
        "relationScope": "ontology-relation-scope",
        "group": "ontology-group",
        "polarity": "ontology-polarity",
        "eventType": "ontology-event-type",
        "materialityPassed": "ontology-materiality-passed",
        "materialityState": "ontology-materiality-state",
        "relevanceState": "ontology-relevance-state",
        "sourceTrustState": "ontology-source-trust-state",
        "dataState": "ontology-data-state",
        "value": "ontology-value-number",
        "tboxClass": "ontology-tbox-class",
        "tboxClasses": "ontology-tbox-class",
        "allowAddOnStrength": "ontology-allow-add-on-strength",
        "trimOnTrendBreak": "ontology-trim-on-trend-break",
        "avoidAveragingDown": "ontology-avoid-averaging-down",
        "impactPolarity": "ontology-impact-polarity",
        "needsReview": "ontology-needs-review",
        "readScope": "ontology-read-scope",
        "peRatio": "ontology-pe-ratio",
        "beta": "ontology-beta",
    }.get(field, "")


def typedb_relation_attribute(field: str) -> str:
    return {
        "weight": "ontology-weight",
        "field": "ontology-field",
        "signalGroup": "ontology-signal-group",
        "polarity": "ontology-polarity",
        "transitionType": "ontology-transition-type",
        "materialityPassed": "ontology-materiality-passed",
        "materialityState": "ontology-materiality-state",
        "relevanceState": "ontology-relevance-state",
        "sourceTrustState": "ontology-source-trust-state",
        "evidenceRole": "ontology-evidence-role",
        "reviewLevel": "ontology-review-level",
        "dataState": "ontology-data-state",
        "changeState": "ontology-change-state",
        "conflictState": "ontology-conflict-state",
    }.get(field, "")
