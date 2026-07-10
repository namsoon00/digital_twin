from typing import Dict, Iterable, List, Tuple

from .market_data import clamp, number
from .ontology_contracts import OntologyEntity, OntologyRelation, PortfolioOntology
from .ontology_inference_materializer import materialize_rule_inference
from .ontology_rulebox_contracts import GraphInferenceRule, GraphRuleCondition


def run_graph_reasoner(graph: PortfolioOntology, rules: Iterable[GraphInferenceRule]) -> None:
    entities_by_id = {item.entity_id: item for item in graph.entities}
    stocks = [
        item
        for item in graph.entities
        if item.kind == "stock" and str((item.properties or {}).get("ontologyBox") or "ABox") != "TBox"
    ]
    for rule in rules:
        if not rule.enabled:
            continue
        for stock in stocks:
            matched, context = rule_matches_entity(graph, entities_by_id, rule, stock)
            if matched:
                materialize_rule_inference(graph, rule, stock, context)


def rule_matches_entity(
    graph: PortfolioOntology,
    entities_by_id: Dict[str, OntologyEntity],
    rule: GraphInferenceRule,
    entity: OntologyEntity,
) -> Tuple[bool, Dict[str, object]]:
    if rule.source_kind and entity.kind != rule.source_kind:
        return False, {}
    matched_conditions: List[Dict[str, object]] = []
    evidence_relation_ids: List[str] = []
    for condition in rule.conditions:
        if condition.kind == "subject_property":
            matched = property_matches(entity.properties or {}, condition.field, condition.operator, condition.value)
            if not matched:
                return False, {}
            matched_conditions.append({
                "conditionId": condition.condition_id,
                "kind": condition.kind,
                "field": condition.field,
                "operator": condition.operator,
                "value": condition.value,
                "actual": (entity.properties or {}).get(condition.field),
            })
            continue
        if condition.kind == "relation":
            relation = first_matching_relation(graph, entities_by_id, entity.entity_id, condition)
            if not relation:
                return False, {}
            relation_id = relation_key(relation)
            evidence_relation_ids.append(relation_id)
            matched_conditions.append({
                "conditionId": condition.condition_id,
                "kind": condition.kind,
                "relationId": relation_id,
                "relationType": relation.relation_type,
                "target": relation.target if relation.source == entity.entity_id else relation.source,
                "weight": number(relation.weight),
            })
            continue
        return False, {}
    confidence = clamp(0.62 + len(matched_conditions) * 0.08, 0.0, 0.94)
    return True, {
        "matchedConditions": matched_conditions,
        "evidenceRelationIds": sorted(set(evidence_relation_ids)),
        "confidence": round(confidence, 3),
    }


def first_matching_relation(
    graph: PortfolioOntology,
    entities_by_id: Dict[str, OntologyEntity],
    entity_id_value: str,
    condition: GraphRuleCondition,
) -> OntologyRelation:
    for relation in graph.relations:
        if relation.relation_type != condition.relation_type:
            continue
        if number(relation.weight) < number(condition.min_weight):
            continue
        if condition.direction == "in" and relation.target != entity_id_value:
            continue
        if condition.direction != "in" and relation.source != entity_id_value:
            continue
        target_id = relation.source if condition.direction == "in" else relation.target
        target = entities_by_id.get(target_id)
        if condition.target_kind and (not target or target.kind != condition.target_kind):
            continue
        if not property_filters_match(relation.properties or {}, condition.relation_property_filters):
            continue
        if target and not property_filters_match(target.properties or {}, condition.target_property_filters):
            continue
        return relation
    return None


def property_matches(properties: Dict[str, object], field: str, operator: str, expected: object) -> bool:
    actual = properties.get(field)
    op = str(operator or "==").strip().lower()
    if op in {"exists", "present"}:
        return actual not in {None, ""}
    if op in {"not_empty", "nonempty"}:
        return bool(str(actual or "").strip())
    if op in {"==", "eq"}:
        return compare_equal(actual, expected)
    if op in {"!=", "ne"}:
        return not compare_equal(actual, expected)
    if op in {"in"}:
        expected_values = expected if isinstance(expected, list) else [expected]
        return any(compare_equal(actual, item) for item in expected_values)
    if op in {"contains"}:
        return str(expected or "") in str(actual or "")
    actual_number = number(actual)
    expected_number = number(expected)
    if op in {"<=", "lte"}:
        return actual_number <= expected_number
    if op in {">=", "gte"}:
        return actual_number >= expected_number
    if op in {"<", "lt"}:
        return actual_number < expected_number
    if op in {">", "gt"}:
        return actual_number > expected_number
    return compare_equal(actual, expected)


def property_filters_match(properties: Dict[str, object], filters: Dict[str, object]) -> bool:
    for key, expected in (filters or {}).items():
        if key == "minValue":
            if not property_matches(properties, "value", ">=", expected):
                return False
            continue
        if key == "maxValue":
            if not property_matches(properties, "value", "<=", expected):
                return False
            continue
        if key == "minMaterialityScore":
            if not property_matches(properties, "materialityScore", ">=", expected):
                return False
            continue
        if key == "minRiskImpact":
            actual = properties.get("riskImpact") if properties.get("riskImpact") is not None else properties.get("opinionImpact")
            if number(actual) < number(expected):
                return False
            continue
        if key == "minSupportImpact":
            if number(properties.get("supportImpact")) < number(expected):
                return False
            continue
        actual = properties.get(key)
        if key in {"tboxClass", "tboxClasses"}:
            actual = list_property_values(properties.get("tboxClasses")) + list_property_values(properties.get("tboxClass"))
        if isinstance(expected, dict):
            if not property_matches(properties, key, str(expected.get("operator") or "=="), expected.get("value")):
                return False
            continue
        if not filter_value_matches(actual, expected):
            return False
    return True


def filter_value_matches(actual: object, expected: object) -> bool:
    if isinstance(expected, list):
        return any(filter_value_matches(actual, item) for item in expected)
    if isinstance(actual, list):
        return any(compare_equal(item, expected) for item in actual)
    return compare_equal(actual, expected)


def list_property_values(value: object) -> List[object]:
    if isinstance(value, list):
        return [item for item in value if item not in (None, "")]
    if value in (None, ""):
        return []
    return [value]


def compare_equal(actual: object, expected: object) -> bool:
    if isinstance(expected, (int, float)) or isinstance(actual, (int, float)):
        return number(actual) == number(expected)
    return str(actual or "").strip().lower() == str(expected or "").strip().lower()


def relation_key(item: OntologyRelation) -> str:
    return "|".join([item.source, item.relation_type, item.target])
