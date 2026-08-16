"""Compose actual price history and operational events for one instrument."""

import re
from typing import Dict, Iterable, List

from ..domain.instrument_timeline import InstrumentTimelineQuery
from ..domain.portfolio import utc_now_iso


def text(value: object) -> str:
    return str(value or "").strip()


def object_payload(value: object) -> Dict[str, object]:
    converter = getattr(value, "to_dict", None)
    if callable(converter):
        result = converter()
        return dict(result or {}) if isinstance(result, dict) else {}
    return dict(value or {}) if isinstance(value, dict) else {}


def mentions_symbol(value: object, symbol: str) -> bool:
    """Match a symbol in structured notification context without partial codes."""

    if isinstance(value, dict):
        return any(mentions_symbol(item, symbol) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(mentions_symbol(item, symbol) for item in value)
    candidate = text(value).upper()
    if not candidate:
        return False
    if candidate == symbol:
        return True
    return bool(re.search(r"(?<![A-Z0-9])" + re.escape(symbol) + r"(?![A-Z0-9])", candidate))


def marker(
    marker_id: object,
    kind: str,
    occurred_at: object,
    title: object,
    summary: object = "",
    source: object = "",
    tone: str = "neutral",
    detail_type: str = "",
    detail_key: object = "",
    metadata: Dict[str, object] = None,
) -> Dict[str, object]:
    return {
        "id": text(marker_id),
        "type": kind,
        "occurredAt": text(occurred_at),
        "title": text(title),
        "summary": text(summary),
        "source": text(source),
        "tone": tone,
        "detailType": detail_type,
        "detailKey": text(detail_key),
        "metadata": dict(metadata or {}),
    }


class InstrumentTimelineQueryService:
    """Read-only projection across time-series and operational stores."""

    def __init__(
        self,
        time_series_store,
        evidence_store,
        calendar_store,
        decision_episode_store,
        hypothesis_lifecycle_store,
        notification_job_store,
        symbol_store=None,
    ):
        self.time_series_store = time_series_store
        self.evidence_store = evidence_store
        self.calendar_store = calendar_store
        self.decision_episode_store = decision_episode_store
        self.hypothesis_lifecycle_store = hypothesis_lifecycle_store
        self.notification_job_store = notification_job_store
        self.symbol_store = symbol_store

    def query(self, query: InstrumentTimelineQuery) -> Dict[str, object]:
        request = query.normalized()
        if not request.symbol:
            raise ValueError("symbol is required")

        rows = list(self.time_series_store.load_instrument_series(
            request.account_id,
            request.symbol,
            request.interval,
            request.limit,
        ) or [])
        selected_interval = request.interval
        if not rows and request.interval != "1d":
            rows = list(self.time_series_store.load_instrument_series(
                request.account_id,
                request.symbol,
                "1d",
                request.limit,
            ) or [])
            if rows:
                selected_interval = "1d"

        candles = [self.candle(row) for row in rows]
        candles = [item for item in candles if item["time"] and item["close"] > 0]
        events = self.events(request)
        providers = sorted({text(row.get("provider")) for row in rows if text(row.get("provider"))})
        latest_at = max((text(row.get("updatedAt") or row.get("generatedAt")) for row in rows), default="")
        identity = self.instrument_identity(request.symbol, rows)
        return {
            "generatedAt": utc_now_iso(),
            "instrument": identity,
            "query": {
                "accountId": request.account_id,
                "range": request.range_key,
                "requestedInterval": request.interval,
                "interval": selected_interval,
            },
            "series": {
                "dataMode": "actual",
                "availability": "ready" if candles else "no-data",
                "source": "market-time-series-store",
                "backendId": text(getattr(self.time_series_store, "active_backend_id", lambda: "")()),
                "interval": selected_interval,
                "providers": providers,
                "latestAt": latest_at,
                "pointCount": len(candles),
                "candles": candles,
            },
            "events": events,
            "sources": self.source_summary(candles, events, providers, latest_at),
        }

    def instrument_identity(self, symbol: str, rows: Iterable[Dict[str, object]]) -> Dict[str, object]:
        stored = None
        getter = getattr(self.symbol_store, "get", None)
        if callable(getter):
            stored = getter(symbol)
        payload = object_payload(stored)
        first = next(iter(rows or []), {})
        return {
            "symbol": symbol,
            "name": text(payload.get("name") or first.get("name") or symbol),
            "market": text(payload.get("market") or first.get("market")),
            "currency": text(payload.get("currency") or first.get("currency")),
        }

    @staticmethod
    def candle(row: Dict[str, object]) -> Dict[str, object]:
        close = float(row.get("currentPrice") or 0)
        open_price = float(row.get("openPrice") or close)
        high = float(row.get("highPrice") or max(open_price, close))
        low = float(row.get("lowPrice") or min(open_price, close))
        return {
            "time": text(row.get("bucketAt") or row.get("generatedAt")),
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": float(row.get("volume") or 0),
            "foreignNetVolume": float(row.get("foreignNetVolume") or 0),
            "institutionNetVolume": float(row.get("institutionNetVolume") or 0),
            "individualNetVolume": float(row.get("individualNetVolume") or 0),
            "provider": text(row.get("provider")),
            "dataQuality": text(row.get("dataQuality")),
        }

    def events(self, request: InstrumentTimelineQuery) -> List[Dict[str, object]]:
        result: List[Dict[str, object]] = []
        for evidence in self.evidence_store.latest(symbol=request.symbol, limit=100):
            payload = object_payload(evidence)
            polarity = text(payload.get("stockImpactPolarity") or payload.get("polarity"))
            result.append(marker(
                payload.get("evidenceId"),
                "evidence",
                payload.get("publishedAt") or payload.get("observedAt"),
                payload.get("title") or "새 투자 근거",
                payload.get("articleSummaryKo") or payload.get("summary"),
                payload.get("source"),
                "negative" if polarity in {"negative", "risk", "counter"} else "positive" if polarity in {"positive", "support"} else "neutral",
                "research-evidence",
                payload.get("evidenceId"),
                {"evidenceRole": payload.get("evidenceRole"), "dataState": payload.get("dataState")},
            ))
        for event in self.calendar_store.list(symbol=request.symbol, limit=100):
            payload = object_payload(event)
            result.append(marker(
                payload.get("eventId"),
                "calendar",
                payload.get("startsAt"),
                payload.get("title") or "투자 일정",
                payload.get("notes"),
                payload.get("source"),
                "warning" if int(payload.get("importance") or 0) >= 70 else "neutral",
                "investment-calendar-event",
                payload.get("eventId"),
                {"eventType": payload.get("eventType"), "importance": payload.get("importance")},
            ))
        for episode in self.decision_episode_store.list(request.account_id, request.symbol, limit=50):
            payload = object_payload(episode)
            action = text(payload.get("action"))
            result.append(marker(
                payload.get("episodeId"),
                "decision",
                payload.get("decidedAt"),
                (action or "판단") + " 판단",
                payload.get("decisionSummary") or payload.get("investmentView"),
                payload.get("source") or "investment-brain",
                "negative" if action in {"SELL", "TRIM"} else "positive" if action in {"BUY", "ADD"} else "neutral",
                "investment-action",
                payload.get("episodeId"),
                {
                    "inferenceGenerationId": payload.get("inferenceGenerationId"),
                    "decisionReadiness": payload.get("decisionReadiness"),
                },
            ))
        for transition in self.hypothesis_lifecycle_store.list_events(
            account_id=request.account_id,
            symbol=request.symbol,
            limit=100,
        ):
            payload = object_payload(transition)
            result.append(marker(
                payload.get("transitionId"),
                "hypothesis",
                payload.get("occurredAt"),
                "가설 " + text(payload.get("currentStateLabel") or payload.get("currentState") or "상태 변경"),
                payload.get("reason"),
                "TypeDB InferenceBox",
                "warning" if payload.get("materialChange") else "neutral",
                "hypothesis-review",
                payload.get("lifecycleKey"),
                {
                    "previousState": payload.get("previousState"),
                    "currentState": payload.get("currentState"),
                    "inferenceGenerationId": payload.get("inferenceGenerationId"),
                    "materialChange": bool(payload.get("materialChange")),
                },
            ))
        symbol_reader = getattr(self.notification_job_store, "recent_for_symbol", None)
        notification_jobs = (
            symbol_reader(request.symbol, request.account_id, limit=100)
            if callable(symbol_reader)
            else []
        )
        # Legacy jobs can predate the indexed symbol projection. Keep the
        # common path indexed, and use one bounded compatibility read only
        # when no projected row exists for the instrument.
        if not notification_jobs:
            notification_jobs = self.notification_job_store.recent(limit=200)
        for job in notification_jobs:
            payload = object_payload(job)
            if not mentions_symbol(payload.get("context"), request.symbol) and not mentions_symbol(payload.get("text"), request.symbol):
                continue
            result.append(marker(
                payload.get("jobId"),
                "notification",
                payload.get("updatedAt") or payload.get("createdAt"),
                "알림 " + text(payload.get("status") or "상태"),
                payload.get("text"),
                payload.get("messageType"),
                "negative" if payload.get("status") == "failed" else "positive" if payload.get("status") == "sent" else "neutral",
                "notification-job",
                payload.get("jobId"),
                {"status": payload.get("status"), "messageType": payload.get("messageType")},
            ))
        return sorted(
            [item for item in result if item["occurredAt"]],
            key=lambda item: (item["occurredAt"], item["id"]),
            reverse=True,
        )[:300]

    @staticmethod
    def source_summary(
        candles: List[Dict[str, object]],
        events: List[Dict[str, object]],
        providers: List[str],
        latest_at: str,
    ) -> List[Dict[str, object]]:
        event_counts: Dict[str, int] = {}
        for event in events:
            event_counts[event["type"]] = event_counts.get(event["type"], 0) + 1
        return [
            {
                "dataset": "가격·거래량",
                "store": "시계열 DB",
                "providers": providers,
                "dataMode": "actual",
                "count": len(candles),
                "latestAt": latest_at,
            },
            {
                "dataset": "뉴스·공시",
                "store": "Research Evidence",
                "providers": sorted({event["source"] for event in events if event["type"] == "evidence" and event["source"]}),
                "dataMode": "actual",
                "count": event_counts.get("evidence", 0),
            },
            {
                "dataset": "일정·판단·가설·알림",
                "store": "운영 DB / TypeDB 추론 이력",
                "providers": ["Investment Calendar", "Investment Brain", "TypeDB InferenceBox", "Notification Ledger"],
                "dataMode": "actual",
                "count": sum(event_counts.get(key, 0) for key in ["calendar", "decision", "hypothesis", "notification"]),
            },
        ]
