import hashlib
import re
from dataclasses import dataclass
from typing import Dict, List
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .source import SourceRegistry, SourceRegistryEntry, unknown_entry


SOURCE_PROVENANCE_VERSION = "news-source-provenance-v3-canonical-publisher"
TRACKING_KEYS = {"fbclid", "gclid", "ref", "source", "src", "ocid", "output"}


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _slug(value: object) -> str:
    return re.sub(r"[^0-9a-z가-힣.]+", "-", _clean(value).casefold()).strip("-")


def canonical_article_url(value: object) -> str:
    raw = _clean(value)
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    host = parsed.netloc.casefold().split(":")[0].removeprefix("www.").removeprefix("m.")
    query = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        normalized = key.casefold()
        if normalized.startswith("utm_") or normalized in TRACKING_KEYS:
            continue
        query.append((key, item))
    path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/") or "/"
    return urlunsplit(((parsed.scheme or "https").casefold(), host, path, urlencode(sorted(query)), ""))


def _hash(prefix: str, value: object) -> str:
    text = _clean(value)
    return prefix + ":" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:24] if text else ""


def normalized_content(value: object) -> str:
    return re.sub(r"[^0-9a-z가-힣%$]+", " ", _clean(value).casefold()).strip()


def document_identity(canonical_url: object) -> str:
    return _hash("document", canonical_article_url(canonical_url))


def content_fingerprint(value: object) -> str:
    normalized = normalized_content(value)
    return _hash("content", normalized) if len(normalized) >= 120 else ""


def syndication_identity(title: object, body: object) -> str:
    normalized_body = normalized_content(body)
    if len(normalized_body) >= 120:
        return _hash("syndication", " ".join(normalized_body.split()[:320]))
    normalized_title = normalized_content(title)
    return _hash("syndication-title", normalized_title) if len(normalized_title) >= 24 else ""


def distribution_channel(value: object) -> str:
    text = _clean(value)
    lowered = text.casefold()
    if "google" in lowered and "news" in lowered:
        if " kr" in lowered or lowered.endswith("kr"):
            return "Google News KR"
        if " us" in lowered or lowered.endswith("us"):
            return "Google News US"
        return "Google News"
    if "gdelt" in lowered:
        return "GDELT"
    if "yahoo" in lowered and "search" in lowered:
        return "Yahoo Finance Search"
    if "yahoo" in lowered and "rss" in lowered:
        return "Yahoo Finance RSS"
    return text


def classify_content_type(payload: Dict[str, object], entry: SourceRegistryEntry, title: object, canonical_url: object) -> str:
    kind = _clean(payload.get("kind") or payload.get("articleType")).casefold()
    corpus = " ".join([_clean(title), _clean(canonical_url), _clean(payload.get("eventType"))]).casefold()
    if entry.primary or kind in {"disclosure", "filing", "sec-filing", "official"}:
        return entry.default_content_type
    if re.search(r"(?:^|[\s/])(opinion|commentary|column|editorial|기고|칼럼|사설)(?:[\s/:]|$)", corpus):
        return "opinion"
    if any(token in corpus for token in ["press release", "news release", "보도자료"]):
        return "press-release"
    if any(token in corpus for token in ["analysis", "analyst", "전망", "분석"]):
        return "analysis"
    return entry.default_content_type or "reporting"


@dataclass(frozen=True)
class PublisherIdentity:
    publisher: str
    publisher_id: str
    publisher_domain: str
    publisher_tier: str
    publisher_type: str
    source_trust_state: str
    distribution_channel: str
    canonical_host: str
    content_type: str
    declared_publisher: str = ""
    republisher: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "publisher": self.publisher,
            "publisherId": self.publisher_id,
            "publisherDomain": self.publisher_domain,
            "publisherTier": self.publisher_tier,
            "publisherType": self.publisher_type,
            "sourceTrustState": self.source_trust_state,
            "distributionChannel": self.distribution_channel,
            "canonicalHost": self.canonical_host,
            "contentType": self.content_type,
            "declaredPublisher": self.declared_publisher,
            "republisher": self.republisher,
        }


@dataclass(frozen=True)
class SourceProvenance:
    identity: PublisherIdentity
    canonical_url: str
    resolved_by: str
    syndication_state: str
    provenance_complete: bool
    article_verification: Dict[str, object]
    source_path: List[Dict[str, object]]
    reason_codes: List[str]
    document_identity: str
    content_fingerprint: str
    syndication_identity: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "version": SOURCE_PROVENANCE_VERSION,
            "originalPublisher": {
                "publisherId": self.identity.publisher_id,
                "name": self.identity.publisher,
                "domain": self.identity.publisher_domain,
                "tier": self.identity.publisher_tier,
                "publisherType": self.identity.publisher_type,
            },
            "declaredPublisher": self.identity.declared_publisher,
            "republisher": self.identity.republisher,
            "distributionChannel": self.identity.distribution_channel,
            "canonicalUrl": self.canonical_url,
            "canonicalHost": self.identity.canonical_host,
            "resolvedBy": self.resolved_by,
            "contentType": self.identity.content_type,
            "syndicationState": self.syndication_state,
            "provenanceComplete": self.provenance_complete,
            "articleVerification": dict(self.article_verification),
            "sourcePath": list(self.source_path),
            "reasonCodes": list(self.reason_codes),
            "documentIdentity": self.document_identity,
            "contentFingerprint": self.content_fingerprint,
            "syndicationIdentity": self.syndication_identity,
        }


def resolve_source_provenance(
    payload: Dict[str, object],
    *,
    title: object = "",
    summary: object = "",
    source: object = "",
    provider: object = "",
    url: object = "",
    published_at: object = "",
    registry: object = "",
    **_unused: object,
) -> SourceProvenance:
    payload = payload if isinstance(payload, dict) else {}
    existing = payload.get("sourceProvenance") if isinstance(payload.get("sourceProvenance"), dict) else {}
    canonical_url = canonical_article_url(
        payload.get("articleCanonicalUrl") or payload.get("canonicalUrl") or url or payload.get("articleSourceUrl")
    )
    try:
        host = (urlsplit(canonical_url).hostname or "").casefold().removeprefix("www.").removeprefix("m.")
    except ValueError:
        host = ""
    source_registry = SourceRegistry(registry)
    # The provider feed identifies the publisher it discovered. Persisted
    # enrichment may contain a stale publisher inferred from a search channel,
    # so a fresh feed declaration must win during revalidation.
    declared = _clean(
        source
        or payload.get("declaredPublisher")
        or existing.get("declaredPublisher")
        or payload.get("articlePublisher")
        or payload.get("sourcePublisher")
        or payload.get("publisher")
    )
    host_entry = source_registry.by_host(host)
    declared_entry = source_registry.by_name(declared)
    declared_domain_mismatch = bool(
        host
        and declared_entry
        and (not host_entry or host_entry.publisher_id != declared_entry.publisher_id)
    )
    official_domain_mismatch = bool(
        declared_domain_mismatch
        and declared_entry.primary
    )
    if host_entry and host_entry.publisher_type != "discovery":
        entry = host_entry
        resolved_by = "canonical-domain-registry"
    elif official_domain_mismatch:
        entry = unknown_entry(host, declared)
        resolved_by = "official-publisher-domain-mismatch"
    elif host:
        # A canonical document hosted on an unregistered domain must not
        # inherit the trust tier of Yahoo/Google metadata. Keep its readable
        # feed name, but bind identity and trust to the actual host.
        entry = unknown_entry(host, declared)
        resolved_by = "canonical-domain-publisher-mismatch" if declared_domain_mismatch else "canonical-domain"
    elif declared_entry and declared_entry.publisher_type != "discovery":
        entry = declared_entry
        resolved_by = "publisher-metadata-registry"
    elif host_entry:
        entry = host_entry
        resolved_by = "discovery-domain"
    elif declared_entry:
        entry = declared_entry
        resolved_by = "discovery-metadata"
    else:
        entry = unknown_entry(host, declared if not (declared_entry and declared_entry.publisher_type == "discovery") else "")
        resolved_by = "canonical-domain" if host else "unresolved"
    channel = distribution_channel(payload.get("distributionChannel") or provider or payload.get("provider"))
    republisher = ""
    if declared_entry and declared_entry.publisher_id != entry.publisher_id and declared_entry.publisher_type != "discovery":
        republisher = declared_entry.name
    elif not declared_entry and declared and _slug(declared) not in {_slug(entry.name), _slug(channel)} and not source_registry.by_name(declared) == source_registry.by_name(channel):
        republisher = declared
    facts = payload.get("articleFacts") if isinstance(payload.get("articleFacts"), dict) else {}
    body = _clean(payload.get("articleText") or payload.get("articleTextPreview") or facts.get("bodyText") or facts.get("bodyPreview") or summary)
    body_available = bool(facts.get("bodyAvailable")) or len(body) >= 120
    published_available = bool(_clean(published_at or payload.get("publishedAt") or payload.get("published_at")))
    author_available = bool(_clean(payload.get("articleAuthor") or payload.get("author") or facts.get("author")))
    canonical_available = bool(canonical_url and host)
    non_discovery = entry.publisher_type != "discovery" and entry.publisher_id != "unknown"
    provenance_complete = bool(
        canonical_available
        and non_discovery
        and host_entry
        and host_entry.publisher_type != "discovery"
    )
    verification_reasons = []
    if not body_available:
        verification_reasons.append("article-body-missing")
    if not published_available:
        verification_reasons.append("published-time-missing")
    if not author_available:
        verification_reasons.append("byline-missing")
    if not provenance_complete:
        verification_reasons.append("original-publisher-unresolved")
    if official_domain_mismatch:
        verification_reasons.append("official-publisher-domain-mismatch")
    elif declared_domain_mismatch:
        verification_reasons.append("declared-publisher-domain-mismatch")
    verification_state = "verified" if provenance_complete and body_available and published_available else ("partial" if provenance_complete else "unverified")
    content_type = classify_content_type(payload, entry, title, canonical_url)
    if entry.publisher_type == "discovery":
        syndication_state = "discovery-only"
    elif republisher:
        syndication_state = "republished"
    elif provenance_complete:
        syndication_state = "original"
    else:
        syndication_state = "unattributed"
    identity = PublisherIdentity(
        entry.name,
        entry.publisher_id,
        entry.domains[0] if entry.domains else host,
        entry.tier,
        entry.publisher_type,
        entry.source_trust_state,
        channel,
        host,
        content_type,
        declared,
        republisher,
    )
    source_path = [{"role": "original-publisher", "name": entry.name, "id": entry.publisher_id}]
    if republisher:
        source_path.append({"role": "republisher", "name": republisher, "id": _slug(republisher)})
    if channel:
        source_path.append({"role": "distribution-channel", "name": channel, "id": _slug(channel)})
    return SourceProvenance(
        identity,
        canonical_url,
        resolved_by,
        syndication_state,
        provenance_complete,
        {
            "state": verification_state,
            "canonicalUrlAvailable": canonical_available,
            "publisherResolved": non_discovery,
            "bodyAvailable": body_available,
            "publishedTimeAvailable": published_available,
            "bylineAvailable": author_available,
            "reasonCodes": verification_reasons,
        },
        source_path,
        verification_reasons,
        document_identity(canonical_url),
        content_fingerprint(body),
        syndication_identity(title, body),
    )


def annotate_source_provenance(payload: Dict[str, object], **context: object) -> Dict[str, object]:
    result = dict(payload or {})
    previous = result.get("sourceProvenance") if isinstance(result.get("sourceProvenance"), dict) else {}
    provenance = resolve_source_provenance(result, **context)
    identity = provenance.identity
    normalized_provenance = provenance.to_dict()
    for key in ["evidenceRelationship", "syndicationRootEvidenceId", "storyRootEvidenceId", "independentEvidenceKey"]:
        if previous.get(key) not in (None, ""):
            normalized_provenance[key] = previous.get(key)
    if normalized_provenance.get("evidenceRelationship") in {"exact-duplicate", "syndicated-copy"}:
        normalized_provenance["syndicationState"] = normalized_provenance["evidenceRelationship"]
    result["sourceProvenance"] = normalized_provenance
    result["sourceIdentity"] = identity.to_dict()
    result["articleVerification"] = dict(provenance.article_verification)
    result["declaredPublisher"] = identity.declared_publisher
    result["articlePublisher"] = identity.publisher
    result["sourcePublisher"] = identity.publisher
    result["sourceOrigin"] = identity.publisher_id
    result["publisherIdentity"] = identity.publisher_id
    result["distributionChannel"] = identity.distribution_channel
    result["contentType"] = identity.content_type
    result["syndicationState"] = normalized_provenance.get("syndicationState") or provenance.syndication_state
    result["sourceTrustState"] = identity.source_trust_state
    result["sourceKind"] = identity.publisher_type
    result["sourcePlatform"] = identity.distribution_channel
    result["articleCanonicalUrl"] = provenance.canonical_url or result.get("articleCanonicalUrl") or ""
    result["documentIdentity"] = provenance.document_identity
    result["articleBodyFingerprint"] = provenance.content_fingerprint
    result["syndicationIdentity"] = provenance.syndication_identity
    return result


def publisher_identity(payload: Dict[str, object], source: object = "", provider: object = "", url: object = "") -> PublisherIdentity:
    return resolve_source_provenance(payload, source=source, provider=provider, url=url).identity
