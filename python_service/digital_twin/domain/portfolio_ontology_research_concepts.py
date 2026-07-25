from typing import Dict, List

from .investment_research import research_evidence_from_external_signals, research_evidence_from_facts
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
    raw_payload = getattr(item, "raw_payload", {}) if isinstance(getattr(item, "raw_payload", {}), dict) else {}
    state = research_evidence_state(item, raw_payload)
    props = {
        "source": "research-evidence",
        "polarity": polarity,
        **state,
        "aiInfluenceLabel": str(getattr(item, "title", "") or getattr(item, "kind", "") or "리서치 근거"),
    }
    for key in [
        "relationScope",
        "directMention",
        "matchedAliases",
        "mentionedPeers",
        "topicTags",
        "marketTopics",
        "eventType",
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
            "aiNeedsReview": ai_analysis.get("needsReview"),
        })
    return props


def research_evidence_state(item: object, raw_payload: Dict[str, object]) -> Dict[str, object]:
    governance = raw_payload.get("evidenceGovernance") if isinstance(raw_payload.get("evidenceGovernance"), dict) else {}
    eligible = bool(governance.get("investmentJudgmentEligible"))
    relation_scope = str(raw_payload.get("relationScope") or "").strip().lower()
    analysis = raw_payload.get("aiAnalysis") if isinstance(raw_payload.get("aiAnalysis"), dict) else {}
    read_scope = str(
        raw_payload.get("articleReadStatus")
        or analysis.get("readScope")
        or raw_payload.get("readScope")
        or ""
    ).strip().lower()
    body_read = read_scope in {"body", "full-body", "full", "article-body"}
    article_facts = raw_payload.get("articleFacts") if isinstance(raw_payload.get("articleFacts"), dict) else {}
    body_read = body_read and article_facts.get("bodyQualityPassed") is not False and raw_payload.get("bodyQualityPassed") is not False
    polarity = str(getattr(item, "polarity", "") or "context").strip().lower()
    evidence_role = "risk" if polarity in {"risk", "negative", "bearish"} else "support" if polarity in {"support", "positive", "bullish"} else "context"
    if not eligible:
        data_state, review_level, validation_state = "insufficient", "blocked", "blocked"
        evidence_role = "blocking"
    elif body_read and relation_scope == "direct":
        data_state, review_level, validation_state = "sufficient", "check", "ready"
    else:
        data_state, review_level, validation_state = "partial", "observe", "conditional"
    materiality_passed = raw_payload.get("materialityPassed")
    if materiality_passed is None:
        materiality_passed = bool(eligible and relation_scope == "direct" and (body_read or raw_payload.get("eventType")))
    news_states = news_domain.news_state_payload(raw_payload)
    return {
        "evidenceRole": evidence_role,
        "reviewLevel": review_level,
        "dataState": data_state,
        "validationState": validation_state,
        "relationScope": relation_scope or "unknown",
        "bodyRead": body_read,
        "materialityPassed": bool(materiality_passed),
        "relevanceState": news_states["relevanceState"],
        "sourceTrustState": news_states["sourceTrustState"],
        "materialityState": news_states["materialityState"],
    }


def add_governed_claim_concepts(
    graph: PortfolioOntology,
    stock_id: str,
    item: object,
    raw_payload: Dict[str, object],
) -> None:
    governance = raw_payload.get("evidenceGovernance") if isinstance(raw_payload.get("evidenceGovernance"), dict) else {}
    evidence_key = str(getattr(item, "evidence_id", "") or "").strip()
    if not evidence_key:
        return
    source = str(getattr(item, "source", "") or "unknown")
    document_label = str(getattr(item, "title", "") or getattr(item, "summary", "") or evidence_key)
    document_id = add_entity(graph, "retrieved-document", evidence_key, document_label, {
        "tboxClass": "RetrievedDocument",
        "source": source,
        "sourceUrl": getattr(item, "url", ""),
        "publishedAt": getattr(item, "published_at", ""),
        "observedAt": getattr(item, "observed_at", ""),
    })
    source_id = add_entity(graph, "research-source", source, source, {
        "tboxClass": "DataSource",
        "sourceUrl": getattr(item, "url", ""),
    })
    ledger = raw_payload.get("claimLedger") if isinstance(raw_payload.get("claimLedger"), dict) else {}
    claims = [dict(row) for row in ledger.get("claims") or [] if isinstance(row, dict)]
    if not claims and governance.get("claimId"):
        claims = [{
            "claimId": governance.get("claimId"),
            "statement": str(getattr(item, "summary", "") or getattr(item, "title", "") or evidence_key),
            "state": governance.get("claimState") or governance.get("verificationStatus") or "reported",
            "verificationStatus": governance.get("verificationStatus"),
            "entityResolutionStatus": governance.get("entityResolutionStatus"),
            "investmentJudgmentEligible": governance.get("investmentJudgmentEligible"),
            "reasons": governance.get("reasons") or [],
            "sourceOrigin": governance.get("sourceOrigin"),
        }]
    base_props = {
        "source": "evidence-governance",
        "sourceEvidenceId": evidence_key,
        "sourcePolicy": governance.get("sourcePolicy"),
    }
    add_relation(graph, document_id, source_id, "RETRIEVED_FROM", weight=1.0, evidence_ids=[evidence_key], properties=base_props)
    for claim in claims:
        claim_key = str(claim.get("claimId") or "").strip()
        if not claim_key:
            continue
        statement = str(claim.get("statement") or getattr(item, "summary", "") or getattr(item, "title", "") or evidence_key)
        state = str(claim.get("state") or "reported")
        eligible = bool(claim.get("investmentJudgmentEligible"))
        tbox_class = "VerifiedClaim" if eligible else "DisputedClaim" if state == "conflicted" else "RejectedClaim" if state in {"rejected", "expired", "superseded"} else "ExtractedClaim"
        claim_id = add_entity(graph, "verified-claim", claim_key, statement, {
            "tboxClass": tbox_class,
            "verificationStatus": claim.get("verificationStatus"),
            "claimState": state,
            "entityResolutionStatus": claim.get("entityResolutionStatus"),
            "sourceTrustState": claim.get("sourceTrustState") or governance.get("sourceTrustState"),
            "sourceOrigin": claim.get("sourceOrigin") or governance.get("sourceOrigin"),
            "independentSourceCount": claim.get("independentSourceCount") or 0,
            "officialEvidenceIds": claim.get("officialEvidenceIds") or [],
            "corroboratingEvidenceIds": claim.get("corroboratingEvidenceIds") or [],
            "conflictingEvidenceIds": claim.get("conflictingEvidenceIds") or [],
            "supersededByEvidenceId": claim.get("supersededByEvidenceId") or "",
            "validationState": "ready" if eligible else "blocked" if state in {"conflicted", "superseded", "rejected", "expired"} else "conditional",
            "dataState": "sufficient" if eligible else "partial",
            "evidenceId": evidence_key,
            "excerpt": claim.get("excerpt") or statement,
            "excerptIndex": claim.get("excerptIndex"),
            "excerptStart": claim.get("excerptStart"),
            "excerptEnd": claim.get("excerptEnd"),
            "checkedAt": governance.get("checkedAt"),
            "investmentJudgmentEligible": eligible,
        })
        assessment_id = add_entity(graph, "evidence-assessment", claim_key, "근거 품질 검증", {
            "tboxClass": "EvidenceAssessment",
            "verificationStatus": claim.get("verificationStatus"),
            "claimState": state,
            "entityResolutionStatus": claim.get("entityResolutionStatus"),
            "sourceTrustState": claim.get("sourceTrustState") or governance.get("sourceTrustState"),
            "independentSourceCount": claim.get("independentSourceCount") or 0,
            "validationState": "ready" if eligible else "conditional",
            "dataState": "sufficient" if eligible else "partial",
            "reasons": claim.get("reasons") or [],
            "sourcePolicy": governance.get("sourcePolicy"),
        })
        relation_props = {
            **base_props,
            "verificationStatus": claim.get("verificationStatus"),
            "claimState": state,
            "investmentJudgmentEligible": eligible,
        }
        add_relation(graph, document_id, claim_id, "ASSERTS", weight=1.0, evidence_ids=[evidence_key], properties=relation_props)
        add_relation(graph, claim_id, stock_id, "RESOLVES_TO", weight=1.0, evidence_ids=[evidence_key], properties=relation_props)
        add_relation(graph, claim_id, assessment_id, "VERIFIED_BY", weight=1.0, evidence_ids=[evidence_key], properties=relation_props)
        for corroborating_id in claim.get("corroboratingEvidenceIds") or []:
            peer_id = add_entity(graph, "retrieved-document", str(corroborating_id), str(corroborating_id), {
                "tboxClass": "RetrievedDocument",
                "sourceEvidenceId": str(corroborating_id),
            })
            add_relation(graph, claim_id, peer_id, "CORROBORATED_BY", weight=1.0, evidence_ids=[evidence_key, str(corroborating_id)], properties=relation_props)
        for official_id in claim.get("officialEvidenceIds") or []:
            official_document_id = add_entity(graph, "retrieved-document", str(official_id), str(official_id), {
                "tboxClass": "RetrievedDocument",
                "sourceEvidenceId": str(official_id),
                "documentRole": "official-verification",
            })
            add_relation(graph, claim_id, official_document_id, "OFFICIALLY_VERIFIED_BY", weight=1.0, evidence_ids=[evidence_key, str(official_id)], properties=relation_props)
        for superseded_claim_id in claim.get("supersedesClaimIds") or []:
            previous_id = add_entity(graph, "verified-claim", str(superseded_claim_id), str(superseded_claim_id), {
                "tboxClass": "ExtractedClaim",
                "claimState": "superseded",
            })
            add_relation(graph, claim_id, previous_id, "SUPERSEDES", weight=1.0, evidence_ids=[evidence_key], properties=relation_props)


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
        **research_evidence_state(item, raw_payload),
        "eventType": raw_payload.get("eventType"),
    })
    source_label = str(getattr(item, "source", "") or "ResearchEvidence").strip() or "ResearchEvidence"
    source_id = add_entity(graph, "data-source", source_label, source_label, {
        "tboxClass": "DataSource",
        "tboxClasses": ["DataSource", "Provenance"],
        "documentType": str(shape["documentType"]),
    })
    add_relation(graph, stock_id, document_id, "HAS_OBSERVATION", weight=1.0, evidence_ids=[evidence_id], properties=props)
    add_relation(graph, stock_id, document_id, "HAS_EXTERNAL_SIGNAL", weight=1.0, evidence_ids=[evidence_id], properties=props)
    add_relation(graph, document_id, stock_id, "MENTIONS_INSTRUMENT", weight=1.0, evidence_ids=[evidence_id], properties=props)
    add_relation(graph, event_id, document_id, "HAS_PROVENANCE", weight=1.0, evidence_ids=[evidence_id], properties={**props, "source": "research-document"})
    add_relation(graph, document_id, source_id, "HAS_PROVENANCE", weight=1.0, evidence_ids=[evidence_id], properties={**props, "source": "research-document-source"})
    if thesis_id:
        add_relation(graph, document_id, thesis_id, "MATERIAL_TO", weight=1.0, evidence_ids=[evidence_id], properties=props)
    if active_opinion_id:
        add_relation(graph, document_id, active_opinion_id, "IMPACTS_OPINION", weight=1.0, evidence_ids=[evidence_id], properties=props)


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
        **research_evidence_state(item, raw_payload),
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
        source_as_of = str(getattr(item, "published_at", "") or getattr(item, "observed_at", "") or "")
        source_fetched_at = str(getattr(item, "observed_at", "") or source_as_of)
        conflict_id = add_entity(graph, "article-analysis-conflict", evidence_id or str(getattr(item, "title", "") or ""), "뉴스 영향 분석 충돌: " + str(getattr(item, "title", "") or ""), {
            "tboxClass": "DataQualityRisk",
            "tboxClasses": ["Risk", "DataQualityRisk", "ArticleAIAnalysis", "DataQualitySignal"],
            "symbol": str(getattr(item, "symbol", "") or ""),
            "sourceEvidenceId": evidence_id,
            "dataScope": "news-analysis-conflict",
            "evidenceRole": "blocking",
            "reviewLevel": "blocked",
            "dataState": "insufficient",
            "validationState": "blocked",
            "analysisConflictSource": raw_payload.get("analysisConflictSource"),
            "analysisConflictExistingPolarity": raw_payload.get("analysisConflictExistingPolarity"),
            "analysisConflictAiPolarity": raw_payload.get("analysisConflictAiPolarity"),
            "analysisConflictReasonKo": raw_payload.get("analysisConflictReasonKo"),
            "dataQualityRisk": raw_payload.get("dataQualityRisk"),
            "observationDomain": "news",
            "freshnessRequired": True,
            "freshnessStatus": "unknown",
            "sourceAsOf": source_as_of,
            "sourceFetchedAt": source_fetched_at,
            "sourceTimestampPresent": bool(source_as_of),
            "maxAgeMinutes": 180,
        })
        conflict_props = {
            **props,
            "source": "article-ai-analysis-conflict",
            "dataScope": "news-analysis-conflict",
            "polarity": "blocking",
            "evidenceRole": "blocking",
            "reviewLevel": "blocked",
            "dataState": "insufficient",
            "validationState": "blocked",
            "aiInfluenceLabel": raw_payload.get("analysisConflictReasonKo") or "뉴스 영향 분석 충돌",
        }
        add_relation(graph, stock_id, conflict_id, "HAS_DATA_QUALITY", weight=1.0, evidence_ids=[evidence_id], properties=conflict_props)
        add_relation(graph, analysis_id, conflict_id, "HAS_DATA_QUALITY", weight=1.0, evidence_ids=[evidence_id], properties=conflict_props)


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
        "evidenceRole": "blocking",
        "reviewLevel": "blocked",
        "dataState": "insufficient",
        "validationState": "blocked",
    })
    quality_props = {
        **props,
        "source": "news-quality-gate",
        "polarity": "blocking",
        "evidenceRole": "blocking",
        "reviewLevel": "blocked",
        "dataState": "insufficient",
        "validationState": "blocked",
        "dataScope": "news-quality",
        "scope": "news-quality",
        "aiInfluenceLabel": "뉴스 근거 품질 제한: " + ", ".join(reasons),
    }
    add_relation(graph, stock_id, quality_id, "HAS_DATA_QUALITY", weight=1.0, evidence_ids=[evidence_id], properties=quality_props)
    add_relation(graph, event_id, quality_id, "HAS_DATA_QUALITY", weight=1.0, evidence_ids=[evidence_id], properties=quality_props)


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
        evidence_state = research_evidence_state(item, raw_payload)
        materiality_passed = bool(evidence_state.get("materialityPassed"))
        relation_weight = 1.0
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
            **evidence_state,
            "matchedAliases": raw_payload.get("matchedAliases"),
            "mentionedPeers": raw_payload.get("mentionedPeers"),
            "topicTags": raw_payload.get("topicTags"),
            "marketTopics": raw_payload.get("marketTopics"),
            "eventType": raw_payload.get("eventType"),
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
            "risk" if str(item.polarity or "").lower() in {"negative", "risk", "bearish"} else "support" if str(item.polarity or "").lower() in {"positive", "support", "bullish"} else "context",
            "sufficient" if bool((raw_payload.get("evidenceGovernance") or {}).get("investmentJudgmentEligible")) else "partial",
        ))
        props = event_relation_properties(item)
        add_relation(graph, stock_id, event_id, "HAS_OBSERVATION", weight=1.0, evidence_ids=[item.evidence_id], properties=props)
        add_relation(graph, stock_id, event_id, "HAS_EXTERNAL_SIGNAL", weight=1.0, evidence_ids=[item.evidence_id], properties=props)
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
                "materialityPassed": materiality_passed,
                "reviewLevel": evidence_state.get("reviewLevel"),
                "dataState": evidence_state.get("dataState"),
            })
            add_relation(graph, event_id, event_type_id, "HAS_EVENT_TYPE", weight=relation_weight, evidence_ids=[item.evidence_id], properties=props)
            add_relation(graph, event_type_id, stock_id, "AFFECTS", weight=1.0, evidence_ids=[item.evidence_id], properties=props)
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
            add_relation(graph, topic_id, stock_id, "AFFECTS", weight=1.0, evidence_ids=[item.evidence_id], properties=props)
        mentioned_peers = raw_payload.get("mentionedPeers") if isinstance(raw_payload.get("mentionedPeers"), list) else []
        for peer in unique_list(mentioned_peers or [])[:6]:
            peer_id = add_entity(graph, "peer-company", str(peer), str(peer), {
                "tboxClass": "PeerCompanyMention",
                "peerName": str(peer),
                "symbol": symbol,
            })
            add_relation(graph, event_id, peer_id, "MENTIONS_PEER", weight=relation_weight, evidence_ids=[item.evidence_id], properties=props)
            add_relation(graph, peer_id, stock_id, "AFFECTS", weight=1.0, evidence_ids=[item.evidence_id], properties=props)
        if thesis_id:
            add_relation(graph, event_id, thesis_id, "MATERIAL_TO", weight=1.0, evidence_ids=[item.evidence_id], properties=props)
        if active_opinion_id:
            add_relation(graph, event_id, active_opinion_id, "IMPACTS_OPINION", weight=1.0, evidence_ids=[item.evidence_id], properties=props)
        add_relation(graph, event_id, event_id, "DECAYS_AFTER", weight=1.0, evidence_ids=[item.evidence_id], properties={
            "source": "research-evidence",
            "decayPolicy": "materiality-decay",
            "defaultDays": 3 if item.kind in {"news", "market-move"} else 14,
            "aiInfluenceLabel": "이벤트 영향 시간 감쇠",
        })
