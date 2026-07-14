import signal
import time
from typing import Dict, List

from ..domain.events import market_data_collected_event, ontology_reasoning_requested_event
from ..domain.fact_changes import market_fact_change
from ..domain.materiality import market_change_materiality
from ..domain.portfolio import utc_now_iso
from ..infrastructure.kis_market_signals import KIS_CACHE_ACCOUNT_ID, KIS_CACHE_PROVIDER
from ..infrastructure.kis_realtime_ws import KISRealtimeSymbolSelector, KISRealtimeWebSocketClient, int_setting


class KISRealtimeWebSocketRunner:
    def __init__(
        self,
        client: KISRealtimeWebSocketClient,
        symbol_selector: KISRealtimeSymbolSelector,
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
            reasoning = ontology_reasoning_requested_event(
                event,
                "kis-realtime-websocket",
                changed_symbols,
                changed_count=len(changed_symbols),
                observed_count=len(changed_symbols),
                fact_types=["MarketQuote", "ExecutionFlow", "OrderBook"],
                reason="KIS WebSocket 체결·호가 변경을 TypeDB ABox에 반영하고 RuleBox 추론을 갱신합니다. 투자자별 수급은 별도 REST live-poll 품질로 분리합니다.",
                materiality_assessments=[materiality_assessments[symbol] for symbol in changed_symbols if symbol in materiality_assessments],
            )
            if hasattr(self.event_publisher, "publish"):
                self.event_publisher.publish(event)
                self.event_publisher.publish(reasoning)
            else:
                self.event_publisher.handle(event)
                self.event_publisher.handle(reasoning)
        return {"status": "ok", "published": bool(self.event_publisher), **result}

    def run_once(self, duration_seconds: int = 0, force: bool = False) -> Dict[str, object]:
        if not self.enabled() and not force:
            return {"status": "disabled", "provider": "kis-websocket", "savedCount": 0}
        symbols = self.symbol_selector.symbols()
        if not symbols:
            return {"status": "noSymbols", "provider": "kis-websocket", "savedCount": 0}
        result = self.client.collect(
            symbols,
            duration_seconds or self.collect_duration_seconds(),
            on_update=self.record_updates,
        )
        flush_result = self.flush_events(force=True)
        result["eventFlush"] = flush_result
        return result


class KISRealtimeWebSocketScheduler:
    def __init__(self, runner: KISRealtimeWebSocketRunner, reconnect_delay_seconds: int = 5):
        self.runner = runner
        self.reconnect_delay_seconds = max(1, int(reconnect_delay_seconds or 5))
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        print("Python KIS realtime WebSocket worker started. reconnect=" + str(self.reconnect_delay_seconds) + "s")
        while self.running:
            try:
                result = self.runner.run_once()
                print(
                    "KIS realtime websocket "
                    + str(result.get("status"))
                    + " saved="
                    + str(result.get("savedCount", 0))
                    + " symbols="
                    + str(len(result.get("symbols") or [])),
                    flush=True,
                )
            except Exception as error:  # noqa: BLE001 - realtime feed should reconnect after vendor/network errors.
                print("Python KIS realtime WebSocket error: " + str(error), flush=True)
            end_at = time.monotonic() + self.reconnect_delay_seconds
            while self.running and time.monotonic() < end_at:
                self.runner.sleep_fn(min(1.0, end_at - time.monotonic()))
