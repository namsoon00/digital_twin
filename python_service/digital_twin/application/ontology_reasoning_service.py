import inspect
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, List

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


class OntologyReasoningRunner:
    def __init__(
        self,
        event_reader,
        cursor_store,
        monitor_runner_factory: Callable,
        event_publisher=None,
        settings: Dict[str, object] = None,
        rule_candidate_service=None,
    ):
        self.event_reader = event_reader
        self.cursor_store = cursor_store
        self.monitor_runner_factory = monitor_runner_factory
        self.event_publisher = event_publisher
        self.settings = dict(settings or {})
        self.rule_candidate_service = rule_candidate_service

    def enabled(self) -> bool:
        return truthy(self.settings.get("ontologyReasoningEnabled"), True)

    def batch_size(self) -> int:
        return int_setting(self.settings, "ontologyReasoningBatchSize", 20, 1, 200)

    def rule_candidate_ai_enabled(self) -> bool:
        return truthy(self.settings.get("ontologyRuleCandidateAiEnabled"), True)

    def rule_candidate_interval_minutes(self) -> int:
        return int_setting(self.settings, "ontologyRuleCandidateAiIntervalMinutes", 60, 5, 1440)

    def pending_requests(self, limit: int = 0) -> List[object]:
        processed = set(self.cursor_store.processed_event_ids())
        events = [
            event
            for event in self.event_reader.events(name=ONTOLOGY_REASONING_REQUESTED)
            if event.event_id not in processed and int((event.payload or {}).get("changedCount") or 0) > 0
        ]
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
            for symbol in (event.payload or {}).get("symbols") or []:
                clean = str(symbol or "").upper().strip()
                if clean and clean not in symbols:
                    symbols.append(clean)
        return symbols

    def run_once(self, limit: int = 0, force: bool = True) -> Dict[str, object]:
        if not self.enabled():
            return {"status": "disabled", "processedCount": 0, "alertCount": 0}
        requests = self.pending_requests(limit)
        if not requests:
            return {"status": "idle", "processedCount": 0, "alertCount": 0}
        symbols = self.request_symbols(requests)
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
            reason="데이터 변경 이벤트가 온톨로지 추론 사이클을 실행했습니다.",
        )
        rule_candidate_result = self.propose_rule_candidates(symbols, requests, alerts, force=False)
        self.publish(completed)
        self.cursor_store.mark_processed(trigger_event_ids)
        return {
            "status": "ok",
            "processedCount": len(trigger_event_ids),
            "alertCount": len(alerts or []),
            "symbols": symbols,
            "accountIds": [item for item in account_ids if item],
            "ruleCandidateResult": rule_candidate_result,
        }

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
        return {
            "enabled": self.enabled(),
            "pendingCount": len(pending),
            "batchSize": self.batch_size(),
            "processedCount": len(self.cursor_store.processed_event_ids()),
            "pendingSymbols": self.request_symbols(pending),
            "ruleCandidateAiEnabled": self.rule_candidate_ai_enabled(),
            "ruleCandidateAiDue": self.rule_candidate_due(),
        }
