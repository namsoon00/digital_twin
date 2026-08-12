import hashlib
import re
from datetime import datetime
from typing import Dict
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


STORY_IDENTITY_VERSION = "news-story-identity-v2"
TRACKING_KEYS = {"fbclid", "gclid", "ref", "source", "utm_campaign", "utm_medium", "utm_source", "utm_term"}


def _hash(value: str) -> str:
    return "story:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _normalized_url(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
    except ValueError:
        return ""
    query = urlencode([(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key.lower() not in TRACKING_KEYS])
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/") or "/", query, ""))


def _normalized_title(value: object) -> str:
    text = re.sub(r"\s+[-|:]\s+[^-|:]{2,40}$", "", str(value or "").casefold())
    return re.sub(r"[^a-z0-9가-힣]+", " ", text).strip()


def _date_bucket(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return text[:10]


def story_identity(item: Dict[str, object]) -> str:
    item = item if isinstance(item, dict) else {}
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    sources = (item, payload, payload.get("articleFacts") if isinstance(payload.get("articleFacts"), dict) else {})

    def first(*keys: str) -> str:
        for source in sources:
            for key in keys:
                value = source.get(key) if isinstance(source, dict) else None
                if value not in (None, "", [], {}):
                    return str(value).strip()
        return ""

    explicit = first("storyClusterId", "eventClusterId", "canonicalEventId")
    if explicit:
        return explicit if explicit.startswith("story:") else _hash("explicit|" + explicit)
    duplicate_root = first("duplicateOfClaimId", "canonicalClaimId", "claimId")
    if duplicate_root:
        return _hash("claim|" + duplicate_root)
    canonical_url = _normalized_url(first("articleCanonicalUrl", "canonicalUrl", "url", "sourceUrl"))
    body_fingerprint = first("articleBodyFingerprint", "bodyFingerprint", "contentHash")
    if canonical_url and body_fingerprint:
        return _hash("document|" + canonical_url + "|" + body_fingerprint)
    symbol = first("symbol", "ticker", "relatedSymbol").upper()
    event_type = first("eventType", "newsType", "category").lower() or "general"
    title = _normalized_title(first("title", "headline", "name"))
    published = _date_bucket(first("publishedAt", "observedAt", "seenDate"))
    if not title:
        return ""
    return _hash("event|" + "|".join([symbol, event_type, published, title]))
