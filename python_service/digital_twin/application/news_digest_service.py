import hashlib
import html
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Tuple

from ..domain.accounts import AccountConfig
from ..domain.events import DomainEvent, RESEARCH_EVIDENCE_COLLECTED
from ..domain.market_data import number
from ..domain.message_types import NEWS_DIGEST
from ..domain.notifications import NotificationJob, notification_debug_number
from ..domain.portfolio import utc_now_iso


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


def reliability_label(value: object) -> str:
    score = number(value)
    if 0 < score <= 1:
        score *= 100
    if score >= 80:
        return "높음"
    if score >= 60:
        return "보통"
    if score > 0:
        return "낮음"
    return "미확인"


def impact_label(item: Dict[str, object]) -> str:
    raw = (
        clean_text(item.get("stockImpactLabel"))
        or IMPACT_LABELS.get(clean_text(item.get("stockImpactPolarity")).lower(), "")
        or IMPACT_LABELS.get(clean_text(item.get("polarity")).lower(), "")
    )
    return raw or "중립"


def article_read_status(item: Dict[str, object]) -> str:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    return clean_text(item.get("articleReadStatus") or payload.get("articleReadStatus"))


def article_analysis_label(item: Dict[str, object]) -> str:
    status = article_read_status(item)
    if status == "body":
        return "기사 본문 읽음, 본문 기반 요약/영향 계산"
    return "제목/RSS 요약만 사용"


def item_summary(item: Dict[str, object]) -> str:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    return (
        bounded_text(item.get("articleSummaryKo"), 180)
        or bounded_text(item.get("analysisSummary"), 180)
        or bounded_text(item.get("summary"), 180)
        or bounded_text(payload.get("articleSummaryKo"), 180)
        or bounded_text(item.get("title"), 180)
    )


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

    def event_items(self, event: DomainEvent) -> List[Dict[str, object]]:
        payload = event.payload or {}
        if "materialChangedItems" in payload:
            raw_items = payload.get("materialChangedItems") or []
        else:
            raw_items = payload.get("changedItems") or []
        if not isinstance(raw_items, list):
            return []
        items = [dict(item) for item in raw_items if isinstance(item, dict)]
        if self.require_article_body():
            items = [item for item in items if article_read_status(item) == "body"]
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
            return items[: self.max_items]
        scoped = []
        for item in items:
            symbol = normalized_symbol(item.get("symbol"))
            if symbol in known_symbols:
                item = dict(item)
                item["portfolioBucket"] = "보유" if symbol in holdings else "관심"
                item["displayName"] = holdings.get(symbol) or watchlist.get(symbol) or symbol
                scoped.append(item)
        return scoped[: self.max_items]

    def enqueue_account_digest(self, account: AccountConfig, items: List[Dict[str, object]], event: DomainEvent) -> None:
        primary = items[0]
        primary_id = item_evidence_id(primary)
        context = self.context(account, items, event)
        job = NotificationJob.create(
            "",
            account_id=account.account_id,
            account_label=account.label,
            message_type=NEWS_DIGEST,
            source_event_id=event.event_id,
            source_event_name=event.name,
            dedupe_key="newsDigest:" + account.account_id + ":" + short_dedupe_token(primary_id),
            context=context,
        )
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
        return {
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
                "primaryEvidenceId": item_evidence_id(primary),
                "primaryUrl": clean_text(primary.get("url")),
                "primaryTitle": clean_text(primary.get("title")),
                "primaryPublishedAt": clean_text(primary.get("publishedAt") or primary.get("observedAt")),
                "primaryArticleReadStatus": article_read_status(primary),
                "materialityScores": materiality_scores,
            },
        }

    def message_text(self, account: AccountConfig, items: List[Dict[str, object]], event: DomainEvent, tracking_number: str = "") -> str:
        reference = latest_timestamp(items)
        symbols = []
        for item in items:
            symbol = normalized_symbol(item.get("symbol"))
            name = clean_text(item.get("displayName") or symbol)
            bucket = clean_text(item.get("portfolioBucket") or "대상")
            label = name + ("(" + symbol + ")" if symbol and symbol != name else "")
            impact = impact_label(item)
            if label:
                symbols.append("• " + html_text(label) + ": " + html_text(bucket + " · " + impact + " 뉴스"))
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
                "• 판단: 영향 " + html_text(impact_label(item)) + ", 신뢰도 " + html_text(reliability)
                + (", 관련성 " + html_text(relevance) if relevance else "")
                + (", 중요도 " + html_text(importance) if importance else ""),
                "• 요약: " + html_text(item_summary(item)),
                "• 원문: " + link,
            ])
            if index < len(items):
                item_lines.append("")
        reason = "신선도·관련성·중요도 기준을 통과한 새 뉴스/피드 근거가 저장됐습니다."
        number = tracking_number or notification_debug_number(event.event_id)
        parts = [
            "🔔 새 알림",
            "",
            "[관찰] 🗞️ 뉴스/피드 새 정보",
            "보유/관심 종목 새 근거 감지",
            "",
            "요약",
            "• 기준시각: " + html_text(kst_datetime_text(reference)),
            "• 신규 중요 뉴스: " + str(len(items)) + "건",
            "• 목적: 다음 장 시작 전 가격 반응과 거래량을 확인하기 위한 준비 알림입니다.",
            "",
            "먼저 볼 것",
            *(symbols or ["• 대상 종목을 확인하세요."]),
            "",
            "새 정보",
            *item_lines,
            "",
            "알림이 온 이유",
            "• " + html_text(reason),
            "",
            "알림 추적",
            "• 번호: " + html_text(number),
        ]
        return "\n".join(parts).strip()
