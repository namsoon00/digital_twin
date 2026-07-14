import hashlib
import html
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Set, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .news_analysis import normalized_article_title


TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "spm",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
    "yclid",
}
ARTICLE_KIND_VALUES = {"news", "article", "news-article", "rss", "research"}
ARTICLE_CONTEXT_KEYS = {
    "newsDigest",
    "newsHeadlines",
    "researchEvidence",
    "evidence",
    "counterEvidence",
    "sourceReferences",
}
ARTICLE_MARKER_KEYS = {
    "articleAiAnalysisVersion",
    "articleAnalysisSource",
    "articleFacts",
    "articleReadStatus",
    "articleSummaryKo",
    "materialityScore",
    "relevanceScore",
    "stockImpactLabel",
    "stockImpactPolarity",
    "stockImpactScore",
}
URL_KEYS = {"url", "sourceUrl", "sourceURL", "source_url", "link"}
SOURCE_URL_LIST_KEYS = {"sourceUrls", "source_urls"}


@dataclass
class SentArticleFilterResult:
    context: Dict[str, object]
    removed_items: List[Dict[str, object]] = field(default_factory=list)
    before_count: int = 0
    after_count: int = 0

    @property
    def removed_count(self) -> int:
        return len(self.removed_items)


def _text(value: object) -> str:
    return " ".join(str(value if value is not None else "").split()).strip()


def _hash_key(prefix: str, value: object) -> str:
    text = _text(value)
    if not text:
        return ""
    return prefix + ":" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:20]


def normalize_article_url(value: object) -> str:
    raw = html.unescape(_text(value))
    if not raw.startswith(("http://", "https://")):
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    query_items = []
    for key, item_value in parse_qsl(parsed.query, keep_blank_values=True):
        normalized_key = str(key or "").strip().lower()
        if normalized_key.startswith("utm_") or normalized_key in TRACKING_QUERY_KEYS:
            continue
        query_items.append((key, item_value))
    path = re.sub(r"/+$", "", parsed.path or "/") or "/"
    return urlunsplit((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        path,
        urlencode(sorted(query_items), doseq=True),
        "",
    ))


def normalize_article_title_for_identity(value: object) -> str:
    title = html.unescape(normalized_article_title(value))
    title = re.sub(r"^\s*(?:\[[^\]]{1,20}\]\s*)+", "", title)
    title = re.sub(r"^(속보|단독|종합|특징주|update|breaking)\s*[:：-]?\s*", "", title, flags=re.IGNORECASE)
    title = re.sub(r"[^0-9A-Za-z가-힣%$₩]+", " ", title)
    return " ".join(title.casefold().split()).strip()


def _nested_dicts(item: Dict[str, object]) -> List[Dict[str, object]]:
    if not isinstance(item, dict):
        return []
    rows = [item]
    for key in ["payload", "rawPayload", "raw_payload", "articleFacts"]:
        value = item.get(key)
        if isinstance(value, dict):
            rows.append(value)
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    facts = payload.get("articleFacts") if isinstance(payload.get("articleFacts"), dict) else {}
    if facts:
        rows.append(facts)
    return rows


def _first_nested_value(item: Dict[str, object], keys: Iterable[str]) -> str:
    for payload in _nested_dicts(item):
        for key in keys:
            value = payload.get(key)
            if value not in (None, ""):
                return _text(value)
    return ""


def _path_has_article_context(path: Tuple[str, ...]) -> bool:
    return any(part in ARTICLE_CONTEXT_KEYS for part in path)


def is_article_like(item: Dict[str, object], path: Tuple[str, ...] = ()) -> bool:
    if not isinstance(item, dict):
        return False
    kind = _first_nested_value(item, ["kind", "type", "sourceKind"]).casefold()
    if kind in ARTICLE_KIND_VALUES or "news" in kind or "article" in kind:
        return True
    if _path_has_article_context(path):
        return bool(_first_nested_value(item, ["title", "summary", "articleSummaryKo", *URL_KEYS]))
    return any(key in payload for payload in _nested_dicts(item) for key in ARTICLE_MARKER_KEYS)


def article_identity_keys(item: Dict[str, object]) -> Set[str]:
    if not isinstance(item, dict):
        return set()
    keys: Set[str] = set()
    evidence_id = _first_nested_value(item, ["evidenceId", "evidence_id", "id"])
    if evidence_id:
        keys.add(_hash_key("evidence", evidence_id))
    url = _first_nested_value(item, URL_KEYS)
    normalized_url = normalize_article_url(url)
    if normalized_url:
        keys.add(_hash_key("url", normalized_url))
    title = normalize_article_title_for_identity(_first_nested_value(item, ["title", "headline", "name"]))
    if len(title) >= 12:
        keys.add(_hash_key("title", title))
    takeaway = normalize_article_title_for_identity(_first_nested_value(item, ["eventTakeaway", "takeaway"]))
    if len(takeaway) >= 16:
        keys.add(_hash_key("takeaway", takeaway))
    return {key for key in keys if key}


def news_digest_article_item(context: Dict[str, object]) -> Dict[str, object]:
    digest = context.get("newsDigest") if isinstance(context.get("newsDigest"), dict) else {}
    if not digest:
        return {}
    return {
        "kind": "news",
        "evidenceId": digest.get("primaryEvidenceId"),
        "url": digest.get("primaryUrl"),
        "title": digest.get("primaryTitle"),
        "publishedAt": digest.get("primaryPublishedAt"),
    }


def collect_article_identity_keys_from_context(context: Dict[str, object]) -> Set[str]:
    keys: Set[str] = set()

    def visit(value: object, path: Tuple[str, ...] = ()) -> None:
        if isinstance(value, dict):
            if is_article_like(value, path):
                keys.update(article_identity_keys(value))
            digest_item = news_digest_article_item(value)
            if digest_item:
                keys.update(article_identity_keys(digest_item))
            for key, child in value.items():
                visit(child, path + (str(key),))
        elif isinstance(value, list):
            for child in value:
                visit(child, path)

    visit(context or {})
    return keys


def article_filter_context_summary(result: SentArticleFilterResult, sent_keys: Set[str]) -> Dict[str, object]:
    titles = []
    for item in result.removed_items:
        title = _first_nested_value(item, ["title", "headline", "summary", "articleSummaryKo"])
        if title and title not in titles:
            titles.append(title)
    return {
        "enabled": True,
        "policy": "sent-article-once",
        "removedCount": result.removed_count,
        "beforeCount": result.before_count,
        "afterCount": result.after_count,
        "matchedKeyCount": len(sent_keys),
        "removedTitles": titles[:5],
        "reason": "이미 발송한 기사 또는 같은 제목의 기사 근거는 다시 판단하지 않습니다.",
    }


def article_digest_context_item(item: Dict[str, object]) -> Dict[str, object]:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    return {
        "kind": "news",
        "evidenceId": _first_nested_value(item, ["evidenceId", "evidence_id", "id"]),
        "symbol": _first_nested_value(item, ["symbol"]),
        "source": _first_nested_value(item, ["source", "domain", "provider"]),
        "title": _first_nested_value(item, ["title", "headline"]),
        "url": _first_nested_value(item, URL_KEYS),
        "publishedAt": _first_nested_value(item, ["publishedAt", "published_at", "observedAt", "observed_at"]),
        "articleReadStatus": _first_nested_value(item, ["articleReadStatus"]) or _text(payload.get("articleReadStatus")),
        "identityKeys": sorted(article_identity_keys(item)),
    }


def _article_count(value: object) -> int:
    count = 0

    def visit(item: object, path: Tuple[str, ...] = ()) -> None:
        nonlocal count
        if isinstance(item, dict):
            if is_article_like(item, path) and article_identity_keys(item):
                count += 1
            for key, child in item.items():
                visit(child, path + (str(key),))
        elif isinstance(item, list):
            for child in item:
                visit(child, path)

    visit(value)
    return count


def filter_sent_articles_from_context(context: Dict[str, object], sent_keys: Set[str]) -> SentArticleFilterResult:
    sent = set(sent_keys or set())
    removed: List[Dict[str, object]] = []

    def visit(value: object, path: Tuple[str, ...] = ()):
        if isinstance(value, list):
            rows = []
            for child in value:
                if isinstance(child, dict) and is_article_like(child, path):
                    keys = article_identity_keys(child)
                    if keys and keys.intersection(sent):
                        removed.append(child)
                        continue
                if isinstance(child, str) and path and path[-1] in SOURCE_URL_LIST_KEYS:
                    normalized_url = normalize_article_url(child)
                    if normalized_url and _hash_key("url", normalized_url) in sent:
                        removed.append({"kind": "news", "url": child})
                        continue
                rows.append(visit(child, path))
            return rows
        if isinstance(value, dict):
            return {str(key): visit(child, path + (str(key),)) for key, child in value.items()}
        return value

    before = _article_count(context or {})
    filtered = visit(dict(context or {}))
    after = _article_count(filtered or {})
    return SentArticleFilterResult(filtered, removed, before, after)
