from typing import Dict, List

from .market_data import number
from .ontology_contracts import (
    OntologyEntity,
    OntologyEvidence,
    OntologyOpinion,
    OntologyRelation,
    PortfolioOntology,
)
from .ontology_schema import abox_relation_properties, add_entity, add_relation


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
