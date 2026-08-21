import hashlib
import re
from datetime import datetime
from typing import Dict, List, Set
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


STORY_IDENTITY_VERSION = "news-story-identity-v4"
TRACKING_KEYS = {"fbclid", "gclid", "ref", "source", "utm_campaign", "utm_medium", "utm_source", "utm_term"}
EVENT_ACTIONS = (
    ("compensation", ("임금", "임단협", "성과급", "상여", "wage", "salary", "bonus", "compensation")),
    ("buyback", ("자기주식", "자사주", "주식소각", "share buyback", "stock repurchase", "share cancellation")),
    ("reorganization", ("조직개편", "쇄신", "인사개편", "reorganization", "restructuring meeting")),
    ("ipo", ("기업공개", "상장 추진", "ipo", "initial public offering")),
    ("strike", ("파업", "쟁의", "strike", "walkout")),
    ("app-store", ("앱스토어", "app store")),
    ("contract", ("공급계약", "수주", "supply agreement", "contract award")),
    ("earnings", ("실적", "매출", "영업이익", "earnings", "revenue", "profit")),
)
COMPENSATION_MARKERS = ("임금", "임단협", "성과급", "상여", "보너스", "wage", "salary", "bonus", "compensation")
SHARE_COMPENSATION_MARKERS = ("지급", "교부", "처분", "보상", "pay", "paid", "grant", "award", "transfer")
BUYBACK_EXECUTION_MARKERS = ("취득", "매입", "소각", "buyback", "repurchase", "cancellation", "cancel shares")
EVENT_STOP_WORDS = {
    "the", "a", "an", "and", "or", "to", "of", "for", "in", "on", "with", "from",
    "about", "are", "as", "how", "what", "why", "finance", "yahoo", "news",
    "stock", "stocks", "share", "shares",
    "관련", "대한", "통해", "위한", "올해", "지난", "종합", "단독", "속보",
}


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


def _first(item: Dict[str, object], *keys: str) -> str:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    facts = payload.get("articleFacts") if isinstance(payload.get("articleFacts"), dict) else {}
    for source in (item, payload, facts):
        for key in keys:
            value = source.get(key) if isinstance(source, dict) else None
            if value not in (None, "", [], {}):
                return str(value).strip()
    return ""


def _event_action(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value or "").casefold())
    if any(marker in text for marker in COMPENSATION_MARKERS) and any(
        marker in text for marker in SHARE_COMPENSATION_MARKERS
    ):
        return "compensation"
    if any(marker in text for marker in BUYBACK_EXECUTION_MARKERS):
        return "buyback"
    for action, markers in EVENT_ACTIONS:
        if any(marker in text for marker in markers):
            return action
    return "general"


def _number_keys(value: object) -> List[str]:
    units = {
        "%": "percent",
        "조": "trillion",
        "조원": "trillion",
        "억원": "hundred-million",
        "억": "hundred-million",
        "trillion": "trillion",
        "billion": "billion",
        "million": "million",
    }
    rows = []
    for number, unit in re.findall(
        r"(?<![0-9])([0-9]+(?:\.[0-9]+)?)\s*(%|조원?|억원?|trillion|billion|million)",
        str(value or "").casefold(),
    ):
        rows.append(number.rstrip("0").rstrip(".") + ":" + units.get(unit, unit))
    return sorted(set(rows))[:8]


def _event_tokens(value: object) -> Set[str]:
    tokens = {
        token for token in re.findall(r"[a-z0-9가-힣]{2,}", str(value or "").casefold())
        if token not in EVENT_STOP_WORDS and not token.isdigit()
    }
    return set(sorted(tokens)[:32])


def story_event_features(item: Dict[str, object]) -> Dict[str, object]:
    title = _first(item, "title", "headline", "name")
    summary = _first(item, "articleSummaryKo", "summary", "koreanSummary")
    event_type = _first(item, "eventType", "newsType", "category").casefold() or "general"
    corpus = title + " " + summary
    return {
        "symbol": _first(item, "symbol", "ticker", "relatedSymbol").upper(),
        "date": _date_bucket(_first(item, "publishedAt", "observedAt", "seenDate")),
        "eventType": event_type,
        # Clustering must fail closed. Generic words in an AI summary (for
        # example "earnings impact") cannot turn unrelated headlines into
        # the same corporate action.
        "action": _event_action(title),
        "numbers": _number_keys(corpus),
        "tokens": _event_tokens(corpus),
    }


def event_cluster_identity(item: Dict[str, object]) -> str:
    features = story_event_features(item)
    if features["symbol"] and features["date"] and features["action"] != "general" and features["numbers"]:
        return _hash("semantic|" + "|".join([
            str(features["symbol"]),
            str(features["date"]),
            str(features["action"]),
            str(features["eventType"]),
            ",".join(features["numbers"]),
        ]))
    return ""


def same_story_event(left: Dict[str, object], right: Dict[str, object]) -> bool:
    left_features = story_event_features(left)
    right_features = story_event_features(right)
    if not left_features["symbol"] or left_features["symbol"] != right_features["symbol"]:
        return False
    try:
        date_distance = abs((
            datetime.fromisoformat(str(left_features["date"]))
            - datetime.fromisoformat(str(right_features["date"]))
        ).days)
    except ValueError:
        date_distance = 0 if left_features["date"] == right_features["date"] else 99
    if date_distance > 1:
        return False
    action = left_features["action"]
    if action == "general" or action != right_features["action"]:
        return False
    left_event_type = str(left_features["eventType"] or "general")
    right_event_type = str(right_features["eventType"] or "general")
    if left_event_type != right_event_type and "general" not in {left_event_type, right_event_type}:
        return False
    left_numbers = set(left_features["numbers"])
    right_numbers = set(right_features["numbers"])
    if left_numbers and right_numbers and not left_numbers.intersection(right_numbers):
        return False
    shared_tokens = set(left_features["tokens"]).intersection(right_features["tokens"])
    required_overlap = 1 if left_numbers and right_numbers else 3
    return len(shared_tokens) >= required_overlap


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
    semantic_identity = event_cluster_identity(item)
    if semantic_identity:
        return semantic_identity
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
