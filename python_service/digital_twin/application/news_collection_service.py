import signal
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Tuple

from ..domain.accounts import AccountConfig
from ..domain.data_freshness import age_minutes, parse_datetime, utc_iso
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
        sleep_fn=time.sleep,
    ):
        self.account_repository = account_repository
        self.monitor_store = monitor_store
        self.symbol_store = symbol_store
        self.evidence_store = evidence_store
        self.gateway = gateway
        self.settings = dict(settings or {})
        self.event_publisher = event_publisher
        self.article_analysis_service = article_analysis_service
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

    def cleanup_enabled(self) -> bool:
        return truthy(self.settings.get("newsEvidenceCleanupEnabled"), True)

    def max_news_age_minutes(self) -> int:
        fallback = int_setting(self.settings, "newsCollectionLookbackMinutes", 180, 5, 1440 * 7)
        return int_setting(self.settings, "newsEvidenceMaxAgeMinutes", fallback, 5, 1440 * 30)

    def cleanup_batch_size(self) -> int:
        return int_setting(self.settings, "newsEvidenceCleanupBatchSize", 500, 1, 5000)

    def keep_undated_news(self) -> bool:
        return truthy(self.settings.get("newsEvidenceKeepUndated"), False)

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
        cleanup = self.delete_stale_news()
        targets = self.targets()
        if not targets:
            return {"status": "noTargets", "targetCount": 0, "fetchedCount": 0, "savedCount": 0, "staleDeletedCount": cleanup.get("deleted", 0), "staleCleanup": cleanup}
        collected: List[ResearchEvidence] = []
        stale_items: List[ResearchEvidence] = []
        statuses: List[Dict[str, object]] = []
        for index, target in enumerate(targets):
            if index and self.rate_limit_seconds():
                self.sleep_fn(self.rate_limit_seconds())
            items, target_statuses = self.gateway.collect_for_target(target)
            if self.article_analysis_service and hasattr(self.article_analysis_service, "analyze_many"):
                items = self.article_analysis_service.analyze_many(target, items)
            items, target_stale = self.fresh_news_items(items)
            collected.extend(items)
            stale_items.extend(target_stale)
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
            "staleSkippedCount": len(stale_items),
            "staleDeletedCount": cleanup.get("deleted", 0),
            "staleCleanup": cleanup,
            "changedSymbols": changed_symbols,
            "materialChangedCount": len(material_items),
            "materialChangedSymbols": material_symbols,
            "changedItems": changed_item_payloads,
            "materialChangedItems": material_item_payloads,
            "materialityAssessments": materiality_assessments,
            "symbols": [target.symbol for target in targets],
            "providers": self.gateway.providers(),
            "statuses": statuses[-50:],
            "articleAnalysisHealth": self.article_analysis_health(collected, len(stale_items), cleanup),
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
                        reason="뉴스/리서치 근거 변경을 TypeDB ABox에 반영하고 RuleBox 추론을 갱신합니다. 알림은 중요 변경 게이트를 별도로 통과해야 합니다.",
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
                        reason="뉴스/리서치 근거 변경을 TypeDB ABox에 반영하고 RuleBox 추론을 갱신합니다. 알림은 중요 변경 게이트를 별도로 통과해야 합니다.",
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
            "staleCleanup": {
                "enabled": self.cleanup_enabled(),
                "maxAgeMinutes": self.max_news_age_minutes(),
                "cleanupBatchSize": self.cleanup_batch_size(),
            },
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
