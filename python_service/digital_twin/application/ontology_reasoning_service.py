import inspect
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, List, Tuple

from ..domain.events import ONTOLOGY_REASONING_REQUESTED, ontology_reasoning_completed_event


DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}


def truthy(value: object, default: bool = True) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text not in DISABLED_VALUES


def int_setting(settings: Dict[str, object], key: str, fallback: int, lower: int = 1, upper: int = 1000) -> int:
    try:
        parsed = int(float(str((settings or {}).get(key) or "").strip()))
    except ValueError:
        parsed = fallback
    return max(lower, min(upper, parsed))


def float_value(value: object, fallback: float = 0.0) -> float:
    try:
        return float(str(value if value is not None else "").strip())
    except ValueError:
        return fallback


def event_payload(event: object) -> Dict[str, object]:
    return dict(getattr(event, "payload", {}) or {})


def event_symbols(event: object) -> List[str]:
    symbols: List[str] = []
    for symbol in event_payload(event).get("symbols") or []:
        clean = str(symbol or "").upper().strip()
        if clean and clean not in symbols:
            symbols.append(clean)
    return symbols


def event_changed_count(event: object) -> int:
    payload = event_payload(event)
    return int(float_value(payload.get("changedCount"), 0.0) or 0)


def event_materiality_score(event: object) -> float:
    payload = event_payload(event)
    scores: List[float] = []
    for item in payload.get("materialityAssessments") or []:
        if not isinstance(item, dict):
            continue
        for key in ("score", "materialityScore", "importanceScore", "changeScore"):
            if item.get(key) is not None:
                scores.append(float_value(item.get(key), 0.0))
    return max(scores or [0.0])


TRIGGER_PRIORITY = {
    "research-evidence-update": 45.0,
    "investment-calendar-update": 40.0,
    "market-data-update": 30.0,
    "kis-realtime-update": 26.0,
    "portfolio-snapshot-update": 24.0,
    "data-update": 20.0,
}


class OntologyReasoningRunner:
    def __init__(
        self,
        event_reader,
        cursor_store,
        monitor_runner_factory: Callable,
        event_publisher=None,
        settings: Dict[str, object] = None,
        rule_candidate_service=None,
        research_store=None,
        now_provider: Callable = None,
    ):
        self.event_reader = event_reader
        self.cursor_store = cursor_store
        self.monitor_runner_factory = monitor_runner_factory
        self.event_publisher = event_publisher
        self.settings = dict(settings or {})
        self.rule_candidate_service = rule_candidate_service
        self.research_store = research_store
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def enabled(self) -> bool:
        return truthy(self.settings.get("ontologyReasoningEnabled"), True)

    def batch_size(self) -> int:
        return int_setting(self.settings, "ontologyReasoningBatchSize", 200, 1, 200)

    def max_symbols_per_run(self) -> int:
        return int_setting(self.settings, "ontologyReasoningMaxSymbolsPerRun", 50, 0, 200)

    def event_scan_limit(self, requested_limit: int = 0) -> int:
        fallback = max(1500, int(requested_limit or self.batch_size()) * 40)
        return int_setting(self.settings, "ontologyReasoningEventScanLimit", fallback, 50, 10000)

    def min_interval_seconds(self) -> int:
        return int_setting(self.settings, "ontologyReasoningMinIntervalSeconds", 180, 0, 3600)

    def urgent_min_interval_seconds(self) -> int:
        return int_setting(self.settings, "ontologyReasoningUrgentMinIntervalSeconds", 60, 0, 3600)

    def urgent_materiality_score(self) -> int:
        return int_setting(self.settings, "ontologyReasoningUrgentMaterialityScore", 85, 0, 100)

    def rule_candidate_ai_enabled(self) -> bool:
        return truthy(self.settings.get("ontologyRuleCandidateAiEnabled"), True)

    def rule_candidate_interval_minutes(self) -> int:
        return int_setting(self.settings, "ontologyRuleCandidateAiIntervalMinutes", 60, 5, 1440)

    def cursor_payload(self) -> Dict[str, object]:
        if not hasattr(self.cursor_store, "load"):
            return {}
        try:
            payload = self.cursor_store.load()
        except Exception:  # noqa: BLE001 - cursor progress is an optimization.
            return {}
        return dict(payload or {})

    def save_cursor_payload(self, payload: Dict[str, object]) -> None:
        if not hasattr(self.cursor_store, "save"):
            return
        self.cursor_store.save(dict(payload or {}))

    def event_symbol_progress(self, payload: Dict[str, object] = None) -> Dict[str, List[str]]:
        payload = self.cursor_payload() if payload is None else dict(payload or {})
        raw = payload.get("eventSymbolProgress") if isinstance(payload.get("eventSymbolProgress"), dict) else {}
        progress: Dict[str, List[str]] = {}
        for event_id, symbols in raw.items():
            clean_event_id = str(event_id or "").strip()
            if not clean_event_id:
                continue
            clean_symbols = [
                str(symbol or "").upper().strip()
                for symbol in (symbols or [])
                if str(symbol or "").strip()
            ][:200]
            if clean_symbols:
                progress[clean_event_id] = clean_symbols
        return progress

    def last_reasoned_at_by_symbol(self, payload: Dict[str, object] = None) -> Dict[str, str]:
        payload = self.cursor_payload() if payload is None else dict(payload or {})
        raw = payload.get("lastReasonedAtBySymbol") if isinstance(payload.get("lastReasonedAtBySymbol"), dict) else {}
        return {
            str(symbol or "").upper().strip(): str(stamp or "").strip()
            for symbol, stamp in raw.items()
            if str(symbol or "").strip() and str(stamp or "").strip()
        }

    def event_min_interval_seconds(self, event: object) -> int:
        trigger = str(event_payload(event).get("trigger") or "data-update").strip()
        if trigger in {"research-evidence-update", "investment-calendar-update"}:
            return self.urgent_min_interval_seconds()
        if event_materiality_score(event) >= self.urgent_materiality_score():
            return self.urgent_min_interval_seconds()
        return self.min_interval_seconds()

    def event_symbol_due(self, event: object, symbol: str, cursor_payload: Dict[str, object] = None) -> bool:
        interval = self.event_min_interval_seconds(event)
        if interval <= 0:
            return True
        raw = self.last_reasoned_at_by_symbol(cursor_payload).get(str(symbol or "").upper().strip(), "")
        if not raw:
            return True
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return True
        now = self.now_provider()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return (now.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() >= interval

    def due_event_symbols(self, event: object, progress: Dict[str, List[str]] = None, cursor_payload: Dict[str, object] = None) -> List[str]:
        remaining = self.remaining_event_symbols(event, progress)
        event_id = str(getattr(event, "event_id", "") or "")
        if event_id and event_id in (progress or {}):
            return remaining
        return [symbol for symbol in remaining if self.event_symbol_due(event, symbol, cursor_payload)]

    def remaining_event_symbols(self, event: object, progress: Dict[str, List[str]] = None) -> List[str]:
        symbols = event_symbols(event)
        if not symbols:
            return []
        progress = self.event_symbol_progress() if progress is None else progress
        processed = set(progress.get(str(getattr(event, "event_id", "") or ""), []) or [])
        return [symbol for symbol in symbols if symbol not in processed]

    def request_priority(self, event: object) -> float:
        payload = event_payload(event)
        trigger = str(payload.get("trigger") or "data-update").strip()
        materiality = event_materiality_score(event)
        changed = min(25.0, float(event_changed_count(event)))
        source_bonus = TRIGGER_PRIORITY.get(trigger, 18.0)
        fact_bonus = 0.0
        fact_types = {str(item or "").strip() for item in payload.get("factTypes") or []}
        if "ResearchEvidence" in fact_types:
            fact_bonus += 12.0
        if "MarketQuote" in fact_types:
            fact_bonus += 5.0
        return materiality + changed + source_bonus + fact_bonus

    def pending_requests(self, limit: int = 0) -> List[object]:
        processed = set(self.cursor_store.processed_event_ids())
        cursor_payload = self.cursor_payload()
        progress = self.event_symbol_progress(cursor_payload)
        reader = getattr(self.event_reader, "recent_events", None)
        if callable(reader):
            source_events = reader(
                name=ONTOLOGY_REASONING_REQUESTED,
                limit=self.event_scan_limit(limit),
            )
        else:
            source_events = self.event_reader.events(
                name=ONTOLOGY_REASONING_REQUESTED,
                limit=self.event_scan_limit(limit),
            )
        events = [
            event
            for event in source_events
            if event.event_id not in processed
            and event_changed_count(event) > 0
            and (not event_symbols(event) or self.due_event_symbols(event, progress, cursor_payload))
        ]
        events.sort(
            key=lambda event: (
                (1000.0 if str(getattr(event, "event_id", "") or "") in progress else 0.0)
                + self.request_priority(event),
                getattr(event, "occurred_at", ""),
                getattr(event, "event_id", ""),
            ),
            reverse=True,
        )
        return events[: max(1, int(limit or self.batch_size()))]

    def publish(self, event) -> None:
        if not self.event_publisher:
            return
        if hasattr(self.event_publisher, "publish"):
            self.event_publisher.publish(event)
        else:
            self.event_publisher.handle(event)

    def request_symbols(self, requests: Iterable[object]) -> List[str]:
        symbols = []
        for event in requests or []:
            for symbol in event_symbols(event):
                if symbol and symbol not in symbols:
                    symbols.append(symbol)
        return symbols

    def request_symbol_batches(self, requests: Iterable[object]) -> Tuple[Dict[str, List[str]], List[str], int]:
        max_symbols = self.max_symbols_per_run()
        cursor_payload = self.cursor_payload()
        progress = self.event_symbol_progress(cursor_payload)
        batches: Dict[str, List[str]] = {}
        selected: List[str] = []
        omitted_symbols: List[str] = []
        partial_event_ids = {str(event_id or "") for event_id in progress.keys()}
        processing_partial_events = False
        for event in requests or []:
            event_id = str(getattr(event, "event_id", "") or "")
            remaining = self.due_event_symbols(event, progress, cursor_payload)
            if not event_symbols(event):
                batches[event_id] = []
                continue
            if processing_partial_events and event_id not in partial_event_ids:
                for symbol in remaining:
                    if symbol not in selected and symbol not in omitted_symbols:
                        omitted_symbols.append(symbol)
                continue
            if event_id in partial_event_ids:
                processing_partial_events = True
            for symbol in remaining:
                if max_symbols and len(selected) >= max_symbols and symbol not in selected:
                    if symbol not in omitted_symbols:
                        omitted_symbols.append(symbol)
                    continue
                if symbol not in selected:
                    selected.append(symbol)
                batches.setdefault(event_id, []).append(symbol)
        return batches, selected, len(omitted_symbols)

    def mark_requests_processed(self, requests: Iterable[object], batches: Dict[str, List[str]]) -> Dict[str, object]:
        cursor_payload = self.cursor_payload()
        progress = self.event_symbol_progress(cursor_payload)
        completed_event_ids: List[str] = []
        partial_event_ids: List[str] = []
        for event in requests or []:
            event_id = str(getattr(event, "event_id", "") or "").strip()
            if not event_id:
                continue
            all_symbols = event_symbols(event)
            selected_symbols = [symbol for symbol in batches.get(event_id, []) if symbol]
            if not all_symbols:
                completed_event_ids.append(event_id)
                progress.pop(event_id, None)
                continue
            existing_symbols = list(progress.get(event_id, []) or [])
            merged = []
            for symbol in existing_symbols + selected_symbols:
                if symbol not in merged:
                    merged.append(symbol)
            if not selected_symbols and not existing_symbols:
                continue
            if set(all_symbols).issubset(set(merged)):
                completed_event_ids.append(event_id)
                progress.pop(event_id, None)
            else:
                partial_event_ids.append(event_id)
                progress[event_id] = merged[:200]
        if hasattr(self.cursor_store, "save"):
            cursor_payload["eventSymbolProgress"] = progress
            self.save_cursor_payload(cursor_payload)
        if completed_event_ids:
            self.cursor_store.mark_processed(completed_event_ids)
        return {
            "completedEventIds": completed_event_ids,
            "partialEventIds": partial_event_ids,
        }

    def run_once(self, limit: int = 0, force: bool = True) -> Dict[str, object]:
        if not self.enabled():
            return {"status": "disabled", "processedCount": 0, "alertCount": 0}
        requests = self.pending_requests(limit)
        if not requests:
            return {"status": "idle", "processedCount": 0, "alertCount": 0}
        symbol_batches, symbols, omitted_symbol_count = self.request_symbol_batches(requests)
        runner = self.monitor_runner_factory()
        if "symbol_filter" in inspect.signature(runner.run_once).parameters:
            alerts = runner.run_once(force=force, symbol_filter=symbols)
        else:
            alerts = runner.run_once(force=force)
        account_ids = [getattr(account, "account_id", "") for account in getattr(runner, "accounts", [])]
        trigger_event_ids = [event.event_id for event in requests]
        completed = ontology_reasoning_completed_event(
            trigger_event_ids,
            account_ids,
            symbols,
            len(alerts or []),
            status="ok",
            reason=(
                "데이터 변경 이벤트가 온톨로지 추론 사이클을 실행했습니다."
                + (f" 심볼 한도 {self.max_symbols_per_run()}개가 적용되어 {omitted_symbol_count}개는 다음 사이클로 이월했습니다." if omitted_symbol_count else "")
            ),
        )
        rule_candidate_result = self.propose_rule_candidates(symbols, requests, alerts, force=False)
        refreshed_research_runs = self.mark_research_runs_refreshed(requests)
        self.publish(completed)
        progress_result = self.mark_requests_processed(requests, symbol_batches)
        self.mark_symbols_reasoned(symbols)
        return {
            "status": "ok",
            "processedCount": len(trigger_event_ids),
            "completedEventCount": len(progress_result.get("completedEventIds") or []),
            "partialEventCount": len(progress_result.get("partialEventIds") or []),
            "alertCount": len(alerts or []),
            "symbols": symbols,
            "maxSymbolsPerRun": self.max_symbols_per_run(),
            "omittedSymbolCount": omitted_symbol_count,
            "accountIds": [item for item in account_ids if item],
            "ruleCandidateResult": rule_candidate_result,
            "refreshedResearchRunIds": refreshed_research_runs,
        }

    def mark_symbols_reasoned(self, symbols: Iterable[str]) -> None:
        clean_symbols = [str(symbol or "").upper().strip() for symbol in symbols or [] if str(symbol or "").strip()]
        if not clean_symbols or not hasattr(self.cursor_store, "load") or not hasattr(self.cursor_store, "save"):
            return
        payload = self.cursor_payload()
        last_by_symbol = self.last_reasoned_at_by_symbol(payload)
        now = self.now_provider()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        stamp = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        for symbol in clean_symbols:
            last_by_symbol[symbol] = stamp
        payload["lastReasonedAtBySymbol"] = dict(sorted(last_by_symbol.items()))
        self.save_cursor_payload(payload)

    def mark_research_runs_refreshed(self, requests: Iterable[object]) -> List[str]:
        if not self.research_store or not hasattr(self.research_store, "mark_reasoning_refreshed"):
            return []
        refreshed = []
        for event in requests or []:
            run_id = str(event_payload(event).get("researchRunId") or "").strip()
            if not run_id or run_id in refreshed:
                continue
            try:
                result = self.research_store.mark_reasoning_refreshed(run_id, True)
            except Exception:  # noqa: BLE001 - run audit failure must not invalidate the active graph generation.
                continue
            if result:
                refreshed.append(run_id)
        return refreshed

    def propose_rule_candidates(
        self,
        symbols: Iterable[str] = None,
        requests: Iterable[object] = None,
        alerts: Iterable[object] = None,
        force: bool = False,
    ) -> Dict[str, object]:
        if not self.rule_candidate_ai_enabled():
            return {"status": "disabled", "candidateCount": 0, "savedCount": 0}
        if not self.rule_candidate_service:
            return {"status": "not-configured", "candidateCount": 0, "savedCount": 0}
        if not force and not self.rule_candidate_due():
            return {"status": "cooldown", "candidateCount": 0, "savedCount": 0}
        try:
            result = self.rule_candidate_service.propose(
                symbols=symbols or [],
                trigger="ontology-reasoning",
                requests=requests or [],
                alerts=alerts or [],
            )
        except Exception as error:  # noqa: BLE001 - AI proposal must not block graph reasoning.
            result = {"status": "error", "reason": str(error)[:180], "candidateCount": 0, "savedCount": 0}
        self.mark_rule_candidate_run(result)
        return result

    def rule_candidate_due(self) -> bool:
        if not hasattr(self.cursor_store, "load"):
            return True
        payload = self.cursor_store.load()
        raw = str(payload.get("lastRuleCandidateAiAt") or "")
        if not raw:
            return True
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return True
        elapsed = datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)
        return elapsed.total_seconds() >= self.rule_candidate_interval_minutes() * 60

    def mark_rule_candidate_run(self, result: Dict[str, object]) -> None:
        if not hasattr(self.cursor_store, "load") or not hasattr(self.cursor_store, "save"):
            return
        payload = self.cursor_store.load()
        payload["lastRuleCandidateAiAt"] = datetime.now(timezone.utc).isoformat()
        payload["lastRuleCandidateAiResult"] = {
            "status": str((result or {}).get("status") or ""),
            "candidateCount": int((result or {}).get("candidateCount") or 0),
            "savedCount": int((result or {}).get("savedCount") or 0),
        }
        self.cursor_store.save(payload)

    def status(self) -> Dict[str, object]:
        pending = self.pending_requests(self.batch_size())
        progress = self.event_symbol_progress()
        _batches, next_symbols, omitted_count = self.request_symbol_batches(pending)
        return {
            "enabled": self.enabled(),
            "pendingCount": len(pending),
            "batchSize": self.batch_size(),
            "maxSymbolsPerRun": self.max_symbols_per_run(),
            "processedCount": len(self.cursor_store.processed_event_ids()),
            "pendingSymbols": self.request_symbols(pending),
            "nextSymbols": next_symbols,
            "nextOmittedSymbolCount": omitted_count,
            "partialEventCount": len(progress),
            "ruleCandidateAiEnabled": self.rule_candidate_ai_enabled(),
            "ruleCandidateAiDue": self.rule_candidate_due(),
        }
