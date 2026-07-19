from typing import Dict, List

from .investment_research import research_evidence_from_external_signals, research_evidence_from_facts
from .market_data import number
from . import news_analysis as news_domain
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
        "sourceKind",
        "sourcePlatform",
        "entityLinks",
        "qualityGate",
        "analysisConflict",
        "analysisConflictSource",
        "analysisConflictExistingPolarity",
        "analysisConflictAiPolarity",
        "analysisConflictReasonKo",
        "dataQualityRisk",
        "dataQualityRiskScore",
    ]:
        if key in raw_payload:
            props[key] = raw_payload.get(key)
    ai_analysis = raw_payload.get("aiAnalysis") if isinstance(raw_payload.get("aiAnalysis"), dict) else {}
    if ai_analysis:
        props.update({
            "aiAnalysisVersion": ai_analysis.get("version"),
            "aiAnalysisModel": ai_analysis.get("model"),
            "aiImpactPolarity": ai_analysis.get("impactPolarity"),
            "aiImpactLabelKo": ai_analysis.get("impactLabelKo"),
            "aiImpactConfidence": ai_analysis.get("confidence"),
            "aiMaterialityScore": ai_analysis.get("materialityScore"),
            "aiNeedsReview": ai_analysis.get("needsReview"),
        })
    if "materialityPassed" not in props and raw_payload.get("materialityScore") is not None:
        props["materialityPassed"] = number(raw_payload.get("materialityScore")) >= 65
    if polarity == "risk":
        props["opinionImpact"] = min(18.0, max(4.0, impact))
    elif polarity == "support":
        props["supportImpact"] = min(14.0, max(3.0, impact))
    return props


def add_governed_claim_concepts(
    graph: PortfolioOntology,
    stock_id: str,
    item: object,
    raw_payload: Dict[str, object],
) -> None:
    governance = raw_payload.get("evidenceGovernance") if isinstance(raw_payload.get("evidenceGovernance"), dict) else {}
    if not governance.get("investmentJudgmentEligible"):
        return
    claim_key = str(governance.get("claimId") or "").strip()
    evidence_key = str(getattr(item, "evidence_id", "") or "").strip()
    if not claim_key or not evidence_key:
        return
    statement = str(getattr(item, "summary", "") or getattr(item, "title", "") or evidence_key)
    source = str(getattr(item, "source", "") or "unknown")
    document_id = add_entity(graph, "retrieved-document", evidence_key, statement, {
        "tboxClass": "RetrievedDocument",
        "source": source,
        "sourceUrl": getattr(item, "url", ""),
        "publishedAt": getattr(item, "published_at", ""),
        "observedAt": getattr(item, "observed_at", ""),
    })
    claim_id = add_entity(graph, "verified-claim", claim_key, statement, {
        "tboxClass": "VerifiedClaim",
        "verificationStatus": governance.get("verificationStatus"),
        "entityResolutionStatus": governance.get("entityResolutionStatus"),
        "confidence": getattr(item, "confidence", 0),
        "evidenceId": evidence_key,
        "checkedAt": governance.get("checkedAt"),
        "investmentJudgmentEligible": True,
    })
    assessment_id = add_entity(graph, "evidence-assessment", claim_key, "근거 품질 검증", {
        "tboxClass": "EvidenceAssessment",
        "verificationStatus": governance.get("verificationStatus"),
        "entityResolutionStatus": governance.get("entityResolutionStatus"),
        "confidence": getattr(item, "confidence", 0),
        "reasons": governance.get("reasons") or [],
        "sourcePolicy": governance.get("sourcePolicy"),
    })
    source_id = add_entity(graph, "research-source", source, source, {
        "tboxClass": "DataSource",
        "sourceUrl": getattr(item, "url", ""),
    })
    relation_props = {
        "source": "evidence-governance",
        "verificationStatus": governance.get("verificationStatus"),
        "investmentJudgmentEligible": True,
    }
    add_relation(graph, document_id, source_id, "RETRIEVED_FROM", weight=1.0, evidence_ids=[evidence_key], properties=relation_props)
    add_relation(graph, document_id, claim_id, "ASSERTS", weight=1.0, evidence_ids=[evidence_key], properties=relation_props)
    add_relation(graph, claim_id, stock_id, "RESOLVES_TO", weight=1.0, evidence_ids=[evidence_key], properties=relation_props)
    add_relation(graph, claim_id, assessment_id, "VERIFIED_BY", weight=1.0, evidence_ids=[evidence_key], properties=relation_props)


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


def add_news_ai_analysis_concept(
    graph: PortfolioOntology,
    stock_id: str,
    event_id: str,
    item: object,
    props: Dict[str, object],
    relation_weight: float,
) -> None:
    raw_payload = getattr(item, "raw_payload", {}) if isinstance(getattr(item, "raw_payload", {}), dict) else {}
    analysis = raw_payload.get("aiAnalysis") if isinstance(raw_payload.get("aiAnalysis"), dict) else {}
    if not analysis:
        return
    summary = analysis.get("summary") if isinstance(analysis.get("summary"), dict) else {}
    evidence_id = str(getattr(item, "evidence_id", "") or "")
    analysis_id = add_entity(graph, "article-ai-analysis", evidence_id or str(getattr(item, "title", "") or ""), "기사 AI 분석: " + str(getattr(item, "title", "") or ""), {
        "tboxClass": "ArticleAIAnalysis",
        "tboxClasses": ["Observation", "ExternalObservation", "ExternalSignal", "NewsEvent", "ArticleAIAnalysis", "DataQuality"],
        "symbol": str(getattr(item, "symbol", "") or ""),
        "sourceEvidenceId": evidence_id,
        "version": analysis.get("version"),
        "promptVersion": analysis.get("promptVersion"),
        "model": analysis.get("model"),
        "status": analysis.get("status"),
        "readScope": analysis.get("readScope"),
        "sourceTextHash": analysis.get("sourceTextHash"),
        "relationScope": analysis.get("relationScope"),
        "eventType": analysis.get("eventType"),
        "impactPolarity": analysis.get("impactPolarity"),
        "impactLabelKo": analysis.get("impactLabelKo"),
        "confidence": analysis.get("confidence"),
        "materialityScore": analysis.get("materialityScore"),
        "relevanceScore": analysis.get("relevanceScore"),
        "oneLineKo": summary.get("oneLineKo"),
        "briefKo": summary.get("briefKo"),
        "keyTakeaways": summary.get("keyTakeaways"),
        "whyItMatters": summary.get("whyItMatters"),
        "watchPoints": summary.get("watchPoints"),
        "riskSignals": analysis.get("riskSignals"),
        "supportSignals": analysis.get("supportSignals"),
        "contrastSignals": analysis.get("contrastSignals"),
        "keyNumbers": analysis.get("keyNumbers"),
        "rationaleKo": analysis.get("rationaleKo"),
        "needsReview": analysis.get("needsReview"),
        "reasoningLimitations": analysis.get("reasoningLimitations"),
    })
    add_relation(graph, event_id, analysis_id, "HAS_ANALYSIS", weight=relation_weight, evidence_ids=[evidence_id], properties={**props, "source": "article-ai-analysis"})
    add_relation(graph, analysis_id, event_id, "EXPLAINS", weight=relation_weight, evidence_ids=[evidence_id], properties={**props, "source": "article-ai-analysis"})
    add_relation(graph, analysis_id, stock_id, "AFFECTS", weight=relation_weight, evidence_ids=[evidence_id], properties={**props, "source": "article-ai-analysis"})
    if raw_payload.get("analysisConflict"):
        risk_score = number(raw_payload.get("dataQualityRiskScore")) or 7.0
        conflict_id = add_entity(graph, "article-analysis-conflict", evidence_id or str(getattr(item, "title", "") or ""), "뉴스 영향 분석 충돌: " + str(getattr(item, "title", "") or ""), {
            "tboxClass": "DataQualityRisk",
            "tboxClasses": ["Risk", "DataQualityRisk", "ArticleAIAnalysis", "DataQualitySignal"],
            "symbol": str(getattr(item, "symbol", "") or ""),
            "sourceEvidenceId": evidence_id,
            "dataScope": "news-analysis-conflict",
            "riskImpact": risk_score,
            "opinionImpact": risk_score,
            "analysisConflictSource": raw_payload.get("analysisConflictSource"),
            "analysisConflictExistingPolarity": raw_payload.get("analysisConflictExistingPolarity"),
            "analysisConflictAiPolarity": raw_payload.get("analysisConflictAiPolarity"),
            "analysisConflictReasonKo": raw_payload.get("analysisConflictReasonKo"),
            "dataQualityRisk": raw_payload.get("dataQualityRisk"),
        })
        conflict_props = {
            **props,
            "source": "article-ai-analysis-conflict",
            "dataScope": "news-analysis-conflict",
            "riskImpact": risk_score,
            "opinionImpact": risk_score,
            "aiInfluenceLabel": raw_payload.get("analysisConflictReasonKo") or "뉴스 영향 분석 충돌",
        }
        add_relation(graph, stock_id, conflict_id, "HAS_DATA_QUALITY", weight=round(max(0.42, relation_weight), 4), evidence_ids=[evidence_id], properties=conflict_props)
        add_relation(graph, analysis_id, conflict_id, "HAS_DATA_QUALITY", weight=round(max(0.42, relation_weight), 4), evidence_ids=[evidence_id], properties=conflict_props)


def add_news_quality_risk_concepts(
    graph: PortfolioOntology,
    stock_id: str,
    event_id: str,
    item: object,
    props: Dict[str, object],
    relation_weight: float,
) -> None:
    if str(getattr(item, "kind", "") or "").lower() != "news":
        return
    raw_payload = getattr(item, "raw_payload", {}) if isinstance(getattr(item, "raw_payload", {}), dict) else {}
    analysis = raw_payload.get("aiAnalysis") if isinstance(raw_payload.get("aiAnalysis"), dict) else {}
    read_scope = str(analysis.get("readScope") or raw_payload.get("readScope") or "").strip()
    relation_scope = str(raw_payload.get("relationScope") or analysis.get("relationScope") or "").strip()
    direct_mention = raw_payload.get("directMention")
    needs_review = bool(analysis.get("needsReview") or raw_payload.get("needsReview"))
    reasons = []
    if read_scope in {"title+rss-summary", "title-only", "rss-summary"}:
        reasons.append("article-body-missing")
    if relation_scope == "direct" and direct_mention is False:
        reasons.append("direct-subject-unconfirmed")
    if needs_review:
        reasons.append("analysis-needs-review")
    if not reasons:
        return
    evidence_id = str(getattr(item, "evidence_id", "") or "")
    risk_score = min(9.0, 3.0 + len(reasons) * 1.6)
    quality_id = add_entity(graph, "article-quality-risk", evidence_id or str(getattr(item, "title", "") or ""), "기사 근거 품질 제한: " + str(getattr(item, "title", "") or ""), {
        "tboxClass": "ArticleQualityRisk",
        "tboxClasses": ["Risk", "DataQualityRisk", "ArticleQualityRisk", "NewsEvent", "DataQualitySignal"],
        "symbol": str(getattr(item, "symbol", "") or ""),
        "sourceEvidenceId": evidence_id,
        "dataScope": "news-quality",
        "relationScope": relation_scope,
        "readScope": read_scope,
        "directMention": direct_mention,
        "needsReview": needs_review,
        "qualityReasons": reasons,
        "riskImpact": risk_score,
        "opinionImpact": risk_score,
    })
    quality_props = {
        **props,
        "source": "news-quality-gate",
        "polarity": "risk",
        "dataScope": "news-quality",
        "scope": "news-quality",
        "riskImpact": risk_score,
        "opinionImpact": risk_score,
        "aiInfluenceLabel": "뉴스 근거 품질 제한: " + ", ".join(reasons),
    }
    add_relation(graph, stock_id, quality_id, "HAS_DATA_QUALITY", weight=round(max(0.42, relation_weight), 4), evidence_ids=[evidence_id], properties=quality_props)
    add_relation(graph, event_id, quality_id, "HAS_DATA_QUALITY", weight=round(max(0.42, relation_weight), 4), evidence_ids=[evidence_id], properties=quality_props)


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
        if item.kind == "news" and not news_domain.relation_scope_is_investable(relation_scope):
            continue
        materiality_passed = raw_payload.get("materialityPassed") if "materialityPassed" in raw_payload else None
        if materiality_passed is None and raw_payload.get("materialityScore") is not None:
            materiality_passed = number(raw_payload.get("materialityScore")) >= 65
        scope_weight = {
            "direct": 1.0,
            "related_product": 0.7,
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
            "aiAnalysisVersion": (raw_payload.get("aiAnalysis") or {}).get("version") if isinstance(raw_payload.get("aiAnalysis"), dict) else None,
            "aiImpactPolarity": (raw_payload.get("aiAnalysis") or {}).get("impactPolarity") if isinstance(raw_payload.get("aiAnalysis"), dict) else None,
            "aiImpactLabelKo": (raw_payload.get("aiAnalysis") or {}).get("impactLabelKo") if isinstance(raw_payload.get("aiAnalysis"), dict) else None,
            "articleSummaryKo": raw_payload.get("articleSummaryKo"),
            "sourceKind": raw_payload.get("sourceKind"),
            "sourcePlatform": raw_payload.get("sourcePlatform"),
            "qualityGate": raw_payload.get("qualityGate"),
            "evidenceGovernance": raw_payload.get("evidenceGovernance"),
            "investmentJudgmentEligible": bool((raw_payload.get("evidenceGovernance") or {}).get("investmentJudgmentEligible")) if isinstance(raw_payload.get("evidenceGovernance"), dict) else False,
        })
        add_governed_claim_concepts(graph, stock_id, item, raw_payload)
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
        add_news_ai_analysis_concept(graph, stock_id, event_id, item, props, relation_weight)
        add_news_quality_risk_concepts(graph, stock_id, event_id, item, props, relation_weight)
        if relation_scope in {"related_product", "peer", "sector", "market"}:
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
