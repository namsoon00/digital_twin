import inspect
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List, Tuple

from ..domain.accounts import AccountConfig
from ..domain.data_freshness import age_minutes, parse_datetime, utc_iso
from ..domain.events import DomainEvent, ontology_reasoning_requested_event, research_evidence_collected_event
from ..domain.investment_research import NewsCollectionTarget, ResearchEvidence
from ..domain.market_data import known_stock, number
from ..domain.materiality import evidence_materiality
from ..domain.repositories import AccountRepository, MonitorSnapshotReader, ResearchEvidenceGateway, ResearchEvidenceRepository, SymbolUniverseRepository
from ..domain.symbol_universe import ListedSymbol, normalize_market


DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}
RSS_PROVIDER_MARKERS = ("google news", "google_rss", "yahoo finance rss", "yahoo_finance", "rss")


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


def float_setting(settings: Dict[str, str], key: str, fallback: float, lower: float = 0.0, upper: float = 1.0) -> float:
    try:
        parsed = float(str(settings.get(key) or "").strip())
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


def parse_news_timestamp(value: object):
    parsed = parse_datetime(value)
    if parsed:
        return parsed
    text = str(value or "").strip()
    for pattern in ["%Y%m%dT%H%M%SZ", "%Y%m%d"]:
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def evidence_reference_timestamp(item: ResearchEvidence) -> str:
    return str(getattr(item, "published_at", "") or getattr(item, "observed_at", "") or "").strip()


def evidence_age_minutes(item: ResearchEvidence, now=None):
    parsed = parse_news_timestamp(evidence_reference_timestamp(item))
    if not parsed:
        return None
    return age_minutes(parsed.isoformat(), now=now)


def evidence_payload(item: ResearchEvidence) -> Dict[str, object]:
    payload = getattr(item, "raw_payload", {})
    return payload if isinstance(payload, dict) else {}


def evidence_article_facts(item: ResearchEvidence) -> Dict[str, object]:
    facts = evidence_payload(item).get("articleFacts")
    return facts if isinstance(facts, dict) else {}


def evidence_provider_text(item: ResearchEvidence) -> str:
    payload = evidence_payload(item)
    return str(payload.get("provider") or getattr(item, "source", "") or "").strip().lower()


def evidence_is_rss_source(item: ResearchEvidence) -> bool:
    provider = evidence_provider_text(item).replace("-", "_")
    return any(marker in provider for marker in RSS_PROVIDER_MARKERS)


def evidence_has_article_body(item: ResearchEvidence) -> bool:
    facts = evidence_article_facts(item)
    if "bodyAvailable" in facts:
        return bool(facts.get("bodyAvailable"))
    status = str(evidence_payload(item).get("articleReadStatus") or facts.get("readStatus") or "").strip()
    return status == "body"


def evidence_is_feed_only_rss(item: ResearchEvidence) -> bool:
    return getattr(item, "kind", "") == "news" and evidence_is_rss_source(item) and not evidence_has_article_body(item)


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
        article_analysis_service=None,
        health_service=None,
        sleep_fn=time.sleep,
        now_provider: Callable = None,
        monotonic_fn: Callable = time.monotonic,
    ):
        self.account_repository = account_repository
        self.monitor_store = monitor_store
        self.symbol_store = symbol_store
        self.evidence_store = evidence_store
        self.gateway = gateway
        self.settings = dict(settings or {})
        self.event_publisher = event_publisher
        self.article_analysis_service = article_analysis_service
        self.health_service = health_service
        self.sleep_fn = sleep_fn
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.monotonic_fn = monotonic_fn

    def with_health(self, result: Dict[str, object]) -> Dict[str, object]:
        if not self.health_service or not hasattr(self.health_service, "record_news_collection"):
            return result
        try:
            health, event = self.health_service.record_news_collection(result)
            result["pipelineHealth"] = health.to_dict()
            if event and self.event_publisher:
                if hasattr(self.event_publisher, "publish"):
                    self.event_publisher.publish(event)
                else:
                    self.event_publisher.handle(event)
        except Exception as error:  # noqa: BLE001 - observability must not stop collection.
            result["pipelineHealth"] = {"state": "unknown", "reason": str(error)[:180]}
        return result

    def enabled(self) -> bool:
        return truthy(self.settings.get("newsCollectionEnabled"), True)

    def max_symbols(self) -> int:
        return int_setting(self.settings, "newsCollectionMaxSymbols", 3, 1, 500)

    def collection_interval_seconds(self) -> int:
        return int_setting(self.settings, "newsCollectionIntervalSeconds", 60, 1, 86400)

    def run_budget_seconds(self) -> int:
        return int_setting(self.settings, "newsCollectionRunBudgetSeconds", 45, 5, 600)

    def rate_limit_seconds(self) -> float:
        return max(0.0, number(self.settings.get("newsCollectionRateLimitSeconds")) or 0.25)

    def include_watchlist(self) -> bool:
        return truthy(self.settings.get("newsCollectionIncludeWatchlist"), True)

    def include_holdings(self) -> bool:
        return truthy(self.settings.get("newsCollectionIncludeHoldings"), True)

    def cleanup_enabled(self) -> bool:
        return truthy(self.settings.get("newsEvidenceCleanupEnabled"), True)

    def max_news_age_minutes(self) -> int:
        fallback = int_setting(self.settings, "newsCollectionLookbackMinutes", 180, 5, 1440 * 7)
        return int_setting(self.settings, "newsEvidenceMaxAgeMinutes", fallback, 5, 1440 * 30)

    def cleanup_batch_size(self) -> int:
        return int_setting(self.settings, "newsEvidenceCleanupBatchSize", 500, 1, 5000)

    def keep_undated_news(self) -> bool:
        return truthy(self.settings.get("newsEvidenceKeepUndated"), False)

    def require_article_body_for_rss(self) -> bool:
        return truthy(self.settings.get("newsCollectionRequireArticleBodyForRss"), True)

    def stale_cutoff_iso(self) -> str:
        return utc_iso(datetime.now(timezone.utc) - timedelta(minutes=self.max_news_age_minutes()))

    def delete_stale_news(self) -> Dict[str, object]:
        if not self.cleanup_enabled() or not hasattr(self.evidence_store, "delete_stale_news"):
            return {"enabled": self.cleanup_enabled(), "deleted": 0, "cutoffIso": self.stale_cutoff_iso()}
        cutoff = self.stale_cutoff_iso()
        try:
            deleted = int(self.evidence_store.delete_stale_news(cutoff, self.cleanup_batch_size()) or 0)
            return {"enabled": True, "deleted": deleted, "cutoffIso": cutoff, "maxAgeMinutes": self.max_news_age_minutes()}
        except Exception as error:  # noqa: BLE001 - cleanup failure should not stop fresh collection.
            return {"enabled": True, "deleted": 0, "cutoffIso": cutoff, "status": "error", "message": str(error)[:180], "maxAgeMinutes": self.max_news_age_minutes()}

    def delete_feed_only_rss_news(self) -> Dict[str, object]:
        enabled = self.cleanup_enabled() and self.require_article_body_for_rss()
        if not enabled or not hasattr(self.evidence_store, "latest") or not hasattr(self.evidence_store, "delete"):
            return {"enabled": enabled, "deleted": 0}
        try:
            items = self.evidence_store.latest(kind="news", limit=self.cleanup_batch_size())
            targets = [item for item in items if evidence_is_feed_only_rss(item)]
            deleted = 0
            for item in targets:
                if self.evidence_store.delete(item.evidence_id):
                    deleted += 1
            return {"enabled": True, "deleted": deleted, "scanned": len(items), "reason": "rssFeedOnlyWithoutArticleBody"}
        except Exception as error:  # noqa: BLE001 - cleanup failure should not stop fresh collection.
            return {"enabled": True, "deleted": 0, "status": "error", "message": str(error)[:180]}

    def fresh_news_items(self, items: Iterable[ResearchEvidence]) -> Tuple[List[ResearchEvidence], List[ResearchEvidence]]:
        now = datetime.now(timezone.utc)
        fresh: List[ResearchEvidence] = []
        stale: List[ResearchEvidence] = []
        max_age = self.max_news_age_minutes()
        for item in items or []:
            if getattr(item, "kind", "") != "news":
                fresh.append(item)
                continue
            age = evidence_age_minutes(item, now=now)
            if age is None:
                if self.keep_undated_news():
                    fresh.append(item)
                else:
                    stale.append(item)
                continue
            if age <= max_age:
                fresh.append(item)
            else:
                stale.append(item)
        return fresh, stale

    def article_analysis_health(self, items: Iterable[ResearchEvidence], stale_skipped_count: int, cleanup: Dict[str, object]) -> Dict[str, object]:
        news_items = [item for item in items or [] if getattr(item, "kind", "") == "news"]
        body_read = 0
        source_blocked = 0
        feed_only = 0
        body_missing = 0
        ai_analyzed = 0
        ai_fallback = 0
        for item in news_items:
            payload = getattr(item, "raw_payload", {}) if isinstance(getattr(item, "raw_payload", {}), dict) else {}
            facts = payload.get("articleFacts") if isinstance(payload.get("articleFacts"), dict) else {}
            status = str(payload.get("articleReadStatus") or facts.get("readStatus") or "").strip()
            body_available = bool(facts.get("bodyAvailable")) if "bodyAvailable" in facts else status == "body"
            if status == "source-blocked":
                source_blocked += 1
            elif status == "body" and body_available:
                body_read += 1
            else:
                feed_only += 1
            if not body_available:
                body_missing += 1
            analysis = payload.get("aiAnalysis") if isinstance(payload.get("aiAnalysis"), dict) else {}
            if analysis:
                ai_analyzed += 1
                if str(analysis.get("status") or "") == "fallback":
                    ai_fallback += 1
        total = len(news_items)
        body_failure_rate = (body_missing / total) if total else 0.0
        warn_rate = float_setting(self.settings, "newsArticleBodyFailureWarnRate", 0.4, 0.0, 1.0)
        warn_min = int_setting(self.settings, "newsArticleBodyFailureMinimumCount", 5, 1, 1000)
        status = "empty" if not total else ("degraded" if total >= warn_min and body_failure_rate >= warn_rate else "ok")
        return {
            "status": status,
            "newsCount": total,
            "bodyReadCount": body_read,
            "feedOnlyCount": feed_only,
            "sourceBlockedCount": source_blocked,
            "bodyMissingCount": body_missing,
            "bodyFailureRate": round(body_failure_rate, 3),
            "bodyFailureWarnRate": warn_rate,
            "aiAnalyzedCount": ai_analyzed,
            "aiFallbackCount": ai_fallback,
            "aiMissingCount": max(0, total - ai_analyzed),
            "staleSkippedCount": int(stale_skipped_count or 0),
            "staleDeletedCount": int((cleanup or {}).get("deleted") or 0),
            "freshnessMaxAgeMinutes": self.max_news_age_minutes(),
        }

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
        if name.upper() == symbol:
            name = ""
        known = known_stock(symbol)
        name = name or str(known.get("name") or "").strip()
        market = normalize_market(str(known.get("market") or market))
        currency = currency or str(known.get("currency") or "").upper().strip()
        sector = sector or str(known.get("sector") or "").strip()
        targets[symbol] = NewsCollectionTarget(
            symbol=symbol,
            name=name or symbol,
            market=market,
            currency=currency or ("KRW" if market in {"KOSPI", "KOSDAQ"} else "USD"),
            sector=sector,
        )

    def all_targets(self) -> List[NewsCollectionTarget]:
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
        return sorted(
            targets.values(),
            key=lambda target: (target.normalized_symbol(), target.normalized_market(), str(target.name or "").casefold()),
        )

    def target_plan(self) -> Dict[str, object]:
        candidates = self.all_targets()
        limit = self.max_symbols()
        now = self.now_provider()
        if not isinstance(now, datetime):
            now = datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)
        interval = self.collection_interval_seconds()
        slot = max(0, int(now.timestamp()) // interval)
        start_index = 0
        if len(candidates) <= limit:
            selected = list(candidates)
        elif candidates:
            start_index = (slot * limit) % len(candidates)
            selected = [candidates[(start_index + index) % len(candidates)] for index in range(limit)]
        else:
            selected = []
        next_rotation = datetime.fromtimestamp((slot + 1) * interval, timezone.utc).isoformat().replace("+00:00", "Z")
        return {
            "mode": "round-robin-window",
            "targets": selected,
            "candidateCount": len(candidates),
            "selectedCount": len(selected),
            "maxSymbols": limit,
            "rotationSlot": slot,
            "rotationStartIndex": start_index,
            "nextRotationAt": next_rotation,
            "selectedSymbols": [target.symbol for target in selected],
            "omittedSymbolCount": max(0, len(candidates) - len(selected)),
        }

    def targets(self) -> List[NewsCollectionTarget]:
        return list(self.target_plan().get("targets") or [])

    def run_once(self, force: bool = False) -> Dict[str, object]:
        if not self.enabled() and not force:
            return self.with_health({"status": "disabled", "targetCount": 0, "fetchedCount": 0, "savedCount": 0})
        cleanup = self.delete_stale_news()
        feed_only_cleanup = self.delete_feed_only_rss_news()
        target_plan = self.target_plan()
        targets = list(target_plan.get("targets") or [])
        selection_metadata = {key: value for key, value in target_plan.items() if key != "targets"}
        if not targets:
            return self.with_health({"status": "noTargets", "targetCount": 0, "fetchedCount": 0, "savedCount": 0, "staleDeletedCount": cleanup.get("deleted", 0), "staleCleanup": cleanup, "feedOnlyRssCleanup": feed_only_cleanup, "targetSelection": selection_metadata})
        collected: List[ResearchEvidence] = []
        stale_items: List[ResearchEvidence] = []
        statuses: List[Dict[str, object]] = []
        target_failures: List[Dict[str, object]] = []
        processed_symbols: List[str] = []
        deferred_symbols: List[str] = []
        budget_seconds = self.run_budget_seconds()
        started = self.monotonic_fn()
        budget_exhausted = False
        for index, target in enumerate(targets):
            if self.monotonic_fn() - started >= budget_seconds:
                budget_exhausted = True
                deferred_symbols = [item.symbol for item in targets[index:]]
                break
            if index and self.rate_limit_seconds():
                self.sleep_fn(self.rate_limit_seconds())
            if self.monotonic_fn() - started >= budget_seconds:
                budget_exhausted = True
                deferred_symbols = [item.symbol for item in targets[index:]]
                break
            processed_symbols.append(target.symbol)
            try:
                items, target_statuses = self.gateway.collect_for_target(target)
                if self.article_analysis_service and hasattr(self.article_analysis_service, "analyze_many"):
                    analyze_many = self.article_analysis_service.analyze_many
                    try:
                        parameters = inspect.signature(analyze_many).parameters.values()
                        accepts_kwargs = any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters)
                        parameter_names = {parameter.name for parameter in parameters}
                    except (TypeError, ValueError):
                        accepts_kwargs = False
                        parameter_names = set()
                    analysis_kwargs = {}
                    if accepts_kwargs or "deadline_monotonic" in parameter_names:
                        analysis_kwargs["deadline_monotonic"] = started + budget_seconds
                    if accepts_kwargs or "monotonic_fn" in parameter_names:
                        analysis_kwargs["monotonic_fn"] = self.monotonic_fn
                    items = analyze_many(target, items, **analysis_kwargs)
                items, target_stale = self.fresh_news_items(items)
                collected.extend(items)
                stale_items.extend(target_stale)
                statuses.extend(target_statuses)
            except Exception as error:  # noqa: BLE001 - one target must not stall the complete collection rotation.
                failure = {
                    "symbol": target.symbol,
                    "stage": "collect-or-analyze",
                    "reason": str(error)[:240],
                }
                target_failures.append(failure)
                statuses.append({"source": "news-collection-runner", "symbol": target.symbol, "ok": False, "message": failure["reason"]})
        def build_collection_result(saved: int, changed_symbols: List[str], changed_items: List[ResearchEvidence]) -> Dict[str, object]:
            symbols = list(changed_symbols or [])
            if saved and not symbols:
                symbols = sorted(set(str(item.symbol or "").upper().strip() for item in collected if str(item.symbol or "").strip()))
            items = list(changed_items or [])
            if saved and not items:
                changed_symbols_set = set(symbols)
                items = [item for item in collected if str(item.symbol or "").upper().strip() in changed_symbols_set]
            materiality_assessments = [evidence_materiality(item, self.settings).to_dict() for item in items]
            material_items = [
                item
                for item, assessment in zip(items, materiality_assessments)
                if assessment.get("passed")
            ]
            material_symbols = sorted(set(str(item.symbol or "").upper().strip() for item in material_items if str(item.symbol or "").strip()))
            return {
                "status": "ok",
                "targetCount": len(targets),
                "processedTargetCount": len(processed_symbols),
                "targetSelection": selection_metadata,
                "runBudget": {
                    "seconds": budget_seconds,
                    "elapsedSeconds": round(self.monotonic_fn() - started, 3),
                    "exhausted": budget_exhausted,
                    "deferredSymbols": deferred_symbols,
                },
                "fetchedCount": len(collected),
                "savedCount": saved,
                "changedCount": saved,
                "staleSkippedCount": len(stale_items),
                "staleDeletedCount": cleanup.get("deleted", 0),
                "staleCleanup": cleanup,
                "feedOnlyRssCleanup": feed_only_cleanup,
                "changedSymbols": symbols,
                "materialChangedCount": len(material_items),
                "materialChangedSymbols": material_symbols,
                "changedItems": [item.to_dict() for item in items[:50]],
                "materialChangedItems": [item.to_dict() for item in material_items[:50]],
                "materialityAssessments": materiality_assessments,
                "symbols": [target.symbol for target in targets],
                "providers": self.gateway.providers(),
                "statuses": statuses[-50:],
                "targetFailures": target_failures,
                "articleAnalysisHealth": self.article_analysis_health(collected, len(stale_items), cleanup),
                "dataQuality": "actual",
            }

        def collection_events(result: Dict[str, object]) -> List[DomainEvent]:
            if not int(result.get("savedCount") or 0):
                return []
            event = research_evidence_collected_event(result)
            events = [event]
            ontology_symbols = list(result.get("changedSymbols") or [])
            if ontology_symbols:
                events.append(ontology_reasoning_requested_event(
                    event,
                    "research-evidence-update",
                    ontology_symbols,
                    changed_count=len(ontology_symbols),
                    observed_count=len(collected),
                    fact_types=["ResearchEvidence", "NewsEvent"],
                    reason="뉴스/리서치 근거 변경을 TypeDB ABox에 반영하고 네이티브 규칙 추론을 갱신합니다. 알림은 중요 변경 게이트를 별도로 통과해야 합니다.",
                    materiality_assessments=list(result.get("materialityAssessments") or []),
                ))
            return events

        event_state: Dict[str, object] = {}
        if (
            collected
            and self.event_publisher
            and hasattr(self.evidence_store, "upsert_many_with_events")
            and hasattr(self.event_publisher, "dispatch_recorded")
        ):
            def event_builder(saved: int, changed_symbols: List[str], changed_items: List[ResearchEvidence]) -> List[DomainEvent]:
                built = build_collection_result(saved, changed_symbols, changed_items)
                events = collection_events(built)
                event_state["result"] = built
                event_state["events"] = events
                return events

            saved, recorded_events = self.evidence_store.upsert_many_with_events(collected, event_builder)
            result = event_state.get("result")
            if not isinstance(result, dict):
                result = build_collection_result(
                    saved,
                    list(getattr(self.evidence_store, "last_changed_symbols", []) or []),
                    list(getattr(self.evidence_store, "last_changed_items", []) or []),
                )
            for event in recorded_events:
                self.event_publisher.dispatch_recorded(event)
            return self.with_health(result)

        saved = self.evidence_store.upsert_many(collected) if collected else 0
        result = build_collection_result(
            saved,
            list(getattr(self.evidence_store, "last_changed_symbols", []) or []),
            list(getattr(self.evidence_store, "last_changed_items", []) or []),
        )
        if self.event_publisher and saved:
            for event in collection_events(result):
                if hasattr(self.event_publisher, "publish"):
                    self.event_publisher.publish(event)
                else:
                    self.event_publisher.handle(event)
        return self.with_health(result)

    def status(self) -> Dict[str, object]:
        target_plan = self.target_plan()
        targets = list(target_plan.get("targets") or [])
        selection_metadata = {key: value for key, value in target_plan.items() if key != "targets"}
        summary = self.evidence_store.summary() if hasattr(self.evidence_store, "summary") else {}
        return {
            "enabled": self.enabled(),
            "targetCount": len(targets),
            "maxSymbols": self.max_symbols(),
            "runBudgetSeconds": self.run_budget_seconds(),
            "targetSelection": selection_metadata,
            "providers": self.gateway.providers(),
            "koreanProviders": self.gateway.korean_providers() if hasattr(self.gateway, "korean_providers") else [],
            "symbols": [target.symbol for target in targets[:50]],
            "evidence": summary,
            "staleCleanup": {
                "enabled": self.cleanup_enabled(),
                "maxAgeMinutes": self.max_news_age_minutes(),
                "cleanupBatchSize": self.cleanup_batch_size(),
            },
            "pipelineHealth": self.health_service.pipeline_state("newsCollection") if self.health_service and hasattr(self.health_service, "pipeline_state") else {},
        }
