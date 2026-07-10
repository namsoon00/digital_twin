from dataclasses import asdict, dataclass
from typing import Dict, List

from .ontology_contracts import PortfolioOntology
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

    def to_dict(self) -> Dict[str, object]:
        return {
            "status": self.status,
            "errorCount": self.error_count,
            "warningCount": self.warning_count,
            "issues": [item.to_dict() for item in self.issues],
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
    for entity in graph.entities or []:
        properties = entity.properties or {}
        if properties.get("ontologyBox") == "TBox":
            continue
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
    error_count = len([item for item in issues if item.severity == "error"])
    warning_count = len([item for item in issues if item.severity == "warning"])
    return OntologyValidationReport(
        status="valid" if not error_count else "invalid",
        error_count=error_count,
        warning_count=warning_count,
        issues=issues,
    )
