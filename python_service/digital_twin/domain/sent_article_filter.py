import hashlib
import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Dict, Iterable, List, Set, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .news_analysis import normalized_article_title
from ..news_intelligence.domain.story import story_identity


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
ARTICLE_KIND_VALUES = {"news", "article", "news-article", "rss", "research", "disclosure", "filing", "sec-filing", "sec_filing"}
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
    "materialityState",
    "relevanceState",
    "sourceTrustState",
    "stockImpactLabel",
    "stockImpactPolarity",
    "decisionInlineEligible",
}
STORY_CLUSTER_KEYS = (
    "storyClusterId",
    "story_cluster_id",
    "eventClusterId",
    "event_cluster_id",
    "canonicalEventId",
)
STORY_CLAIM_ROOT_KEYS = (
    "duplicateOfClaimId",
    "duplicate_of_claim_id",
    "canonicalClaimId",
    "canonical_claim_id",
    "claimId",
    "claim_id",
)
STORY_FACT_KEYS = ("factId", "fact_id", "claimId", "claim_id", "factIds", "claimIds", "keyFacts", "facts")
STORY_FOLLOWUP_FACT_KEYS = ("factId", "fact_id", "factIds", "keyFacts", "facts")
URL_KEYS = {"url", "sourceUrl", "sourceURL", "source_url", "link"}
SOURCE_URL_LIST_KEYS = {"sourceUrls", "source_urls"}
DEFAULT_CONTEXT_SCAN_MAX_DEPTH = 8
DEFAULT_CONTEXT_SCAN_MAX_NODES = 1200
DEFAULT_CONTEXT_SCAN_MAX_KEYS = 800
SUPPRESSIBLE_IDENTITY_PREFIXES = {"event", "evidence", "story", "url"}
EVENT_FAMILY_WINDOW_DAYS = {
    "capital_policy": 0,
    "earnings": 1,
}
NEWS_DECISION_DRIVER_CATEGORIES = {
    "news",
    "article",
    "disclosure",
    "research",
    "research-evidence",
    "research_evidence",
    "source-alert-event",
    "source_alert_event",
}
NEWS_DECISION_DRIVER_DATA_KEY_TOKENS = {
    "news",
    "article",
    "disclosure",
    "research",
    "sourceevent",
    "source_event",
}


@dataclass
class SentArticleFilterResult:
    context: Dict[str, object]
    removed_items: List[Dict[str, object]] = field(default_factory=list)
    before_count: int = 0
    after_count: int = 0

    @property
    def removed_count(self) -> int:
        return len(self.removed_items)


@dataclass(frozen=True)
class ArticleIdentitySet:
    exact_keys: Set[str] = field(default_factory=set)
    story_keys: Set[str] = field(default_factory=set)
    fact_keys: Set[str] = field(default_factory=set)
    weak_keys: Set[str] = field(default_factory=set)

    def suppressible_keys(self) -> Set[str]:
        return set(self.exact_keys) | set(self.story_keys) | set(self.fact_keys)


def suppressible_identity_key(value: object) -> bool:
    text = _text(value)
    if not text or ":" not in text:
        return False
    return text.split(":", 1)[0] in SUPPRESSIBLE_IDENTITY_PREFIXES


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


@lru_cache(maxsize=4096)
def _normalize_article_title_for_identity_cached(value: str) -> str:
    title = html.unescape(normalized_article_title(value))
    title = re.sub(r"^\s*(?:\[[^\]]{1,20}\]\s*)+", "", title)
    title = re.sub(r"^(속보|단독|종합|특징주|update|breaking)\s*[:：-]?\s*", "", title, flags=re.IGNORECASE)
    title = re.sub(r"[^0-9A-Za-z가-힣%$₩]+", " ", title)
    return " ".join(title.casefold().split()).strip()


def normalize_article_title_for_identity(value: object) -> str:
    return _normalize_article_title_for_identity_cached(_text(value)[:800])


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


def _truthy(value: object) -> bool:
    if value is True:
        return True
    return str(value or "").strip().casefold() in {"1", "true", "yes", "y"}


def _nested_values(item: Dict[str, object], keys: Iterable[str]) -> List[str]:
    values: List[str] = []
    accepted = tuple(keys or [])

    def append_value(value: object) -> None:
        if isinstance(value, dict):
            for nested in value.values():
                append_value(nested)
            return
        if isinstance(value, (list, tuple, set)):
            for nested in value:
                append_value(nested)
            return
        text = _text(value)
        if text and text not in values:
            values.append(text)

    for payload in _nested_dicts(item):
        for key in accepted:
            raw = payload.get(key)
            append_value(raw)
    return values


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


def article_story_cluster_id(item: Dict[str, object]) -> str:
    """Stable story identity across syndicated or translated article copies."""

    return story_identity(item)


def _article_event_date(item: Dict[str, object]):
    raw = _first_nested_value(item, ["publishedAt", "published_at", "observedAt", "observed_at", "seenDate"])
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(raw[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return parsed.date()


def article_event_family_keys(item: Dict[str, object]) -> Set[str]:
    """Group only event classes with a reliably bounded corporate episode."""

    symbol = _first_nested_value(item, ["symbol", "ticker", "relatedSymbol"]).upper()
    event_type = _first_nested_value(item, ["eventType", "event_type", "newsType"]).lower()
    event_date = _article_event_date(item)
    if not symbol or not event_date or event_type not in EVENT_FAMILY_WINDOW_DAYS:
        return set()
    window = EVENT_FAMILY_WINDOW_DAYS[event_type]
    return {
        _hash_key("event", "|".join([symbol, event_type, (event_date + timedelta(days=offset)).isoformat()]))
        for offset in range(-window, window + 1)
    }


def article_story_fact_keys(item: Dict[str, object]) -> Set[str]:
    """Return fact-level identities that may justify a story follow-up."""

    cluster = article_story_cluster_id(item)
    if not cluster:
        return set()
    facts: List[str] = []
    facts.extend(_nested_values(item, STORY_FACT_KEYS))
    # A body-read event takeaway is the best low-noise fallback when a source
    # does not expose stable claim IDs. Feed-only copies intentionally do not
    # create a fact update from rewritten headlines alone.
    read_status = _first_nested_value(item, ["articleReadStatus", "readStatus", "readScope"]).strip().lower()
    if read_status in {"body", "full", "article-body"}:
        facts.extend(_nested_values(item, ["eventTakeaway", "impactReasonKo", "stockImpactReasonKo"]))
    keys: Set[str] = set()
    for fact in facts:
        normalized = normalize_article_title_for_identity(fact)
        if len(normalized) >= 12:
            keys.add(cluster + ":fact:" + hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:20])
    return keys


def article_identity_set(item: Dict[str, object]) -> ArticleIdentitySet:
    if not isinstance(item, dict):
        return ArticleIdentitySet()
    exact_keys: Set[str] = set()
    story_keys: Set[str] = set()
    weak_keys: Set[str] = set()
    evidence_id = _first_nested_value(item, ["evidenceId", "evidence_id", "id"])
    if evidence_id:
        exact_keys.add(_hash_key("evidence", evidence_id))
    url = _first_nested_value(item, URL_KEYS)
    normalized_url = normalize_article_url(url)
    if normalized_url:
        exact_keys.add(_hash_key("url", normalized_url))
    title = normalize_article_title_for_identity(_first_nested_value(item, ["title", "headline", "name"]))
    if len(title) >= 12:
        weak_keys.add(_hash_key("title", title))
    takeaway = normalize_article_title_for_identity(_first_nested_value(item, ["eventTakeaway", "takeaway"]))
    if len(takeaway) >= 16:
        weak_keys.add(_hash_key("takeaway", takeaway))
    cluster = article_story_cluster_id(item)
    if cluster:
        story_keys.add(cluster)
    story_keys.update(article_event_family_keys(item))
    fact_keys = article_story_fact_keys(item)
    return ArticleIdentitySet(
        exact_keys={key for key in exact_keys if key},
        story_keys={key for key in story_keys if key},
        fact_keys={key for key in fact_keys if key},
        weak_keys={key for key in weak_keys if key},
    )


def article_identity_keys(item: Dict[str, object]) -> Set[str]:
    """Return only identities strong enough to suppress a notification."""

    return article_identity_set(item).suppressible_keys()


def article_weak_identity_keys(item: Dict[str, object]) -> Set[str]:
    return set(article_identity_set(item).weak_keys)


def article_has_new_story_fact(item: Dict[str, object], sent_keys: Set[str]) -> bool:
    cluster = article_story_cluster_id(item)
    if not cluster:
        return False
    # Rewritten headlines and per-publisher claim ids are not enough to
    # reopen a sent story. A follow-up needs an explicit fact identity from
    # the evidence pipeline, which is auditable and can represent a real new
    # disclosed number, action, correction, or verified claim.
    fact_keys: Set[str] = set()
    for fact in _nested_values(item, STORY_FOLLOWUP_FACT_KEYS):
        normalized = normalize_article_title_for_identity(fact)
        if len(normalized) >= 12:
            fact_keys.add(cluster + ":fact:" + hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:20])
    return bool(fact_keys and any(key not in set(sent_keys or set()) for key in fact_keys))


def news_digest_article_item(context: Dict[str, object]) -> Dict[str, object]:
    digest = context.get("newsDigest") if isinstance(context.get("newsDigest"), dict) else {}
    if not digest:
        return {}
    return {
        "kind": digest.get("eventKind") or "news",
        "evidenceId": digest.get("primaryEvidenceId"),
        "url": digest.get("primaryUrl"),
        "title": digest.get("primaryTitle"),
        "publishedAt": digest.get("primaryPublishedAt"),
        "storyClusterId": digest.get("eventClusterId"),
        "storyUpdate": digest.get("deliveryMode") == "story-update",
    }


def _add_precomputed_article_identity_keys(value: object, keys: Set[str]) -> None:
    if not isinstance(value, dict):
        return
    raw_keys = value.get("identityKeys") or value.get("articleKeys")
    if isinstance(raw_keys, list):
        for raw_key in raw_keys:
            text = _text(raw_key)
            if suppressible_identity_key(text):
                keys.add(text)
    digest = value.get("newsDigest") if isinstance(value.get("newsDigest"), dict) else {}
    digest_keys = digest.get("articleKeys") if isinstance(digest.get("articleKeys"), list) else []
    for raw_key in digest_keys:
        text = _text(raw_key)
        if suppressible_identity_key(text):
            keys.add(text)


def _scan_budget_exhausted(visited: List[int], keys: Set[str], max_nodes: int, max_keys: int) -> bool:
    return visited[0] >= max_nodes or len(keys) >= max_keys


def collect_article_identity_keys_from_context(
    context: Dict[str, object],
    max_depth: int = DEFAULT_CONTEXT_SCAN_MAX_DEPTH,
    max_nodes: int = DEFAULT_CONTEXT_SCAN_MAX_NODES,
    max_keys: int = DEFAULT_CONTEXT_SCAN_MAX_KEYS,
) -> Set[str]:
    keys: Set[str] = set()
    visited = [0]

    def visit(value: object, path: Tuple[str, ...] = ()) -> None:
        if _scan_budget_exhausted(visited, keys, max_nodes, max_keys) or len(path) > max_depth:
            return
        visited[0] += 1
        if isinstance(value, dict):
            _add_precomputed_article_identity_keys(value, keys)
            if is_article_like(value, path):
                keys.update(article_identity_keys(value))
            digest_item = news_digest_article_item(value)
            if digest_item:
                keys.update(article_identity_keys(digest_item))
            if _scan_budget_exhausted(visited, keys, max_nodes, max_keys):
                return
            for key, child in value.items():
                visit(child, path + (str(key),))
        elif isinstance(value, list):
            for child in value[:max_nodes]:
                visit(child, path)
                if _scan_budget_exhausted(visited, keys, max_nodes, max_keys):
                    break

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
        "policy": "sent-story-once-with-new-fact-followup",
        "removedCount": result.removed_count,
        "beforeCount": result.before_count,
        "afterCount": result.after_count,
        "matchedKeyCount": len(sent_keys),
        "removedTitles": titles[:5],
        "reason": "이미 발송한 같은 기사·같은 사건은 다시 판단하지 않고, 검증된 새 사실이 추가된 경우만 후속 판단합니다.",
    }


def article_digest_context_item(item: Dict[str, object]) -> Dict[str, object]:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    kind = _first_nested_value(item, ["kind", "type", "sourceKind"]) or "news"
    return {
        "kind": kind,
        "evidenceId": _first_nested_value(item, ["evidenceId", "evidence_id", "id"]),
        "symbol": _first_nested_value(item, ["symbol"]),
        "source": _first_nested_value(item, ["source", "domain", "provider"]),
        "title": _first_nested_value(item, ["title", "headline"]),
        "url": _first_nested_value(item, URL_KEYS),
        "publishedAt": _first_nested_value(item, ["publishedAt", "published_at", "observedAt", "observed_at"]),
        "receiptNo": _first_nested_value(item, ["receiptNo", "receipt_no"]),
        "receiptDate": _first_nested_value(item, ["receiptDate", "receipt_date"]),
        "articleReadStatus": (
            _first_nested_value(item, ["articleReadStatus", "readStatus"])
            or _text(payload.get("articleReadStatus") or payload.get("readStatus"))
        ),
        "storyClusterId": article_story_cluster_id(item),
        "eventType": _first_nested_value(item, ["eventType", "event_type", "newsType"]),
        "relationScope": _first_nested_value(item, ["relationScope"]),
        "storyFactKeys": sorted(article_story_fact_keys(item)),
        "storyUpdate": _truthy(item.get("storyUpdate")),
        "identityKeys": sorted(article_identity_keys(item)),
        "weakIdentityKeys": sorted(article_weak_identity_keys(item)),
        "articleSourceRevision": _first_nested_value(item, ["articleSourceRevision"]),
        "articleEnrichmentRevision": _first_nested_value(item, ["articleEnrichmentRevision"]),
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
                    if keys and keys.intersection(sent) and not article_has_new_story_fact(child, sent):
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


def news_story_impact_from_context(context: Dict[str, object]) -> Dict[str, object]:
    """Select one verified, material story for compact decision notifications."""

    candidates: List[Dict[str, object]] = []

    def visit(value: object, path: Tuple[str, ...] = (), depth: int = 0) -> None:
        if depth > 7 or len(candidates) >= 80:
            return
        if isinstance(value, dict):
            if is_article_like(value, path):
                candidates.append(value)
            for key, child in value.items():
                visit(child, path + (str(key),), depth + 1)
        elif isinstance(value, list):
            for child in value[:120]:
                visit(child, path, depth + 1)

    visit(context or {})
    ranked: List[Tuple[int, Dict[str, object]]] = []
    for item in candidates:
        title = _first_nested_value(item, ["title", "headline", "articleSummaryKo"])
        if not title:
            continue
        read_status = _first_nested_value(item, ["articleReadStatus", "readStatus", "readScope"]).lower()
        materiality = _first_nested_value(item, ["materialityState"]).lower()
        relevance = _first_nested_value(item, ["relevanceState"]).lower()
        polarity = _first_nested_value(item, ["stockImpactPolarity", "impactPolarity", "polarity"]).lower()
        data_state = _first_nested_value(item, ["dataState"]).lower()
        validation_state = _first_nested_value(item, ["validationState"]).lower()
        trust_state = _first_nested_value(item, ["sourceTrustState"]).lower()
        inline_eligible = _truthy(_first_nested_value(item, ["decisionInlineEligible"]))
        is_body = read_status in {"body", "full", "article-body"}
        is_material = materiality == "material"
        is_direct = relevance == "direct"
        directional = polarity in {"risk", "support"}
        is_ready = data_state == "sufficient" and validation_state == "ready"
        is_trusted = trust_state in {"trusted", "standard"}
        if not (inline_eligible and is_body and is_material and is_direct and directional and is_ready and is_trusted):
            continue
        rank = 4 + (2 if polarity in {"risk", "support"} else 0)
        ranked.append((rank, item))
    if not ranked:
        return {}
    ranked.sort(key=lambda row: row[0], reverse=True)
    item = ranked[0][1]
    return {
        "storyClusterId": article_story_cluster_id(item),
        "storyFactKeys": sorted(article_story_fact_keys(item)),
        "headline": _first_nested_value(item, ["eventTakeaway", "articleSummaryKo", "title", "headline"]),
        "source": _first_nested_value(item, ["source", "provider", "domain"]),
        "url": _first_nested_value(item, URL_KEYS),
        "impact": _first_nested_value(item, ["stockImpactLabel", "stockImpactPolarity"]),
        "material": True,
        "verified": True,
        "decisionInlineEligible": True,
        "decisionInlineReason": _first_nested_value(item, ["decisionInlineReasonKo"]),
        "identityKeys": sorted(article_identity_keys(item)),
        "evidenceKeys": sorted({
            *_nested_values(item, ["sourceEventKey", "eventKey", "evidenceId", "evidence_id", "articleId", "article_id"]),
            normalize_article_url(_first_nested_value(item, URL_KEYS)),
        } - {""}),
        "decisionChanging": False,
        "deliveryMode": "event-digest",
    }


def _relation_context_from_notification_context(context: Dict[str, object]) -> Dict[str, object]:
    context = context if isinstance(context, dict) else {}
    relation = context.get("ontologyRelationContext")
    if not isinstance(relation, dict):
        relation = context.get("relationContext")
    return relation if isinstance(relation, dict) else {}


def news_story_is_decision_driver(impact: Dict[str, object], context: Dict[str, object]) -> bool:
    """Require the action plan itself to name news/research as a driver.

    A directly related article can be useful context without being the reason
    to send an investment action alert. This guard prevents the compact alert
    from attaching the most recent article to a macro/trend-driven decision.
    The relation-diff check below then ties the selected article to this exact
    decision generation.
    """

    impact = impact if isinstance(impact, dict) else {}
    if not impact.get("decisionInlineEligible"):
        return False
    relation = _relation_context_from_notification_context(context)
    plan = relation.get("executionPlan") if isinstance(relation.get("executionPlan"), dict) else {}
    drivers = plan.get("decisionDrivers") if isinstance(plan.get("decisionDrivers"), list) else []
    for driver in drivers:
        if not isinstance(driver, dict):
            continue
        category = str(driver.get("category") or "").strip().casefold()
        data_keys = {
            str(item or "").strip().casefold()
            for item in (driver.get("dataKeys") or [])
            if str(item or "").strip()
        }
        direct_category = category in NEWS_DECISION_DRIVER_CATEGORIES
        direct_data_key = any(
            any(token in key for token in NEWS_DECISION_DRIVER_DATA_KEY_TOKENS)
            for key in data_keys
        )
        if direct_category or direct_data_key:
            return True
    return False


def news_story_changes_decision(
    impact: Dict[str, object],
    relation_diff: Dict[str, object],
    context: Dict[str, object] = None,
) -> bool:
    """Allow an inline story only when it is new evidence for this change."""

    impact = impact if isinstance(impact, dict) else {}
    relation_diff = relation_diff if isinstance(relation_diff, dict) else {}
    transition = relation_diff.get("decisionTransition") if isinstance(relation_diff.get("decisionTransition"), dict) else {}
    if (
        not impact.get("decisionInlineEligible")
        or not transition.get("material")
        or not news_story_is_decision_driver(impact, context or {})
    ):
        return False
    added = {str(item or "").strip().casefold() for item in relation_diff.get("addedEvidenceKeys") or [] if str(item or "").strip()}
    story_keys = {
        str(item or "").strip().casefold()
        for item in list(impact.get("identityKeys") or []) + list(impact.get("evidenceKeys") or [])
        if str(item or "").strip()
    }
    if not added or not story_keys or not added.intersection(story_keys):
        return False
    components = set(str(item or "") for item in relation_diff.get("materialComponents") or [])
    return "evidenceKeys" in components or str(transition.get("kind") or "") == "initial"
