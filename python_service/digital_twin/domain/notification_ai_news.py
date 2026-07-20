import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional

from .notification_ai_constants import KST
from .notification_ai_context import (
    active_investment_opinion_value,
    context_raw_lines,
    normalized_text,
    relation_facts,
)
from .news_analysis import relation_scope_is_excluded
from .security_lines import security_lines_for_symbol


NEWS_DATE_KEYS = (
    "publishedAt",
    "published_at",
    "observedAt",
    "observed_at",
    "seenDate",
    "seendate",
    "pubDate",
    "published",
    "time_published",
    "timePublished",
)
LOW_QUALITY_RELATION_SCOPES = {"noise", "unrelated", "irrelevant", "platform_noise", "publisher_noise", "entity_mismatch", "low_confidence_context", "syndicated_duplicate"}
MISLEADING_KEYWORDS_BY_SYMBOL = {
    "PLTR": {"전기차", "ev", "electric vehicle", "battery", "배터리"},
}

NEWS_SYMBOL_KEYS = (
    "symbol",
    "ticker",
    "targetSymbol",
    "target_symbol",
    "securitySymbol",
    "security_symbol",
    "instrumentSymbol",
    "instrument_symbol",
)
NEWS_SYMBOL_LIST_KEYS = (
    "symbols",
    "tickers",
    "targetSymbols",
    "target_symbols",
    "relatedSymbols",
    "related_symbols",
)


def normalized_news_symbol(value: object) -> str:
    text = str(value or "").upper().strip()
    if text.endswith((".KS", ".KQ")):
        text = text[:-3]
    return text


def target_symbols_from_context(context: Dict[str, object]) -> List[str]:
    context = context if isinstance(context, dict) else {}
    relation_context = context.get("ontologyRelationContext") if isinstance(context.get("ontologyRelationContext"), dict) else {}
    facts = relation_context.get("facts") if isinstance(relation_context.get("facts"), dict) else {}
    subject = relation_context.get("subject") if isinstance(relation_context.get("subject"), dict) else {}
    candidates = [
        context.get("symbol"),
        context.get("rawSymbol"),
        facts.get("symbol"),
        subject.get("symbol"),
    ]
    symbols: List[str] = []
    for value in candidates:
        symbol = normalized_news_symbol(value)
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    for base_symbol in list(symbols):
        for line in security_lines_for_symbol(base_symbol):
            for value in [line.local_symbol, line.symbol, line.underlying_symbol]:
                symbol = normalized_news_symbol(value)
                if symbol and symbol not in symbols:
                    symbols.append(symbol)
    return symbols


def news_item_explicit_symbols(item: Dict[str, object]) -> List[str]:
    symbols: List[str] = []
    for payload in _nested_payloads(item):
        for key in NEWS_SYMBOL_KEYS:
            symbol = normalized_news_symbol(payload.get(key))
            if symbol and symbol not in symbols:
                symbols.append(symbol)
        for key in NEWS_SYMBOL_LIST_KEYS:
            raw = payload.get(key)
            values = raw if isinstance(raw, list) else re.split(r"[,|/\s]+", str(raw or ""))
            for value in values:
                symbol = normalized_news_symbol(value)
                if symbol and symbol not in symbols:
                    symbols.append(symbol)
    return symbols


def news_item_targets_context(item: Dict[str, object], context: Dict[str, object]) -> bool:
    targets = set(target_symbols_from_context(context))
    explicit = set(news_item_explicit_symbols(item))
    return not targets or not explicit or bool(targets.intersection(explicit))


def _items_from_target_group(raw: object, context: Dict[str, object]) -> List[Dict[str, object]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if not isinstance(raw, dict):
        return []
    if isinstance(raw.get("items"), list):
        return [item for item in raw.get("items") or [] if isinstance(item, dict)]
    rows: List[Dict[str, object]] = []
    for symbol in target_symbols_from_context(context):
        group = raw.get(symbol)
        if isinstance(group, list):
            rows.extend(item for item in group if isinstance(item, dict))
        elif isinstance(group, dict) and isinstance(group.get("items"), list):
            rows.extend(item for item in group.get("items") or [] if isinstance(item, dict))
    return rows


def news_headline_items(context: Dict[str, object]) -> List[Dict[str, object]]:
    facts = relation_facts(context)
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    rows: List[Dict[str, object]] = []
    for container in [facts, metadata, context]:
        rows.extend(_items_from_target_group(container.get("newsHeadlines"), context))
    return [
        item for item in rows
        if str(item.get("title") or "").strip() and news_item_targets_context(item, context)
    ]


def _nested_payloads(item: Dict[str, object]) -> List[Dict[str, object]]:
    if not isinstance(item, dict):
        return []
    rows = [item]
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    raw_payload = item.get("rawPayload") if isinstance(item.get("rawPayload"), dict) else {}
    if payload:
        rows.append(payload)
    if raw_payload:
        rows.append(raw_payload)
    return rows


def news_item_value(item: Dict[str, object], *keys: str) -> object:
    for key in keys:
        for payload in _nested_payloads(item):
            value = payload.get(key)
            if value not in (None, ""):
                return value
    return ""


def news_item_number(item: Dict[str, object], *keys: str) -> float:
    value = news_item_value(item, *keys)
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0
    return number * 100 if 0 < number <= 1 else number


def target_terms_from_context(context: Dict[str, object]) -> List[str]:
    values = [
        context.get("symbol") if isinstance(context, dict) else "",
        context.get("rawSymbol") if isinstance(context, dict) else "",
        context.get("target") if isinstance(context, dict) else "",
        context.get("displayTarget") if isinstance(context, dict) else "",
        context.get("title") if isinstance(context, dict) else "",
    ]
    terms: List[str] = []
    for value in values:
        for token in re.split(r"[/|,()\s]+", str(value or "")):
            text = token.strip()
            if len(text) >= 2 and text.lower() not in {"the", "and", "주식", "알림"} and text not in terms:
                terms.append(text)
    return terms[:8]


def news_item_text(item: Dict[str, object]) -> str:
    values = [
        item.get("title"),
        item.get("summary"),
        item.get("articleSummaryKo"),
        item.get("domain"),
        item.get("provider"),
        item.get("source"),
        news_item_value(item, "title"),
        news_item_value(item, "summary"),
        news_item_value(item, "articleSummaryKo"),
        news_item_value(item, "coreKeyword", "keyword", "keywords", "핵심키워드"),
    ]
    return " ".join(str(value or "") for value in values if str(value or "").strip())


def news_summary_candidate(item: Dict[str, object]) -> str:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    summary = str(
        item.get("articleSummaryKo")
        or payload.get("articleSummaryKo")
        or item.get("summary")
        or item.get("title")
        or ""
    ).strip()
    noisy_markers = ["관련 뉴스입니다", "뉴스 유형은", "관련성 분류는", "핵심 키워드는"]
    if any(marker in summary for marker in noisy_markers):
        return str(item.get("title") or summary).strip()
    return summary


def news_item_quality_reason(item: Dict[str, object], context: Dict[str, object] = None) -> str:
    context = context or {}
    if not news_item_targets_context(item, context):
        return "different target symbol"
    scope = str(news_item_value(item, "relationScope", "relation_scope") or "").strip().lower()
    if scope in LOW_QUALITY_RELATION_SCOPES or relation_scope_is_excluded(scope):
        return "relationScope=" + scope
    relevance = news_item_number(item, "relevanceScore", "relevance_score")
    reliability = news_item_number(item, "sourceReliability", "confidence")
    materiality = news_item_number(item, "materialityScore", "impactScore", "impact_score")
    if relevance and relevance < 35:
        return "low relevance"
    if reliability and reliability < 35:
        return "low reliability"
    target_terms = target_terms_from_context(context)
    text = normalized_text(news_item_text(item))
    mentions_target = any(normalized_text(term) in text for term in target_terms if len(str(term)) >= 2)
    if target_terms and not mentions_target and (relevance < 55 or materiality < 45):
        return "target not mentioned"
    symbol = str((context or {}).get("symbol") or (target_terms[0] if target_terms else "")).upper()
    misleading = MISLEADING_KEYWORDS_BY_SYMBOL.get(symbol, set())
    keyword_text = normalized_text(news_item_value(item, "coreKeyword", "keyword", "keywords", "핵심키워드"))
    if misleading and any(keyword in keyword_text for keyword in misleading):
        return "misleading keyword"
    return ""


def news_item_is_usable(item: Dict[str, object], context: Dict[str, object] = None) -> bool:
    return not news_item_quality_reason(item, context)


def parse_news_datetime(value: object) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    if re.fullmatch(r"\d{8}", raw):
        try:
            return datetime.strptime(raw, "%Y%m%d").replace(tzinfo=KST)
        except ValueError:
            return None
    candidates = [
        raw,
        raw.replace("Z", "+00:00"),
        raw.replace(" KST", "+09:00"),
        raw.replace(" GMT", "+00:00"),
    ]
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
        except ValueError:
            pass
    for fmt in ["%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S", "%Y%m%d%H%M%S"]:
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        parsed = parsedate_to_datetime(raw)
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (TypeError, ValueError):
        return None


def news_item_published_at(item: Dict[str, object]) -> Optional[datetime]:
    for key in NEWS_DATE_KEYS:
        parsed = parse_news_datetime(news_item_value(item, key))
        if parsed:
            return parsed
    return None


def news_reference_datetime(context: Dict[str, object] = None) -> datetime:
    candidates: List[object] = []
    if isinstance(context, dict):
        candidates.extend([
            context.get("referenceDate"),
            context.get("sentTime"),
            context.get("eventGeneratedAt"),
            context.get("generatedAt"),
        ])
        for line in context_raw_lines(context):
            if line.startswith("기준일:") or line.startswith("기준시각:"):
                candidates.append(line.split(":", 1)[1].strip())
    for value in candidates:
        parsed = parse_news_datetime(value)
        if parsed:
            return parsed
    return datetime.now(timezone.utc)


def news_freshness_score(item: Dict[str, object], reference: datetime = None) -> float:
    published = news_item_published_at(item)
    if not published:
        return 30.0
    reference = reference or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    age_hours = max(0.0, (reference.astimezone(timezone.utc) - published.astimezone(timezone.utc)).total_seconds() / 3600.0)
    if age_hours <= 6:
        return 100.0
    if age_hours <= 24:
        return 85.0
    if age_hours <= 72:
        return 65.0
    if age_hours <= 168:
        return 35.0
    return 10.0


def news_item_rank_score(item: Dict[str, object], reference: datetime = None) -> float:
    relevance = news_item_number(item, "relevanceScore", "relevance_score")
    materiality = news_item_number(item, "materialityScore", "impactScore", "impact_score")
    reliability = news_item_number(item, "sourceReliability", "confidence")
    freshness = news_freshness_score(item, reference)
    impact_label = str(news_item_value(item, "stockImpactLabel") or "").strip()
    impact_bonus = 6.0 if impact_label and impact_label not in {"중립", "neutral", "Neutral"} else 0.0
    return round(relevance * 0.45 + freshness * 0.30 + reliability * 0.15 + materiality * 0.10 + impact_bonus, 3)


def news_cluster_key(item: Dict[str, object]) -> str:
    title = str(item.get("title") or item.get("summary") or news_item_value(item, "title") or "").lower()
    title = re.sub(r"https?://\S+", "", title)
    title = re.sub(r"[^0-9a-z가-힣]+", " ", title).strip()
    return " ".join(title.split())[:80]


def rank_news_items(items: List[Dict[str, object]], context: Dict[str, object] = None, limit: int = 0) -> List[Dict[str, object]]:
    reference = news_reference_datetime(context or {})
    ranked: List[Dict[str, object]] = []
    seen_clusters = set()
    for index, item in enumerate(items or []):
        if not isinstance(item, dict):
            continue
        if not news_item_is_usable(item, context or {}):
            continue
        cluster = news_cluster_key(item)
        if cluster and cluster in seen_clusters:
            continue
        if cluster:
            seen_clusters.add(cluster)
        relevance = news_item_number(item, "relevanceScore", "relevance_score")
        freshness = news_freshness_score(item, reference)
        ranked.append({
            "index": index,
            "score": news_item_rank_score(item, reference),
            "relevance": relevance,
            "freshness": freshness,
            "item": item,
        })
    ranked.sort(key=lambda row: (row["score"], row["relevance"], row["freshness"], -row["index"]), reverse=True)
    rows = [row["item"] for row in ranked]
    return rows[:limit] if limit and limit > 0 else rows


def selected_news_headline_items(context: Dict[str, object], limit: int = 3) -> List[Dict[str, object]]:
    return rank_news_items(news_headline_items(context), context, limit)


def research_evidence_items(context: Dict[str, object]) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []

    def add_items(raw_items) -> None:
        for item in _items_from_target_group(raw_items, context):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("summary") or "").strip()
            if title and news_item_targets_context(item, context):
                rows.append(item)

    facts = relation_facts(context)
    add_items(facts.get("researchEvidence"))
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    add_items(metadata.get("researchEvidence"))
    add_items(context.get("researchEvidence"))
    active_opinion = active_investment_opinion_value(context)
    if active_opinion:
        add_items(active_opinion.get("evidence"))
        add_items(active_opinion.get("counterEvidence"))
    seen = set()
    unique: List[Dict[str, object]] = []
    for item in rows:
        key = "|".join([
            str(item.get("kind") or ""),
            str(item.get("source") or ""),
            str(item.get("title") or item.get("summary") or ""),
            str(item.get("url") or ""),
        ])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def selected_research_evidence_items(context: Dict[str, object], limit: int = 8) -> List[Dict[str, object]]:
    return rank_news_items(research_evidence_items(context), context, limit)
