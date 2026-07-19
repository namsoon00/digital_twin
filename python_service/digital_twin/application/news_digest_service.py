import hashlib
import html
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Tuple

from ..domain.accounts import AccountConfig
from ..domain.events import DomainEvent, RESEARCH_EVIDENCE_COLLECTED
from ..domain.market_data import number
from ..domain.message_types import NEWS_DIGEST
from ..domain.investment_research import NewsCollectionTarget
from ..domain.investment_strategy_guidance import merge_strategy_context, strategy_message_lines
from ..domain.news_analysis import analysis_payload_requires_refresh, classify_news_relevance, clean_article_summary_noise, relation_scope_is_investable
from ..domain.notifications import NotificationJob, notification_debug_number
from ..domain.portfolio import utc_now_iso
from ..domain.sent_article_filter import (
    article_digest_context_item,
    article_identity_keys,
    collect_article_identity_keys_from_context,
)


KST = timezone(timedelta(hours=9))
IMPACT_LABELS = {
    "support": "우호",
    "positive": "우호",
    "risk": "위험",
    "negative": "위험",
    "context": "중립",
    "neutral": "중립",
}


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


def score_text(value: object, suffix: str = "점") -> str:
    numeric = number(value)
    if numeric == 0 and str(value or "").strip() not in {"0", "0.0"}:
        return ""
    if abs(numeric - round(numeric)) < 0.05:
        return str(int(round(numeric))) + suffix
    return ("%.1f" % numeric).rstrip("0").rstrip(".") + suffix


def normalized_score(value: object) -> float:
    score = number(value)
    return score * 100 if 0 < score <= 1 else score


def reliability_label(value: object) -> str:
    score = normalized_score(value)
    if score >= 80:
        return "높음"
    if score >= 60:
        return "보통"
    if score > 0:
        return "낮음"
    return "미확인"


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
        or bounded_text(item.get("title"), 180)
    )


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


def compact_digest_line(label: str, value: object, seen: set, limit: int = 420) -> str:
    text = bounded_text(value, limit)
    if not text:
        return ""
    key = re.sub(r"\s+", "", text).casefold()
    if key in seen:
        return ""
    seen.add(key)
    return "• " + label + ": " + html_text(text)


def item_watch_text(item: Dict[str, object]) -> str:
    return ai_watch_line(item) or "다음 장 가격 반응과 거래량 동반 여부"


def alert_reason_context_item(item: Dict[str, object]) -> Dict[str, object]:
    symbol = normalized_symbol(item.get("symbol"))
    name = clean_text(item.get("displayName") or symbol or "종목")
    title = bounded_text(item.get("title"), 90)
    bucket = clean_text(item.get("portfolioBucket") or "대상")
    relevance = score_text(item.get("relevanceScore") or (item.get("payload") or {}).get("relevanceScore"))
    importance = score_text(item.get("materialityScore") or (item.get("payload") or {}).get("materialityScore"))
    return {
        "symbol": symbol,
        "name": name,
        "title": title,
        "bucket": bucket,
        "impact": impact_label(item),
        "relevance": relevance,
        "importance": importance,
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
    title = str(primary.get("title") or "제목 미확인")
    impact = str(primary.get("impact") or "중립")
    score_parts = [part for part in [primary.get("relevance"), primary.get("importance")] if part]
    score_text_value = "·".join(str(part) for part in score_parts)
    lines = [
        "• 새 뉴스가 들어왔습니다: " + html_text(title),
        "• 이 뉴스는 " + html_text(target) + " " + html_text(bucket) + " 종목과 직접 관련된 " + html_text(impact) + " 뉴스로 분류됐습니다.",
    ]
    if score_text_value:
        lines.append("• 관련성·중요도 " + html_text(score_text_value) + " 기준을 통과해서 지금 알림을 보냈습니다.")
    else:
        lines.append("• 새 근거가 기존 보유/관심 종목과 연결되어 지금 알림을 보냈습니다.")
    lines.extend([
        "• 단독 매수·매도 신호가 아니라, 가격 반응과 거래량이 같은 방향으로 따라오는지 확인하라는 알림입니다.",
        "• 확인할 것: " + html_text(primary.get("watch") or "다음 장 가격 반응과 거래량 동반 여부"),
    ])
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


def item_sort_key(item: Dict[str, object]) -> Tuple[float, float, float]:
    materiality = number(item.get("materialityScore") or (item.get("payload") or {}).get("materialityScore"))
    relevance = number(item.get("relevanceScore") or (item.get("payload") or {}).get("relevanceScore"))
    impact = number(item.get("stockImpactScore") or item.get("impactScore"))
    return materiality, relevance, impact


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

    def quality_gate_enabled(self) -> bool:
        value = self.settings.get("newsDigestHighQualityOnly")
        if value in (None, ""):
            return True
        return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}

    def min_relevance_score(self) -> float:
        return number(self.settings.get("newsDigestMinRelevanceScore")) or 85

    def min_materiality_score(self) -> float:
        return number(self.settings.get("newsDigestMinMaterialityScore")) or 70

    def min_neutral_materiality_score(self) -> float:
        return number(self.settings.get("newsDigestMinNeutralMaterialityScore")) or 78

    def min_source_reliability(self) -> float:
        return number(self.settings.get("newsDigestMinSourceReliability")) or 68

    def sent_article_filter_enabled(self) -> bool:
        value = self.settings.get("sentArticleFilterEnabled", self.settings.get("newsSentArticleFilterEnabled"))
        if value in (None, ""):
            return True
        return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}

    def sent_article_history_limit(self) -> int:
        return max(20, min(200, int(number(self.settings.get("sentArticleFilterHistoryLimit")) or 200)))

    def item_payload_score(self, item: Dict[str, object], key: str) -> float:
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        return normalized_score(item.get(key) if item.get(key) not in (None, "") else payload.get(key))

    def item_relation_scope(self, item: Dict[str, object]) -> str:
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        return clean_text(item.get("relationScope") or payload.get("relationScope")).lower()

    def refresh_item_analysis(self, item: Dict[str, object]) -> Dict[str, object]:
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
        summary = item_summary(item)
        if not summary or "Comprehensive" in summary or "Google News입니다" in summary or "상승-으로-date" in summary:
            return False
        reliability = self.item_payload_score(item, "sourceReliability")
        relevance = self.item_payload_score(item, "relevanceScore")
        materiality = self.item_payload_score(item, "materialityScore")
        impact = normalized_score(item.get("stockImpactScore") or item.get("impactScore"))
        polarity = clean_text(item.get("stockImpactPolarity") or item.get("polarity")).lower()
        label = impact_label(item)
        required_materiality = self.min_neutral_materiality_score() if label == "중립" or polarity in {"context", "neutral"} else self.min_materiality_score()
        return (
            reliability >= self.min_source_reliability()
            and relevance >= self.min_relevance_score()
            and max(materiality, impact) >= required_materiality
        )

    def handle(self, event: DomainEvent) -> None:
        if event.name != RESEARCH_EVIDENCE_COLLECTED:
            return
        items = self.event_items(event)
        if not items:
            return
        accounts = [account for account in (self.account_repository.load() or []) if isinstance(account, AccountConfig) and account.enabled]
        for account in accounts:
            scoped_items = self.items_for_account(account, items)
            if scoped_items:
                self.enqueue_account_digest(account, scoped_items, event)

    def previously_sent_article_keys(self, account: AccountConfig) -> set:
        if not self.sent_article_filter_enabled() or not hasattr(self.queue, "recent"):
            return set()
        try:
            recent_jobs = self.queue.recent(limit=self.sent_article_history_limit(), status="done")
        except TypeError:
            recent_jobs = self.queue.recent(self.sent_article_history_limit())
        except Exception:  # noqa: BLE001 - duplicate filtering must not block new evidence handling.
            return set()
        keys = set()
        account_id = str(getattr(account, "account_id", "") or "")
        for job in recent_jobs or []:
            if account_id and str(getattr(job, "account_id", "") or "") != account_id:
                continue
            keys.update(collect_article_identity_keys_from_context(getattr(job, "context", {}) or {}))
        return keys

    def exclude_previously_sent_articles(self, account: AccountConfig, items: List[Dict[str, object]]) -> List[Dict[str, object]]:
        sent_keys = self.previously_sent_article_keys(account)
        if not sent_keys:
            return items
        return [item for item in items if not article_identity_keys(item).intersection(sent_keys)]

    def event_items(self, event: DomainEvent) -> List[Dict[str, object]]:
        payload = event.payload or {}
        if "materialChangedItems" in payload:
            raw_items = payload.get("materialChangedItems") or []
        else:
            raw_items = payload.get("changedItems") or []
        if not isinstance(raw_items, list):
            return []
        items = [self.refresh_item_analysis(dict(item)) for item in raw_items if isinstance(item, dict)]
        items = [item for item in items if relation_scope_is_investable(self.item_relation_scope(item))]
        if self.require_article_body():
            items = [item for item in items if article_read_status(item) == "body"]
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
            return self.exclude_previously_sent_articles(account, items)[: self.max_items]
        scoped = []
        for item in items:
            symbol = normalized_symbol(item.get("symbol"))
            if symbol in known_symbols:
                item = dict(item)
                item["portfolioBucket"] = "보유" if symbol in holdings else "관심"
                item["displayName"] = holdings.get(symbol) or watchlist.get(symbol) or symbol
                scoped.append(item)
        return self.exclude_previously_sent_articles(account, scoped)[: self.max_items]

    def enqueue_account_digest(self, account: AccountConfig, items: List[Dict[str, object]], event: DomainEvent) -> None:
        primary = items[0]
        primary_id = item_evidence_id(primary)
        article_keys = sorted({key for item in items for key in article_identity_keys(item)})
        primary_token = "|".join(sorted(article_identity_keys(primary))) or primary_id
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
            "policy": "sent-article-once",
            "articleKeyCount": len(article_keys),
            "reason": "이미 발송한 기사 또는 같은 제목의 기사 근거는 다시 판단하지 않습니다.",
        }
        text = self.message_text(account, items, event, notification_debug_number(job.job_id))
        context["body"] = text
        job.text = text
        job.context = context
        self.queue.enqueue(job)

    def context(self, account: AccountConfig, items: List[Dict[str, object]], event: DomainEvent) -> Dict[str, object]:
        primary = items[0]
        symbols = [normalized_symbol(item.get("symbol")) for item in items if normalized_symbol(item.get("symbol"))]
        materiality_scores = [number(item.get("materialityScore") or (item.get("payload") or {}).get("materialityScore")) for item in items]
        severity = "ALERT" if max(materiality_scores or [0]) >= 80 or impact_label(primary) == "위험" else "WATCH"
        article_items = [article_digest_context_item(item) for item in items]
        article_keys = sorted({key for item in items for key in article_identity_keys(item)})
        context = {
            "messageType": NEWS_DIGEST,
            "accountId": account.account_id,
            "accountLabel": account.label,
            "severity": severity,
            "symbol": normalized_symbol(primary.get("symbol")),
            "title": "뉴스/피드 새 정보",
            "body": "",
            "referenceDate": latest_timestamp(items),
            "generatedAt": event.occurred_at,
            "notificationSignals": ["important", "confirmingData", "actionable"],
            "messageDeliveryLevel": account.message_delivery_profile()["level"],
            "messageDeliveryLevelLabel": account.message_delivery_profile()["label"],
            "newsDigest": {
                "itemCount": len(items),
                "symbols": symbols,
                "items": article_items,
                "articleKeys": article_keys,
                "primaryEvidenceId": item_evidence_id(primary),
                "primaryUrl": clean_text(primary.get("url")),
                "primaryTitle": clean_text(primary.get("title")),
                "primaryPublishedAt": clean_text(primary.get("publishedAt") or primary.get("observedAt")),
                "primaryArticleReadStatus": article_read_status(primary),
                "materialityScores": materiality_scores,
            },
        }
        return merge_strategy_context(context, account)

    def message_text(self, account: AccountConfig, items: List[Dict[str, object]], event: DomainEvent, tracking_number: str = "") -> str:
        reference = latest_timestamp(items)
        strategy_context = merge_strategy_context({}, account)
        strategy_lines = strategy_message_lines(strategy_context)
        effect_lines = impact_summary_lines(items)
        reason_lines = alert_reason_lines(items)
        item_lines = []
        for index, item in enumerate(items, start=1):
            symbol = normalized_symbol(item.get("symbol"))
            name = clean_text(item.get("displayName") or symbol or "종목")
            title = clean_text(item.get("title"))
            source = clean_text(item.get("source") or "출처 미확인")
            published_at = short_datetime_text(item.get("publishedAt") or item.get("observedAt")) or "기사일 미확인"
            relevance = score_text(item.get("relevanceScore") or (item.get("payload") or {}).get("relevanceScore"))
            importance = score_text(item.get("materialityScore") or (item.get("payload") or {}).get("materialityScore"))
            reliability = reliability_label(item.get("sourceReliability") or (item.get("payload") or {}).get("sourceReliability"))
            url = clean_text(item.get("url"))
            link = '<a href="' + html_attr(url) + '">원문 보기</a>' if url else "원문 없음"
            item_lines.extend([
                str(index) + ". " + html_text(name + (" / " + symbol if symbol and symbol != name else "")),
                "• 제목: " + html_text(title),
                "• 기사일: " + html_text(published_at) + ", 출처: " + html_text(source),
                "• 분석: " + html_text(article_analysis_label(item)),
            ])
            facts_line = article_facts_line(item)
            summary_line = item_summary(item)
            impact_reason = item_impact_reason(item)
            portfolio_implication = item_portfolio_implication(item)
            action_boundary = item_action_boundary(item)
            reason_line = ai_reason_line(item)
            watch_line = ai_watch_line(item)
            item_lines.extend([
                "• 판단: 영향 " + html_text(impact_label(item)) + ", 신뢰도 " + html_text(reliability)
                + (", 관련성 " + html_text(relevance) if relevance else "")
                + (", 중요도 " + html_text(importance) if importance else ""),
            ])
            seen_detail_lines = set()
            for line in [
                compact_digest_line("핵심 내용", summary_line, seen_detail_lines),
                compact_digest_line("투자 영향", portfolio_implication or impact_reason, seen_detail_lines),
                compact_digest_line("판단 근거", facts_line or reason_line, seen_detail_lines, limit=260),
            ]:
                if line:
                    item_lines.append(line)
            if action_boundary:
                item_lines.append("• 대응 경계: " + html_text(action_boundary))
            if watch_line:
                item_lines.append("• 확인할 것: " + html_text(watch_line))
            item_lines.append("• 원문: " + link)
            if index < len(items):
                item_lines.append("")
        number = tracking_number or notification_debug_number(event.event_id)
        parts = [
            "🔔 새 알림 · 새 뉴스 " + str(len(items)) + "건",
            "",
            "[관찰] 🗞️ 뉴스/피드 새 정보",
            "보유/관심 종목 새 근거 감지",
            "",
            "요약",
            "• 기준시각: " + html_text(kst_datetime_text(reference)),
            "• 신규 중요 뉴스: " + str(len(items)) + "건",
            "• 목적: 다음 장 시작 전 가격 반응과 거래량을 확인하기 위한 준비 알림입니다.",
            "",
            "계정 성향 기준",
            *(html_text(line) for line in strategy_lines),
            "",
            "이번 뉴스 핵심",
            *(html_text(line) for line in effect_lines or ["• 기사별 영향 해석을 확인하세요."]),
            "",
            "기사 상세",
            *item_lines,
            "",
            "알림이 온 이유",
            *reason_lines,
            "",
            "알림 추적",
            "• 번호: " + html_text(number),
        ]
        return "\n".join(parts).strip()
