import signal
import time
from typing import Dict, Iterable, List

from ..domain.accounts import AccountConfig
from ..domain.events import ontology_reasoning_requested_event, research_evidence_collected_event
from ..domain.investment_research import NewsCollectionTarget, ResearchEvidence
from ..domain.market_data import number
from ..domain.materiality import evidence_materiality
from ..domain.repositories import AccountRepository, MonitorSnapshotReader, ResearchEvidenceGateway, ResearchEvidenceRepository, SymbolUniverseRepository
from ..domain.symbol_universe import ListedSymbol, normalize_market


DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}


def truthy(value: object, default: bool = True) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text not in DISABLED_VALUES


def int_setting(settings: Dict[str, str], key: str, fallback: int, lower: int = 0, upper: int = 100000) -> int:
    try:
        parsed = int(float(str(settings.get(key) or "").strip()))
    except ValueError:
        parsed = fallback
    return max(lower, min(upper, parsed))


def default_market_for_symbol(symbol: str) -> str:
    return "KOSPI" if str(symbol or "").strip().isdigit() else "NASDAQ"


def snapshot_items(previous: Dict[str, object]) -> Iterable[Dict[str, object]]:
    for state in (previous or {}).values():
        if not isinstance(state, dict):
            continue
        for group in ["positions", "watchlist"]:
            values = state.get(group)
            if isinstance(values, dict):
                for item in values.values():
                    if isinstance(item, dict):
                        yield item


class NewsCollectionRunner:
    def __init__(
        self,
        account_repository: AccountRepository,
        monitor_store: MonitorSnapshotReader,
        symbol_store: SymbolUniverseRepository,
        evidence_store: ResearchEvidenceRepository,
        gateway: ResearchEvidenceGateway,
        settings: Dict[str, str],
        event_publisher=None,
        sleep_fn=time.sleep,
    ):
        self.account_repository = account_repository
        self.monitor_store = monitor_store
        self.symbol_store = symbol_store
        self.evidence_store = evidence_store
        self.gateway = gateway
        self.settings = dict(settings or {})
        self.event_publisher = event_publisher
        self.sleep_fn = sleep_fn

    def enabled(self) -> bool:
        return truthy(self.settings.get("newsCollectionEnabled"), True)

    def max_symbols(self) -> int:
        return int_setting(self.settings, "newsCollectionMaxSymbols", 40, 1, 500)

    def rate_limit_seconds(self) -> float:
        return max(0.0, number(self.settings.get("newsCollectionRateLimitSeconds")) or 0.25)

    def include_watchlist(self) -> bool:
        return truthy(self.settings.get("newsCollectionIncludeWatchlist"), True)

    def include_holdings(self) -> bool:
        return truthy(self.settings.get("newsCollectionIncludeHoldings"), True)

    def symbol_from_store(self, symbol: str, market: str = "") -> ListedSymbol:
        if not self.symbol_store or not hasattr(self.symbol_store, "get"):
            return None
        try:
            return self.symbol_store.get(symbol, market) or self.symbol_store.get(symbol)
        except Exception:
            return None

    def add_target(self, targets: Dict[str, NewsCollectionTarget], payload: Dict[str, object], fallback_market: str = "") -> None:
        symbol = str(payload.get("symbol") or "").upper().strip()
        if not symbol or symbol in targets:
            return
        market = normalize_market(str(payload.get("market") or fallback_market or default_market_for_symbol(symbol)))
        stored = self.symbol_from_store(symbol, market)
        name = str(payload.get("name") or "").strip()
        currency = str(payload.get("currency") or "").upper().strip()
        sector = str(payload.get("sector") or "").strip()
        if stored:
            name = name or stored.name
            market = stored.market or market
            currency = currency or stored.currency
            sector = sector or stored.sector
        targets[symbol] = NewsCollectionTarget(
            symbol=symbol,
            name=name or symbol,
            market=market,
            currency=currency or ("KRW" if market in {"KOSPI", "KOSDAQ"} else "USD"),
            sector=sector,
        )

    def targets(self) -> List[NewsCollectionTarget]:
        targets: Dict[str, NewsCollectionTarget] = {}
        if self.include_holdings():
            for item in snapshot_items(getattr(self.monitor_store, "previous", {}) or {}):
                self.add_target(targets, item)
        if self.include_watchlist():
            for account in self.account_repository.load() or []:
                if not isinstance(account, AccountConfig) or not account.enabled:
                    continue
                for symbol in account.watchlist_symbols or []:
                    self.add_target(targets, {"symbol": symbol})
        return list(targets.values())[: self.max_symbols()]

    def run_once(self, force: bool = False) -> Dict[str, object]:
        if not self.enabled() and not force:
            return {"status": "disabled", "targetCount": 0, "fetchedCount": 0, "savedCount": 0}
        targets = self.targets()
        if not targets:
            return {"status": "noTargets", "targetCount": 0, "fetchedCount": 0, "savedCount": 0}
        collected: List[ResearchEvidence] = []
        statuses: List[Dict[str, object]] = []
        for index, target in enumerate(targets):
            if index and self.rate_limit_seconds():
                self.sleep_fn(self.rate_limit_seconds())
            items, target_statuses = self.gateway.collect_for_target(target)
            collected.extend(items)
            statuses.extend(target_statuses)
        saved = self.evidence_store.upsert_many(collected) if collected else 0
        changed_symbols = list(getattr(self.evidence_store, "last_changed_symbols", []) or [])
        if saved and not changed_symbols:
            changed_symbols = sorted(set(str(item.symbol or "").upper().strip() for item in collected if str(item.symbol or "").strip()))
        changed_items = list(getattr(self.evidence_store, "last_changed_items", []) or [])
        if saved and not changed_items:
            changed_symbols_set = set(changed_symbols)
            changed_items = [item for item in collected if str(item.symbol or "").upper().strip() in changed_symbols_set]
        materiality_assessments = [evidence_materiality(item, self.settings).to_dict() for item in changed_items]
        material_items = [
            item
            for item, assessment in zip(changed_items, materiality_assessments)
            if assessment.get("passed")
        ]
        material_symbols = sorted(set(str(item.symbol or "").upper().strip() for item in material_items if str(item.symbol or "").strip()))
        changed_item_payloads = [item.to_dict() for item in changed_items[:50]]
        material_item_payloads = [item.to_dict() for item in material_items[:50]]
        result = {
            "status": "ok",
            "targetCount": len(targets),
            "fetchedCount": len(collected),
            "savedCount": saved,
            "changedCount": saved,
            "changedSymbols": changed_symbols,
            "materialChangedCount": len(material_items),
            "materialChangedSymbols": material_symbols,
            "changedItems": changed_item_payloads,
            "materialChangedItems": material_item_payloads,
            "materialityAssessments": materiality_assessments,
            "symbols": [target.symbol for target in targets],
            "providers": self.gateway.providers(),
            "statuses": statuses[-50:],
            "dataQuality": "actual",
        }
        ontology_symbols = changed_symbols
        if self.event_publisher and saved:
            event = research_evidence_collected_event(result)
            if hasattr(self.event_publisher, "publish"):
                self.event_publisher.publish(event)
                if ontology_symbols:
                    self.event_publisher.publish(ontology_reasoning_requested_event(
                        event,
                        "research-evidence-update",
                        ontology_symbols,
                        changed_count=len(ontology_symbols),
                        observed_count=len(collected),
                        fact_types=["ResearchEvidence", "NewsEvent"],
                        reason="뉴스/리서치 근거 변경을 Neo4j ABox에 반영하고 RuleBox 추론을 갱신합니다. 알림은 중요 변경 게이트를 별도로 통과해야 합니다.",
                        materiality_assessments=materiality_assessments,
                    ))
            else:
                self.event_publisher.handle(event)
                if ontology_symbols:
                    self.event_publisher.handle(ontology_reasoning_requested_event(
                        event,
                        "research-evidence-update",
                        ontology_symbols,
                        changed_count=len(ontology_symbols),
                        observed_count=len(collected),
                        fact_types=["ResearchEvidence", "NewsEvent"],
                        reason="뉴스/리서치 근거 변경을 Neo4j ABox에 반영하고 RuleBox 추론을 갱신합니다. 알림은 중요 변경 게이트를 별도로 통과해야 합니다.",
                        materiality_assessments=materiality_assessments,
                    ))
        return result

    def status(self) -> Dict[str, object]:
        targets = self.targets()
        summary = self.evidence_store.summary() if hasattr(self.evidence_store, "summary") else {}
        return {
            "enabled": self.enabled(),
            "targetCount": len(targets),
            "maxSymbols": self.max_symbols(),
            "providers": self.gateway.providers(),
            "symbols": [target.symbol for target in targets[:50]],
            "evidence": summary,
        }


class NewsCollectionScheduler:
    def __init__(self, runner: NewsCollectionRunner, interval_seconds: int):
        self.runner = runner
        self.interval_seconds = max(60, int(interval_seconds or 60))
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        print("Python news collector started. interval=" + str(self.interval_seconds) + "s")
        while self.running:
            started = time.monotonic()
            try:
                result = self.runner.run_once()
                print("News collection " + str(result.get("status")) + " saved=" + str(result.get("savedCount", 0)) + " fetched=" + str(result.get("fetchedCount", 0)))
            except Exception as error:  # noqa: BLE001 - long-running collector must continue after provider failures.
                print("Python news collector error: " + str(error))
            elapsed = time.monotonic() - started
            sleep_seconds = max(1.0, self.interval_seconds - elapsed)
            end_at = time.monotonic() + sleep_seconds
            while self.running and time.monotonic() < end_at:
                time.sleep(min(1.0, end_at - time.monotonic()))
