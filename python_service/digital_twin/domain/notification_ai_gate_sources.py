import html
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional

from .accounts import normalize_message_delivery_level
from .notification_ai import (
    news_headline_items,
    news_item_number,
    news_item_rank_score,
    notification_ai_prompt_context,
    research_evidence_items,
)
from .notification_ai_gate_contracts import KST
from .notification_ai_gate_text import (
    _line_after_colon,
    _number,
    _raw_lines,
    _text,
    append_unique_text,
    recursive_values,
    reference_date,
)


SOURCE_URL_KEYS = {"url", "sourceUrl", "sourceURL", "source_url", "link", "sourceUrls"}


def source_url_candidates(value: object) -> List[str]:
    if isinstance(value, list):
        rows: List[str] = []
        for item in value:
            rows.extend(source_url_candidates(item))
        return rows
    text = html.unescape(str(value or "").strip())
    return [text] if text.startswith(("http://", "https://")) else []


def source_url_is_truncated(value: str) -> bool:
    text = str(value or "").strip()
    return text.endswith("...") or text.endswith("…")


def append_unique_source_url(rows: List[str], value: object) -> None:
    for url in source_url_candidates(value):
        if not url:
            continue
        url_prefix = url.rstrip(".…")
        replaced = False
        skip = False
        for index, existing in enumerate(list(rows)):
            existing_prefix = existing.rstrip(".…")
            if existing == url:
                skip = True
                break
            if source_url_is_truncated(existing) and url.startswith(existing_prefix):
                rows[index] = url
                replaced = True
                break
            if source_url_is_truncated(url) and existing.startswith(url_prefix):
                skip = True
                break
        if not skip and not replaced:
            rows.append(url)


def collect_source_urls(value: object, limit: int = 16) -> List[str]:
    rows: List[str] = []

    def visit(item: object) -> None:
        if len(rows) >= limit:
            return
        if isinstance(item, dict):
            for key, raw in item.items():
                normalized = str(key or "").strip()
                if normalized in SOURCE_URL_KEYS:
                    append_unique_source_url(rows, raw)
                elif isinstance(raw, (dict, list)):
                    visit(raw)
        elif isinstance(item, list):
            for part in item:
                visit(part)

    visit(value)
    return rows[:limit]


def collect_source_detail_items(value: object, limit: int = 32) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []

    def visit(item: object) -> None:
        if len(rows) >= limit:
            return
        if isinstance(item, dict):
            if any(str(item.get(key) or "").strip().startswith(("http://", "https://")) for key in SOURCE_URL_KEYS):
                rows.append(item)
            for raw in item.values():
                if isinstance(raw, (dict, list)):
                    visit(raw)
        elif isinstance(item, list):
            for part in item:
                visit(part)

    visit(value)
    return rows[:limit]


def source_urls_from_context(context: Dict[str, object], payload: Dict[str, object] = None) -> List[str]:
    message_type = str((context or {}).get("messageType") or (context or {}).get("rule") or "notification")
    prompt_context = notification_ai_prompt_context(message_type, context or {})
    raw_urls = collect_source_urls([context or {}, prompt_context or {}, payload or {}], 24)
    return select_source_urls_for_message(context or {}, raw_urls, payload or {})


def source_labels_from_context(context: Dict[str, object], payload: Dict[str, object] = None) -> List[str]:
    prompt_context = notification_ai_prompt_context(str((context or {}).get("messageType") or (context or {}).get("rule") or "notification"), context or {})
    facts = prompt_context.get("facts") if isinstance(prompt_context.get("facts"), dict) else {}
    rows: List[str] = []
    for item in facts.get("researchEvidence") or []:
        if isinstance(item, dict):
            append_unique_text(rows, item.get("source") or item.get("domain") or item.get("provider"), 80)
    for item in facts.get("newsHeadlines") or []:
        if isinstance(item, dict):
            append_unique_text(rows, item.get("domain"), 80)
    disclosure = facts.get("disclosure") if isinstance(facts.get("disclosure"), dict) else {}
    append_unique_text(rows, disclosure.get("provider"), 80)
    for line in _raw_lines(context or {}):
        if str(line).startswith("출처"):
            append_unique_text(rows, _line_after_colon([line], "출처"), 80)
    for item in recursive_values(payload or {}, {"source", "provider", "domain"}, 8):
        append_unique_text(rows, item, 80)
    return rows[:8]

def source_url_label(url: str, index: int) -> str:
    text = str(url or "").lower()
    if "dart.fss.or.kr" in text or "opendart" in text:
        prefix = "공시 원문"
    elif "sec.gov" in text:
        prefix = "SEC 원문"
    elif "news" in text or "rss" in text:
        prefix = "뉴스 원문"
    else:
        prefix = "원문"
    return prefix + " " + str(index)


def first_source_url(item: Dict[str, object]) -> str:
    if not isinstance(item, dict):
        return ""
    for key in ["url", "sourceUrl", "sourceURL", "source_url", "link"]:
        value = str(item.get(key) or "").strip()
        if value.startswith(("http://", "https://")):
            return html.unescape(value)
    return ""


def source_detail_number(item: Dict[str, object], *keys: str) -> float:
    if not isinstance(item, dict):
        return 0.0
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    raw_payload = item.get("rawPayload") if isinstance(item.get("rawPayload"), dict) else {}
    for key in keys:
        for source in [item, payload, raw_payload]:
            value = source.get(key) if isinstance(source, dict) else None
            if value not in (None, ""):
                return _number(value)
    return 0.0


def source_reliability_text(item: Dict[str, object]) -> str:
    value = source_detail_number(item, "sourceReliability", "confidence")
    if not value:
        return ""
    pct = value * 100 if value <= 1 else value
    if pct >= 75:
        label = "높음"
    elif pct >= 55:
        label = "보통"
    else:
        label = "낮음"
    return label + "(" + str(round(pct, 1)).rstrip("0").rstrip(".") + "%)"


def source_score_piece(label: str, value: float) -> str:
    if not value:
        return ""
    return label + " " + str(round(value, 1)).rstrip("0").rstrip(".") + "점"


def source_detail_map(context: Dict[str, object]) -> Dict[str, Dict[str, object]]:
    message_type = str((context or {}).get("messageType") or (context or {}).get("rule") or "notification")
    prompt_context = notification_ai_prompt_context(message_type, context or {})
    rows: List[Dict[str, object]] = []
    rows.extend(research_evidence_items(context or {}))
    rows.extend(news_headline_items(context or {}))
    rows.extend(collect_source_detail_items([context or {}, prompt_context or {}], 48))
    details: Dict[str, Dict[str, object]] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        url = first_source_url(item)
        if not url:
            continue
        if url not in details:
            details[url] = item
            continue
        if news_item_rank_score(item, news_reference_datetime_for_source(context)) > news_item_rank_score(details[url], news_reference_datetime_for_source(context)):
            details[url] = item
    return details


def news_reference_datetime_for_source(context: Dict[str, object]) -> datetime:
    reference = reference_date(context or {})
    parsed = parse_source_datetime(reference)
    return parsed or datetime.now(timezone.utc)


def parse_source_datetime(value: object) -> Optional[datetime]:
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


def source_url_display_limit(context: Dict[str, object]) -> int:
    level = normalize_message_delivery_level((context or {}).get("messageDeliveryLevel"))
    return 5 if level == "advanced" else 3


def source_url_kind_priority(url: str, detail: Dict[str, object]) -> float:
    text = str(url or "").lower()
    kind = str((detail or {}).get("kind") or "").lower()
    if "dart.fss.or.kr" in text or "opendart" in text or kind == "disclosure":
        return 40.0
    if "sec.gov" in text:
        return 34.0
    return 0.0


def source_url_rank_score(url: str, detail: Dict[str, object], context: Dict[str, object]) -> float:
    if isinstance(detail, dict) and detail:
        relevance = news_item_number(detail, "relevanceScore", "relevance_score")
        return source_url_kind_priority(url, detail) + news_item_rank_score(detail, news_reference_datetime_for_source(context)) + (5.0 if relevance >= 80 else 0.0)
    text = str(url or "").lower()
    if "dart.fss.or.kr" in text or "opendart" in text:
        return 70.0
    if "sec.gov" in text:
        return 64.0
    return 20.0


def source_url_cluster_key(url: str, detail: Dict[str, object]) -> str:
    title = str((detail or {}).get("title") or (detail or {}).get("summary") or "").lower()
    if title:
        title = re.sub(r"[^0-9a-z가-힣]+", " ", title).strip()
        return "title:" + " ".join(title.split())[:80]
    return "url:" + str(url or "").strip()


def all_source_urls_for_context(context: Dict[str, object], payload: Dict[str, object] = None) -> List[str]:
    message_type = str((context or {}).get("messageType") or (context or {}).get("rule") or "notification")
    prompt_context = notification_ai_prompt_context(message_type, context or {})
    return collect_source_urls([context or {}, prompt_context or {}, payload or {}], 48)


def select_source_urls_for_message(
    context: Dict[str, object],
    urls: List[str],
    payload: Dict[str, object] = None,
) -> List[str]:
    limit = source_url_display_limit(context)
    details = source_detail_map(context)
    candidates: List[Dict[str, object]] = []
    seen_urls = set()
    for url in list(urls or []) + list(details.keys()):
        text = str(url or "").strip()
        if not text or text in seen_urls:
            continue
        seen_urls.add(text)
        detail = source_detail_for_url(text, details)
        candidates.append({
            "url": text,
            "detail": detail,
            "score": source_url_rank_score(text, detail, context),
            "relevance": news_item_number(detail, "relevanceScore", "relevance_score") if detail else 0.0,
        })
    candidates.sort(key=lambda item: (item["score"], item["relevance"], item["url"]), reverse=True)
    selected: List[str] = []
    selected_clusters = set()
    for item in candidates:
        cluster = source_url_cluster_key(item["url"], item["detail"])
        if cluster in selected_clusters:
            continue
        if item["relevance"] and item["relevance"] < 35 and len(candidates) > limit:
            continue
        selected.append(item["url"])
        selected_clusters.add(cluster)
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        for item in candidates:
            if item["url"] in selected:
                continue
            selected.append(item["url"])
            if len(selected) >= limit:
                break
    return selected[:limit]


def source_detail_for_url(url: str, details: Dict[str, Dict[str, object]]) -> Dict[str, object]:
    if url in details:
        return details[url]
    url_prefix = str(url or "").rstrip(".…")
    for key, item in details.items():
        key_prefix = str(key or "").rstrip(".…")
        if url_prefix and key.startswith(url_prefix):
            return item
        if key_prefix and url.startswith(key_prefix):
            return item
    return {}


def source_detail_summary(item: Dict[str, object]) -> str:
    if not isinstance(item, dict):
        return ""
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    raw_payload = item.get("rawPayload") if isinstance(item.get("rawPayload"), dict) else {}
    return _text(
        item.get("articleSummaryKo")
        or payload.get("articleSummaryKo")
        or raw_payload.get("articleSummaryKo")
        or item.get("summary")
        or item.get("analysisSummary")
        or payload.get("analysisSummary")
        or raw_payload.get("analysisSummary")
        or item.get("title"),
        140,
    )


def source_detail_text(item: Dict[str, object], *keys: str) -> str:
    if not isinstance(item, dict):
        return ""
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    raw_payload = item.get("rawPayload") if isinstance(item.get("rawPayload"), dict) else {}
    for key in keys:
        for source in [item, payload, raw_payload]:
            value = str(source.get(key) or "").strip() if isinstance(source, dict) else ""
            if value:
                return _text(value, 220)
    return ""


def source_detail_raw_text(item: Dict[str, object], *keys: str) -> str:
    if not isinstance(item, dict):
        return ""
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    raw_payload = item.get("rawPayload") if isinstance(item.get("rawPayload"), dict) else {}
    for key in keys:
        for source in [item, payload, raw_payload]:
            value = str(source.get(key) or "").strip() if isinstance(source, dict) else ""
            if value:
                return value
    return ""


def source_date_label(url: str, item: Dict[str, object]) -> str:
    kind = str((item or {}).get("kind") or "").strip().lower() if isinstance(item, dict) else ""
    text = str(url or "").lower()
    if kind == "news" or "news" in text or "rss" in text:
        return "기사일"
    if "dart.fss.or.kr" in text or "opendart" in text:
        return "공시일"
    return "게시일"


def format_source_published_at(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if re.fullmatch(r"\d{8}", raw):
        try:
            return datetime.strptime(raw, "%Y%m%d").strftime("%Y-%m-%d")
        except ValueError:
            return raw
    candidates = [
        raw,
        raw.replace("Z", "+00:00"),
        raw.replace(" KST", "+09:00"),
        raw.replace(" GMT", "+00:00"),
    ]
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")
        except ValueError:
            pass
    for fmt in ["%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S", "%Y%m%d%H%M%S"]:
        try:
            parsed = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
            return parsed.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")
        except ValueError:
            pass
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")
    except (TypeError, ValueError):
        return _text(raw, 40)


def source_published_at_text(item: Dict[str, object]) -> str:
    raw = source_detail_raw_text(
        item,
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
    return format_source_published_at(raw)


def source_url_rows(urls: List[str], context: Dict[str, object]) -> List[str]:
    details = source_detail_map(context)
    rows: List[str] = []
    displayed_urls = {str(url or "").strip() for url in urls or [] if str(url or "").strip()}
    for index, url in enumerate(urls, start=1):
        text = str(url or "").strip()
        if not text:
            continue
        label = source_url_label(text, index)
        detail = source_detail_for_url(text, details)
        title = _text((detail or {}).get("title") or (detail or {}).get("summary"), 92)
        source = _text((detail or {}).get("source") or (detail or {}).get("domain") or (detail or {}).get("provider"), 40)
        reliability = source_reliability_text(detail)
        relevance = source_score_piece("관련성", source_detail_number(detail, "relevanceScore"))
        materiality = source_score_piece("중요도", source_detail_number(detail, "materialityScore"))
        impact_label = source_detail_text(detail, "stockImpactLabel")
        impact_reason = source_detail_text(detail, "stockImpactReasonKo")
        published_at = source_published_at_text(detail)
        summary = source_detail_summary(detail)
        link_text = "• <a href=\"" + html.escape(text, quote=True) + "\">" + html.escape(label, quote=False) + "</a>"
        rows.append(link_text + (": " + html.escape(title, quote=False) if title else ""))
        meta = ", ".join(item for item in [
            (source_date_label(text, detail) + " " + published_at) if published_at else "",
            ("신뢰도 " + reliability) if reliability else "",
            relevance,
            materiality,
            ("주가 영향 " + impact_label) if impact_label else "",
            ("출처 " + source) if source else "",
        ] if item)
        if meta:
            rows.append("  " + html.escape(meta, quote=False))
        if summary and summary != title:
            rows.append("  " + html.escape("요약: " + summary, quote=False))
        if impact_reason:
            rows.append("  " + html.escape("영향 분석: " + impact_reason, quote=False))
    all_urls = set(all_source_urls_for_context(context))
    all_urls.update(details.keys())
    omitted = len([url for url in all_urls if url and url not in displayed_urls])
    if omitted > 0:
        rows.append("• " + html.escape("외 " + str(omitted) + "건은 웹 상세에서 확인", quote=False))
    return rows
