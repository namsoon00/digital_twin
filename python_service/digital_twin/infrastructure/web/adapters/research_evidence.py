"""Web research evidence boundary."""

from digital_twin.infrastructure import operational_store as stores
from digital_twin.infrastructure.settings import runtime_settings
from digital_twin.infrastructure.stale_read_model import StaleReadModelCache
from digital_twin.infrastructure.web.cache import cached_api_payload
from digital_twin.infrastructure.web.common import configured
from digital_twin.infrastructure.web.common import first_query
from digital_twin.infrastructure.web.common import request_bool
from digital_twin.infrastructure.web.events import RealtimeEventBridge
from digital_twin.infrastructure.web.events import new_domain_event
from digital_twin.modules.decisions.domain.prompt_evidence_admission import assess_prompt_evidence
from digital_twin.modules.news_intelligence.domain.integration_events import research_evidence_lifecycle_events
from digital_twin.modules.news_intelligence.domain.investment_evidence_governance import claim_quality_summary
from digital_twin.modules.news_intelligence.domain.investment_research import NewsCollectionTarget
from digital_twin.modules.news_intelligence.domain.news_ai_analysis import apply_news_ai_analysis
from digital_twin.modules.news_intelligence.domain.news_ai_analysis import has_mojibake
from digital_twin.modules.news_intelligence.domain.news_ai_analysis import local_news_ai_analysis
from digital_twin.modules.news_intelligence.domain.news_ai_analysis import news_ai_analysis_is_current
from digital_twin.modules.news_intelligence.public import ResearchEvidenceGovernanceService
from digital_twin.modules.news_intelligence.public import evidence_eligibility
from digital_twin.modules.news_intelligence.public import build_information_brief
from digital_twin.platform.domain.event_types import APP_ITEM_REMOVED
from digital_twin.platform.domain.event_types import APP_ITEM_UPDATED
from typing import Dict
from typing import List
import urllib.error
import urllib.parse
import urllib.request


RESEARCH_EVIDENCE_SUMMARY_READ_MODEL = StaleReadModelCache(
    "research-evidence-summary",
    ttl_seconds=90,
    retry_cooldown_seconds=20,
)


RESEARCH_EVIDENCE_PAGE_READ_MODEL = StaleReadModelCache(
    "research-evidence-page",
    ttl_seconds=15,
    retry_cooldown_seconds=5,
)


def _research_evidence_summary_source_payload() -> Dict[str, object]:
    store = stores.research_evidence_store()
    analysis_rows = list(store.latest(kind="news", limit=500) or []) if hasattr(store, "latest") else []
    official_rows = []
    if hasattr(store, "latest"):
        for official_kind in ["disclosure", "filing", "sec-filing"]:
            official_rows.extend(list(store.latest(kind=official_kind, limit=500) or []))
    return {
        "status": "ok",
        "summary": store.summary(),
        "articleAnalysis": research_evidence_article_analysis_summary(analysis_rows),
        "officialAnalysis": research_evidence_official_analysis_summary(official_rows),
    }


def research_evidence_payload(query: Dict[str, List[str]]) -> Dict[str, object]:
    limit = max(1, min(100, int(first_query(query, "limit") or 8)))
    offset = max(0, int(first_query(query, "offset") or 0))
    symbol = configured(first_query(query, "symbol")).upper()
    kind = configured(first_query(query, "kind"))
    search = configured(first_query(query, "query") or first_query(query, "q"))
    page_key = "|".join([symbol or "all", kind or "all", search or "all", str(limit), str(offset)])

    def load_page() -> Dict[str, object]:
        store = stores.research_evidence_store()
        items, total = store.latest_page(
            symbol=symbol,
            kind=kind,
            limit=limit,
            offset=offset,
            query=search,
        )
        return {
            "items": [research_evidence_list_payload(item) for item in items],
            "claimQuality": claim_quality_summary(items),
            "total": total,
        }

    page = cached_api_payload(
        RESEARCH_EVIDENCE_PAGE_READ_MODEL,
        page_key,
        load_page,
        force=request_bool(first_query(query, "refresh"), False),
    )
    items = list(page.get("items") or [])
    total = int(page.get("total") or 0)
    summaries = cached_api_payload(
        RESEARCH_EVIDENCE_SUMMARY_READ_MODEL,
        "all",
        _research_evidence_summary_source_payload,
        force=request_bool(first_query(query, "refreshSummary"), False),
    )
    return {
        "items": items,
        "summary": summaries.get("summary") or {"total": total},
        "claimQuality": page.get("claimQuality") or {},
        "articleAnalysis": summaries.get("articleAnalysis") or {},
        "officialAnalysis": summaries.get("officialAnalysis") or {},
        "readCache": {
            "page": page.get("readCache") or {},
            "summary": summaries.get("readCache") or {},
        },
        "symbol": symbol,
        "kind": kind,
        "limit": limit,
        "offset": offset,
        "total": total,
        "query": search,
    }


def research_evidence_official_analysis_summary(items) -> Dict[str, object]:
    rows = [
        item for item in items or []
        if str(getattr(item, "kind", "") or "").lower() in {"disclosure", "filing", "sec-filing"}
    ]
    counts = {
        "officialCount": len(rows),
        "metadataOnlyCount": 0,
        "documentVerifiedCount": 0,
        "analysisReadyCount": 0,
        "alertEligibleCount": 0,
        "promptEligibleCount": 0,
        "needsReviewCount": 0,
        "providerCounts": {},
        "issueCounts": {},
    }
    for item in rows:
        raw = item.raw_payload if isinstance(getattr(item, "raw_payload", None), dict) else {}
        quality = raw.get("disclosureDocumentQuality") if isinstance(raw.get("disclosureDocumentQuality"), dict) else {}
        analysis = raw.get("disclosureAnalysis") if isinstance(raw.get("disclosureAnalysis"), dict) else {}
        admission = assess_prompt_evidence(
            raw,
            kind=item.kind,
            published_at=item.published_at,
            observed_at=item.observed_at,
        ).to_dict()
        counts["metadataOnlyCount"] += int(str(raw.get("officialDocumentState") or "") == "metadata-only")
        counts["documentVerifiedCount"] += int(bool(raw.get("documentVerified")))
        counts["analysisReadyCount"] += int(bool(raw.get("analysisReady")))
        counts["alertEligibleCount"] += int(bool(admission.get("alertEligible")))
        counts["promptEligibleCount"] += int(bool(admission.get("promptEligible")))
        counts["needsReviewCount"] += int(bool(analysis.get("needsReview")))
        provider = str(item.source or "unknown")
        counts["providerCounts"][provider] = int(counts["providerCounts"].get(provider) or 0) + 1
        for issue in quality.get("issues") or []:
            key = str(issue or "unknown")
            counts["issueCounts"][key] = int(counts["issueCounts"].get(key) or 0) + 1
    counts["providerCounts"] = dict(sorted(counts["providerCounts"].items()))
    counts["issueCounts"] = dict(sorted(counts["issueCounts"].items()))
    return counts


def research_evidence_article_analysis_summary(items) -> Dict[str, object]:
    """Expose a bounded, display-only quality funnel for retained news."""
    rows = [item for item in items or [] if str(getattr(item, "kind", "")) == "news"]
    counts = {
        "newsCount": len(rows),
        "bodyReadCount": 0,
        "translationCompleteCount": 0,
        "translationPendingCount": 0,
        "translationUnavailableCount": 0,
        "summaryReadyCount": 0,
        "summaryNeedsReviewCount": 0,
        "summaryBlockedCount": 0,
        "analysisFallbackCount": 0,
        "analysisDeferredCount": 0,
        "displayEligibleCount": 0,
        "alertEligibleCount": 0,
        "reasoningEligibleCount": 0,
        "promptEligibleCount": 0,
        "decisionEligibleCount": 0,
        "stalePromptBlockedCount": 0,
        "provenanceCompleteCount": 0,
        "unresolvedPublisherCount": 0,
        "duplicatePublicationCount": 0,
        "independentConfirmationCount": 0,
        "sameStoryCount": 0,
        "followUpCount": 0,
        "contentInvalidReviewCount": 0,
        "bodyQualityBlockedCount": 0,
        "eventClusterCount": 0,
        "publisherTierCounts": {},
        "contentTypeCounts": {},
        "distributionChannelCounts": {},
    }
    event_clusters = set()
    for item in rows:
        eligibility = evidence_eligibility(item)
        counts["displayEligibleCount"] += int(bool(eligibility.get("displayEligible")))
        counts["alertEligibleCount"] += int(bool(eligibility.get("alertEligible")))
        counts["reasoningEligibleCount"] += int(bool(eligibility.get("reasoningEligible")))
        raw = item.raw_payload if isinstance(getattr(item, "raw_payload", None), dict) else {}
        prompt_admission = assess_prompt_evidence(
            raw,
            kind=getattr(item, "kind", "news"),
            published_at=getattr(item, "published_at", ""),
            observed_at=getattr(item, "observed_at", ""),
        ).to_dict()
        counts["promptEligibleCount"] += int(bool(prompt_admission.get("promptEligible")))
        counts["decisionEligibleCount"] += int(bool(prompt_admission.get("decisionEligible")))
        counts["stalePromptBlockedCount"] += int(
            "evidence-stale" in list(prompt_admission.get("reasonCodes") or [])
        )
        source_identity = eligibility.get("sourceIdentity") if isinstance(eligibility.get("sourceIdentity"), dict) else {}
        provenance = raw.get("sourceProvenance") if isinstance(raw.get("sourceProvenance"), dict) else {}
        original = provenance.get("originalPublisher") if isinstance(provenance.get("originalPublisher"), dict) else {}
        counts["provenanceCompleteCount"] += int(bool(provenance.get("provenanceComplete")))
        counts["unresolvedPublisherCount"] += int(not source_identity.get("publisherId") or source_identity.get("publisherId") == "unknown")
        relationship = str(provenance.get("evidenceRelationship") or raw.get("evidenceRelationship") or "")
        counts["duplicatePublicationCount"] += int(relationship in {"exact-duplicate", "syndicated-copy"})
        counts["independentConfirmationCount"] += int(relationship == "independent-confirmation")
        counts["sameStoryCount"] += int(relationship == "same-story")
        counts["followUpCount"] += int(relationship == "follow-up")
        cluster_id = str(raw.get("storyClusterId") or "")
        if cluster_id:
            event_clusters.add(cluster_id)
        counts["contentInvalidReviewCount"] += int(str(eligibility.get("reviewState") or "") == "content-invalid")
        tier = str(source_identity.get("publisherTier") or original.get("tier") or "D")
        content_type = str(source_identity.get("contentType") or provenance.get("contentType") or "unknown")
        channel = str(source_identity.get("distributionChannel") or provenance.get("distributionChannel") or "direct")
        counts["publisherTierCounts"][tier] = int(counts["publisherTierCounts"].get(tier) or 0) + 1
        counts["contentTypeCounts"][content_type] = int(counts["contentTypeCounts"].get(content_type) or 0) + 1
        counts["distributionChannelCounts"][channel] = int(counts["distributionChannelCounts"].get(channel) or 0) + 1
        facts = raw.get("articleFacts") if isinstance(raw.get("articleFacts"), dict) else {}
        counts["bodyQualityBlockedCount"] += int(facts.get("bodyQualityPassed") is False or raw.get("bodyQualityPassed") is False)
        if str(raw.get("articleReadStatus") or facts.get("readStatus") or "") == "body" or bool(facts.get("bodyAvailable")):
            counts["bodyReadCount"] += 1
        language = str(raw.get("sourceLanguage") or "").strip().lower()
        translation_status = str(raw.get("translationStatus") or "").strip().lower()
        if language == "en":
            if translation_status == "complete":
                counts["translationCompleteCount"] += 1
            elif translation_status == "unavailable":
                counts["translationUnavailableCount"] += 1
            else:
                counts["translationPendingCount"] += 1
        quality = raw.get("articleSummaryQuality") if isinstance(raw.get("articleSummaryQuality"), dict) else {}
        quality_state = str(quality.get("state") or raw.get("summaryQualityState") or "").strip().lower()
        if quality_state == "ready":
            counts["summaryReadyCount"] += 1
        elif quality_state == "blocked":
            counts["summaryBlockedCount"] += 1
        else:
            counts["summaryNeedsReviewCount"] += 1
        analysis = raw.get("aiAnalysis") if isinstance(raw.get("aiAnalysis"), dict) else {}
        status = str(analysis.get("status") or "").strip().lower()
        if status == "fallback":
            counts["analysisFallbackCount"] += 1
        elif status == "deferred":
            counts["analysisDeferredCount"] += 1
    counts["eventClusterCount"] = len(event_clusters)
    return counts


def revalidate_research_evidence_payload(payload: Dict[str, object]) -> Dict[str, object]:
    body = payload if isinstance(payload, dict) else {}
    result = ResearchEvidenceGovernanceService(
        stores.research_evidence_store(),
        runtime_settings(),
    ).revalidate(
        symbol=configured(body.get("symbol")).upper(),
        limit=max(1, min(5000, int(body.get("limit") or 500))),
    )
    new_domain_event(
        APP_ITEM_UPDATED,
        "research-evidence-governance",
        {"type": "researchEvidenceGovernance", "result": result},
    )
    return result


def research_evidence_list_payload(item, include_detail: bool = False) -> Dict[str, object]:
    item, analysis_source = projected_research_evidence(item)
    news_eligibility = evidence_eligibility(item) if str(item.kind or "").lower() == "news" else {}
    raw = item.raw_payload if isinstance(item.raw_payload, dict) else {}
    states = item.state_payload()
    governance = raw.get("evidenceGovernance") if isinstance(raw.get("evidenceGovernance"), dict) else {}
    prompt_admission = assess_prompt_evidence(
        raw,
        kind=item.kind,
        published_at=item.published_at,
        observed_at=item.observed_at,
    ).to_dict()
    source_identity = news_eligibility.get("sourceIdentity") if isinstance(news_eligibility.get("sourceIdentity"), dict) else {}
    source_provenance = raw.get("sourceProvenance") if isinstance(raw.get("sourceProvenance"), dict) else {}
    original_publisher = source_provenance.get("originalPublisher") if isinstance(source_provenance.get("originalPublisher"), dict) else {}
    article_verification = source_provenance.get("articleVerification") if isinstance(source_provenance.get("articleVerification"), dict) else {}
    claim_ledger = raw.get("claimLedger") if isinstance(raw.get("claimLedger"), dict) else {}
    claim_summary = claim_ledger.get("summary") if isinstance(claim_ledger.get("summary"), dict) else {}
    disclosure_analysis = raw.get("disclosureAnalysis") if isinstance(raw.get("disclosureAnalysis"), dict) else {}
    disclosure_quality = raw.get("disclosureDocumentQuality") if isinstance(raw.get("disclosureDocumentQuality"), dict) else {}
    document_lifecycle = raw.get("documentLifecycle") if isinstance(raw.get("documentLifecycle"), dict) else {}
    article_summary_ko = str(raw.get("articleSummaryKo") or "")
    article_summary_quality = raw.get("articleSummaryQuality") if isinstance(raw.get("articleSummaryQuality"), dict) else {}
    summary_issues = list(article_summary_quality.get("issues") or [])
    if has_mojibake(article_summary_ko) or "text-encoding-corrupt" in summary_issues:
        # Never pass unreadable legacy source text to compact feed cards. The
        # raw article remains auditable in storage and the async worker can
        # retry it when a clean source becomes available.
        article_summary_ko = "원문 인코딩 점검으로 요약을 보류했습니다."
    compact_raw = {}
    for key in ["name", "provider", "articleType", "analysisStatus", "relevanceState", "impactLabel", "impactSummary", "koreanSummary", "priceImpact", "sourceTrustState", "materialityState", "dataState", "validationState", "articleReadStatus", "stockImpact", "stockImpactLabel", "stockImpactPolarity", "stockImpactReasonKo", "originalTitle", "translatedTitleKo", "sourceLanguage", "translationStatus", "summaryQualityState", "articleSummaryQuality", "externalFactDatasetId", "externalFactSourceRevision", "officialDocumentDatasetId", "officialDocumentFactRevision", "officialDocumentFactPayloadHash", "officialDocumentFetchedAt"]:
        if raw.get(key) not in (None, "", [], {}):
            compact_raw[key] = raw.get(key)
    payload = {
        "evidenceId": item.evidence_id,
        "symbol": item.symbol,
        "kind": item.kind,
        "source": item.source,
        "title": item.title,
        "summary": item.summary,
        "url": item.url,
        "observedAt": item.observed_at,
        "publishedAt": item.published_at,
        "polarity": item.polarity,
        "evidenceRole": item.polarity,
        "relationScope": str(raw.get("relationScope") or ""),
        "eventType": str(raw.get("eventType") or ""),
        "sourceTrustState": states["sourceTrustState"],
        "materialityState": states["materialityState"],
        "dataState": states["dataState"],
        "validationState": states["validationState"],
        "analysisSummary": str(raw.get("analysisSummary") or ""),
        "articleSummaryKo": article_summary_ko,
        "originalTitle": str(raw.get("originalTitle") or item.title or ""),
        "translatedTitleKo": str(raw.get("translatedTitleKo") or ""),
        "sourceLanguage": str(raw.get("sourceLanguage") or ""),
        "translationStatus": str(raw.get("translationStatus") or ""),
        "summaryQualityState": str(raw.get("summaryQualityState") or ""),
        "articleSummaryQuality": article_summary_quality,
        "analysisStatus": str((raw.get("aiAnalysis") or {}).get("status") or raw.get("analysisStatus") or "") if isinstance(raw.get("aiAnalysis") or {}, dict) else str(raw.get("analysisStatus") or ""),
        "articleReadStatus": str(raw.get("articleReadStatus") or ""),
        "stockImpact": str(raw.get("stockImpact") or ""),
        "stockImpactLabel": str(raw.get("stockImpactLabel") or ""),
        "stockImpactPolarity": str(raw.get("stockImpactPolarity") or ""),
        "stockImpactReasonKo": str(raw.get("stockImpactReasonKo") or ""),
        "sourceKind": str(raw.get("sourceKind") or ""),
        "sourcePlatform": str(raw.get("sourcePlatform") or ""),
        "newsEligibility": news_eligibility,
        "archiveEligible": bool(news_eligibility.get("archiveEligible")) if news_eligibility else True,
        "displayEligible": bool(news_eligibility.get("displayEligible")) if news_eligibility else bool(prompt_admission.get("displayEligible")),
        "alertEligible": bool(news_eligibility.get("alertEligible")) if news_eligibility else bool(prompt_admission.get("alertEligible")),
        "reasoningEligible": bool(news_eligibility.get("reasoningEligible")) if news_eligibility else bool(prompt_admission.get("decisionEligible")),
        "promptEvidenceAdmission": prompt_admission,
        "eligibilityAudit": {
            "displayEligible": bool(news_eligibility.get("displayEligible")) if news_eligibility else bool(prompt_admission.get("displayEligible")),
            "alertEligible": bool(news_eligibility.get("alertEligible")) if news_eligibility else bool(prompt_admission.get("alertEligible")),
            "reasoningEligible": bool(news_eligibility.get("reasoningEligible")) if news_eligibility else bool(prompt_admission.get("decisionEligible")),
            "promptEligible": bool(prompt_admission.get("promptEligible")),
            "usage": str(prompt_admission.get("usage") or "blocked"),
            "freshnessState": str(prompt_admission.get("freshnessState") or "unknown"),
            "reasonCodes": list(prompt_admission.get("reasonCodes") or []),
            "reviewReasonCodes": list(news_eligibility.get("reviewReasonCodes") or []) if news_eligibility else [],
        },
        "reviewState": str(news_eligibility.get("reviewState") or "") if news_eligibility else "",
        "reviewReasonCodes": list(news_eligibility.get("reviewReasonCodes") or []) if news_eligibility else [],
        "storyClusterId": str(raw.get("storyClusterId") or ""),
        "storyRootEvidenceId": str(source_provenance.get("storyRootEvidenceId") or raw.get("storyRootEvidenceId") or ""),
        "officialDocumentState": str(raw.get("officialDocumentState") or ""),
        "documentVerified": bool(raw.get("documentVerified")),
        "analysisReady": bool(raw.get("analysisReady")),
        "metadataVerified": bool(raw.get("metadataVerified")),
        "documentHash": str(raw.get("documentHash") or ""),
        "documentCharCount": int(raw.get("documentCharCount") or disclosure_quality.get("documentCharCount") or 0),
        "externalFactDatasetId": str(raw.get("externalFactDatasetId") or ""),
        "externalFactSourceRevision": str(raw.get("externalFactSourceRevision") or ""),
        "officialDocumentDatasetId": str(raw.get("officialDocumentDatasetId") or ""),
        "officialDocumentFactRevision": str(raw.get("officialDocumentFactRevision") or ""),
        "officialDocumentFactPayloadHash": str(raw.get("officialDocumentFactPayloadHash") or ""),
        "officialDocumentFetchedAt": str(raw.get("officialDocumentFetchedAt") or ""),
        "officialDocumentPreview": str(raw.get("officialDocumentPreview") or "")[:2000],
        "disclosureDocumentQuality": disclosure_quality,
        "documentLifecycle": document_lifecycle,
        "disclosureAnalysis": {
            "status": str(disclosure_analysis.get("status") or ""),
            "version": str(disclosure_analysis.get("version") or ""),
            "source": str(disclosure_analysis.get("source") or ""),
            "summary": str(disclosure_analysis.get("summary") or ""),
            "impactSummary": str(disclosure_analysis.get("impactSummary") or ""),
            "uncertaintySummary": str(disclosure_analysis.get("uncertaintySummary") or ""),
            "confirmedFacts": list(disclosure_analysis.get("confirmedFacts") or [])[:4],
            "materialNumbers": list(disclosure_analysis.get("materialNumbers") or [])[:12],
            "documentDates": list(disclosure_analysis.get("documentDates") or [])[:8],
            "watchItems": list(disclosure_analysis.get("watchItems") or [])[:4],
            "sourceSections": list(disclosure_analysis.get("sourceSections") or [])[:4],
            "needsReview": bool(disclosure_analysis.get("needsReview")),
            "lines": list(disclosure_analysis.get("lines") or [])[:6],
        },
        "sourceRevision": str(raw.get("sourceRevision") or raw.get("receiptNo") or raw.get("accessionNumber") or ""),
        "sourceAsOf": str(raw.get("sourceAsOf") or item.published_at or item.observed_at or ""),
        "sourceFetchedAt": str(raw.get("sourceFetchedAt") or ""),
        "sourceDocuments": {
            "primaryUrl": str(item.url or raw.get("officialDocumentUrl") or ""),
            "filingIndexUrl": str(raw.get("filingIndexUrl") or ""),
            "primaryDocument": str(raw.get("primaryDocument") or ""),
            "receiptNo": str(raw.get("receiptNo") or ""),
            "accessionNumber": str(raw.get("accessionNumber") or ""),
        },
        "disclosureCategory": str(raw.get("disclosureCategory") or ""),
        "disclosureTaxonomyVersion": str(raw.get("version") or "") if str(item.kind or "").lower() in {"disclosure", "filing", "sec-filing"} else "",
        "publisher": str(source_identity.get("publisher") or original_publisher.get("name") or raw.get("articlePublisher") or item.source),
        "publisherId": str(source_identity.get("publisherId") or original_publisher.get("publisherId") or raw.get("sourceOrigin") or ""),
        "publisherDomain": str(source_identity.get("publisherDomain") or original_publisher.get("domain") or ""),
        "publisherTier": str(source_identity.get("publisherTier") or original_publisher.get("tier") or ""),
        "publisherType": str(source_identity.get("publisherType") or original_publisher.get("publisherType") or ""),
        "declaredPublisher": str(source_identity.get("declaredPublisher") or source_provenance.get("declaredPublisher") or ""),
        "republisher": str(source_identity.get("republisher") or source_provenance.get("republisher") or ""),
        "distributionChannel": str(source_identity.get("distributionChannel") or source_provenance.get("distributionChannel") or raw.get("provider") or ""),
        "contentType": str(source_identity.get("contentType") or source_provenance.get("contentType") or raw.get("contentType") or ""),
        "syndicationState": str(source_provenance.get("syndicationState") or raw.get("syndicationState") or ""),
        "evidenceRelationship": str(source_provenance.get("evidenceRelationship") or raw.get("evidenceRelationship") or ""),
        "provenanceComplete": bool(source_provenance.get("provenanceComplete")),
        "sourcePath": list(source_provenance.get("sourcePath") or []),
        "articleVerification": article_verification,
        "sourceProvenance": source_provenance,
        "claimVerification": {
            "claimState": str(governance.get("claimState") or ""),
            "verificationStatus": str(governance.get("verificationStatus") or ""),
            "investmentJudgmentEligible": bool(governance.get("investmentJudgmentEligible")),
            "sourcePublisher": str(governance.get("sourcePublisher") or raw.get("sourcePublisher") or item.source),
            "sourceOrigin": str(governance.get("sourceOrigin") or raw.get("sourceOrigin") or ""),
            "independentSourceCount": int(governance.get("independentSourceCount") or 0),
            "officialEvidenceIds": list(governance.get("officialEvidenceIds") or []),
            "corroboratingEvidenceIds": list(governance.get("corroboratingEvidenceIds") or []),
            "conflictingEvidenceIds": list(governance.get("conflictingEvidenceIds") or []),
            "supersededByEvidenceId": str(governance.get("supersededByEvidenceId") or ""),
            "claimCount": int(claim_summary.get("claimCount") or 0),
            "eligibleClaimCount": int(claim_summary.get("eligibleClaimCount") or 0),
        },
        "analysisSource": analysis_source,
        "informationBrief": build_information_brief(item, eligibility=news_eligibility) if str(item.kind).lower() in {"news", "disclosure", "filing", "sec-filing", "sec_filing"} else None,
        "payload": compact_raw,
        "detailPath": "/api/research-evidence/" + urllib.parse.quote(str(item.evidence_id or "")),
    }
    if not include_detail:
        for key in [
            "newsEligibility", "eligibilityAudit", "reviewReasonCodes",
            "officialDocumentPreview", "disclosureDocumentQuality", "documentLifecycle",
            "sourceDocuments", "sourcePath", "articleVerification",
            "sourceProvenance",
        ]:
            payload.pop(key, None)
    return payload


def projected_research_evidence(item):
    """Fill legacy news analysis fields without making a network call.

    Historical evidence predates the article-analysis contract.  The list and
    detail APIs project those rows through the same deterministic analyser used
    by collection, so an empty field never falls back to title-keyword UI
    classification.  The worker persists this projection for new rows; this
    adapter keeps old rows truthful until their normal retention cycle ends.
    """
    raw = item.raw_payload if isinstance(getattr(item, "raw_payload", None), dict) else {}
    has_legacy_analysis = (
        bool(raw.get("articleSummaryKo"))
        and bool(raw.get("stockImpactPolarity"))
        and not has_mojibake(raw.get("articleSummaryKo"))
    )
    if getattr(item, "kind", "") != "news" or news_ai_analysis_is_current(item) or has_legacy_analysis:
        return item, "stored"
    target = NewsCollectionTarget(
        symbol=str(getattr(item, "symbol", "") or ""),
        name=str(raw.get("name") or getattr(item, "symbol", "") or ""),
        market=str(raw.get("market") or ""),
        currency=str(raw.get("currency") or ""),
        sector=str(raw.get("sector") or ""),
    )
    try:
        analysis = local_news_ai_analysis(target, item).to_dict()
        return apply_news_ai_analysis(item, analysis), "legacy-projection"
    except Exception:  # noqa: BLE001 - an incomplete legacy article must remain readable.
        return item, "unavailable"


def research_evidence_detail_payload(evidence_id: str) -> Dict[str, object]:
    repository = stores.research_evidence_store()
    item = repository.get(evidence_id)
    if not item:
        return {}
    projected, analysis_source = projected_research_evidence(item)
    payload = projected.to_dict()
    payload.update(research_evidence_list_payload(projected, include_detail=True))
    payload["payload"] = dict(projected.raw_payload or {})
    payload["promptEvidenceAdmission"] = assess_prompt_evidence(
        projected.raw_payload,
        kind=projected.kind,
        published_at=projected.published_at,
        observed_at=projected.observed_at,
    ).to_dict()
    payload["analysisSource"] = analysis_source
    if hasattr(repository, "story_history"):
        payload["storyTimeline"] = [
            {
                "evidenceId": peer.evidence_id,
                "title": str((peer.raw_payload or {}).get("translatedTitleKo") or peer.title),
                "publishedAt": peer.published_at,
                "source": peer.source,
                "url": peer.url,
                "state": str(peer.lifecycle_state or "active"),
                "relationship": str((peer.raw_payload or {}).get("evidenceRelationship") or ""),
            }
            for peer in repository.story_history(item, limit=12)
        ]
    return {"item": payload}


def delete_research_evidence_payload(evidence_id: str, query: Dict[str, List[str]]) -> Dict[str, object]:
    normalized_id = configured(evidence_id)
    if not normalized_id:
        raise ValueError("삭제할 근거 ID가 필요합니다.")
    store = stores.research_evidence_store()
    removed = False
    if hasattr(store, "retract_many_with_events"):
        mutation, recorded_events = store.retract_many_with_events(
            [normalized_id],
            "manual-evidence-retraction",
            lambda value: research_evidence_lifecycle_events({
                **(value.to_dict() if hasattr(value, "to_dict") else dict(value or {})),
                "status": "ok",
                "reason": "manual-evidence-retraction",
            }),
        )
        removed = bool(getattr(mutation, "retracted_count", 0) or 0)
        bridge = RealtimeEventBridge()
        for event in recorded_events:
            bridge.dispatch_recorded(event)
    else:
        removed = store.delete(normalized_id)
    if removed:
        new_domain_event(
            APP_ITEM_REMOVED,
            normalized_id,
            {"itemId": normalized_id, "type": "researchEvidence"},
        )
    payload = research_evidence_payload(query)
    payload["deleted"] = removed
    payload["deletedId"] = normalized_id
    return payload
