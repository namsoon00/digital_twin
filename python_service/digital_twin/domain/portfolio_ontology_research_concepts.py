from typing import Dict, List

from .investment_research import research_evidence_from_external_signals, research_evidence_from_facts
from .market_data import number
from .ontology_contracts import OntologyEvidence, PortfolioOntology
from .ontology_schema import add_entity, add_relation


def unique_list(values: List[str]) -> List[str]:
    seen = set()
    rows: List[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        rows.append(text)
    return rows


def event_tbox_classes(item: object) -> List[str]:
    kind = str(getattr(item, "kind", "") or "").lower()
    classes = ["Observation", "ExternalObservation", "ResearchEvidence", "ExternalSignal", "Evidence"]
    if "news" in kind:
        classes.extend(["NewsEvent", "NewsArticle", "EventRisk"])
    elif "disclosure" in kind or "filing" in kind:
        classes.extend(["DisclosureEvent", "DisclosureFiling", "EventRisk"])
    elif "financial" in kind or "earning" in kind:
        classes.extend(["FundamentalObservation", "EarningsEvent", "ValuationSignal"])
    elif "market" in kind:
        classes.extend(["PriceObservation", "PriceSignal"])
    return unique_list(classes)


def event_relation_properties(item: object) -> Dict[str, object]:
    polarity = str(getattr(item, "polarity", "") or "context")
    impact = number(getattr(item, "impact_score", 0))
    raw_payload = getattr(item, "raw_payload", {}) if isinstance(getattr(item, "raw_payload", {}), dict) else {}
    props = {
        "source": "research-evidence",
        "polarity": polarity,
        "impactScore": round(impact, 2),
        "confidence": round(number(getattr(item, "confidence", 0)), 2),
        "aiInfluenceLabel": str(getattr(item, "title", "") or getattr(item, "kind", "") or "리서치 근거"),
    }
    for key in [
        "relationScope",
        "relevanceScore",
        "sourceReliability",
        "directMention",
        "matchedAliases",
        "mentionedPeers",
        "topicTags",
        "marketTopics",
        "eventType",
        "materialityScore",
        "materialityPassed",
        "ontologyRelations",
        "analysisSummary",
        "analysisVersion",
    ]:
        if key in raw_payload:
            props[key] = raw_payload.get(key)
    if "materialityPassed" not in props and raw_payload.get("materialityScore") is not None:
        props["materialityPassed"] = number(raw_payload.get("materialityScore")) >= 65
    if polarity == "risk":
        props["opinionImpact"] = min(18.0, max(4.0, impact))
    elif polarity == "support":
        props["supportImpact"] = min(14.0, max(3.0, impact))
    return props


def evidence_document_shape(item: object) -> Dict[str, object]:
    kind = str(getattr(item, "kind", "") or "").lower()
    source = str(getattr(item, "source", "") or "").lower()
    title = str(getattr(item, "title", "") or "").lower()
    url = str(getattr(item, "url", "") or "").lower()
    disclosure_terms = ["disclosure", "filing", "dart", "opendart", "edgar", "sec", "공시", "보고서"]
    if any(token in value for token in disclosure_terms for value in [kind, source, title, url]):
        return {
            "kind": "disclosure-filing",
            "tboxClass": "DisclosureFiling",
            "tboxClasses": ["Observation", "ExternalObservation", "ExternalSignal", "DisclosureEvent", "DisclosureFiling", "EventRisk"],
            "documentType": "disclosure",
        }
    if "news" in kind or str(getattr(item, "url", "") or "").strip():
        return {
            "kind": "news-article",
            "tboxClass": "NewsArticle",
            "tboxClasses": ["Observation", "ExternalObservation", "ExternalSignal", "NewsEvent", "NewsArticle", "EventRisk"],
            "documentType": "news",
        }
    return {}


def add_research_document_concept(
    graph: PortfolioOntology,
    stock_id: str,
    event_id: str,
    thesis_id: str,
    active_opinion_id: str,
    item: object,
    props: Dict[str, object],
    relation_weight: float,
) -> None:
    shape = evidence_document_shape(item)
    if not shape:
        return
    raw_payload = getattr(item, "raw_payload", {}) if isinstance(getattr(item, "raw_payload", {}), dict) else {}
    evidence_id = str(getattr(item, "evidence_id", "") or "")
    document_id = add_entity(graph, str(shape["kind"]), evidence_id or str(getattr(item, "title", "") or ""), str(getattr(item, "title", "") or shape["tboxClass"]), {
        "tboxClass": str(shape["tboxClass"]),
        "tboxClasses": list(shape["tboxClasses"]),
        "symbol": str(getattr(item, "symbol", "") or ""),
        "kind": str(getattr(item, "kind", "") or ""),
        "source": str(getattr(item, "source", "") or ""),
        "title": str(getattr(item, "title", "") or ""),
        "summary": str(getattr(item, "summary", "") or ""),
        "url": str(getattr(item, "url", "") or ""),
        "publishedAt": str(getattr(item, "published_at", "") or ""),
        "observedAt": str(getattr(item, "observed_at", "") or ""),
        "documentType": str(shape["documentType"]),
        "relationScope": raw_payload.get("relationScope"),
        "relevanceScore": raw_payload.get("relevanceScore"),
        "sourceReliability": raw_payload.get("sourceReliability"),
        "materialityScore": raw_payload.get("materialityScore"),
        "materialityPassed": raw_payload.get("materialityPassed"),
        "eventType": raw_payload.get("eventType"),
    })
    source_label = str(getattr(item, "source", "") or "ResearchEvidence").strip() or "ResearchEvidence"
    source_id = add_entity(graph, "data-source", source_label, source_label, {
        "tboxClass": "DataSource",
        "tboxClasses": ["DataSource", "Provenance"],
        "documentType": str(shape["documentType"]),
    })
    add_relation(graph, stock_id, document_id, "HAS_OBSERVATION", weight=relation_weight, evidence_ids=[evidence_id], properties=props)
    add_relation(graph, stock_id, document_id, "HAS_EXTERNAL_SIGNAL", weight=relation_weight, evidence_ids=[evidence_id], properties=props)
    add_relation(graph, document_id, stock_id, "MENTIONS_INSTRUMENT", weight=relation_weight, evidence_ids=[evidence_id], properties=props)
    add_relation(graph, event_id, document_id, "HAS_PROVENANCE", weight=relation_weight, evidence_ids=[evidence_id], properties={**props, "source": "research-document"})
    add_relation(graph, document_id, source_id, "HAS_PROVENANCE", weight=1.0, evidence_ids=[evidence_id], properties={**props, "source": "research-document-source"})
    if thesis_id:
        add_relation(graph, document_id, thesis_id, "MATERIAL_TO", weight=round((number(getattr(item, "impact_score", 0)) or 2) / 20, 4), evidence_ids=[evidence_id], properties=props)
    if active_opinion_id:
        add_relation(graph, document_id, active_opinion_id, "IMPACTS_OPINION", weight=round((number(getattr(item, "impact_score", 0)) or 2) / 20, 4), evidence_ids=[evidence_id], properties=props)

def add_research_evidence_concepts(
    graph: PortfolioOntology,
    stock_id: str,
    thesis_id: str,
    active_opinion_id: str,
    symbol: str,
    facts: Dict[str, object],
    external_signals: Dict[str, object],
) -> None:
    evidence_by_id = {}
    for item in research_evidence_from_facts(symbol, facts or {}) + research_evidence_from_external_signals(symbol, external_signals or {}):
        evidence_by_id[item.evidence_id] = item
    for item in evidence_by_id.values():
        raw_payload = item.raw_payload if isinstance(item.raw_payload, dict) else {}
        relation_scope = str(raw_payload.get("relationScope") or "").lower().strip()
        materiality_passed = raw_payload.get("materialityPassed") if "materialityPassed" in raw_payload else None
        if materiality_passed is None and raw_payload.get("materialityScore") is not None:
            materiality_passed = number(raw_payload.get("materialityScore")) >= 65
        scope_weight = {
            "direct": 1.0,
            "peer": 0.62,
            "sector": 0.48,
            "market": 0.28,
            "noise": 0.0,
        }.get(relation_scope, 0.72)
        relation_weight = round(max(0.12, number(item.confidence) * scope_weight), 4)
        event_id = add_entity(graph, "research-evidence", item.evidence_id, item.title, {
            "tboxClass": "ResearchEvidence",
            "tboxClasses": event_tbox_classes(item),
            "symbol": item.symbol,
            "kind": item.kind,
            "source": item.source,
            "title": item.title,
            "summary": item.summary,
            "url": item.url,
            "publishedAt": item.published_at,
            "observedAt": item.observed_at,
            "polarity": item.polarity,
            "impactScore": round(number(item.impact_score), 1),
            "confidence": round(number(item.confidence), 2),
            "relationScope": raw_payload.get("relationScope"),
            "relevanceScore": raw_payload.get("relevanceScore"),
            "sourceReliability": raw_payload.get("sourceReliability"),
            "matchedAliases": raw_payload.get("matchedAliases"),
            "mentionedPeers": raw_payload.get("mentionedPeers"),
            "topicTags": raw_payload.get("topicTags"),
            "marketTopics": raw_payload.get("marketTopics"),
            "eventType": raw_payload.get("eventType"),
            "materialityScore": raw_payload.get("materialityScore"),
            "materialityPassed": materiality_passed,
            "analysisSummary": raw_payload.get("analysisSummary"),
            "analysisVersion": raw_payload.get("analysisVersion"),
        })
        graph.evidence.append(OntologyEvidence(
            item.evidence_id,
            stock_id,
            item.kind,
            item.source,
            item.title,
            item.to_dict(),
            item.confidence,
        ))
        props = event_relation_properties(item)
        add_relation(graph, stock_id, event_id, "HAS_OBSERVATION", weight=round(number(item.confidence), 4), evidence_ids=[item.evidence_id], properties=props)
        add_relation(graph, stock_id, event_id, "HAS_EXTERNAL_SIGNAL", weight=round(number(item.confidence), 4), evidence_ids=[item.evidence_id], properties=props)
        add_relation(graph, event_id, stock_id, "MENTIONS_INSTRUMENT", weight=relation_weight, evidence_ids=[item.evidence_id], properties=props)
        add_research_document_concept(graph, stock_id, event_id, thesis_id, active_opinion_id, item, props, relation_weight)
        if relation_scope in {"peer", "sector", "market"}:
            add_relation(graph, event_id, stock_id, "AFFECTS", weight=relation_weight, evidence_ids=[item.evidence_id], properties=props)
        event_type = str(raw_payload.get("eventType") or "").strip()
        if event_type:
            event_type_id = add_entity(graph, "news-event-type", event_type, event_type, {
                "tboxClass": "NewsEventType",
                "eventType": event_type,
                "symbol": symbol,
                "materialityScore": raw_payload.get("materialityScore"),
            })
            add_relation(graph, event_id, event_type_id, "HAS_EVENT_TYPE", weight=relation_weight, evidence_ids=[item.evidence_id], properties=props)
            add_relation(graph, event_type_id, stock_id, "AFFECTS", weight=round(relation_weight * 0.7, 4), evidence_ids=[item.evidence_id], properties=props)
        ontology_relations = raw_payload.get("ontologyRelations") if isinstance(raw_payload.get("ontologyRelations"), list) else []
        for relation in ontology_relations[:5]:
            if not isinstance(relation, dict):
                continue
            relation_type = str(relation.get("type") or "").strip().upper()
            if not relation_type:
                continue
            add_relation(
                graph,
                event_id,
                stock_id,
                relation_type,
                weight=relation_weight,
                evidence_ids=[item.evidence_id],
                properties={**props, "newsOntologyRelation": dict(relation)},
            )
        topic_tags = raw_payload.get("topicTags") if isinstance(raw_payload.get("topicTags"), list) else []
        market_topics = raw_payload.get("marketTopics") if isinstance(raw_payload.get("marketTopics"), list) else []
        for topic in unique_list(list(topic_tags or []) + list(market_topics or []))[:8]:
            topic_id = add_entity(graph, "news-topic", str(topic), str(topic), {
                "tboxClass": "NewsTopic",
                "topic": str(topic),
                "symbol": symbol,
                "relationScope": relation_scope,
            })
            add_relation(graph, event_id, topic_id, "HAS_TOPIC", weight=relation_weight, evidence_ids=[item.evidence_id], properties=props)
            add_relation(graph, topic_id, stock_id, "AFFECTS", weight=round(relation_weight * 0.8, 4), evidence_ids=[item.evidence_id], properties=props)
        mentioned_peers = raw_payload.get("mentionedPeers") if isinstance(raw_payload.get("mentionedPeers"), list) else []
        for peer in unique_list(mentioned_peers or [])[:6]:
            peer_id = add_entity(graph, "peer-company", str(peer), str(peer), {
                "tboxClass": "PeerCompanyMention",
                "peerName": str(peer),
                "symbol": symbol,
            })
            add_relation(graph, event_id, peer_id, "MENTIONS_PEER", weight=relation_weight, evidence_ids=[item.evidence_id], properties=props)
            add_relation(graph, peer_id, stock_id, "AFFECTS", weight=round(relation_weight * 0.75, 4), evidence_ids=[item.evidence_id], properties=props)
        if thesis_id:
            add_relation(graph, event_id, thesis_id, "MATERIAL_TO", weight=round((number(item.impact_score) or 2) / 20, 4), evidence_ids=[item.evidence_id], properties=props)
        if active_opinion_id:
            add_relation(graph, event_id, active_opinion_id, "IMPACTS_OPINION", weight=round((number(item.impact_score) or 2) / 20, 4), evidence_ids=[item.evidence_id], properties=props)
        add_relation(graph, event_id, event_id, "DECAYS_AFTER", weight=1.0, evidence_ids=[item.evidence_id], properties={
            "source": "research-evidence",
            "decayPolicy": "materiality-decay",
            "defaultDays": 3 if item.kind in {"news", "market-move"} else 14,
            "aiInfluenceLabel": "이벤트 영향 시간 감쇠",
        })
