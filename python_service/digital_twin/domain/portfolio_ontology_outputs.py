from typing import Dict, List

from .market_data import clamp, number
from .ontology_contracts import (
    OntologyEntity,
    OntologyEvidence,
    OntologyOpinion,
    OntologyRelation,
    PortfolioOntology,
    entity_id,
)
from .ontology_prompting import entity_label_map, relation_key
from .ontology_schema import abox_relation_properties, add_entity, add_relation
from .portfolio_ontology_opinions import ontology_action_label


def relation_relation_label(relation: OntologyRelation, labels: Dict[str, str]) -> str:
    properties = relation.properties or {}
    explicit = str(properties.get("aiInfluenceLabel") or properties.get("label") or "").strip()
    if explicit:
        return explicit
    source = labels.get(relation.source, relation.source)
    target = labels.get(relation.target, relation.target)
    return source + " " + relation.relation_type + " " + target


def dedupe_entities(items: List[OntologyEntity]) -> List[OntologyEntity]:
    merged: Dict[str, OntologyEntity] = {}
    for item in items:
        if item.entity_id in merged:
            merged[item.entity_id].properties.update(item.properties or {})
            continue
        merged[item.entity_id] = item
    return list(merged.values())

def dedupe_relations(items: List[OntologyRelation]) -> List[OntologyRelation]:
    merged: Dict[str, OntologyRelation] = {}
    for item in items:
        if (item.properties or {}).get("ontologyBox") != "TBox":
            item.properties = abox_relation_properties(item.relation_type, item.properties or {})
        key = "|".join([item.source, item.relation_type, item.target])
        if key in merged:
            merged[key].weight = max(number(merged[key].weight), number(item.weight))
            merged[key].evidence_ids = sorted(set(merged[key].evidence_ids + item.evidence_ids))
            merged[key].properties.update(item.properties or {})
            continue
        merged[key] = item
    return list(merged.values())

def dedupe_evidence(items: List[OntologyEvidence]) -> List[OntologyEvidence]:
    merged: Dict[str, OntologyEvidence] = {}
    for item in items:
        merged[item.evidence_id] = item
    return list(merged.values())

def relation_influence_score(relation: OntologyRelation) -> (float, float):
    properties = relation.properties or {}
    polarity = str(properties.get("polarity") or properties.get("signalPolarity") or "").lower()
    if polarity == "context":
        return 0.0, 0.0
    risk = number(properties.get("opinionImpact") or properties.get("riskImpact") or properties.get("impactScore"))
    support = number(properties.get("supportImpact"))
    if not risk and polarity == "risk":
        risk = number(relation.weight) * 8
    if not support and polarity == "support":
        support = number(relation.weight) * 8
    if relation.relation_type in {"CONTRADICTS", "EXPOSED_TO"} and not support:
        risk = max(risk, number(relation.weight) * (12 if relation.relation_type == "CONTRADICTS" else 8))
    if relation.relation_type == "SUPPORTED_BY" and not risk:
        support = max(support, number(relation.weight) * 8)
    return max(0.0, risk), max(0.0, support)

def relation_influence_rows(graph: PortfolioOntology, stock_id: str) -> List[Dict[str, object]]:
    labels = entity_label_map(graph)
    portfolio_id = entity_id("portfolio", graph.portfolio_id)
    stock_ids = {item.entity_id for item in graph.entities if item.kind == "stock"}
    neighbor_ids = {
        relation.source if relation.target == stock_id else relation.target
        for relation in graph.relations
        if relation.source == stock_id or relation.target == stock_id
    }
    rows: List[Dict[str, object]] = []
    for relation in graph.relations:
        if relation.properties.get("ontologyBox") == "TBox":
            continue
        direct = relation.source == stock_id or relation.target == stock_id
        neighbor = not direct and (relation.source in neighbor_ids or relation.target in neighbor_ids)
        if neighbor:
            endpoints = {relation.source, relation.target}
            other_stock_ids = stock_ids - {stock_id}
            if endpoints & other_stock_ids:
                continue
            if portfolio_id in endpoints:
                continue
            if relation.source in neighbor_ids and relation.target in neighbor_ids:
                continue
        if not direct and not neighbor:
            continue
        risk, support = relation_influence_score(relation)
        if not risk and not support:
            continue
        rows.append({
            "relationId": relation_key(relation),
            "scope": "direct" if direct else "neighbor",
            "type": relation.relation_type,
            "source": relation.source,
            "sourceLabel": labels.get(relation.source, relation.source),
            "target": relation.target,
            "targetLabel": labels.get(relation.target, relation.target),
            "riskImpact": round(risk, 2),
            "supportImpact": round(support, 2),
            "label": relation_relation_label(relation, labels),
            "properties": dict(relation.properties or {}),
        })
    return rows

def opinion_action_from_relation_pressure(opinion: OntologyOpinion, source: str, pressure: float) -> (str, str):
    if source == "watchlist":
        if pressure >= 65:
            return "관심 종목: 리스크 관계 우선 점검", "caution"
        if pressure >= 45:
            return "관심 종목: 진입 조건 재확인", "hold"
        return "관심 종목: 진입 기준 대기", "hold"
    return ontology_action_label(pressure, number((opinion.legacy_model or {}).get("profitLossRate")), opinion.contradictions, opinion.dominant_risks)

def apply_relation_driven_opinions(graph: PortfolioOntology) -> None:
    stock_entities = {
        str((item.properties or {}).get("symbol") or "").upper(): item
        for item in graph.entities
        if item.kind == "stock"
    }
    for opinion in graph.opinions:
        stock = stock_entities.get(str(opinion.symbol or "").upper())
        if not stock:
            continue
        properties = stock.properties or {}
        source = str(properties.get("source") or "holding")
        influences = relation_influence_rows(graph, stock.entity_id)
        base_pressure = number((opinion.legacy_model or {}).get("baseOntologyPressure") or opinion.ontology_pressure)
        base_thesis = str((opinion.legacy_model or {}).get("baseThesis") or opinion.thesis or "")
        opinion.legacy_model.setdefault("baseOntologyPressure", round(base_pressure, 1))
        opinion.legacy_model.setdefault("baseThesis", base_thesis)
        opinion.legacy_model.setdefault("profitLossRate", properties.get("profitLossRate", 0))
        risk_impact = sum(number(item.get("riskImpact")) for item in influences)
        support_impact = sum(number(item.get("supportImpact")) for item in influences)
        opinion.relation_influences = influences
        opinion.ontology_pressure = round(clamp(base_pressure + risk_impact - min(18.0, support_impact * 0.65), 0.0, 100.0), 1)
        action, tone = opinion_action_from_relation_pressure(opinion, source, opinion.ontology_pressure)
        opinion.action = action
        opinion.tone = tone
        risk_labels = [item["label"] for item in influences if number(item.get("riskImpact")) > 0]
        support_labels = [item["label"] for item in influences if number(item.get("supportImpact")) > 0]
        for label in risk_labels[:4]:
            if label not in opinion.dominant_risks:
                opinion.dominant_risks.append(label)
        for label in support_labels[:4]:
            if label not in opinion.supporting_beliefs:
                opinion.supporting_beliefs.append(label)
        relation_summary = []
        if risk_labels:
            relation_summary.append("관계 리스크: " + ", ".join(risk_labels[:2]))
        if support_labels:
            relation_summary.append("관계 지지: " + ", ".join(support_labels[:2]))
        opinion.thesis = "; ".join([item for item in [base_thesis] + relation_summary if item])

def insight_type_for_opinion(opinion: OntologyOpinion, stock_source: str) -> str:
    if opinion.contradictions:
        return "contradictionDetected"
    if any("데이터" in str(item) or "부족" in str(item) for item in opinion.dominant_risks + opinion.contradictions):
        return "dataQualityWarning"
    if stock_source == "watchlist":
        return "watchlistEntrySignal"
    if opinion.ontology_pressure >= 55 or opinion.tone in {"danger", "caution"}:
        return "riskIncrease"
    if opinion.opportunities or opinion.supporting_beliefs:
        return "opportunityDetected"
    return "portfolioExposureShift"

def add_ontology_insight_concepts(graph: PortfolioOntology) -> None:
    stock_entities = {
        str((item.properties or {}).get("symbol") or "").upper(): item
        for item in graph.entities
        if item.kind == "stock"
    }
    reasoning_id = entity_id("reasoning-cycle", "ontologyReasoning")
    dispatch_id = entity_id("notification-dispatch", "investmentInsight")
    insight_policy_id = entity_id("insight-policy", "meaningful-change")
    importance_gate_id = entity_id("importance-gate", "materiality-first")
    ai_review_id = entity_id("concept", "ai-investment-review")
    for opinion in graph.opinions:
        stock = stock_entities.get(str(opinion.symbol or "").upper())
        if not stock:
            continue
        source = str((stock.properties or {}).get("source") or "holding")
        insight_type = insight_type_for_opinion(opinion, source)
        materiality_score = max(number(opinion.ontology_pressure), number(opinion.conviction))
        if opinion.contradictions:
            materiality_score = max(materiality_score, 78)
        materiality_threshold = 55.0
        dispatch_candidate = bool(materiality_score >= materiality_threshold or opinion.contradictions or source == "watchlist")
        insight_id = add_entity(graph, "insight", opinion.symbol + ":" + insight_type, stock.label + " " + opinion.action, {
            "tboxClass": "Insight",
            "symbol": opinion.symbol,
            "insightType": insight_type,
            "severity": opinion.tone,
            "score": number(opinion.ontology_pressure),
            "confidence": number(opinion.conviction),
            "thesis": opinion.thesis,
            "relationInfluenceCount": len(opinion.relation_influences or []),
            "dispatchCandidate": dispatch_candidate,
        })
        assessment_id = add_entity(graph, "materiality-assessment", opinion.symbol + ":" + insight_type, stock.label + " 중요 변경 평가", {
            "tboxClass": "MaterialityAssessment",
            "symbol": opinion.symbol,
            "score": round(materiality_score, 1),
            "threshold": materiality_threshold,
            "passed": dispatch_candidate,
            "grade": "watch" if dispatch_candidate else "record",
            "components": {
                "relationStrength": round(number(opinion.ontology_pressure), 1),
                "confidence": round(number(opinion.conviction), 1),
                "contradiction": 78 if opinion.contradictions else 0,
            },
        })
        add_relation(graph, reasoning_id, insight_id, "PRODUCES_INSIGHT", weight=round(number(opinion.conviction) / 100, 4), properties={"source": "ontology-reasoning"})
        add_relation(graph, stock.entity_id, insight_id, "CREATED_FROM_RELATION", weight=round(number(opinion.ontology_pressure) / 100, 4), properties={"source": "ontology-reasoning"})
        add_relation(graph, insight_id, entity_id("insight-type", insight_type), "HAS_INSIGHT_TYPE", weight=1.0, properties={"source": "ontology-reasoning"})
        add_relation(graph, insight_id, insight_policy_id, "EVALUATED_BY", weight=1.0, properties={"source": "ontology-reasoning"})
        add_relation(graph, insight_id, assessment_id, "EVALUATED_BY", weight=round(materiality_score / 100, 4), properties={"source": "materiality-gate"})
        add_relation(graph, assessment_id, importance_gate_id, "PASSES_IMPORTANCE_GATE" if dispatch_candidate else "BLOCKED_BY_IMPORTANCE_GATE", weight=round(materiality_score / 100, 4), properties={"source": "materiality-gate"})
        add_relation(graph, insight_id, dispatch_id, "DISPATCHED_BY", weight=1.0, properties={"source": "ontology-reasoning", "mode": "insight-driven-only"})
        if dispatch_candidate:
            intent_id = add_entity(graph, "notification-intent", opinion.symbol + ":" + insight_type, stock.label + " 알림 의도", {
                "tboxClass": "NotificationIntent",
                "symbol": opinion.symbol,
                "insightType": insight_type,
                "materialityScore": round(materiality_score, 1),
                "status": "send-candidate",
            })
            add_relation(graph, insight_id, intent_id, "CREATES_NOTIFICATION_INTENT", weight=round(materiality_score / 100, 4), properties={"source": "materiality-gate"})
            add_relation(graph, intent_id, dispatch_id, "DISPATCHED_BY", weight=round(materiality_score / 100, 4), properties={"source": "materiality-gate"})
        add_relation(graph, insight_id, ai_review_id, "REQUESTS_OPINION_FROM", weight=1.0, properties={"source": "ontology-reasoning"})
