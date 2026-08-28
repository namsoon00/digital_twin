from dataclasses import dataclass, field
import hashlib
import json
from typing import Dict, Mapping


ARTICLE_SOURCE_REVISION_VERSION = "news-article-source-revision-v1"
ARTICLE_ENRICHMENT_REVISION_VERSION = "news-article-enrichment-revision-v2-semantic"
AUTHORITATIVE_ANALYSIS_STATUSES = {"complete", "ok", "success", "verified"}
ENRICHMENT_REVISION_VOLATILE_KEYS = {
    "checkedAt",
    "collectedAt",
    "completedAt",
    "createdAt",
    "evaluatedAt",
    "externalCompletedAt",
    "fetchedAt",
    "lastAttemptAt",
    "lastExternalAttemptAt",
    "lastLocalRepairAt",
    "observedAt",
    "processedAt",
    "queuedAt",
    "updatedAt",
}
ENRICHMENT_PAYLOAD_KEYS = {
    "aiAnalysis",
    "articleAiAnalysisVersion",
    "articleSummaryKo",
    "articleSummaryQuality",
    "claimLedger",
    "decisionInlineEligible",
    "decisionInlineReasonKo",
    "evidenceGovernance",
    "eventClassificationVersion",
    "eventEpisodeId",
    "eventFingerprint",
    "eventType",
    "newsEligibility",
    "newsIntelligenceVersion",
    "originalTitle",
    "promptEvidenceAdmission",
    "stockImpact",
    "stockImpactLabel",
    "stockImpactPolarity",
    "stockImpactReasonKo",
    "summaryQualityState",
    "sourceLanguage",
    "sourceIdentity",
    "sourceProvenance",
    "storyClusterId",
    "storyIdentityVersion",
    "translatedTitleKo",
    "translationStatus",
}
ENRICHMENT_ARTICLE_FACT_KEYS = {
    "analysisConflict",
    "analysisQuality",
    "analysisSource",
    "dataState",
    "decisionInlineEligible",
    "decisionInlineReasonKo",
    "eventTakeaway",
    "eventType",
    "eventTypeLabel",
    "impactReasonKo",
    "keySentences",
    "materialityState",
    "numbers",
    "relationScope",
    "relevanceState",
    "sourceTrustState",
    "stockImpact",
    "stockImpactLabel",
    "stockImpactPolarity",
    "summaryKo",
    "topics",
    "validationState",
}


def _mapping(value: object) -> Dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _payload(value: object) -> Dict[str, object]:
    if isinstance(value, Mapping):
        nested = value.get("payload")
        return _mapping(nested) if isinstance(nested, Mapping) else dict(value)
    return _mapping(getattr(value, "raw_payload", {}))


def _text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _field(value: object, payload: Dict[str, object], *keys: str) -> str:
    if isinstance(value, Mapping):
        for key in keys:
            candidate = value.get(key)
            if candidate not in (None, "", [], {}):
                return _text(candidate)
    else:
        attribute_keys = {
            "evidenceId": "evidence_id",
            "publishedAt": "published_at",
        }
        for key in keys:
            candidate = getattr(value, attribute_keys.get(key, key), None)
            if candidate not in (None, "", [], {}):
                return _text(candidate)
    for key in keys:
        candidate = payload.get(key)
        if candidate not in (None, "", [], {}):
            return _text(candidate)
    return ""


def article_source_revision(value: object) -> str:
    """Fingerprint immutable source content, excluding collection and AI clocks."""

    payload = _payload(value)
    facts = _mapping(payload.get("articleFacts"))
    body = _text(
        payload.get("articleText")
        or payload.get("articleTextPreview")
        or facts.get("bodyText")
        or facts.get("bodyPreview")
        or payload.get("articleSourceSummary")
        or facts.get("feedSummaryPreview")
    )
    source = {
        "version": ARTICLE_SOURCE_REVISION_VERSION,
        "evidenceId": _field(value, payload, "evidenceId", "evidence_id"),
        "symbol": _field(value, payload, "symbol").upper(),
        "title": _field(value, payload, "title"),
        "body": body,
        "url": _field(value, payload, "articleCanonicalUrl", "canonicalUrl", "url"),
        "publishedAt": _field(value, payload, "publishedAt", "published_at"),
    }
    digest = hashlib.sha256(
        json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32]
    return "news-source:" + digest


def analysis_status(payload: Dict[str, object]) -> str:
    analysis = _mapping((payload or {}).get("aiAnalysis"))
    return _text(analysis.get("status")).lower()


def authoritative_enrichment(payload: Dict[str, object]) -> bool:
    values = dict(payload or {})
    status = analysis_status(values)
    translation = _text(values.get("translationStatus")).lower()
    language = _text(values.get("sourceLanguage")).lower()
    translation_ready = not language.startswith("en") or translation in {"complete", "not-required", "unavailable"}
    return status in AUTHORITATIVE_ANALYSIS_STATUSES and translation_ready


def enrichment_payload_snapshot(payload: Dict[str, object]) -> Dict[str, object]:
    source = dict(payload or {})
    snapshot = {
        key: source.get(key)
        for key in ENRICHMENT_PAYLOAD_KEYS
        if source.get(key) not in (None, "", [], {})
    }
    facts = _mapping(source.get("articleFacts"))
    enrichment_facts = {
        key: facts.get(key)
        for key in ENRICHMENT_ARTICLE_FACT_KEYS
        if facts.get(key) not in (None, "", [], {})
    }
    if enrichment_facts:
        snapshot["articleFacts"] = enrichment_facts
    for key in ("dataState", "materialityState", "relevanceState", "sourceTrustState", "validationState"):
        if source.get(key) not in (None, ""):
            snapshot[key] = source.get(key)
    return snapshot


def apply_enrichment_snapshot(payload: Dict[str, object], snapshot: Dict[str, object]) -> Dict[str, object]:
    merged = dict(payload or {})
    enrichment = dict(snapshot or {})
    for key, candidate in enrichment.items():
        if key == "articleFacts":
            facts = _mapping(merged.get("articleFacts"))
            facts.update(_mapping(candidate))
            merged["articleFacts"] = facts
        else:
            merged[key] = candidate
    return merged


def enrichment_revision_material(value: object) -> object:
    """Remove operational clocks while preserving semantic analysis content."""

    if isinstance(value, Mapping):
        return {
            key: enrichment_revision_material(candidate)
            for key, candidate in value.items()
            if key not in ENRICHMENT_REVISION_VOLATILE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [enrichment_revision_material(candidate) for candidate in value]
    return value


def article_enrichment_revision(value: object) -> str:
    payload = _payload(value)
    source_revision = _text(payload.get("articleSourceRevision")) or article_source_revision(value)
    snapshot = enrichment_revision_material(enrichment_payload_snapshot(payload))
    digest = hashlib.sha256(
        json.dumps(
            {
                "version": ARTICLE_ENRICHMENT_REVISION_VERSION,
                "sourceRevision": source_revision,
                "enrichment": snapshot,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:32]
    return "news-enrichment:" + digest


@dataclass(frozen=True)
class NewsTarget:
    symbol: str
    name: str = ""
    market: str = ""

    def normalized_symbol(self) -> str:
        return str(self.symbol or "").upper().strip()


@dataclass(frozen=True)
class NewsArticle:
    article_id: str
    target: NewsTarget
    title: str
    body: str = ""
    summary: str = ""
    publisher: str = ""
    distribution_channel: str = ""
    canonical_url: str = ""
    published_at: str = ""
    lifecycle_state: str = "active"
    metadata: Dict[str, object] = field(default_factory=dict)

    def text(self) -> str:
        return " ".join(part.strip() for part in (self.title, self.body or self.summary) if part and part.strip())
