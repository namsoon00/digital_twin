from dataclasses import asdict, dataclass
from typing import Dict, List

from .ontology_contracts import PortfolioOntology
from .ontology_semantics import (
    endpoint_matches_family,
    entity_class_family,
    relation_endpoint_contract,
    semantic_contract_summary,
)
from .ontology_tbox import tbox_class_def, tbox_relation_def


@dataclass(frozen=True)
class OntologyValidationIssue:
    severity: str
    code: str
    subject: str
    message: str

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class OntologyValidationReport:
    status: str
    error_count: int
    warning_count: int
    issues: List[OntologyValidationIssue]
    semantic_contract: Dict[str, object]

    def to_dict(self) -> Dict[str, object]:
        return {
            "status": self.status,
            "errorCount": self.error_count,
            "warningCount": self.warning_count,
            "issues": [item.to_dict() for item in self.issues],
            "semanticContract": dict(self.semantic_contract or {}),
        }


def _entity_classes(properties: Dict[str, object]) -> List[str]:
    classes = []
    if properties.get("tboxClass"):
        classes.append(str(properties.get("tboxClass")))
    classes.extend(str(item) for item in properties.get("tboxClasses") or [] if item)
    seen = set()
    result = []
    for item in classes:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def validate_ontology(graph: PortfolioOntology) -> OntologyValidationReport:
    issues: List[OntologyValidationIssue] = []
    entity_ids = {item.entity_id for item in graph.entities or []}
    required_lifecycle_fields = ["accountId", "aboxSnapshotId", "tboxVersion"]
    worldview = dict(getattr(graph, "worldview", {}) or {})
    scoped_manifest = str(worldview.get("scopedAboxManifestVersion") or "").strip()
    if scoped_manifest:
        required_lifecycle_fields.extend([
            "aboxScopeId",
            "aboxScopeType",
            "scopeGenerationId",
            "worldviewManifestId",
        ])
    for entity in graph.entities or []:
        properties = entity.properties or {}
        if properties.get("ontologyBox") == "TBox":
            continue
        if properties.get("ontologyBox") == "ABox":
            missing_lifecycle = [field for field in required_lifecycle_fields if not properties.get(field)]
            if missing_lifecycle:
                issues.append(OntologyValidationIssue(
                    "warning",
                    "missing_abox_lifecycle",
                    entity.entity_id,
                    "ABox entity is missing lifecycle fields: " + ", ".join(missing_lifecycle),
                ))
        classes = _entity_classes(properties)
        if not classes:
            issues.append(OntologyValidationIssue(
                "warning",
                "missing_tbox_class",
                entity.entity_id,
                "ABox entity has no tboxClass or tboxClasses.",
            ))
            continue
        for class_name in classes:
            if not tbox_class_def(class_name):
                issues.append(OntologyValidationIssue(
                    "error",
                    "unknown_tbox_class",
                    entity.entity_id,
                    "Unknown TBox class: " + class_name,
                ))
    for relation in graph.relations or []:
        properties = relation.properties or {}
        if properties.get("ontologyBox") == "TBox":
            continue
        if properties.get("ontologyBox") == "ABox":
            missing_lifecycle = [field for field in required_lifecycle_fields if not properties.get(field)]
            if missing_lifecycle:
                issues.append(OntologyValidationIssue(
                    "warning",
                    "missing_abox_lifecycle",
                    relation.source + " -> " + relation.target,
                    "ABox relation is missing lifecycle fields: " + ", ".join(missing_lifecycle),
                ))
        if relation.source not in entity_ids:
            issues.append(OntologyValidationIssue(
                "error",
                "missing_relation_source",
                relation.source + " -> " + relation.target,
                "Relation source entity is missing.",
            ))
        if relation.target not in entity_ids:
            issues.append(OntologyValidationIssue(
                "error",
                "missing_relation_target",
                relation.source + " -> " + relation.target,
                "Relation target entity is missing.",
            ))
        if not tbox_relation_def(relation.relation_type):
            issues.append(OntologyValidationIssue(
                "error",
                "unknown_relation_type",
                relation.source + " -" + relation.relation_type + "-> " + relation.target,
                "Unknown TBox relation type: " + relation.relation_type,
            ))
            continue
        expected_source, expected_target = relation_endpoint_contract(relation.relation_type)
        source_entity = next((item for item in graph.entities or [] if item.entity_id == relation.source), None)
        target_entity = next((item for item in graph.entities or [] if item.entity_id == relation.target), None)
        # Only enforce a contract when both endpoints carry enough semantic
        # information. Legacy/imported rows can still be loaded and receive a
        # missing-class warning above, while a typed ABox cannot connect a
        # company, position, or observation to the wrong relation endpoint.
        if expected_source and expected_target and source_entity and target_entity:
            source_classes = entity_class_family(source_entity.properties, source_entity.kind)
            target_classes = entity_class_family(target_entity.properties, target_entity.kind)
            if source_classes and target_classes and (
                not endpoint_matches_family(source_classes, expected_source)
                or not endpoint_matches_family(target_classes, expected_target)
            ):
                issues.append(OntologyValidationIssue(
                    "error",
                    "relation_endpoint_contract_violation",
                    relation.source + " -" + relation.relation_type + "-> " + relation.target,
                    (
                        "Relation endpoint classes do not satisfy the semantic contract. "
                        "Expected source one of [" + ", ".join(expected_source) + "] and target one of ["
                        + ", ".join(expected_target) + "]."
                    ),
                ))
    for evidence in graph.evidence or []:
        value = evidence.value or {}
        if value.get("ontologyBox") != "ABox":
            continue
        missing_lifecycle = [field for field in required_lifecycle_fields if not value.get(field)]
        if missing_lifecycle:
            issues.append(OntologyValidationIssue(
                "warning",
                "missing_abox_lifecycle",
                evidence.evidence_id,
                "ABox evidence is missing lifecycle fields: " + ", ".join(missing_lifecycle),
            ))
    error_count = len([item for item in issues if item.severity == "error"])
    warning_count = len([item for item in issues if item.severity == "warning"])
    return OntologyValidationReport(
        status="valid" if not error_count else "invalid",
        error_count=error_count,
        warning_count=warning_count,
        issues=issues,
        semantic_contract=semantic_contract_summary(graph),
    )
