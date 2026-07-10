from dataclasses import asdict, dataclass, field as dataclass_field
from typing import Dict, Iterable, List, Tuple

from .market_data import clamp, number
from .ontology_contracts import OntologyBelief, OntologyEntity, OntologyEvidence, OntologyRelation, PortfolioOntology, entity_id
from .ontology_schema import abox_relation_properties


GRAPH_REASONER_VERSION = "neo4j-rulebox-graph-reasoner-v1"


@dataclass(frozen=True)
class GraphRuleCondition:
    condition_id: str
    kind: str
    description: str
    field: str = ""
    operator: str = "=="
    value: object = None
    relation_type: str = ""
    direction: str = "out"
    target_kind: str = ""
    target_property_filters: Dict[str, object] = dataclass_field(default_factory=dict)
    relation_property_filters: Dict[str, object] = dataclass_field(default_factory=dict)
    min_weight: float = 0.0

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class GraphRuleDerivation:
    relation_type: str
    target_kind: str
    target_key: str
    target_label: str
    tbox_class: str
    tbox_classes: List[str] = dataclass_field(default_factory=list)
    polarity: str = "context"
    risk_impact: float = 0.0
    support_impact: float = 0.0
    weight: float = 0.72
    belief_label: str = ""
    ai_influence_label: str = ""
    action_group: str = ""
    action_level: str = ""

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class GraphInferenceRule:
    rule_id: str
    label: str
    version: str
    source_kind: str
    conditions: List[GraphRuleCondition]
    derivations: List[GraphRuleDerivation]
    action_group: str
    action_level: str
    prompt_hint: str
    enabled: bool = True

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["conditionCount"] = len(self.conditions)
        payload["derivationCount"] = len(self.derivations)
        return payload


def default_graph_inference_rules() -> List[GraphInferenceRule]:
    return [
        GraphInferenceRule(
            rule_id="graph.loss_guard.breakdown.v1",
            label="손실 보유 + 기준선 이탈 -> 손실 방어 추론",
            version="v1",
            source_kind="stock",
            action_group="lossControl",
            action_level="review",
            prompt_hint="손실률과 기준선 이탈이 동시에 보이면 손절 결론보다 회복 조건, 60일선, 거래량 동반 여부를 분리해 설명합니다.",
            conditions=[
                GraphRuleCondition(
                    "holding-source",
                    "subject_property",
                    "보유 종목입니다.",
                    field="source",
                    operator="==",
                    value="holding",
                ),
                GraphRuleCondition(
                    "holding-loss",
                    "subject_property",
                    "보유 종목 손익률이 -8% 이하입니다.",
                    field="profitLossRate",
                    operator="<=",
                    value=-8,
                ),
                GraphRuleCondition(
                    "ma-break",
                    "relation",
                    "20일선 또는 60일선 기준을 이탈했습니다.",
                    relation_type="BREAKS_LEVEL",
                    target_kind="key-level",
                    target_property_filters={"levelType": ["ma20", "ma60"]},
                    min_weight=0.5,
                ),
            ],
            derivations=[
                GraphRuleDerivation(
                    relation_type="HAS_INFERRED_RISK",
                    target_kind="risk",
                    target_key="{symbol}:loss-guard-breakdown",
                    target_label="{displayName} 손실 방어 리스크",
                    tbox_class="MarketRisk",
                    tbox_classes=["Risk", "MarketRisk", "ThresholdCrossing"],
                    polarity="risk",
                    risk_impact=13.0,
                    weight=0.86,
                    belief_label="손실 구간에서 기준선 이탈이 겹쳐 손실 방어 판단이 필요합니다.",
                    ai_influence_label="손실 방어 추론",
                ),
                GraphRuleDerivation(
                    relation_type="REQUIRES_NEXT_CHECK",
                    target_kind="next-check",
                    target_key="{symbol}:loss-guard-recheck",
                    target_label="{displayName} 회복 조건 재확인",
                    tbox_class="NextCheck",
                    tbox_classes=["NextCheck", "ActionabilityAssessment"],
                    polarity="context",
                    weight=0.78,
                    belief_label="다음 갱신에서 60일선과 거래량 확인이 필요합니다.",
                    ai_influence_label="회복 조건 재확인",
                    action_group="lossControl",
                    action_level="watch",
                ),
            ],
        ),
        GraphInferenceRule(
            rule_id="graph.profit_protect.trend_break.v1",
            label="수익 보유 + 추세 이탈 -> 수익 보호 추론",
            version="v1",
            source_kind="stock",
            action_group="profitTake",
            action_level="review",
            prompt_hint="수익 상태에서 단기 추세가 무너지면 보유 유지 조건과 분할 익절 조건을 함께 검토합니다.",
            conditions=[
                GraphRuleCondition(
                    "holding-source",
                    "subject_property",
                    "보유 종목입니다.",
                    field="source",
                    operator="==",
                    value="holding",
                ),
                GraphRuleCondition(
                    "holding-profit",
                    "subject_property",
                    "보유 종목 손익률이 +12% 이상입니다.",
                    field="profitLossRate",
                    operator=">=",
                    value=12,
                ),
                GraphRuleCondition(
                    "ma20-break",
                    "relation",
                    "20일선 기준을 이탈했습니다.",
                    relation_type="BREAKS_LEVEL",
                    target_kind="key-level",
                    target_property_filters={"levelType": "ma20"},
                    min_weight=0.5,
                ),
            ],
            derivations=[
                GraphRuleDerivation(
                    relation_type="HAS_ACTION_CANDIDATE",
                    target_kind="action-candidate",
                    target_key="{symbol}:profit-protect",
                    target_label="{displayName} 수익 보호 점검",
                    tbox_class="ActionCandidate",
                    tbox_classes=["ActionCandidate", "ExecutionPlan"],
                    polarity="risk",
                    risk_impact=8.0,
                    weight=0.76,
                    belief_label="수익 구간에서 20일선 이탈이 생겨 수익 보호 기준을 점검합니다.",
                    ai_influence_label="수익 보호 추론",
                    action_group="profitTake",
                    action_level="review",
                )
            ],
        ),
        GraphInferenceRule(
            rule_id="graph.watchlist.pullback.entry.v1",
            label="관심 종목 + 기준선 재시험 -> 진입 관찰 추론",
            version="v1",
            source_kind="stock",
            action_group="entry",
            action_level="watch",
            prompt_hint="관심 종목은 매도 판단이 아니라 진입 조건, 확인 가격대, 반대 신호를 중심으로 설명합니다.",
            conditions=[
                GraphRuleCondition(
                    "watchlist-source",
                    "subject_property",
                    "관심 종목입니다.",
                    field="source",
                    operator="==",
                    value="watchlist",
                ),
                GraphRuleCondition(
                    "level-retest",
                    "relation",
                    "주요 기준선을 재시험 중입니다.",
                    relation_type="RETESTS_LEVEL",
                    target_kind="key-level",
                    min_weight=0.5,
                ),
            ],
            derivations=[
                GraphRuleDerivation(
                    relation_type="HAS_INFERRED_SUPPORT",
                    target_kind="opportunity",
                    target_key="{symbol}:watchlist-entry-retest",
                    target_label="{displayName} 진입 관찰 기회",
                    tbox_class="Opportunity",
                    tbox_classes=["Opportunity", "EntryCondition", "ActionabilityAssessment"],
                    polarity="support",
                    support_impact=7.0,
                    weight=0.72,
                    belief_label="관심 종목이 주요 기준선을 재시험해 진입 관찰 후보가 됩니다.",
                    ai_influence_label="관심 종목 진입 관찰",
                    action_group="entry",
                    action_level="watch",
                )
            ],
        ),
        GraphInferenceRule(
            rule_id="graph.liquidity.execution_guard.v1",
            label="유동성 제약 + 실행 리스크 -> 분할 실행 추론",
            version="v1",
            source_kind="stock",
            action_group="executionRisk",
            action_level="review",
            prompt_hint="유동성·슬리피지 관계가 강하면 투자 의견과 실제 실행 가능성을 분리합니다.",
            conditions=[
                GraphRuleCondition(
                    "liquidity-limited",
                    "relation",
                    "유동성 제한 관계가 의미 있는 강도입니다.",
                    relation_type="LIMITED_BY_LIQUIDITY",
                    target_kind="liquidity-profile",
                    min_weight=0.55,
                )
            ],
            derivations=[
                GraphRuleDerivation(
                    relation_type="HAS_INFERRED_RISK",
                    target_kind="risk",
                    target_key="{symbol}:execution-liquidity",
                    target_label="{displayName} 실행 유동성 리스크",
                    tbox_class="LiquidityRisk",
                    tbox_classes=["Risk", "LiquidityRisk", "ExecutionRisk"],
                    polarity="risk",
                    risk_impact=9.0,
                    weight=0.74,
                    belief_label="유동성 제약이 있어 의견과 실행 계획을 분리해야 합니다.",
                    ai_influence_label="실행 유동성 추론",
                    action_group="executionRisk",
                    action_level="review",
                )
            ],
        ),
    ]


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
                properties=rulebox_relation_properties("HAS_CONDITION", {"ruleId": rule.rule_id, "conditionId": condition.condition_id}),
            ))
        for index, derivation in enumerate(rule.derivations):
            template_id = entity_id("relation-template", rule.rule_id + ":" + str(index))
            graph.entities.append(OntologyEntity(template_id, derivation.target_label, "relation-template", rulebox_properties({
                "tboxClass": "RelationTemplate",
                "tboxClasses": ["RelationTemplate", "DerivedAssertion"],
                "ruleId": rule.rule_id,
                "relationType": derivation.relation_type,
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
        actual = properties.get(key)
        if isinstance(expected, dict):
            if not property_matches(properties, key, str(expected.get("operator") or "=="), expected.get("value")):
                return False
            continue
        if isinstance(expected, list):
            if not any(compare_equal(actual, item) for item in expected):
                return False
            continue
        if not compare_equal(actual, expected):
            return False
    return True


def compare_equal(actual: object, expected: object) -> bool:
    if isinstance(expected, (int, float)) or isinstance(actual, (int, float)):
        return number(actual) == number(expected)
    return str(actual or "").strip().lower() == str(expected or "").strip().lower()


def materialize_rule_inference(
    graph: PortfolioOntology,
    rule: GraphInferenceRule,
    stock: OntologyEntity,
    context: Dict[str, object],
) -> None:
    properties = stock.properties or {}
    symbol = str(properties.get("symbol") or stock.entity_id.replace("stock:", "")).upper()
    display_name = stock.label or symbol
    confidence = number(context.get("confidence"))
    evidence_relation_ids = [str(item) for item in context.get("evidenceRelationIds") or []]
    trace_id = entity_id("inference-trace", symbol + ":" + rule.rule_id)
    trace_label = display_name + " · " + rule.label
    graph.entities.append(OntologyEntity(trace_id, trace_label, "inference-trace", inference_properties({
        "tboxClass": "InferenceTrace",
        "tboxClasses": ["InferenceTrace", "AIJudgmentAudit"],
        "symbol": symbol,
        "ruleId": rule.rule_id,
        "ruleLabel": rule.label,
        "engineVersion": GRAPH_REASONER_VERSION,
        "confidence": confidence,
        "matchedConditions": list(context.get("matchedConditions") or []),
        "evidenceRelationIds": evidence_relation_ids,
        "promptHint": rule.prompt_hint,
    })))
    rule_entity_id = entity_id("rule", rule.rule_id)
    graph.relations.append(OntologyRelation(
        rule_entity_id,
        trace_id,
        "TRIGGERED_INFERENCE",
        weight=confidence,
        evidence_ids=evidence_relation_ids,
        properties=inference_relation_properties("TRIGGERED_INFERENCE", {
            "ruleId": rule.rule_id,
            "aiInfluenceLabel": rule.label,
            "source": GRAPH_REASONER_VERSION,
        }),
    ))
    graph.relations.append(OntologyRelation(
        stock.entity_id,
        trace_id,
        "HAS_INFERENCE_TRACE",
        weight=confidence,
        evidence_ids=evidence_relation_ids,
        properties=inference_relation_properties("HAS_INFERENCE_TRACE", {
            "ruleId": rule.rule_id,
            "aiInfluenceLabel": rule.label,
            "source": GRAPH_REASONER_VERSION,
        }),
    ))
    evidence_id_value = "evidence:inference:" + symbol + ":" + rule.rule_id
    graph.evidence.append(OntologyEvidence(
        evidence_id_value,
        stock.entity_id,
        "inference-trace",
        GRAPH_REASONER_VERSION,
        trace_label,
        {
            "ontologyBox": "InferenceBox",
            "ruleId": rule.rule_id,
            "ruleLabel": rule.label,
            "matchedConditions": list(context.get("matchedConditions") or []),
            "evidenceRelationIds": evidence_relation_ids,
            "promptHint": rule.prompt_hint,
        },
        confidence,
    ))
    for index, derivation in enumerate(rule.derivations):
        target_key = fill_template(derivation.target_key, symbol, display_name)
        target_label = fill_template(derivation.target_label, symbol, display_name)
        target_id = entity_id(derivation.target_kind, target_key)
        graph.entities.append(OntologyEntity(target_id, target_label, derivation.target_kind, inference_properties({
            "tboxClass": derivation.tbox_class,
            "tboxClasses": derivation.tbox_classes or [derivation.tbox_class],
            "symbol": symbol,
            "ruleId": rule.rule_id,
            "ruleLabel": rule.label,
            "polarity": derivation.polarity,
            "actionGroup": derivation.action_group or rule.action_group,
            "actionLevel": derivation.action_level or rule.action_level,
            "inferenceTraceId": trace_id,
        })))
        relation_properties = {
            "ruleId": rule.rule_id,
            "ruleLabel": rule.label,
            "derivationIndex": index,
            "polarity": derivation.polarity,
            "riskImpact": derivation.risk_impact,
            "supportImpact": derivation.support_impact,
            "actionGroup": derivation.action_group or rule.action_group,
            "actionLevel": derivation.action_level or rule.action_level,
            "aiInfluenceLabel": derivation.ai_influence_label or derivation.belief_label or target_label,
            "inferenceTraceId": trace_id,
            "source": GRAPH_REASONER_VERSION,
        }
        graph.relations.append(OntologyRelation(
            stock.entity_id,
            target_id,
            derivation.relation_type,
            weight=derivation.weight,
            evidence_ids=[evidence_id_value] + evidence_relation_ids,
            properties=inference_relation_properties(derivation.relation_type, relation_properties),
        ))
        graph.relations.append(OntologyRelation(
            target_id,
            trace_id,
            "EXPLAINED_BY_TRACE",
            weight=confidence,
            evidence_ids=[evidence_id_value] + evidence_relation_ids,
            properties=inference_relation_properties("EXPLAINED_BY_TRACE", {
                "ruleId": rule.rule_id,
                "source": GRAPH_REASONER_VERSION,
                "aiInfluenceLabel": "추론 경로",
            }),
        ))
        if derivation.belief_label:
            belief_id = "belief:inference:" + symbol + ":" + rule.rule_id + ":" + str(index)
            graph.beliefs.append(OntologyBelief(
                belief_id,
                stock.entity_id,
                derivation.belief_label,
                derivation.polarity if derivation.polarity in {"risk", "support"} else "context",
                confidence,
                [evidence_id_value] + evidence_relation_ids,
            ))


def rulebox_properties(properties: Dict[str, object]) -> Dict[str, object]:
    payload = dict(properties or {})
    payload.setdefault("ontologyBox", "RuleBox")
    payload.setdefault("box", "RuleBox")
    payload.setdefault("boundedContext", "reasoning-insight")
    payload.setdefault("engineVersion", GRAPH_REASONER_VERSION)
    return payload


def inference_properties(properties: Dict[str, object]) -> Dict[str, object]:
    payload = dict(properties or {})
    payload.setdefault("ontologyBox", "InferenceBox")
    payload.setdefault("box", "InferenceBox")
    payload.setdefault("boundedContext", "reasoning-insight")
    payload.setdefault("engineVersion", GRAPH_REASONER_VERSION)
    return payload


def rulebox_relation_properties(relation_type: str, properties: Dict[str, object] = None) -> Dict[str, object]:
    payload = abox_relation_properties(relation_type, properties or {})
    payload.update({"ontologyBox": "RuleBox", "box": "RuleBox", "engineVersion": GRAPH_REASONER_VERSION})
    return payload


def inference_relation_properties(relation_type: str, properties: Dict[str, object] = None) -> Dict[str, object]:
    payload = abox_relation_properties(relation_type, properties or {})
    payload.update({"ontologyBox": "InferenceBox", "box": "InferenceBox", "engineVersion": GRAPH_REASONER_VERSION})
    return payload


def fill_template(template: str, symbol: str, display_name: str) -> str:
    return str(template or "").replace("{symbol}", symbol).replace("{displayName}", display_name)


def relation_key(item: OntologyRelation) -> str:
    return "|".join([item.source, item.relation_type, item.target])
