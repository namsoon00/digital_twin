import re
from dataclasses import dataclass, field
from typing import Dict, List

from .entity import other_company_aliases, target_aliases
from .entity_resolution import TargetEntityResolution, matched_aliases, resolve_target_entity
from .provenance import annotate_source_provenance, resolve_source_provenance


NEWS_ELIGIBILITY_VERSION = "news-eligibility-v2"
INVESTABLE_SCOPES = {"direct", "related_product", "peer", "sector", "market"}
USABLE_ANALYSIS_STATUSES = {"ok", "complete", "completed"}
HARD_BODY_ISSUES = {
    "publisher-navigation",
    "related-news-tail",
    "headline-list-contamination",
    "target-context-diluted",
    "text-encoding-corrupt",
}
HARD_REVIEW_RE = re.compile(
    r"본문\s*(?:오염|불일치)|다른\s*기사|무관한\s*(?:기사|본문)|기사\s*본문\s*대신|"
    r"사이트\s*(?:메뉴|탐색)|source\s*content\s*contamination|body\s*contamination|"
    r"title\s*(?:and|/)\s*body\s*mismatch",
    re.IGNORECASE,
)


def _dict(value: object) -> Dict[str, object]:
    return value if isinstance(value, dict) else {}


def _text(value: object) -> str:
    return str(value or "").strip()


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class EligibilityLayer:
    eligible: bool
    reason_codes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "eligible": self.eligible,
            "decision": "allow" if self.eligible else "block",
            "reasonCodes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class NewsEligibility:
    archive: EligibilityLayer
    display: EligibilityLayer
    alert: EligibilityLayer
    reasoning: EligibilityLayer
    entity_resolution: TargetEntityResolution
    source_identity: Dict[str, object]
    review_state: str = "clear"
    review_reason_codes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "version": NEWS_ELIGIBILITY_VERSION,
            "archiveEligible": self.archive.eligible,
            "displayEligible": self.display.eligible,
            "alertEligible": self.alert.eligible,
            "reasoningEligible": self.reasoning.eligible,
            "layers": {
                "archive": self.archive.to_dict(),
                "display": self.display.to_dict(),
                "alert": self.alert.to_dict(),
                "reasoning": self.reasoning.to_dict(),
            },
            "entityResolution": self.entity_resolution.to_dict(),
            "sourceIdentity": dict(self.source_identity),
            "reviewState": self.review_state,
            "reviewReasonCodes": list(self.review_reason_codes),
        }


def review_classification(payload: Dict[str, object]) -> tuple:
    payload = _dict(payload)
    facts = _dict(payload.get("articleFacts"))
    analysis = _dict(payload.get("aiAnalysis"))
    summary_quality = _dict(payload.get("articleSummaryQuality"))
    issues = {
        _text(value).lower()
        for value in list(facts.get("bodyQualityIssues") or [])
        + list(summary_quality.get("issues") or [])
        if _text(value)
    }
    reasons: List[str] = []
    if issues.intersection(HARD_BODY_ISSUES):
        reasons.append("content-quality-hard-failure")
    review_text = " ".join([
        *[_text(value) for value in analysis.get("reasoningLimitations") or []],
        _text(analysis.get("validationReasonKo")),
        _text(payload.get("analysisConflictReasonKo")),
    ])
    if HARD_REVIEW_RE.search(review_text):
        reasons.append("ai-detected-content-mismatch")
    reasons = list(dict.fromkeys(reasons))
    if reasons:
        return "content-invalid", reasons
    if _bool(analysis.get("needsReview")):
        return "conditional-review", ["ai-review-requested"]
    return "clear", []


def assess_news_eligibility(
    payload: Dict[str, object],
    *,
    title: object = "",
    summary: object = "",
    symbol: object = "",
    name: object = "",
    source: object = "",
    provider: object = "",
    url: object = "",
    lifecycle_state: object = "active",
    **_unused: object,
) -> NewsEligibility:
    payload = _dict(payload)
    facts = _dict(payload.get("articleFacts"))
    analysis = _dict(payload.get("aiAnalysis"))
    summary_quality = _dict(payload.get("articleSummaryQuality"))
    governance = _dict(payload.get("evidenceGovernance"))
    quality_gate = _dict(payload.get("qualityGate"))
    body_text = _text(payload.get("articleText") or facts.get("articleText") or summary)
    resolution = resolve_target_entity(
        title,
        body_text or summary,
        symbol,
        name or payload.get("name") or payload.get("companyName"),
    )
    resolved_provenance = resolve_source_provenance(
        payload,
        title=title,
        summary=summary,
        source=source,
        provider=provider,
        url=url,
    )
    source_profile = _dict(payload.get("sourceIdentity")) or resolved_provenance.identity.to_dict()
    provenance = _dict(payload.get("sourceProvenance")) or resolved_provenance.to_dict()
    active = _text(lifecycle_state or payload.get("evidenceLifecycleState") or "active").lower() == "active"
    kind = _text(payload.get("kind") or "news").lower()
    scope = _text(payload.get("relationScope")).lower()
    relevance = _text(payload.get("relevanceState")).lower()
    trust = _text(payload.get("sourceTrustState") or source_profile.get("sourceTrustState") or facts.get("sourceTrustState")).lower()
    materiality = _text(payload.get("materialityState") or facts.get("materialityState")).lower()
    validation = _text(payload.get("validationState") or facts.get("validationState")).lower()
    read_status = _text(payload.get("articleReadStatus") or facts.get("readStatus")).lower()
    body_available = _bool(facts.get("bodyAvailable")) or read_status in {"body", "full", "full-body", "article-body"}
    body_quality = facts.get("bodyQualityPassed") is True and payload.get("bodyQualityPassed") is not False
    summary_state = _text(summary_quality.get("state") or payload.get("summaryQualityState")).lower()
    analysis_status = _text(analysis.get("status") or payload.get("analysisStatus")).lower()
    content_type = _text(source_profile.get("contentType") or provenance.get("contentType") or payload.get("contentType")).lower()
    relationship = _text(provenance.get("evidenceRelationship") or payload.get("evidenceRelationship")).lower()
    publisher_tier = _text(source_profile.get("publisherTier") or _dict(provenance.get("originalPublisher")).get("tier")).upper()
    target_confirmed = resolution.target_subject_confirmed
    review_state, review_reason_codes = review_classification(payload)
    if not _text(title) and quality_gate.get("targetSubjectConfirmed") is True:
        target_confirmed = True

    archive_reasons: List[str] = []
    if kind and kind != "news":
        archive_reasons.append("not-news")
    archive = EligibilityLayer(not archive_reasons, archive_reasons)

    display_reasons: List[str] = []
    if not active:
        display_reasons.append("lifecycle-inactive")
    if payload.get("excludedReason") or scope not in INVESTABLE_SCOPES or relevance == "unrelated":
        display_reasons.append("entity-or-editorial-excluded")
    if scope == "direct" and not target_confirmed:
        display_reasons.append("target-subject-unconfirmed")
    if not body_available:
        display_reasons.append("article-body-missing")
    if not body_quality:
        display_reasons.append("article-body-quality-failed")
    if summary_state != "ready":
        display_reasons.append("summary-not-ready")
    summary_text = _text(payload.get("articleSummaryKo") or summary)
    summary_target_hits = matched_aliases(summary_text, target_aliases(symbol, name or payload.get("name") or payload.get("companyName")))
    summary_other_hits = matched_aliases(summary_text, other_company_aliases(symbol))
    if summary_text and summary_other_hits and not summary_target_hits:
        display_reasons.append("summary-target-mismatch")
    if analysis_status not in USABLE_ANALYSIS_STATUSES:
        display_reasons.append("external-analysis-not-ready")
    if payload.get("analysisConflict") or facts.get("analysisConflict"):
        display_reasons.append("analysis-conflict")
    if review_state == "content-invalid":
        display_reasons.extend(review_reason_codes)
    if trust not in {"standard", "trusted"}:
        display_reasons.append("source-trust-below-policy")
    if relationship in {"exact-duplicate", "syndicated-copy"}:
        display_reasons.append("duplicate-publication")
    display_reasons = list(dict.fromkeys(display_reasons))
    display = EligibilityLayer(not display_reasons, display_reasons)

    alert_reasons = list(display_reasons)
    if scope != "direct":
        alert_reasons.append("not-direct-subject-event")
    if materiality not in {"notable", "material"}:
        alert_reasons.append("materiality-below-policy")
    if validation == "blocked":
        alert_reasons.append("validation-blocked")
    if content_type in {"opinion", "aggregation", "automated", "unknown"}:
        alert_reasons.append("content-type-not-alertable")
    alert_reasons = list(dict.fromkeys(alert_reasons))
    alert = EligibilityLayer(not alert_reasons, alert_reasons)

    reasoning_reasons = list(alert_reasons)
    if not _bool(governance.get("investmentJudgmentEligible")):
        reasoning_reasons.append("claim-governance-not-eligible")
    if not _bool(provenance.get("provenanceComplete")):
        reasoning_reasons.append("source-provenance-incomplete")
    if publisher_tier not in {"A", "B", "C"}:
        reasoning_reasons.append("publisher-tier-below-reasoning-policy")
    if content_type in {"opinion", "aggregation", "automated", "unknown"}:
        reasoning_reasons.append("content-type-not-reasoning-eligible")
    reasoning_reasons = list(dict.fromkeys(reasoning_reasons))
    reasoning = EligibilityLayer(not reasoning_reasons, reasoning_reasons)
    return NewsEligibility(
        archive,
        display,
        alert,
        reasoning,
        resolution,
        source_profile,
        review_state,
        review_reason_codes,
    )


def annotate_news_eligibility(payload: Dict[str, object], **context: object) -> Dict[str, object]:
    result = annotate_source_provenance(dict(payload or {}), **context)
    result["newsEligibility"] = assess_news_eligibility(result, **context).to_dict()
    return result
