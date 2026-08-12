import hashlib
import html
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Tuple

from ..domain.accounts import AccountConfig
from ..domain.events import DomainEvent, NEWS_ARTICLE_ANALYZED, RESEARCH_EVIDENCE_COLLECTED
from ..domain.data_freshness import freshness_record
from ..domain.market_data import number
from ..domain.message_types import NEWS_DIGEST
from ..domain.investment_research import NewsCollectionTarget
from ..domain.investment_strategy_guidance import merge_strategy_context
from ..domain.news_analysis import (
    NEWS_MATERIALITY_STATE_LABELS,
    NEWS_RELEVANCE_STATE_LABELS,
    NEWS_SOURCE_TRUST_STATE_LABELS,
    analysis_payload_requires_refresh,
    article_body_quality,
    classify_news_relevance,
    clean_article_summary_noise,
    news_state_rank,
    news_state_payload,
    relation_scope_is_investable,
)
from ..domain.news_ai_analysis import clean_summary_text, summary_texts_similar
from ..domain.notifications import NotificationJob, notification_debug_number
from ..domain.portfolio import utc_now_iso
from ..domain.sent_article_filter import (
    article_digest_context_item,
    article_has_new_story_fact,
    article_identity_keys,
    article_story_cluster_id,
    collect_article_identity_keys_from_context,
)
from ..news_intelligence.domain.eligibility import assess_news_eligibility


KST = timezone(timedelta(hours=9))
IMPACT_LABELS = {
    "support": "우호",
    "positive": "우호",
    "risk": "위험",
    "negative": "위험",
    "context": "중립",
    "neutral": "중립",
}

DISCLOSURE_EVIDENCE_KINDS = {"disclosure", "filing", "sec-filing", "sec_filing"}
NEWS_EVIDENCE_KINDS = {"news", "article", "news-article", "rss"}
MAX_SOURCES_PER_EVENT = 3
DEFAULT_RECONCILIATION_INITIAL_LOOKBACK_MINUTES = 1
DEFAULT_RECONCILIATION_MAX_REPLAY_AGE_MINUTES = 180
DEFAULT_RECONCILIATION_BATCH_SIZE = 100


def clean_text(value: object, fallback: str = "") -> str:
    return " ".join(str(value if value is not None else fallback).split()).strip()


def html_text(value: object) -> str:
    return html.escape(clean_text(value), quote=False)


def html_attr(value: object) -> str:
    return html.escape(clean_text(value), quote=True)


def bounded_text(value: object, limit: int = 160) -> str:
    text = clean_text(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def normalized_symbol(value: object) -> str:
    return clean_text(value).upper()


def parse_datetime(value: object):
    text = clean_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text + "T00:00:00+00:00")
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def kst_datetime_text(value: object) -> str:
    parsed = parse_datetime(value)
    if not parsed:
        return clean_text(value)
    return parsed.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")


def short_datetime_text(value: object) -> str:
    parsed = parse_datetime(value)
    if not parsed:
        return clean_text(value)
    return parsed.astimezone(KST).strftime("%m/%d %H:%M KST")


def item_news_states(item: Dict[str, object]) -> Dict[str, str]:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    return news_state_payload({**payload, **item})


def item_payload(item: Dict[str, object]) -> Dict[str, object]:
    return item.get("payload") if isinstance(item.get("payload"), dict) else {}


def item_event_kind(item: Dict[str, object]) -> str:
    raw = clean_text(item.get("kind") or item_payload(item).get("kind") or item.get("evidenceKind")).lower()
    if raw in DISCLOSURE_EVIDENCE_KINDS:
        return "disclosure"
    if raw in NEWS_EVIDENCE_KINDS:
        return "news"
    return ""


def event_label(items: List[Dict[str, object]]) -> str:
    return "공시" if items and item_event_kind(items[0]) == "disclosure" else "뉴스"


def item_original_title(item: Dict[str, object]) -> str:
    payload = item_payload(item)
    analysis = ai_analysis(item)
    return bounded_text(
        analysis.get("originalTitle")
        or item.get("originalTitle")
        or payload.get("originalTitle")
        or item.get("title"),
        520,
    )


def item_translated_title(item: Dict[str, object]) -> str:
    payload = item_payload(item)
    analysis = ai_analysis(item)
    translated = bounded_text(
        analysis.get("translatedTitleKo")
        or item.get("translatedTitleKo")
        or payload.get("translatedTitleKo"),
        520,
    )
    original = item_original_title(item)
    return "" if translated and summary_texts_similar(translated, original) else translated


def item_source_language(item: Dict[str, object]) -> str:
    payload = item_payload(item)
    analysis = ai_analysis(item)
    return clean_text(analysis.get("sourceLanguage") or item.get("sourceLanguage") or payload.get("sourceLanguage")).lower()


def title_needs_korean_translation(item: Dict[str, object]) -> bool:
    title = item_original_title(item)
    language = item_source_language(item)
    if language.startswith("en"):
        return True
    return bool(title and re.search(r"[A-Za-z]", title) and not re.search(r"[가-힣]", title))


def item_title_translation_ready(item: Dict[str, object]) -> bool:
    return item_event_kind(item) != "news" or not title_needs_korean_translation(item) or bool(item_translated_title(item))


def item_importance_label(item: Dict[str, object]) -> str:
    state = item_news_states(item).get("materialityState")
    return {"material": "높음", "notable": "보통", "context": "참고"}.get(state, "확인 필요")


def item_confidence_label(item: Dict[str, object]) -> str:
    states = item_news_states(item)
    payload = item_payload(item)
    if item_event_kind(item) == "disclosure":
        if clean_text(payload.get("officialDocumentText") or item.get("officialDocumentText")):
            return "공식 원문 확인됨"
        return "공식 공시 확인됨"
    read_status = article_read_status(item)
    trust = states.get("sourceTrustState")
    if read_status == "body" and trust == "trusted":
        return "본문·출처 확인됨"
    if read_status == "body" and trust == "standard":
        return "본문 확인됨"
    if read_status == "body":
        return "본문 추가 확인 필요"
    return "제목·RSS 기반"


def freshness_text(value: object, reference: object = "") -> str:
    published = parse_datetime(value)
    if not published:
        return "시각 확인 중"
    compared_at = parse_datetime(reference) or datetime.now(timezone.utc)
    minutes = max(0, int((compared_at - published).total_seconds() // 60))
    if minutes < 1:
        relative = "방금 전"
    elif minutes < 60:
        relative = str(minutes) + "분 전"
    elif minutes < 24 * 60:
        relative = str(minutes // 60) + "시간 전"
    elif minutes < 7 * 24 * 60:
        relative = str(minutes // (24 * 60)) + "일 전"
    else:
        relative = short_datetime_text(value)
    return relative + " · " + short_datetime_text(value)


def event_reference_timestamp(event: DomainEvent) -> str:
    """Use the dispatch event clock, never another article's collection clock."""
    occurred_at = clean_text(getattr(event, "occurred_at", ""))
    return occurred_at if parse_datetime(occurred_at) else utc_now_iso()


def event_is_story_update(items: List[Dict[str, object]]) -> bool:
    return any(bool(item.get("storyUpdate")) for item in items or [])


def event_icon(items: List[Dict[str, object]], reference: object = "") -> str:
    if event_is_story_update(items):
        return "↻"
    if items and item_event_kind(items[0]) == "disclosure":
        return "📄"
    primary = items[0] if items else {}
    states = item_news_states(primary)
    published = parse_datetime(primary.get("publishedAt") or primary.get("observedAt"))
    compared_at = parse_datetime(reference) or datetime.now(timezone.utc)
    age_minutes = (compared_at - published).total_seconds() / 60 if published else 10_000
    if (
        states.get("materialityState") == "material"
        and states.get("relevanceState") == "direct"
        and states.get("sourceTrustState") in {"trusted", "standard"}
        and article_read_status(primary) == "body"
        and 0 <= age_minutes <= 60
    ):
        return "⚡"
    return "📰"


def event_group_key(item: Dict[str, object]) -> str:
    kind = item_event_kind(item) or "news"
    payload = item_payload(item)
    if kind == "disclosure":
        receipt = clean_text(item.get("receiptNo") or payload.get("receiptNo") or payload.get("receipt_no"))
        if receipt:
            return kind + ":receipt:" + receipt
    cluster = article_story_cluster_id(item)
    if cluster:
        return kind + ":" + cluster
    return kind + ":" + item_evidence_id(item)


def source_names(items: List[Dict[str, object]]) -> List[str]:
    rows: List[str] = []
    for item in items or []:
        source = clean_text(item.get("source") or item.get("domain") or item_payload(item).get("sourcePublisher"))
        if source and source not in rows:
            rows.append(source)
    return rows


def event_source_items(items: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """Keep one best article per publisher for a single reported event."""

    selected: List[Dict[str, object]] = []
    seen = set()
    for item in items or []:
        source = clean_text(item.get("source") or item.get("domain") or item_payload(item).get("sourcePublisher"))
        key = source.casefold() or item_evidence_id(item)
        if not key or key in seen:
            continue
        selected.append(item)
        seen.add(key)
        if len(selected) >= MAX_SOURCES_PER_EVENT:
            break
    return selected


def grouped_event_items(items: List[Dict[str, object]]) -> List[List[Dict[str, object]]]:
    """Group syndicated copies into one user-visible event, in importance order."""

    groups: Dict[str, List[Dict[str, object]]] = {}
    for item in sorted(items or [], key=item_sort_key, reverse=True):
        groups.setdefault(event_group_key(item), []).append(item)
    return [event_source_items(group) for group in groups.values() if group]


def confirmed_fact_lines(item: Dict[str, object]) -> List[str]:
    payload = item_payload(item)
    if item_event_kind(item) == "disclosure":
        rows = []
        receipt_date = clean_text(item.get("receiptDate") or payload.get("receiptDate") or payload.get("receipt_date") or item.get("publishedAt"))
        receipt_no = clean_text(item.get("receiptNo") or payload.get("receiptNo") or payload.get("receipt_no"))
        if receipt_date:
            rows.append("접수일 " + receipt_date)
        if receipt_no:
            rows.append("접수번호 " + receipt_no)
        if clean_text(payload.get("officialDocumentText") or item.get("officialDocumentText")):
            rows.append("공식 원문을 확보했습니다.")
        elif rows:
            rows.append("공식 공시 메타데이터를 확인했습니다. 세부 원문은 추가 확인이 필요합니다.")
        return rows[:3]

    facts = article_facts(item)
    rows = []
    takeaway = bounded_text(facts.get("eventTakeaway"), 220)
    summary = item_summary(item)
    if takeaway and not summary_texts_similar(takeaway, summary):
        rows.append(takeaway)
    if not rows:
        for sentence in article_fact_list(facts, "keySentences", 4):
            if not summary_texts_similar(sentence, summary):
                rows.append(sentence)
                break
    if not rows:
        fallback_sentences = article_fact_list(facts, "keySentences", 4)
        if fallback_sentences:
            # A brief summary can legitimately contain every extracted fact.
            # Prefer its second concrete sentence over an empty placeholder.
            rows.append(fallback_sentences[-1])
    numbers = article_fact_list(facts, "numbers", 3)
    if numbers:
        rows.append("본문 수치: " + ", ".join(numbers))
    if not rows:
        rows.append("원문에서 추출한 구조화된 사실을 준비 중입니다.")
    return rows[:3]


def state_at_least(value: object, minimum: object, order: Tuple[str, ...]) -> bool:
    ranks = {state: index for index, state in enumerate(order)}
    return ranks.get(str(value or ""), -1) >= ranks.get(str(minimum or ""), 0)


def impact_label(item: Dict[str, object]) -> str:
    analysis = ai_analysis(item)
    raw = (
        clean_text(analysis.get("impactLabelKo") if isinstance(analysis, dict) else "")
        or clean_text(analysis.get("impact_label_ko") if isinstance(analysis, dict) else "")
        or clean_text(item.get("stockImpactLabel"))
        or IMPACT_LABELS.get(clean_text(item.get("stockImpactPolarity")).lower(), "")
        or IMPACT_LABELS.get(clean_text(item.get("polarity")).lower(), "")
    )
    return raw or "중립"


def article_read_status(item: Dict[str, object]) -> str:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    status = clean_text(item.get("articleReadStatus") or payload.get("articleReadStatus"))
    facts = article_facts(item)
    if status == "body" and isinstance(facts, dict) and facts.get("bodyAvailable") is False:
        return "feed-summary"
    return status


def article_analysis_label(item: Dict[str, object]) -> str:
    status = article_read_status(item)
    analysis = ai_analysis(item)
    model = bounded_text(analysis.get("model"), 40) if isinstance(analysis, dict) else ""
    ai_suffix = "AI 요약/영향 계산"
    if model and model not in {"local-news-semantic-analyzer-v1", "unit"}:
        ai_suffix += " · " + model
    if isinstance(analysis, dict) and str(analysis.get("status") or "") == "fallback":
        ai_suffix += " · fallback"
    if status == "body":
        return "기사 본문 읽음, 본문 기반 " + ai_suffix
    if status == "source-blocked":
        return "제목/RSS 요약만 사용, 소셜·저품질 출처는 본문 근거로 보지 않음, " + ai_suffix
    return "제목/RSS 요약만 사용, " + ai_suffix


def article_facts(item: Dict[str, object]) -> Dict[str, object]:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    for source in [item, payload]:
        facts = source.get("articleFacts") if isinstance(source, dict) else None
        if isinstance(facts, dict):
            return facts
    return {}


def ai_analysis(item: Dict[str, object]) -> Dict[str, object]:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    for source in [item, payload]:
        analysis = source.get("aiAnalysis") if isinstance(source, dict) else None
        if isinstance(analysis, dict):
            return analysis
    return {}


def ai_summary(item: Dict[str, object]) -> Dict[str, object]:
    analysis = ai_analysis(item)
    summary = analysis.get("summary") if isinstance(analysis, dict) else {}
    return summary if isinstance(summary, dict) else {}


def ai_text(item: Dict[str, object], key: str, limit: int = 360) -> str:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    analysis = ai_analysis(item)
    for source in [analysis, item, payload]:
        if not isinstance(source, dict):
            continue
        text = bounded_text(clean_article_summary_noise(source.get(key)), limit)
        if text:
            return text
    return ""


def ai_list(item: Dict[str, object], key: str, limit: int = 4) -> List[str]:
    analysis = ai_analysis(item)
    values = analysis.get(key) if isinstance(analysis, dict) else []
    if not isinstance(values, list):
        return []
    rows: List[str] = []
    for value in values:
        text = bounded_text(value, 80)
        if text and text not in rows:
            rows.append(text)
        if len(rows) >= limit:
            break
    return rows


def article_fact_list(facts: Dict[str, object], key: str, limit: int = 3) -> List[str]:
    value = facts.get(key) if isinstance(facts, dict) else None
    if not isinstance(value, list):
        return []
    rows: List[str] = []
    for item in value:
        text = bounded_text(item, 80)
        if text and text not in rows:
            rows.append(text)
        if len(rows) >= limit:
            break
    return rows


def article_facts_line(item: Dict[str, object]) -> str:
    facts = article_facts(item)
    if not facts:
        return ""
    pieces = []
    takeaway = bounded_text(facts.get("eventTakeaway"), 120)
    numbers = article_fact_list(facts, "numbers", 3)
    topics = article_fact_list(facts, "topics", 4)
    if takeaway:
        pieces.append("핵심 " + takeaway)
    if numbers:
        pieces.append("수치 " + ", ".join(numbers))
    if topics:
        pieces.append("주제 " + ", ".join(topics))
    return bounded_text(" · ".join(pieces), 260)


def item_summary(item: Dict[str, object]) -> str:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    summary = ai_summary(item)
    return (
        bounded_text(clean_article_summary_noise(summary.get("briefKo")), 420)
        or bounded_text(clean_article_summary_noise(summary.get("oneLineKo")), 260)
        or bounded_text(clean_article_summary_noise(item.get("articleSummaryKo")), 360)
        or bounded_text(clean_article_summary_noise(item.get("analysisSummary")), 260)
        or bounded_text(clean_article_summary_noise(item.get("summary")), 360)
        or bounded_text(clean_article_summary_noise(payload.get("articleSummaryKo")), 360)
    )


def item_article_body_is_usable(item: Dict[str, object]) -> bool:
    """Recheck saved body text before turning it into a user-facing alert.

    Older evidence can have been collected before an extractor boundary fix.
    Prefer a current deterministic body check whenever raw text is present,
    while retaining compatibility for compact legacy rows that only expose a
    previously validated body flag.
    """
    payload = item_payload(item)
    facts = article_facts(item)
    raw_body = ""
    for source in (item, payload, facts):
        if not isinstance(source, dict):
            continue
        for key in ("articleText", "articleTextPreview", "bodyPreview"):
            value = clean_text(source.get(key))
            if value:
                raw_body = value
                break
        if raw_body:
            break
    if raw_body:
        return bool(article_body_quality(raw_body).get("passed"))
    for source in (item, payload, facts):
        if not isinstance(source, dict) or "bodyQualityPassed" not in source:
            continue
        value = source.get("bodyQualityPassed")
        if value is False or clean_text(value).lower() in {"0", "false", "no"}:
            return False
    return True


def ai_reason_line(item: Dict[str, object]) -> str:
    risk = ai_list(item, "riskSignals", 3)
    support = ai_list(item, "supportSignals", 3)
    contrast = ai_list(item, "contrastSignals", 2)
    pieces = []
    if risk:
        pieces.append("위험 " + ", ".join(risk))
    if support:
        pieces.append("우호 " + ", ".join(support))
    if contrast:
        pieces.append("반전 문맥 " + ", ".join(contrast))
    return bounded_text(" · ".join(pieces), 260)


def item_impact_reason(item: Dict[str, object]) -> str:
    return (
        ai_text(item, "impactReasonKo", 420)
        or ai_text(item, "stockImpactReasonKo", 420)
        or ai_text(item, "rationaleKo", 420)
    )


def item_portfolio_implication(item: Dict[str, object]) -> str:
    return ai_text(item, "portfolioImplicationKo", 360)


def item_action_boundary(item: Dict[str, object]) -> str:
    return ai_text(item, "actionBoundaryKo", 320)


def item_investment_impact(item: Dict[str, object]) -> str:
    summary = ai_summary(item)
    why_it_matters = bounded_text(clean_summary_text(summary.get("whyItMatters")), 360)
    return why_it_matters or item_portfolio_implication(item) or item_impact_reason(item)


def normalized_impact_kind(item: Dict[str, object]) -> str:
    analysis = ai_analysis(item)
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    raw = clean_text(
        (analysis.get("impactPolarity") if isinstance(analysis, dict) else "")
        or item.get("stockImpactPolarity")
        or payload.get("stockImpactPolarity")
        or item.get("polarity")
    ).lower()
    label = impact_label(item)
    if raw in {"risk", "negative"} or label in {"악재", "위험"}:
        return "risk"
    if raw in {"support", "positive"} or label in {"호재", "우호"}:
        return "support"
    if raw == "mixed" or label == "혼재":
        return "mixed"
    return "neutral"


def impact_summary_bucket(item: Dict[str, object]) -> str:
    kind = normalized_impact_kind(item)
    if kind == "risk":
        return "단기 경계"
    if kind == "support":
        return "우호 재료"
    if kind == "mixed":
        return "방향 확인"
    return "영향 제한"


def impact_summary_lines(items: List[Dict[str, object]]) -> List[str]:
    rows: List[str] = []
    seen = set()
    for item in items:
        symbol = normalized_symbol(item.get("symbol"))
        name = clean_text(item.get("displayName") or symbol or "종목")
        label = name + ("(" + symbol + ")" if symbol and symbol != name else "")
        implication = item_portfolio_implication(item) or item_impact_reason(item) or item_summary(item)
        line = "• " + label + ": " + impact_summary_bucket(item) + ". " + bounded_text(implication, 220)
        key = re.sub(r"\s+", "", line).casefold()
        if line and key not in seen:
            rows.append(line)
            seen.add(key)
        if len(rows) >= 5:
            break
    return rows


def compact_digest_line(label: str, value: object, seen: List[str], limit: int = 420) -> str:
    text = bounded_text(clean_summary_text(value), limit)
    if not text:
        return ""
    if any(summary_texts_similar(text, existing) for existing in seen):
        return ""
    seen.append(text)
    return "• " + label + ": " + html_text(text)


def item_watch_text(item: Dict[str, object]) -> str:
    return ai_watch_line(item) or "다음 장 가격 반응과 거래량 동반 여부"


def alert_reason_context_item(item: Dict[str, object]) -> Dict[str, object]:
    symbol = normalized_symbol(item.get("symbol"))
    name = clean_text(item.get("displayName") or symbol or "종목")
    title = bounded_text(item.get("title"), 90)
    bucket = clean_text(item.get("portfolioBucket") or "대상")
    states = item_news_states(item)
    return {
        "symbol": symbol,
        "name": name,
        "title": title,
        "bucket": bucket,
        "impact": impact_label(item),
        "relevance": NEWS_RELEVANCE_STATE_LABELS.get(states["relevanceState"], ""),
        "importance": NEWS_MATERIALITY_STATE_LABELS.get(states["materialityState"], ""),
        "watch": item_watch_text(item),
    }


def alert_reason_lines(items: List[Dict[str, object]]) -> List[str]:
    if not items:
        return ["• 새 뉴스/피드 근거가 들어와 확인 알림을 보냈습니다."]
    primary = alert_reason_context_item(items[0])
    name = str(primary.get("name") or "종목")
    symbol = str(primary.get("symbol") or "")
    target = name + ((" / " + symbol) if symbol and symbol != name else "")
    bucket = str(primary.get("bucket") or "대상")
    passed_conditions = [part for part in [primary.get("relevance"), primary.get("importance")] if part]
    condition_text = " · ".join(str(part) for part in passed_conditions)
    lines = []
    if condition_text:
        lines.append(
            "• " + html_text(target) + " " + html_text(bucket)
            + " 종목의 새 기사이며 " + html_text(condition_text)
            + " 조건을 통과했습니다."
        )
    else:
        lines.append("• " + html_text(target) + "의 새 기사가 보유/관심 종목 기준을 통과했습니다.")
    lines.append("• 기사 한 건만으로 매수·매도를 결정하지 않고, 시장 확인 항목과 함께 판단합니다.")
    if len(items) > 1:
        lines.append("• 함께 들어온 새 뉴스가 " + str(len(items)) + "건이라 기사 상세에서 각각 확인할 수 있습니다.")
    return lines


def ai_watch_line(item: Dict[str, object]) -> str:
    summary = ai_summary(item)
    values = summary.get("watchPoints") if isinstance(summary, dict) else []
    if not isinstance(values, list):
        return ""
    rows = []
    for value in values:
        text = bounded_text(value, 80)
        if text and text not in rows:
            rows.append(text)
        if len(rows) >= 4:
            break
    return ", ".join(rows)


def item_sort_key(item: Dict[str, object]) -> Tuple[int, int, int, int, float]:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    states = news_state_rank({**payload, **item})
    polarity = normalized_impact_kind(item)
    directional = 1 if polarity in {"risk", "support", "mixed"} else 0
    published = parse_datetime(item.get("publishedAt") or item.get("observedAt"))
    timestamp = published.timestamp() if published else 0.0
    return (*states, directional, timestamp)


def latest_timestamp(items: Iterable[Dict[str, object]]) -> str:
    timestamps = []
    for item in items or []:
        for key in ["publishedAt", "observedAt"]:
            parsed = parse_datetime(item.get(key))
            if parsed:
                timestamps.append(parsed)
    if not timestamps:
        return utc_now_iso()
    return max(timestamps).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def item_evidence_id(item: Dict[str, object]) -> str:
    return clean_text(item.get("evidenceId") or item.get("id") or item.get("url") or item.get("title"))


def short_dedupe_token(value: object) -> str:
    text = clean_text(value)
    if not text:
        return "unknown"
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


class NewsDigestEventReconciler:
    """Replay durable research events when synchronous notification dispatch is lost."""

    def __init__(
        self,
        event_reader,
        enqueuer,
        cursor_store,
        batch_size: int = DEFAULT_RECONCILIATION_BATCH_SIZE,
        initial_lookback_minutes: int = DEFAULT_RECONCILIATION_INITIAL_LOOKBACK_MINUTES,
        max_replay_age_minutes: int = DEFAULT_RECONCILIATION_MAX_REPLAY_AGE_MINUTES,
        now_provider=None,
    ):
        self.event_reader = event_reader
        self.enqueuer = enqueuer
        self.cursor_store = cursor_store
        self.batch_size = max(1, min(500, int(batch_size or DEFAULT_RECONCILIATION_BATCH_SIZE)))
        self.initial_lookback_minutes = max(1, int(initial_lookback_minutes or DEFAULT_RECONCILIATION_INITIAL_LOOKBACK_MINUTES))
        self.max_replay_age_minutes = max(
            self.initial_lookback_minutes,
            int(max_replay_age_minutes or DEFAULT_RECONCILIATION_MAX_REPLAY_AGE_MINUTES),
        )
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.last_result: Dict[str, object] = {}

    @staticmethod
    def timestamp(value: datetime) -> str:
        parsed = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def run_once(self) -> Dict[str, object]:
        reader = getattr(self.event_reader, "research_evidence_events_after", None)
        if not callable(reader):
            self.last_result = {"status": "unsupported", "processedCount": 0, "queuedCount": 0}
            return dict(self.last_result)

        state = self.cursor_store.load() if self.cursor_store and hasattr(self.cursor_store, "load") else {}
        state = dict(state or {}) if isinstance(state, dict) else {}
        now = self.now_provider()
        if not isinstance(now, datetime):
            now = datetime.now(timezone.utc)
        if not now.tzinfo:
            now = now.replace(tzinfo=timezone.utc)
        initial_floor = now - timedelta(minutes=self.initial_lookback_minutes)
        replay_floor = now - timedelta(minutes=self.max_replay_age_minutes)
        stored_at_text = clean_text(state.get("lastOccurredAt"))
        stored_at = parse_datetime(stored_at_text)
        if not stored_at:
            after_at = initial_floor
            after_id = ""
        elif stored_at < replay_floor:
            after_at = replay_floor
            after_id = ""
        else:
            after_at = stored_at
            after_id = clean_text(state.get("lastEventId"))

        events = list(reader(
            after_occurred_at=self.timestamp(after_at),
            after_event_id=after_id,
            limit=self.batch_size,
        ) or [])
        processed = 0
        queued = 0
        last_at = self.timestamp(after_at)
        last_id = after_id
        for event in events:
            if str(getattr(event, "name", "") or "") != RESEARCH_EVIDENCE_COLLECTED:
                continue
            queued += int(self.enqueuer.handle(event) or 0)
            processed += 1
            last_at = clean_text(getattr(event, "occurred_at", "")) or last_at
            last_id = clean_text(getattr(event, "event_id", "")) or last_id

        if self.cursor_store and hasattr(self.cursor_store, "replace"):
            self.cursor_store.replace({
                "lastOccurredAt": last_at,
                "lastEventId": last_id,
                "processedCount": int(state.get("processedCount") or 0) + processed,
                "lastProcessedCount": processed,
                "lastQueuedCount": queued,
                "updatedAt": self.timestamp(now),
            })
        self.last_result = {
            "status": "ok" if processed else "idle",
            "processedCount": processed,
            "queuedCount": queued,
            "cursorOccurredAt": last_at,
            "cursorEventId": last_id,
            "hasMore": len(events) >= self.batch_size,
        }
        return dict(self.last_result)


class NewsDigestEnqueuer:
    def __init__(
        self,
        account_repository,
        monitor_store,
        queue,
        settings: Dict[str, object] = None,
        max_items: int = 3,
    ):
        self.account_repository = account_repository
        self.monitor_store = monitor_store
        self.queue = queue
        self.settings = dict(settings or {})
        self.max_items = max(1, int(max_items or 3))

    def require_article_body(self) -> bool:
        value = self.settings.get("newsDigestRequireArticleBody")
        if value in (None, ""):
            return True
        return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}

    def require_korean_title_translation(self) -> bool:
        value = self.settings.get("newsDigestRequireKoreanTitleTranslation")
        if value in (None, ""):
            return True
        return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}

    def quality_gate_enabled(self) -> bool:
        value = self.settings.get("newsDigestHighQualityOnly")
        if value in (None, ""):
            return True
        return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}

    def minimum_relevance_state(self) -> str:
        return clean_text(self.settings.get("newsDigestMinimumRelevanceState") or "direct").lower()

    def minimum_materiality_state(self) -> str:
        return clean_text(self.settings.get("newsDigestMinimumMaterialityState") or "notable").lower()

    def minimum_neutral_materiality_state(self) -> str:
        return clean_text(self.settings.get("newsDigestMinimumNeutralMaterialityState") or "material").lower()

    def minimum_source_trust_state(self) -> str:
        return clean_text(self.settings.get("newsDigestMinimumSourceTrustState") or "standard").lower()

    def sent_article_filter_enabled(self) -> bool:
        value = self.settings.get("sentArticleFilterEnabled", self.settings.get("newsSentArticleFilterEnabled"))
        if value in (None, ""):
            return True
        return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}

    def sent_article_history_limit(self) -> int:
        return max(20, min(200, int(number(self.settings.get("sentArticleFilterHistoryLimit")) or 200)))

    def item_relation_scope(self, item: Dict[str, object]) -> str:
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        return clean_text(item.get("relationScope") or payload.get("relationScope")).lower()

    def refresh_item_analysis(self, item: Dict[str, object]) -> Dict[str, object]:
        if item_event_kind(item) != "news":
            return item
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        title = clean_text(item.get("title"))
        merged_payload = {**payload, **{key: value for key, value in item.items() if key not in {"payload"}}}
        if not analysis_payload_requires_refresh(merged_payload) or not title:
            return item
        symbol = normalized_symbol(item.get("symbol"))
        target = NewsCollectionTarget(
            symbol,
            clean_text(item.get("name") or item.get("displayName") or symbol),
            clean_text(item.get("market")),
            clean_text(item.get("currency")),
            clean_text(item.get("sector")),
        )
        analysis = classify_news_relevance(
            target,
            title,
            item.get("summary") or item.get("articleSummaryKo") or title,
            item.get("source") or item.get("domain") or "",
            item.get("provider") or payload.get("provider") or "",
        )
        refreshed = dict(item)
        refreshed_payload = dict(payload)
        refreshed_payload.update(analysis)
        refreshed["payload"] = refreshed_payload
        for key, value in analysis.items():
            refreshed[key] = value
        return refreshed

    def item_passes_quality_gate(self, item: Dict[str, object]) -> bool:
        if not self.quality_gate_enabled():
            return True
        if not relation_scope_is_investable(self.item_relation_scope(item)):
            return False
        states = item_news_states(item)
        if item_event_kind(item) == "disclosure":
            return (
                state_at_least(states["sourceTrustState"], self.minimum_source_trust_state(), ("unknown", "limited", "standard", "trusted"))
                and state_at_least(states["materialityState"], self.minimum_materiality_state(), ("context", "notable", "material"))
                and states["validationState"] != "blocked"
            )
        payload = item_payload(item)
        eligibility = assess_news_eligibility(
            {**payload, **{key: value for key, value in item.items() if key != "payload"}},
            title=item.get("title") or item.get("headline") or "",
            summary=item.get("summary") or item.get("articleSummaryKo") or "",
            symbol=item.get("symbol") or "",
            name=payload.get("name") or payload.get("companyName") or "",
            source=item.get("source") or item.get("domain") or "",
            provider=item.get("provider") or payload.get("provider") or "",
            url=item.get("url") or "",
            lifecycle_state=item.get("evidenceLifecycleState") or payload.get("evidenceLifecycleState") or "active",
        )
        if not eligibility.alert.eligible:
            return False
        summary = item_summary(item)
        if not summary or "Comprehensive" in summary or "Google News입니다" in summary or "상승-으로-date" in summary:
            return False
        polarity = clean_text(item.get("stockImpactPolarity") or item.get("polarity")).lower()
        label = impact_label(item)
        required_materiality = self.minimum_neutral_materiality_state() if label == "중립" or polarity in {"context", "neutral"} else self.minimum_materiality_state()
        return (
            state_at_least(states["sourceTrustState"], self.minimum_source_trust_state(), ("unknown", "limited", "standard", "trusted"))
            and state_at_least(states["relevanceState"], self.minimum_relevance_state(), ("unrelated", "context", "related", "direct"))
            and state_at_least(states["materialityState"], required_materiality, ("context", "notable", "material"))
            and states["validationState"] != "blocked"
        )

    def handle(self, event: DomainEvent) -> int:
        if event.name not in {NEWS_ARTICLE_ANALYZED, RESEARCH_EVIDENCE_COLLECTED}:
            return 0
        items = self.event_items(event)
        if not items:
            return 0
        queued = 0
        accounts = [account for account in (self.account_repository.load() or []) if isinstance(account, AccountConfig) and account.enabled]
        for account in accounts:
            scoped_items = self.items_for_account(account, items)
            if scoped_items:
                queued += self.enqueue_account_digest(account, scoped_items, event)
        return queued

    def previously_sent_article_keys(self, account: AccountConfig) -> set:
        if not self.sent_article_filter_enabled():
            return set()
        account_id = str(getattr(account, "account_id", "") or "")
        durable_reader = getattr(self.queue, "sent_article_identity_keys", None)
        if callable(durable_reader):
            try:
                return set(durable_reader(account_id=account_id, limit=self.sent_article_history_limit()) or [])
            except TypeError:
                return set(durable_reader(account_id) or [])
            except Exception:  # noqa: BLE001 - duplicate filtering must not block new evidence handling.
                return set()
        if not hasattr(self.queue, "recent"):
            return set()
        try:
            recent_jobs = self.queue.recent(limit=self.sent_article_history_limit(), status="done")
        except TypeError:
            recent_jobs = self.queue.recent(self.sent_article_history_limit())
        except Exception:  # noqa: BLE001 - duplicate filtering must not block new evidence handling.
            return set()
        keys = set()
        for job in recent_jobs or []:
            if account_id and str(getattr(job, "account_id", "") or "") != account_id:
                continue
            keys.update(collect_article_identity_keys_from_context(getattr(job, "context", {}) or {}))
        return keys

    def exclude_previously_sent_articles(self, account: AccountConfig, items: List[Dict[str, object]]) -> List[Dict[str, object]]:
        sent_keys = self.previously_sent_article_keys(account)
        if not sent_keys:
            return items
        allowed = []
        for item in items:
            if not article_identity_keys(item).intersection(sent_keys):
                allowed.append(item)
                continue
            if article_has_new_story_fact(item, sent_keys):
                follow_up = dict(item)
                follow_up["storyUpdate"] = True
                allowed.append(follow_up)
        return allowed

    def event_items(self, event: DomainEvent) -> List[Dict[str, object]]:
        payload = event.payload or {}
        if "materialChangedItems" in payload:
            raw_items = payload.get("materialChangedItems") or []
        else:
            raw_items = payload.get("changedItems") or []
        if not isinstance(raw_items, list):
            return []
        items = [dict(item) for item in raw_items if isinstance(item, dict)]
        items = [item for item in items if item_event_kind(item) in {"news", "disclosure"}]
        items = [self.refresh_item_analysis(item) for item in items]
        items = [item for item in items if relation_scope_is_investable(self.item_relation_scope(item))]
        if self.require_article_body():
            items = [
                item for item in items
                if item_event_kind(item) == "disclosure"
                or (article_read_status(item) == "body" and item_article_body_is_usable(item))
            ]
        if self.require_korean_title_translation():
            items = [item for item in items if item_title_translation_ready(item)]
        if self.quality_gate_enabled():
            items = [item for item in items if self.item_passes_quality_gate(item)]
        items.sort(key=item_sort_key, reverse=True)
        return items

    def account_symbols(self, account: AccountConfig) -> Tuple[Dict[str, str], Dict[str, str]]:
        holdings: Dict[str, str] = {}
        watchlist: Dict[str, str] = {}
        previous = getattr(self.monitor_store, "previous", {}) or {}
        state = previous.get(account.account_id) if isinstance(previous, dict) else {}
        if isinstance(state, dict):
            positions = state.get("positions") if isinstance(state.get("positions"), dict) else {}
            for payload in positions.values():
                if not isinstance(payload, dict):
                    continue
                symbol = normalized_symbol(payload.get("symbol"))
                if symbol:
                    holdings[symbol] = clean_text(payload.get("name") or symbol)
            watch_payload = state.get("watchlist") if isinstance(state.get("watchlist"), dict) else {}
            for payload in watch_payload.values():
                if not isinstance(payload, dict):
                    continue
                symbol = normalized_symbol(payload.get("symbol"))
                if symbol:
                    watchlist[symbol] = clean_text(payload.get("name") or symbol)
        for symbol in account.watchlist_symbols or []:
            normalized = normalized_symbol(symbol)
            if normalized:
                watchlist.setdefault(normalized, normalized)
        return holdings, watchlist

    def items_for_account(self, account: AccountConfig, items: List[Dict[str, object]]) -> List[Dict[str, object]]:
        holdings, watchlist = self.account_symbols(account)
        known_symbols = set(holdings) | set(watchlist)
        if not known_symbols:
            return self.exclude_previously_sent_articles(account, items)
        scoped = []
        for item in items:
            symbol = normalized_symbol(item.get("symbol"))
            if symbol in known_symbols:
                item = dict(item)
                item["portfolioBucket"] = "보유" if symbol in holdings else "관심"
                item["displayName"] = holdings.get(symbol) or watchlist.get(symbol) or symbol
                scoped.append(item)
        return self.exclude_previously_sent_articles(account, scoped)

    def enqueue_account_digest(self, account: AccountConfig, items: List[Dict[str, object]], event: DomainEvent) -> int:
        queued = 0
        for event_items in grouped_event_items(items)[: self.max_items]:
            queued += self.enqueue_account_event(account, event_items, event)
        return queued

    def enqueue_account_event(self, account: AccountConfig, items: List[Dict[str, object]], event: DomainEvent) -> int:
        primary = items[0]
        primary_id = item_evidence_id(primary)
        article_keys = sorted({key for item in items for key in article_identity_keys(item)})
        primary_token = "|".join(article_keys) or primary_id
        context = self.context(account, items, event)
        job = NotificationJob.create(
            "",
            account_id=account.account_id,
            account_label=account.label,
            message_type=NEWS_DIGEST,
            source_event_id=event.event_id,
            source_event_name=event.name,
            dedupe_key="newsDigest:" + account.account_id + ":" + short_dedupe_token(primary_token),
            context=context,
        )
        context["sentArticleFilter"] = {
            "enabled": self.sent_article_filter_enabled(),
            "policy": "sent-article-once-with-verified-story-updates",
            "articleKeyCount": len(article_keys),
            "reason": "이미 알린 기사·사건은 다시 보내지 않고, 검증된 새 사실이 추가된 경우만 후속 알림으로 보냅니다.",
        }
        text = self.message_text(account, items, event, notification_debug_number(job.job_id))
        context["body"] = text
        job.text = text
        job.context = context
        return 1 if self.queue.enqueue(job) else 0

    def context(self, account: AccountConfig, items: List[Dict[str, object]], event: DomainEvent) -> Dict[str, object]:
        primary = items[0]
        event_kind = item_event_kind(primary) or "news"
        reference = event_reference_timestamp(event)
        icon = event_icon(items, reference)
        sources = source_names(items)
        delivery_mode = "story-update" if event_is_story_update(items) else "new-event"
        symbols = [normalized_symbol(item.get("symbol")) for item in items if normalized_symbol(item.get("symbol"))]
        materiality_states = [item_news_states(item)["materialityState"] for item in items]
        severity = "ALERT" if "material" in materiality_states or impact_label(primary) == "위험" else "WATCH"
        article_items = [article_digest_context_item(item) for item in items]
        article_keys = sorted({key for item in items for key in article_identity_keys(item)})
        payload = item_payload(primary)
        report_name = item_original_title(primary)
        receipt_no = clean_text(primary.get("receiptNo") or payload.get("receiptNo") or payload.get("receipt_no"))
        receipt_date = clean_text(primary.get("receiptDate") or payload.get("receiptDate") or payload.get("receipt_date") or primary.get("publishedAt"))
        raw_lines = []
        if event_kind == "disclosure":
            raw_lines = [
                "공시명: " + report_name,
                "접수일: " + (receipt_date or "확인 필요"),
                "접수번호: " + (receipt_no or "확인 필요"),
                "출처: " + (clean_text(primary.get("source")) or "OpenDART"),
            ]
            document_preview = bounded_text(payload.get("officialDocumentPreview") or payload.get("officialDocumentText"), 6000)
            if document_preview:
                raw_lines.append("공시 원문: " + document_preview)
        context = {
            "messageType": NEWS_DIGEST,
            "accountId": account.account_id,
            "accountLabel": account.label,
            "severity": severity,
            "symbol": normalized_symbol(primary.get("symbol")),
            "title": event_label(items),
            "notificationIcon": icon,
            "titleIcon": icon,
            "body": "",
            "referenceDate": reference,
            "generatedAt": event.occurred_at,
            "dataFreshness": freshness_record(
                clean_text(primary.get("source")) or "뉴스 원문",
                NEWS_DIGEST,
                source_fetched_at=primary.get("observedAt"),
                source_as_of=primary.get("publishedAt") or primary.get("observedAt"),
                data_quality=item_news_states(primary).get("dataState") or "",
            ),
            "notificationSignals": ["important", "confirmingData", "actionable"],
            "messageDeliveryLevel": account.message_delivery_profile()["level"],
            "messageDeliveryLevelLabel": account.message_delivery_profile()["label"],
            "reportName": report_name if event_kind == "disclosure" else "",
            "receiptNo": receipt_no if event_kind == "disclosure" else "",
            "receiptDate": receipt_date if event_kind == "disclosure" else "",
            "provider": clean_text(primary.get("source")) if event_kind == "disclosure" else "",
            "rawLines": raw_lines,
            "newsDigest": {
                "eventKind": event_kind,
                "eventIcon": icon,
                "eventClusterId": event_group_key(primary),
                "deliveryMode": delivery_mode,
                "itemCount": len(items),
                "sourceCount": len(sources),
                "sources": sources,
                "symbols": symbols,
                "items": article_items,
                "articleKeys": article_keys,
                "primaryEvidenceId": item_evidence_id(primary),
                "primaryUrl": clean_text(primary.get("url")),
                "primaryTitle": clean_text(primary.get("title")),
                "primaryPublishedAt": clean_text(primary.get("publishedAt") or primary.get("observedAt")),
                "primaryArticleReadStatus": article_read_status(primary),
                "primarySourceLanguage": item_source_language(primary),
                "primaryOriginalTitle": item_original_title(primary),
                "primaryTranslatedTitleKo": item_translated_title(primary),
                "materialityStates": materiality_states,
            },
        }
        return merge_strategy_context(context, account)

    def message_text(self, account: AccountConfig, items: List[Dict[str, object]], event: DomainEvent, tracking_number: str = "") -> str:
        reference = event_reference_timestamp(event)
        primary = items[0]
        label = event_label(items)
        icon = event_icon(items, reference)
        symbol = normalized_symbol(primary.get("symbol"))
        name = clean_text(primary.get("displayName") or symbol or "종목")
        target = name + (" / " + symbol if symbol and symbol != name else "")
        sources = source_names(items)
        source = sources[0] if sources else "출처 미확인"
        original_title = item_original_title(primary) or clean_text(primary.get("title"))
        translated_title = item_translated_title(primary)
        summary = item_summary(primary)
        facts = confirmed_fact_lines(primary)
        interpretations: List[str] = []
        for value in [item_investment_impact(primary), item_impact_reason(primary)]:
            text = bounded_text(value, 360)
            if text and not any(summary_texts_similar(text, existing) for existing in interpretations):
                interpretations.append(text)
        if not interpretations:
            if item_event_kind(primary) == "disclosure":
                interpretations.append("공시 메타데이터만으로 방향을 단정하지 않고, 원문 세부 내용과 시장 반응을 함께 확인합니다.")
            else:
                interpretations.append("원문과 가격 반응을 추가로 확인한 뒤 조건부 해석을 보완합니다.")
        watch_line = item_watch_text(primary)
        action_boundary = item_action_boundary(primary)
        url = clean_text(primary.get("url"))
        link = '<a href="' + html_attr(url) + '">원문 보기</a>' if url else "원문 링크 없음"
        reason_lines = alert_reason_lines(items)
        number = tracking_number or notification_debug_number(event.event_id)
        parts = [
            icon + " " + label + " · " + html_text(target),
            "중요도 " + html_text(item_importance_label(primary))
            + " · 영향 " + html_text(impact_label(primary))
            + " · 신뢰도 " + html_text(item_confidence_label(primary)),
            html_text(freshness_text(primary.get("publishedAt") or primary.get("observedAt"), reference))
            + " · " + html_text(source),
            "",
            "원문 제목",
            "• " + html_text(original_title or "제목 확인 중"),
        ]
        if translated_title:
            parts.extend(["한국어 제목", "• " + html_text(translated_title)])
        elif title_needs_korean_translation(primary):
            parts.extend(["한국어 제목", "• 번역 생성 중"])
        parts.extend([
            "",
            "한 줄 요약",
            "• " + html_text(summary or "본문 요약 생성 전입니다."),
            "",
            "확인된 사실",
            *("• " + html_text(line) for line in facts),
            "",
            "AI 해석 · 조건부",
            *("• " + html_text(line) for line in interpretations[:2]),
            "",
            "시장 확인",
            "• 다음 확인: " + html_text(watch_line),
        ])
        if action_boundary and not summary_texts_similar(action_boundary, watch_line):
            parts.append("• 판단 경계: " + html_text(action_boundary))
        parts.extend([
            "",
            "출처",
            "• 원문: " + link,
        ])
        if len(sources) > 1:
            parts.append("• 함께 수집된 출처 " + str(len(sources)) + "곳: " + html_text(", ".join(sources)))
        if event_is_story_update(items):
            parts.append("• 기존 사건의 검증된 새 사실이 추가된 후속 알림입니다.")
        parts.extend([
            "",
            "알림이 온 이유",
            *reason_lines,
            "",
            "알림 추적",
            "• 번호: " + html_text(number),
            "• 수집 기준시각: " + html_text(kst_datetime_text(reference)),
        ])
        return "\n".join(parts).strip()
