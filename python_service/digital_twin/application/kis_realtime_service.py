import time
from typing import Dict, List

from ..domain.events import market_data_collected_event, ontology_reasoning_requested_event
from ..domain.fact_changes import market_fact_change
from ..domain.materiality import market_change_materiality
from ..domain.portfolio import utc_now_iso

KIS_CACHE_PROVIDER = "kis"
KIS_CACHE_ACCOUNT_ID = "__market_signals__"


def int_setting(settings: Dict[str, str], key: str, fallback: int, minimum: int = 0, maximum: int = 100000) -> int:
    try:
        value = int(str((settings or {}).get(key) or fallback))
    except (TypeError, ValueError):
        value = fallback
    return max(minimum, min(maximum, value))


class KISRealtimeWebSocketRunner:
    def __init__(
        self,
        client,
        symbol_selector,
        quote_cache,
        settings: Dict[str, str],
        event_publisher=None,
        sleep_fn=time.sleep,
    ):
        self.client = client
        self.symbol_selector = symbol_selector
        self.quote_cache = quote_cache
        self.settings = dict(settings or {})
        self.event_publisher = event_publisher
        self.sleep_fn = sleep_fn
        self.pending_symbols: Dict[str, Dict[str, object]] = {}
        self.last_event_flush = time.monotonic()

    def enabled(self) -> bool:
        return self.client.enabled()

    def collect_duration_seconds(self) -> int:
        return int_setting(self.settings, "kisRealtimeWebSocketCollectSeconds", 30, 3, 3600)

    def reconnect_delay_seconds(self) -> int:
        return int_setting(self.settings, "kisRealtimeWebSocketReconnectSeconds", 5, 1, 300)

    def event_interval_seconds(self) -> int:
        return int_setting(self.settings, "kisRealtimeWebSocketEventIntervalSeconds", 15, 3, 300)

    def status(self) -> Dict[str, object]:
        symbols = self.symbol_selector.symbols()
        return {
            "enabled": self.enabled(),
            "configured": self.client.configured(),
            "provider": "kis-websocket",
            "transport": "websocket",
            "urlConfigured": bool(self.settings.get("kisWebSocketUrl")),
            "symbols": symbols,
            "selectedCount": len(symbols),
            "collectSeconds": self.collect_duration_seconds(),
            "eventIntervalSeconds": self.event_interval_seconds(),
            "cache": self.quote_cache.summary(KIS_CACHE_PROVIDER, KIS_CACHE_ACCOUNT_ID),
        }

    def record_updates(self, updates: List[Dict[str, object]]) -> None:
        for update in updates or []:
            symbol = str(update.get("symbol") or "").upper().strip()
            if not symbol:
                continue
            current = dict(update.get("payload") or {})
            previous = self.pending_symbols.get(symbol, {}).get("previous")
            if previous is None:
                previous = update.get("previous")
            if previous is None:
                previous = self.quote_cache.load(KIS_CACHE_PROVIDER, KIS_CACHE_ACCOUNT_ID, symbol)
            change = market_fact_change(previous or {}, current)
            if not change.get("changed") and symbol in self.pending_symbols:
                self.pending_symbols[symbol]["current"] = current
                continue
            self.pending_symbols[symbol] = {
                "previous": previous or {},
                "current": current,
                "change": change,
                "updatedAt": utc_now_iso(),
            }
        if time.monotonic() - self.last_event_flush >= self.event_interval_seconds():
            self.flush_events()

    def flush_events(self, force: bool = False) -> Dict[str, object]:
        if not self.pending_symbols:
            return {"status": "empty", "published": False}
        pending = self.pending_symbols
        self.pending_symbols = {}
        self.last_event_flush = time.monotonic()
        changed_symbols = []
        changed_fields_by_symbol: Dict[str, List[str]] = {}
        material_symbols = []
        materiality_assessments: Dict[str, Dict[str, object]] = {}
        for symbol, entry in pending.items():
            change = dict(entry.get("change") or {})
            current = dict(entry.get("current") or {})
            previous = dict(entry.get("previous") or {})
            if not change.get("changed") and not force:
                continue
            changed_symbols.append(symbol)
            changed_fields_by_symbol[symbol] = list(change.get("fields") or [])
            assessment = market_change_materiality(symbol, previous, current, change, self.settings)
            materiality_assessments[symbol] = assessment.to_dict()
            if assessment.passed:
                material_symbols.append(symbol)
        if not changed_symbols:
            return {"status": "refresh-only", "published": False}
        result = {
            "status": "ok",
            "provider": "kis-websocket",
            "markets": ["KR"],
            "collectionScope": "realtime-websocket",
            "symbols": changed_symbols,
            "selectedCount": len(changed_symbols),
            "priceCount": len(changed_symbols),
            "candleCount": 0,
            "savedCount": len(changed_symbols),
            "changedCount": len(changed_symbols),
            "changedSymbols": changed_symbols,
            "changedFieldsBySymbol": changed_fields_by_symbol,
            "materialChangedCount": len(material_symbols),
            "materialChangedSymbols": material_symbols,
            "materialityAssessments": materiality_assessments,
            "dataQuality": "actual",
            "transport": "websocket",
        }
        if self.event_publisher:
            event = market_data_collected_event(result)
            reasoning = None
            focus_symbols = []
            reasoning_symbols = getattr(self.symbol_selector, "reasoning_symbols", None)
            if callable(reasoning_symbols):
                try:
                    focus_symbols = [str(item or "").upper().strip() for item in reasoning_symbols() if str(item or "").strip()]
                except Exception:  # noqa: BLE001 - retaining the normal symbol set is safer than losing a real holding tick.
                    focus_symbols = []
            if not focus_symbols:
                fallback_symbols = getattr(self.symbol_selector, "symbols", None)
                if callable(fallback_symbols):
                    focus_symbols = [str(item or "").upper().strip() for item in fallback_symbols() if str(item or "").strip()]
            focus_set = set(focus_symbols)
            investment_material_symbols = [symbol for symbol in material_symbols if symbol in focus_set]
            result["investmentReasoningSymbols"] = investment_material_symbols
            result["backgroundMaterialSymbolCount"] = len([symbol for symbol in material_symbols if symbol not in focus_set])
            if investment_material_symbols:
                reasoning = ontology_reasoning_requested_event(
                    event,
                    "kis-realtime-websocket",
                    investment_material_symbols,
                    changed_count=len(investment_material_symbols),
                    observed_count=len(changed_symbols),
                    fact_types=["MarketQuote", "ExecutionFlow", "OrderBook"],
                    reason="보유·관심 종목에서 중요도가 확인된 KIS WebSocket 체결·호가 변경만 TypeDB ABox와 네이티브 규칙 추론에 반영합니다.",
                    materiality_assessments=[materiality_assessments[symbol] for symbol in investment_material_symbols],
                )
            if hasattr(self.event_publisher, "publish"):
                self.event_publisher.publish(event)
                if reasoning:
                    self.event_publisher.publish(reasoning)
            else:
                self.event_publisher.handle(event)
                if reasoning:
                    self.event_publisher.handle(reasoning)
        return {"status": "ok", "published": bool(self.event_publisher), **result}

    def run_once(self, duration_seconds: int = 0, force: bool = False) -> Dict[str, object]:
        if not self.enabled() and not force:
            return {"status": "disabled", "provider": "kis-websocket", "savedCount": 0}
        symbols = self.symbol_selector.symbols()
        if not symbols:
            return {"status": "noSymbols", "provider": "kis-websocket", "savedCount": 0}
        try:
            result = self.client.collect(
                symbols,
                duration_seconds or self.collect_duration_seconds(),
                on_update=self.record_updates,
            )
        except Exception as error:  # noqa: BLE001 - preserve any tick already received before a transport failure.
            result = {
                "status": "connection-error",
                "provider": "kis-websocket",
                "symbols": symbols,
                "selectedCount": len(symbols),
                "savedCount": 0,
                "dataQuality": "unavailable",
                "transport": "websocket",
                "errorStage": "collect",
                "reason": ("KIS WebSocket 수집 단계 연결이 끊겼습니다: " + str(error))[:360],
                "reconnectRecommended": True,
            }
        flush_result = self.flush_events(force=True)
        result["eventFlush"] = flush_result
        return result
