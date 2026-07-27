import hashlib
import inspect
import os
import socket
import time
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, List, Mapping, Tuple

from ..domain.events import DomainEvent, ONTOLOGY_REASONING_REQUESTED, ontology_reasoning_completed_event
from ..domain.ontology_change_impact import requested_scope_families_for_event_fact_types
from ..domain.ontology_reasoning_batch import adaptive_reasoning_batch_plan
from ..domain.ontology_runtime_operations import native_replay_validation
from ..domain.market_data import known_stock
from ..domain.market_hours import evaluate_market_hours
from ..domain.message_types import INVESTMENT_INSIGHT
from ..domain.investment_evidence_governance import (
    ReasoningGeneration,
    ResearchReasoningHandoff,
    complete_reasoning_handoff,
)
from ..domain.ontology_reasoning_queue import (
    is_generic_research_latest_state,
    mailbox_slot_family,
)


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


def event_fact_revision(event: object, symbol: str) -> str:
    """Read one fact-revision ID from a reasoning request's provenance."""
    clean_symbol = str(symbol or "").upper().strip()
    raw = event_payload(event).get("factRevisionsBySymbol")
    if not clean_symbol or not isinstance(raw, dict):
        return ""
    value = raw.get(clean_symbol)
    if value is None:
        for key, candidate in raw.items():
            if str(key or "").upper().strip() == clean_symbol:
                value = candidate
                break
    return str(value or "").strip()[:160]


def event_changed_count(event: object) -> int:
    payload = event_payload(event)
    return int(float_value(payload.get("changedCount"), 0.0) or 0)


def reasoning_request_provenance(
    events: Iterable[object],
    target_symbols: Iterable[object] = None,
) -> Dict[str, object]:
    """Create bounded scheduler provenance for a TypeDB projection audit.

    This records which source updates asked for a cycle. It is operational
    context only: native TypeDB rules still evaluate the current ABox without
    using this payload as a rule condition.
    """
    targets = {
        str(symbol or "").upper().strip()
        for symbol in target_symbols or []
        if str(symbol or "").strip()
    }
    request_event_ids, source_event_ids, triggers, fact_types, observed_at = set(), set(), set(), set(), []
    changed_fields: Dict[str, set] = {}
    revisions: Dict[str, str] = {}
    for event in events or []:
        payload = event_payload(event)
        event_id = str(getattr(event, "event_id", "") or "").strip()
        if event_id:
            request_event_ids.add(event_id)
        source_event_id = str(payload.get("sourceEventId") or "").strip()
        if source_event_id:
            source_event_ids.add(source_event_id)
        trigger = str(payload.get("trigger") or "").strip()
        if trigger:
            triggers.add(trigger)
        for fact_type in payload.get("factTypes") or []:
            clean_fact_type = str(fact_type or "").strip()
            if clean_fact_type:
                fact_types.add(clean_fact_type)
        for key in ["sourceObservedAt", "sourceAsOf", "observedAt", "generatedAt", "collectedAt"]:
            stamp = str(payload.get(key) or "").strip()
            if stamp:
                observed_at.append(stamp)
                break
        raw_fields = payload.get("changedFieldsBySymbol")
        raw_fields = raw_fields if isinstance(raw_fields, dict) else {}
        raw_revisions = payload.get("factRevisionsBySymbol")
        raw_revisions = raw_revisions if isinstance(raw_revisions, dict) else {}
        event_targets = targets or set(event_symbols(event))
        for raw_symbol, fields in raw_fields.items():
            symbol = str(raw_symbol or "").upper().strip()
            if not symbol or (event_targets and symbol not in event_targets):
                continue
            if not isinstance(fields, (list, tuple, set)):
                continue
            changed_fields.setdefault(symbol, set()).update(
                str(field or "").strip()
                for field in fields
                if str(field or "").strip()
            )
        for raw_symbol, revision in raw_revisions.items():
            symbol = str(raw_symbol or "").upper().strip()
            if not symbol or (event_targets and symbol not in event_targets):
                continue
            clean_revision = str(revision or "").strip()
            if clean_revision:
                revisions[symbol] = clean_revision[:160]
    ordered_fact_types = sorted(fact_types)
    return {
        "version": "reasoning-request-context-v1",
        "requestEventIds": sorted(request_event_ids)[:80],
        "sourceEventIds": sorted(source_event_ids)[:80],
        "triggers": sorted(triggers)[:20],
        "factTypes": ordered_fact_types[:30],
        "requestedScopeFamilies": requested_scope_families_for_event_fact_types(ordered_fact_types),
        "targetSymbols": sorted(targets)[:80],
        "sourceObservedAt": max(observed_at) if observed_at else "",
        "changedFieldsBySymbol": {
            symbol: sorted(values)[:30]
            for symbol, values in sorted(changed_fields.items())
            if values
        },
        "factRevisionsBySymbol": dict(sorted(revisions.items())),
    }


REVIEW_LEVEL_ORDER = {
    "normal": 0,
    "observe": 1,
    "check": 2,
    "act": 3,
    "immediate": 4,
    # ``blocked`` is an unavailable judgement, not an urgent investment
    # condition.  Keeping it out of the escalation order prevents failed
    # projection work from jumping ahead of a valid holding update.
    "blocked": -1,
}

TRIGGER_ORDER = {
    "research-evidence-update": 6,
    "news-analysis-enrichment": 6,
    "research-evidence-lifecycle": 6,
    "investment-calendar-update": 5,
    "market-data-update": 4,
    "kis-realtime-websocket": 3,
    "kis-realtime-update": 3,
    "portfolio-snapshot-update": 2,
    "data-update": 1,
}

# A KIS or market-data event represents a new observation of a complete live
# portfolio snapshot. Its materiality level changes scheduling priority, but
# never makes an older price snapshot more trustworthy than the newest one.
# Generic research-evidence collection is also a fungible observation: the
# source repository retains every evidence item and a later ABox projection
# reads the complete latest evidence set. Hypothesis research handoffs remain
# non-fungible because their individual run-to-generation contract must be
# acknowledged explicitly. ``immediate`` remains outside the mailbox for a
# separately modelled emergency event, rather than a normal quote update.
COALESCIBLE_REALTIME_TRIGGERS = {
    "market-data-update",
    "kis-realtime-update",
    "kis-realtime-websocket",
    "portfolio-snapshot-update",
}

COALESCIBLE_RESEARCH_TRIGGERS = {
    "research-evidence-update",
    "news-analysis-enrichment",
    "research-evidence-lifecycle",
}

OFF_SESSION_REFERENCE_FIELDS = {
    "quoteStatus",
    "dataQuality",
    "freshnessStatus",
    "sourceTimestampState",
    "latencyStatus",
    "marketSession",
    "marketSessionLabel",
    "realTime",
}


def materiality_assessments(event: object) -> List[Dict[str, object]]:
    raw = event_payload(event).get("materialityAssessments") or []
    if isinstance(raw, dict):
        raw = list(raw.values())
    return [dict(item) for item in raw if isinstance(item, dict)]


def materiality_assessment_for_symbol(event: object, symbol: object) -> Dict[str, object]:
    clean = str(symbol or "").upper().strip()
    for item in materiality_assessments(event):
        if str(item.get("subject") or "").upper().strip() == clean:
            return item
    return {}


def event_review_level(event: object) -> str:
    levels = [
        str(item.get("reviewLevel") or "normal").strip().lower()
        for item in materiality_assessments(event)
    ]
    levels = [level for level in levels if level in REVIEW_LEVEL_ORDER]
    return max(levels or ["normal"], key=lambda item: REVIEW_LEVEL_ORDER.get(item, 0))


def normalized_priority_symbols(raw: object) -> Dict[str, int]:
    """Normalize runtime portfolio roles into a scheduler-only priority map.

    This is intentionally a delivery/worker scheduling concern. It never
    changes the TypeDB rule result or the investment judgement; it only keeps
    a live holding from waiting behind background market-data ticks.
    """
    priorities: Dict[str, int] = {}
    if not isinstance(raw, dict):
        return priorities
    role_weights = {
        "holdingSymbols": 2,
        "holdings": 2,
        "positions": 2,
        "watchlistSymbols": 1,
        "watchlist": 1,
    }
    for role, weight in role_weights.items():
        values = raw.get(role) or []
        if isinstance(values, str):
            values = values.split(",")
        if not isinstance(values, (list, tuple, set)):
            continue
        for value in values:
            symbol = str(value or "").upper().strip()
            if symbol:
                priorities[symbol] = max(weight, priorities.get(symbol, 0))
    return priorities


def event_subject_priority(event: object, priority_symbols: Dict[str, int] = None) -> int:
    priorities = priority_symbols or {}
    return max([int(priorities.get(symbol, 0) or 0) for symbol in event_symbols(event)] or [0])


def event_order_key(event: object, priority_symbols: Dict[str, int] = None) -> Tuple[int, int, int, int, int]:
    payload = event_payload(event)
    trigger = str(payload.get("trigger") or "data-update").strip()
    fact_types = {str(item or "").strip() for item in payload.get("factTypes") or []}
    return (
        event_subject_priority(event, priority_symbols),
        REVIEW_LEVEL_ORDER.get(event_review_level(event), 0),
        TRIGGER_ORDER.get(trigger, 0),
        1 if "ResearchEvidence" in fact_types else 0,
        1 if "MarketQuote" in fact_types else 0,
    )


def event_time_key(event: object) -> Tuple[str, str]:
    return (
        str(getattr(event, "occurred_at", "") or ""),
        str(getattr(event, "event_id", "") or ""),
    )


def lightweight_ontology_reasoning_queue_state(
    event_reader,
    cursor_store,
    mailbox_store=None,
    settings: Dict[str, object] = None,
    scan_limit: int = 0,
) -> Dict[str, object]:
    """Read a bounded scheduler signal without constructing a full runner.

    Lower-priority workers only need to know whether live reasoning is waiting.
    Keeping this helper independent of ``OntologyReasoningRunner`` avoids
    registry, monitor, TypeDB, and rule-advisor construction on every shared
    world or maintenance scheduler tick.
    """
    started = time.perf_counter()
    configured = dict(settings or {})
    enabled = truthy(configured.get("ontologyReasoningEnabled"), True)
    try:
        default_limit = int_setting(configured, "ontologyReasoningBatchSize", 200, 1, 200)
        bounded = int(scan_limit or default_limit)
    except (TypeError, ValueError):
        bounded = 50
    bounded = max(1, min(500, bounded))
    result = {
        "enabled": enabled,
        "probeMode": "lightweight-event-mailbox",
        "rawRequestCount": 0,
        "effectivePendingCount": 0,
        "mailboxPendingEntryCount": 0,
        "pendingSymbolCount": 0,
        "pendingSymbols": [],
        "oldestRequestAt": "",
        "queueDispatch": {
            "mode": "lightweight-observation",
            "oldestRequestAt": "",
            "pendingSymbolCount": 0,
            "overduePendingSymbolCount": 0,
        },
        "queueHealth": {"status": "healthy", "reason": "", "scope": "probe-connectivity"},
        "probeHealth": {"status": "healthy", "reason": ""},
        "mailbox": {"enabled": bool(mailbox_store)},
    }
    if not enabled:
        result.update({"status": "disabled", "probeDurationMs": 0})
        return result

    # A durable queue-state row is maintained when a source event enters or
    # leaves the mailbox.  Lower-priority workers must never scan event JSON
    # or the large reasoning cursor just to decide whether they should yield.
    # The legacy path remains for test doubles and rolling migration recovery.
    mailbox_enabled = bool(mailbox_store) and truthy(
        configured.get("ontologyReasoningMailboxEnabled"),
        True,
    )
    fast_state_reader = getattr(mailbox_store, "fast_state", None) if mailbox_enabled else None
    if callable(fast_state_reader):
        try:
            mailbox = dict(fast_state_reader() or {})
        except Exception as error:  # noqa: BLE001 - legacy bounded scan remains the recovery path.
            mailbox = {"enabled": True, "status": "error", "reason": str(error)[:180]}
        if str(mailbox.get("status") or "") != "error":
            mailbox["enabled"] = True
            pending = max(0, int(float(mailbox.get("pendingEntryCount") or 0)))
            symbols = [
                str(symbol or "").upper().strip()
                for symbol in mailbox.get("pendingSymbols") or []
                if str(symbol or "").strip()
            ]
            oldest = str(mailbox.get("oldestPendingAt") or "").strip()
            result.update({
                "status": "pending" if pending else "idle",
                "probeMode": "durable-mailbox-state-v2",
                # The event-log count is an audit metric.  It is deliberately
                # not read on the scheduling hot path.
                "rawRequestCount": 0,
                "auditRequestCount": None,
                "effectivePendingCount": pending,
                "mailboxPendingEntryCount": pending,
                "runningEntryCount": max(0, int(float(mailbox.get("runningEntryCount") or 0))),
                "retryingEntryCount": max(0, int(float(mailbox.get("retryingEntryCount") or 0))),
                "pendingSymbolCount": max(0, int(float(mailbox.get("pendingSymbolCount") or len(symbols)))),
                "pendingSymbols": symbols,
                "oldestRequestAt": oldest,
                "queueDispatch": {
                    "mode": "durable-queue-state",
                    "oldestRequestAt": oldest,
                    "pendingSymbolCount": max(0, int(float(mailbox.get("pendingSymbolCount") or len(symbols)))),
                    "overduePendingSymbolCount": 0,
                },
                # ``probeHealth`` means the read model was available.  It is
                # distinct from queue-delay health, which is evaluated by the
                # operational health service from age and throughput.
                "probeHealth": {"status": "healthy", "reason": ""},
                "queueHealth": {"status": "healthy", "reason": "", "scope": "probe-connectivity"},
                "mailbox": mailbox,
                "probeDurationMs": max(0, int((time.perf_counter() - started) * 1000)),
            })
            return result

    errors = []
    processed = set()
    try:
        processed_reader = getattr(cursor_store, "processed_event_ids", None)
        if callable(processed_reader):
            processed = {
                str(event_id or "").strip()
                for event_id in processed_reader() or []
                if str(event_id or "").strip()
            }
    except Exception as error:  # noqa: BLE001 - mailbox remains a safe fallback signal.
        errors.append("처리 완료 커서 조회 실패: " + str(error)[:120])

    source_events = []
    try:
        reader = getattr(event_reader, "recent_events", None)
        if callable(reader):
            source_events = reader(name=ONTOLOGY_REASONING_REQUESTED, limit=bounded)
        elif event_reader and hasattr(event_reader, "events"):
            source_events = event_reader.events(name=ONTOLOGY_REASONING_REQUESTED, limit=bounded)
    except Exception as error:  # noqa: BLE001 - a probe must not delay a live TypeDB writer.
        errors.append("추론 요청 조회 실패: " + str(error)[:120])
        source_events = []

    pending = []
    for event in source_events or []:
        event_id = str(getattr(event, "event_id", "") or "").strip()
        if not event_id or event_id in processed or event_changed_count(event) <= 0:
            continue
        pending.append(event)
    pending.sort(key=event_time_key)

    mailbox = {"enabled": mailbox_enabled}
    if mailbox_enabled:
        summary = getattr(mailbox_store, "summary", None)
        if callable(summary):
            try:
                payload = summary()
                mailbox = dict(payload or {}) if isinstance(payload, dict) else {}
                mailbox["enabled"] = True
            except Exception as error:  # noqa: BLE001 - direct event observations remain usable.
                mailbox = {"enabled": True, "status": "error", "reason": str(error)[:180]}
        else:
            mailbox["status"] = "not-supported"
    try:
        mailbox_pending = max(0, int(float(mailbox.get("pendingEntryCount") or 0)))
    except (TypeError, ValueError):
        mailbox_pending = 0
    if str(mailbox.get("status") or "") == "error":
        errors.append(str(mailbox.get("reason") or "영속 추론 메일박스 상태를 읽지 못했습니다.")[:160])

    symbols = []
    for event in pending:
        for symbol in event_symbols(event):
            if symbol not in symbols:
                symbols.append(symbol)
    timestamps = [
        str(getattr(event, "occurred_at", "") or "").strip()
        for event in pending
        if str(getattr(event, "occurred_at", "") or "").strip()
    ]
    mailbox_oldest = str(mailbox.get("oldestPendingAt") or "").strip()
    if mailbox_oldest:
        timestamps.append(mailbox_oldest)
    oldest = min(timestamps) if timestamps else ""
    source_pending = len(pending)
    # Mailbox entries and source events may represent the same realtime
    # observation.  Use the larger count as a pressure signal rather than
    # double counting it.
    effective_pending = max(source_pending, mailbox_pending)
    result.update({
        "status": "pending" if effective_pending else ("degraded" if errors else "idle"),
        "rawRequestCount": source_pending,
        "effectivePendingCount": effective_pending,
        "mailboxPendingEntryCount": mailbox_pending,
        "pendingSymbolCount": len(symbols),
        "pendingSymbols": symbols,
        "oldestRequestAt": oldest,
        "queueDispatch": {
            "mode": "lightweight-observation",
            "oldestRequestAt": oldest,
            "pendingSymbolCount": len(symbols),
            "overduePendingSymbolCount": 0,
        },
        "queueHealth": {
            "status": "degraded" if errors else "healthy",
            "reason": " · ".join(errors),
            "scope": "probe-connectivity",
        },
        "probeHealth": {
            "status": "degraded" if errors else "healthy",
            "reason": " · ".join(errors),
        },
        "mailbox": mailbox,
        "probeDurationMs": max(0, int((time.perf_counter() - started) * 1000)),
    })
    return result


def realtime_coalescing_key(event: object) -> Tuple[str, str, Tuple[str, ...]]:
    """Return a conservative replacement key for fungible source observations."""
    payload = event_payload(event)
    trigger = str(payload.get("trigger") or "").strip()
    review_level = event_review_level(event)
    is_generic_research_update = is_generic_research_latest_state(event)
    if trigger not in COALESCIBLE_REALTIME_TRIGGERS and not is_generic_research_update:
        return ()
    if review_level == "immediate":
        return ()
    symbols = event_symbols(event)
    if not symbols:
        return ()
    fact_types = tuple(sorted({str(item or "").strip() for item in payload.get("factTypes") or [] if str(item or "").strip()}))
    account_ids = []
    raw_account_ids = payload.get("accountIds") or payload.get("accountId") or []
    if isinstance(raw_account_ids, str):
        raw_account_ids = [raw_account_ids]
    elif not isinstance(raw_account_ids, (list, tuple, set)):
        raw_account_ids = []
    for value in [payload.get("accountId")] + list(raw_account_ids):
        clean = str(value or "").strip()
        if clean and clean not in account_ids:
            account_ids.append(clean)
    # Empty account scope represents market-wide source observations. It must
    # remain separate from account-scoped snapshots even when both carry the
    # same symbols and fact family.
    account_scope = ",".join(sorted(account_ids)) or "market"
    return account_scope, trigger, fact_types


def event_work_class(event: object) -> str:
    """Classify scheduler work for operational visibility only."""
    payload = event_payload(event)
    trigger = str(payload.get("trigger") or "").strip().lower()
    fact_types = {
        str(item or "").strip().lower()
        for item in payload.get("factTypes") or []
        if str(item or "").strip()
    }
    if not event_symbols(event):
        return "global"
    if "research" in trigger or fact_types & {"researchevidence", "verifiedclaim", "verificationrun", "newsevent"}:
        return "research"
    if trigger in COALESCIBLE_REALTIME_TRIGGERS or fact_types & {
        "marketquote", "technicalindicator", "executionflow", "orderbook",
    }:
        return "realtime-market"
    if "portfolio" in trigger or fact_types & {"portfoliosnapshot"}:
        return "portfolio"
    return "other"


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
        priority_symbols_provider: Callable = None,
        maintenance_runner: Callable = None,
        projection_recovery_probe: Callable = None,
        storage_guard: Callable = None,
        mailbox_store=None,
        queue_health_service=None,
        projection_coordinator_probe: Callable = None,
    ):
        self.event_reader = event_reader
        self.cursor_store = cursor_store
        self.monitor_runner_factory = monitor_runner_factory
        self.event_publisher = event_publisher
        self.settings = dict(settings or {})
        self.rule_candidate_service = rule_candidate_service
        self.research_store = research_store
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.priority_symbols_provider = priority_symbols_provider
        self.maintenance_runner = maintenance_runner
        self.projection_recovery_probe = projection_recovery_probe
        self.storage_guard = storage_guard
        self.mailbox_store = mailbox_store
        self.queue_health_service = queue_health_service
        self.projection_coordinator_probe = projection_coordinator_probe

    def enabled(self) -> bool:
        return truthy(self.settings.get("ontologyReasoningEnabled"), True)

    def batch_size(self) -> int:
        return int_setting(self.settings, "ontologyReasoningBatchSize", 200, 1, 200)

    def max_symbols_per_run(self) -> int:
        return int_setting(self.settings, "ontologyReasoningMaxSymbolsPerRun", 3, 0, 200)

    def native_typedb_rule_execution_enabled(self) -> bool:
        """Return whether this runner delegates investment rules to TypeDB.

        Unit and compatibility callers may not provide either setting.  Keep
        their historical batching behavior unless the native runtime is
        explicitly enabled by the service factory.
        """
        configured = self.settings.get("ontologyReasoningTypeDbNativeRuleExecutionEnabled")
        if configured is None:
            configured = self.settings.get("typedbNativeRuleExecutionEnabled")
        return truthy(configured, False)

    def native_typedb_target_symbol_limit(self) -> int:
        """Bound schema-function work without reducing the complete ABox."""
        return int_setting(self.settings, "typedbNativeRuleTargetSymbolLimit", 1, 1, 200)

    def effective_max_symbols_per_run(self) -> int:
        configured_limit = self.max_symbols_per_run()
        if not self.native_typedb_rule_execution_enabled():
            return configured_limit
        native_limit = self.native_typedb_target_symbol_limit()
        if configured_limit <= 0:
            return native_limit
        return min(configured_limit, native_limit)

    def coherent_snapshot_enabled(self) -> bool:
        return truthy(self.settings.get("ontologyReasoningCoherentSnapshotEnabled"), True)

    def coherent_snapshot_max_symbols(self) -> int:
        return int_setting(self.settings, "ontologyReasoningCoherentSnapshotMaxSymbols", 20, 1, 50)

    def mailbox_enabled(self) -> bool:
        """Use a durable latest-state queue for fungible source observations."""
        return bool(self.mailbox_store) and truthy(self.settings.get("ontologyReasoningMailboxEnabled"), True)

    def mailbox_batch_size(self) -> int:
        return int_setting(self.settings, "ontologyReasoningMailboxBatchSize", self.batch_size(), 1, 1000)

    def mailbox_retention_hours(self) -> int:
        return int_setting(self.settings, "ontologyReasoningMailboxRetentionHours", 72, 1, 24 * 90)

    def mailbox_work_lease_seconds(self) -> int:
        configured = int_setting(self.settings, "ontologyReasoningWorkLeaseSeconds", 600, 30, 3600)
        # A parent can only reclaim a child after its hard timeout and grace
        # window. Keep the durable lease beyond that boundary so a surviving
        # native TypeDB call cannot be raced by the next scheduler turn.
        protected = (
            self.execution_timeout_seconds()
            + self.execution_timeout_grace_seconds()
            + self.execution_timeout_backoff_seconds()
        )
        return min(3600, max(configured, protected))

    def mailbox_work_retry_seconds(self) -> int:
        return int_setting(self.settings, "ontologyReasoningWorkRetrySeconds", 30, 5, 900)

    def mailbox_worker_id(self) -> str:
        configured = str(
            self.settings.get("ontologyReasoningWorkerId")
            or os.environ.get("ONTOLOGY_REASONING_WORKER_ID")
            or ""
        ).strip()
        return configured or "reasoning:" + socket.gethostname() + ":" + str(os.getpid())

    def mailbox_entries_for_requests(self, requests: Iterable[object]) -> List[Dict[str, str]]:
        entries = []
        for event in requests or []:
            metadata = self.mailbox_metadata(event)
            mailbox_key = str(metadata.get("mailboxKey") or "").strip()
            source_event_id = str(metadata.get("sourceEventId") or "").strip()
            if mailbox_key and source_event_id and not any(
                item["mailboxKey"] == mailbox_key and item["sourceEventId"] == source_event_id
                for item in entries
            ):
                entries.append({"mailboxKey": mailbox_key, "sourceEventId": source_event_id})
        return entries

    def claim_mailbox_work(self, entries: Iterable[Mapping[str, object]]) -> Dict[str, object]:
        claim = getattr(self.mailbox_store, "claim", None) if self.mailbox_enabled() else None
        if not callable(claim):
            return {"enabled": False, "claimed": list(entries or []), "blocked": []}
        try:
            return dict(claim(entries, self.mailbox_worker_id(), self.mailbox_work_lease_seconds()) or {})
        except Exception as error:  # noqa: BLE001 - existing mailbox safety still prevents a stale acknowledgement.
            return {"enabled": False, "claimed": list(entries or []), "blocked": [], "reason": str(error)[:180]}

    def checkpoint_mailbox_work(
        self,
        entries: Iterable[Mapping[str, object]],
        stage: str,
        details: Mapping[str, object] = None,
    ) -> None:
        checkpoint = getattr(self.mailbox_store, "checkpoint", None) if self.mailbox_enabled() else None
        if not callable(checkpoint):
            return
        try:
            checkpoint(entries, stage, details or {}, worker_id=self.mailbox_worker_id())
        except TypeError:
            # Older store adapters are still safe because mailbox rows are
            # source-revision guarded at acknowledgement time.
            checkpoint(entries, stage, details or {})
        except Exception:
            return

    def release_mailbox_work(self, entries: Iterable[Mapping[str, object]], reason: str, retry_after_seconds: int = 0) -> None:
        release = getattr(self.mailbox_store, "release", None) if self.mailbox_enabled() else None
        if not callable(release):
            return
        try:
            release(
                entries,
                reason,
                retry_after_seconds or self.mailbox_work_retry_seconds(),
                worker_id=self.mailbox_worker_id(),
            )
        except TypeError:
            release(entries, reason, retry_after_seconds or self.mailbox_work_retry_seconds())
        except Exception:
            return

    def recover_orphaned_mailbox_work(self) -> Dict[str, object]:
        """Resume work left by a confirmed-dead local isolated scheduler.

        This is operational recovery only. It changes no ABox or RuleBox
        fact, and unrecognised or remote worker owners remain lease-protected.
        """
        recover = getattr(self.mailbox_store, "recover_dead_local_worker_leases", None) if self.mailbox_enabled() else None
        if not callable(recover):
            return {"enabled": False, "recovered": []}
        try:
            return dict(recover(
                retry_after_seconds=self.mailbox_work_retry_seconds(),
                minimum_age_seconds=max(30, self.execution_timeout_grace_seconds() * 2),
            ) or {})
        except TypeError:
            return dict(recover(self.mailbox_work_retry_seconds()) or {})
        except Exception as error:  # noqa: BLE001 - normal lease expiry remains a safe fallback.
            return {"enabled": False, "recovered": [], "reason": str(error)[:180]}

    def source_freshness_enabled(self) -> bool:
        # Runtime settings explicitly enable this. Keeping compatibility
        # callers opt-in avoids treating synthetic unit-test timestamps as
        # live source clocks.
        return truthy(self.settings.get("ontologyReasoningSourceFreshnessEnabled"), False)

    def realtime_event_max_age_minutes(self) -> int:
        return int_setting(self.settings, "ontologyReasoningRealtimeEventMaxAgeMinutes", 15, 1, 24 * 60)

    def research_event_max_age_minutes(self) -> int:
        return int_setting(self.settings, "ontologyReasoningResearchEventMaxAgeMinutes", 360, 1, 24 * 30)

    def telemetry_history_limit(self) -> int:
        return int_setting(self.settings, "ontologyReasoningTelemetryHistoryLimit", 80, 10, 500)

    def event_scan_limit(self, requested_limit: int = 0) -> int:
        fallback = max(1500, int(requested_limit or self.batch_size()) * 40)
        return int_setting(self.settings, "ontologyReasoningEventScanLimit", fallback, 50, 10000)

    def min_interval_seconds(self) -> int:
        return int_setting(self.settings, "ontologyReasoningMinIntervalSeconds", 180, 0, 3600)

    def urgent_min_interval_seconds(self) -> int:
        return int_setting(self.settings, "ontologyReasoningUrgentMinIntervalSeconds", 60, 0, 3600)

    def projection_retry_seconds(self) -> int:
        return int_setting(self.settings, "ontologyReasoningProjectionRetrySeconds", 30, 5, 900)

    def projection_circuit_failure_threshold(self) -> int:
        return int_setting(self.settings, "ontologyProjectionCircuitFailureThreshold", 3, 1, 20)

    def projection_circuit_cooldown_seconds(self) -> int:
        return int_setting(self.settings, "ontologyProjectionCircuitCooldownSeconds", 300, 30, 3600)

    def projection_backpressure_enabled(self) -> bool:
        return truthy(self.settings.get("ontologyReasoningBackpressureEnabled"), True)

    def projection_backpressure_factor(self) -> float:
        value = float_value(self.settings.get("ontologyReasoningBackpressureFactor"), 1.15)
        return max(1.0, min(3.0, value))

    def projection_backpressure_max_seconds(self) -> int:
        return int_setting(self.settings, "ontologyReasoningBackpressureMaxSeconds", 900, 60, 3600)

    def fairness_drain_min_interval_seconds(self) -> int:
        """Keep overdue work moving without immediately starting another heavy projection."""
        return int_setting(
            self.settings,
            "ontologyReasoningFairnessDrainMinIntervalSeconds",
            60,
            5,
            900,
        )

    def process_isolation_enabled(self) -> bool:
        """Run the long-lived watch worker through bounded one-shot children.

        TypeDB driver calls can outlive a Python signal or a per-query timeout.
        The watch process therefore stays lightweight while each actual cycle
        has a process boundary that can be terminated without taking down the
        scheduler or unrelated collection workers.
        """
        return truthy(self.settings.get("ontologyReasoningProcessIsolationEnabled"), True)

    def execution_timeout_seconds(self) -> int:
        return int_setting(
            self.settings,
            "ontologyReasoningExecutionTimeoutSeconds",
            240,
            60,
            1800,
        )

    def execution_timeout_grace_seconds(self) -> int:
        return int_setting(
            self.settings,
            "ontologyReasoningExecutionTimeoutGraceSeconds",
            10,
            1,
            60,
        )

    def execution_timeout_backoff_seconds(self) -> int:
        return int_setting(
            self.settings,
            "ontologyReasoningExecutionTimeoutBackoffSeconds",
            300,
            30,
            3600,
        )

    def maintenance_enabled(self) -> bool:
        return truthy(self.settings.get("ontologyReasoningMaintenanceEnabled"), True)

    def maintenance_interval_seconds(self) -> int:
        # Active market sessions may never become queue-idle. Keep the
        # interval short enough to drain immutable ABox/InferenceBox
        # generations after a verified projection, while preserving a
        # bounded maintenance cadence under frequent market updates.
        return int_setting(self.settings, "ontologyReasoningMaintenanceIntervalSeconds", 60, 60, 86400)

    def projection_circuit_state(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        payload = self.cursor_payload() if payload is None else dict(payload or {})
        raw = payload.get("projectionCircuit") if isinstance(payload.get("projectionCircuit"), dict) else {}
        return dict(raw or {})

    def execution_timeout_guard_state(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        payload = self.cursor_payload() if payload is None else dict(payload or {})
        raw = payload.get("executionTimeoutGuard")
        return dict(raw or {}) if isinstance(raw, dict) else {}

    def execution_timeout_guard_remaining_seconds(self, payload: Dict[str, object] = None) -> int:
        guard = self.execution_timeout_guard_state(payload)
        if str(guard.get("status") or "") != "open":
            return 0
        return self.seconds_until(str(guard.get("retryAfterAt") or ""))

    def execution_timeout_guard_display_state(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        """Expose an expired cooldown as non-blocking without claiming recovery."""
        guard = self.execution_timeout_guard_state(payload)
        if (
            str(guard.get("status") or "") == "open"
            and self.execution_timeout_guard_remaining_seconds(payload) <= 0
        ):
            return {
                **guard,
                "status": "cooldown-elapsed",
                "blocking": False,
                "reason": "이전 TypeDB 추론 timeout의 재시도 대기가 끝났습니다. 다음 안전 투영이 성공하면 timeout 이력이 초기화됩니다.",
            }
        if guard:
            return {**guard, "blocking": str(guard.get("status") or "") == "open"}
        return {}

    def mark_execution_timeout_cooldown_elapsed(self) -> Dict[str, object]:
        """Persist elapsed timeout state before a new worker cycle starts.

        This deliberately does not close the timeout incident. Only a verified
        ABox/InferenceBox projection can do that in ``mark_successful_projection``.
        """
        if not hasattr(self.cursor_store, "load") or not hasattr(self.cursor_store, "save"):
            return {}
        payload = self.cursor_payload()
        guard = self.execution_timeout_guard_state(payload)
        if (
            str(guard.get("status") or "") != "open"
            or self.execution_timeout_guard_remaining_seconds(payload) > 0
        ):
            return {}
        now = self.now_provider()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        elapsed = {
            **guard,
            "status": "cooldown-elapsed",
            "retryAfterAt": "",
            "retryAfterSeconds": 0,
            "lastCooldownElapsedAt": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "reason": "이전 TypeDB 추론 timeout의 재시도 대기가 끝났습니다. 다음 안전 투영 성공 전까지 이력만 유지합니다.",
        }
        payload["executionTimeoutGuard"] = elapsed
        self.save_cursor_payload(payload)
        return elapsed

    def projection_circuit_remaining_seconds(self, payload: Dict[str, object] = None) -> int:
        state = self.projection_circuit_state(payload)
        return self.seconds_until(str(state.get("openUntil") or ""))

    def seconds_until(self, stamp: str) -> int:
        if not stamp:
            return 0
        try:
            parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        except ValueError:
            return 0
        now = self.now_provider()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return max(0, int((parsed.astimezone(timezone.utc) - now.astimezone(timezone.utc)).total_seconds()))

    def record_projection_failure(self, reason: str, failures: Iterable[Dict[str, object]] = None) -> Dict[str, object]:
        payload = self.cursor_payload()
        previous = self.projection_circuit_state(payload)
        count = int(previous.get("consecutiveFailures") or 0) + 1
        now = self.now_provider()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)
        threshold = self.projection_circuit_failure_threshold()
        open_seconds = 0
        if count >= threshold:
            exponent = min(3, count - threshold)
            open_seconds = min(3600, self.projection_circuit_cooldown_seconds() * (2 ** exponent))
        state = {
            "status": "open" if open_seconds else "closed",
            "consecutiveFailures": count,
            "failureThreshold": threshold,
            "lastFailureAt": now.isoformat().replace("+00:00", "Z"),
            "lastFailureReason": str(reason or "TypeDB projection failed.")[:500],
            "openUntil": (
                datetime.fromtimestamp(now.timestamp() + open_seconds, timezone.utc).isoformat().replace("+00:00", "Z")
                if open_seconds
                else ""
            ),
            "recentFailures": [
                {
                    "stage": str(item.get("stage") or ""),
                    "status": str(item.get("status") or ""),
                    "reason": str(item.get("reason") or "")[:180],
                }
                for item in list(failures or [])[:5]
                if isinstance(item, dict)
            ],
        }
        payload["projectionCircuit"] = state
        self.save_cursor_payload(payload)
        return state

    def reset_projection_circuit(self) -> None:
        payload = self.cursor_payload()
        previous = self.projection_circuit_state(payload)
        if not previous or (
            int(previous.get("consecutiveFailures") or 0) == 0
            and str(previous.get("status") or "closed") == "closed"
        ):
            return
        payload["projectionCircuit"] = {
            "status": "closed",
            "consecutiveFailures": 0,
            "failureThreshold": self.projection_circuit_failure_threshold(),
            "lastSuccessAt": self.now_provider().astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "openUntil": "",
        }
        self.save_cursor_payload(payload)

    def urgent_review_levels(self) -> set:
        raw = str(self.settings.get("ontologyReasoningUrgentReviewLevels") or "act,immediate")
        allowed = {"act", "immediate"}
        levels = {item.strip().lower() for item in raw.split(",") if item.strip().lower() in allowed}
        return levels or {"act", "immediate"}

    def fairness_max_wait_seconds(self) -> int:
        """Prevent a slow native TypeDB worker from starving one symbol forever."""
        return int_setting(self.settings, "ontologyReasoningFairnessMaxWaitSeconds", 900, 60, 86400)

    def fairness_drain_enabled(self) -> bool:
        """Drain an overdue target without adding another idle scheduler wait.

        TypeDB materialization remains serialised as one ABox/InferenceBox
        generation per projection. A bounded set of read-only native rule
        targets may run within that generation. This only removes the
        cooldown between successful runs after the configured fairness
        deadline has already been exceeded.
        """
        return truthy(self.settings.get("ontologyReasoningFairnessDrainEnabled"), True)

    def symbol_wait_seconds(self, symbol: str, cursor_payload: Dict[str, object] = None):
        stamp = self.last_reasoned_at_by_symbol(cursor_payload).get(str(symbol or "").upper().strip(), "")
        if not stamp:
            return None
        try:
            parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        except ValueError:
            return None
        now = self.now_provider()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return max(0, int((now.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()))

    def symbol_fairness_rank(self, symbol: str, cursor_payload: Dict[str, object] = None) -> Tuple[int, int]:
        wait_seconds = self.symbol_wait_seconds(symbol, cursor_payload)
        if wait_seconds is None:
            # A subject that has never reached native inference should receive
            # one turn before repeated high-priority updates take over again.
            return 2, self.fairness_max_wait_seconds()
        if wait_seconds >= self.fairness_max_wait_seconds():
            return 1, wait_seconds
        return 0, wait_seconds

    def symbol_fairness_state(self, symbol: str, cursor_payload: Dict[str, object] = None) -> str:
        rank, _wait_seconds = self.symbol_fairness_rank(symbol, cursor_payload)
        return "unseen" if rank == 2 else "overdue" if rank == 1 else "normal"

    def fairness_drain_state(
        self,
        selected_symbols: Iterable[str],
        cursor_payload: Dict[str, object] = None,
    ) -> Dict[str, object]:
        """Return a scheduling-only drain decision for already overdue work."""
        payload = self.cursor_payload() if cursor_payload is None else dict(cursor_payload or {})
        overdue_symbols = [
            str(symbol or "").upper().strip()
            for symbol in selected_symbols or []
            if str(symbol or "").strip()
            and self.symbol_fairness_state(str(symbol or "").upper().strip(), payload) == "overdue"
        ]
        return {
            "enabled": self.fairness_drain_enabled(),
            "active": bool(self.fairness_drain_enabled() and overdue_symbols),
            "symbols": list(dict.fromkeys(overdue_symbols)),
            "reason": (
                "대기 한도를 넘긴 종목을 순서대로 따라잡되, TypeDB를 연속 점유하지 않도록 최소 보호 대기를 적용합니다."
                if self.fairness_drain_enabled() and overdue_symbols
                else ""
            ),
        }

    def order_symbols_by_fairness(
        self,
        symbols: Iterable[str],
        priority_symbols: Dict[str, int] = None,
        cursor_payload: Dict[str, object] = None,
    ) -> List[str]:
        priorities = priority_symbols or {}
        unique: List[str] = []
        for value in symbols or []:
            symbol = str(value or "").upper().strip()
            if symbol and symbol not in unique:
                unique.append(symbol)
        original_order = {symbol: index for index, symbol in enumerate(unique)}
        return sorted(
            unique,
            key=lambda symbol: (
                *self.symbol_fairness_rank(symbol, cursor_payload),
                int(priorities.get(symbol, 0) or 0),
                -original_order.get(symbol, 0),
            ),
            reverse=True,
        )

    def event_fairness_rank(
        self,
        event: object,
        progress: Dict[str, List[str]] = None,
        cursor_payload: Dict[str, object] = None,
        priority_symbols: Dict[str, int] = None,
    ) -> Tuple[int, int]:
        due_symbols = self.due_event_symbols(event, progress, cursor_payload, priority_symbols)
        return max([self.symbol_fairness_rank(symbol, cursor_payload) for symbol in due_symbols] or [(0, 0)])

    def fairness_queue(self, symbols: Iterable[str], cursor_payload: Dict[str, object] = None) -> List[Dict[str, object]]:
        payload = self.cursor_payload() if cursor_payload is None else dict(cursor_payload or {})
        last_by_symbol = self.last_reasoned_at_by_symbol(payload)
        rows = []
        for symbol in self.order_symbols_by_fairness(symbols, cursor_payload=payload):
            state = self.symbol_fairness_state(symbol, payload)
            wait_seconds = self.symbol_wait_seconds(symbol, payload)
            rows.append({
                "symbol": symbol,
                "state": state,
                "waitSeconds": wait_seconds,
                "lastReasonedAt": last_by_symbol.get(symbol, ""),
            })
        return rows

    def rule_candidate_ai_enabled(self) -> bool:
        return truthy(self.settings.get("ontologyRuleCandidateAiEnabled"), True)

    def rule_candidate_interval_minutes(self) -> int:
        return int_setting(self.settings, "ontologyRuleCandidateAiIntervalMinutes", 60, 5, 1440)

    def priority_symbols(self) -> Dict[str, int]:
        if not self.priority_symbols_provider:
            return {}
        try:
            return normalized_priority_symbols(self.priority_symbols_provider())
        except Exception:  # noqa: BLE001 - scheduler prioritization must not block graph reasoning.
            return {}

    def ordered_event_symbols(self, event: object, priority_symbols: Dict[str, int] = None) -> List[str]:
        priorities = priority_symbols or {}
        original = event_symbols(event)
        if not original:
            payload = event_payload(event)
            for field in ["targetSymbols", "affectedSymbols", "globalTargetSymbols"]:
                values = payload.get(field) or []
                if isinstance(values, str):
                    values = values.split(",")
                for value in values if isinstance(values, (list, tuple, set)) else []:
                    symbol = str(value or "").upper().strip()
                    if symbol and symbol not in original:
                        original.append(symbol)
            # A subject-less macro, portfolio, or policy fact still has to be
            # reconciled against every live holding. The scheduler derives
            # that operational subject list from account roles; it does not
            # alter the TypeDB rule result.
            if not original and priorities:
                original = list(priorities)
        order = {symbol: index for index, symbol in enumerate(original)}
        return sorted(
            original,
            key=lambda symbol: (-int(priorities.get(symbol, 0) or 0), order.get(symbol, 0)),
        )

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

    def last_projection_attempt_at_by_symbol(self, payload: Dict[str, object] = None) -> Dict[str, str]:
        payload = self.cursor_payload() if payload is None else dict(payload or {})
        raw = (
            payload.get("lastProjectionAttemptAtBySymbol")
            if isinstance(payload.get("lastProjectionAttemptAtBySymbol"), dict)
            else {}
        )
        return {
            str(symbol or "").upper().strip(): str(stamp or "").strip()
            for symbol, stamp in raw.items()
            if str(symbol or "").strip() and str(stamp or "").strip()
        }

    def last_successful_projection_at(self, payload: Dict[str, object] = None) -> str:
        payload = self.cursor_payload() if payload is None else dict(payload or {})
        return str(payload.get("lastSuccessfulProjectionAt") or "").strip()

    def last_projection_runtime(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        payload = self.cursor_payload() if payload is None else dict(payload or {})
        raw = payload.get("lastProjectionRuntime")
        return dict(raw or {}) if isinstance(raw, dict) else {}

    def maintenance_state(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        payload = self.cursor_payload() if payload is None else dict(payload or {})
        raw = payload.get("ontologyMaintenance")
        return dict(raw or {}) if isinstance(raw, dict) else {}

    def timestamp_due(self, stamp: str, interval_seconds: int) -> bool:
        if not stamp or interval_seconds <= 0:
            return True
        try:
            parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        except ValueError:
            return True
        now = self.now_provider()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return (now.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() >= interval_seconds

    def timestamp_remaining_seconds(self, stamp: str, interval_seconds: int) -> int:
        if not stamp or interval_seconds <= 0:
            return 0
        try:
            parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        except ValueError:
            return 0
        now = self.now_provider()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        elapsed = (now.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()
        return max(0, int(interval_seconds - elapsed))

    def event_min_interval_seconds(self, event: object) -> int:
        trigger = str(event_payload(event).get("trigger") or "data-update").strip()
        if trigger in {"research-evidence-update", "investment-calendar-update"}:
            return self.urgent_min_interval_seconds()
        if event_review_level(event) in self.urgent_review_levels():
            return self.urgent_min_interval_seconds()
        return self.min_interval_seconds()

    def projection_min_interval_seconds(self, requests: Iterable[object]) -> int:
        """Return one safe cadence for a whole ABox projection.

        A TypeDB projection replaces the active portfolio ABox as a single
        generation, even when the trigger contains only one symbol. Per-symbol
        cooldowns alone therefore allow a backlog of unrelated symbols to
        repeatedly rebuild the same graph. The most urgent request determines
        the cadence while preserving the shorter urgent interval.
        """
        intervals = [self.event_min_interval_seconds(event) for event in requests or []]
        return min(intervals) if intervals else self.min_interval_seconds()

    def effective_projection_min_interval_seconds(
        self,
        requests: Iterable[object],
        payload: Dict[str, object] = None,
        selected_symbols: Iterable[str] = None,
    ) -> int:
        configured = self.projection_min_interval_seconds(requests)
        urgent = any(
            event_review_level(event) in self.urgent_review_levels()
            or str(event_payload(event).get("trigger") or "") in {"research-evidence-update", "investment-calendar-update"}
            for event in requests or []
        )
        if urgent or not self.projection_backpressure_enabled():
            return configured
        runtime = self.last_projection_runtime(payload)
        duration_ms = int(float_value(runtime.get("durationMs"), 0.0))
        if duration_ms <= 0:
            if self.fairness_drain_state(selected_symbols or [], payload).get("active"):
                return min(configured, self.fairness_drain_min_interval_seconds())
            return configured
        measured = int((duration_ms / 1000.0) * self.projection_backpressure_factor())
        protected_delay = min(self.projection_backpressure_max_seconds(), measured)
        if self.fairness_drain_state(selected_symbols or [], payload).get("active"):
            # Fairness must not turn a multi-minute projection into a tight
            # loop. It may shorten the ordinary cadence, but it still waits
            # for the observed runtime-derived recovery window.
            return max(
                self.fairness_drain_min_interval_seconds(),
                min(configured, protected_delay),
            )
        return max(configured, protected_delay)

    def projection_due(
        self,
        requests: Iterable[object],
        payload: Dict[str, object] = None,
        selected_symbols: Iterable[str] = None,
    ) -> bool:
        return self.timestamp_due(
            self.last_successful_projection_at(payload),
            self.effective_projection_min_interval_seconds(requests, payload, selected_symbols),
        )

    def projection_cooldown_remaining_seconds(
        self,
        requests: Iterable[object],
        payload: Dict[str, object] = None,
        selected_symbols: Iterable[str] = None,
    ) -> int:
        return self.timestamp_remaining_seconds(
            self.last_successful_projection_at(payload),
            self.effective_projection_min_interval_seconds(requests, payload, selected_symbols),
        )

    def event_symbol_due(self, event: object, symbol: str, cursor_payload: Dict[str, object] = None) -> bool:
        interval = self.event_min_interval_seconds(event)
        raw = self.last_reasoned_at_by_symbol(cursor_payload).get(str(symbol or "").upper().strip(), "")
        if not self.timestamp_due(raw, interval):
            return False
        attempted_at = self.last_projection_attempt_at_by_symbol(cursor_payload).get(
            str(symbol or "").upper().strip(),
            "",
        )
        return self.timestamp_due(attempted_at, self.projection_retry_seconds())

    def due_event_symbols(
        self,
        event: object,
        progress: Dict[str, List[str]] = None,
        cursor_payload: Dict[str, object] = None,
        priority_symbols: Dict[str, int] = None,
    ) -> List[str]:
        remaining = self.remaining_event_symbols(event, progress, priority_symbols)
        event_id = str(getattr(event, "event_id", "") or "")
        if event_id and event_id in (progress or {}):
            return remaining
        return [symbol for symbol in remaining if self.event_symbol_due(event, symbol, cursor_payload)]

    def remaining_event_symbols(
        self,
        event: object,
        progress: Dict[str, List[str]] = None,
        priority_symbols: Dict[str, int] = None,
    ) -> List[str]:
        symbols = self.ordered_event_symbols(event, priority_symbols)
        if not symbols:
            return []
        progress = self.event_symbol_progress() if progress is None else progress
        processed = set(progress.get(str(getattr(event, "event_id", "") or ""), []) or [])
        return [symbol for symbol in symbols if symbol not in processed]

    def pending_requests(self, limit: int = 0) -> List[object]:
        processed = set(self.cursor_store.processed_event_ids())
        cursor_payload = self.cursor_payload()
        progress = self.event_symbol_progress(cursor_payload)
        priority_symbols = self.priority_symbols()
        ingress_reader = getattr(self.event_reader, "unmaterialized_reasoning_events", None)
        reader = getattr(self.event_reader, "recent_events", None)
        if self.durable_mailbox_ingress_enabled() and callable(ingress_reader):
            source_events = ingress_reader(limit=self.event_scan_limit(limit))
        elif callable(reader):
            source_events = reader(
                name=ONTOLOGY_REASONING_REQUESTED,
                limit=self.event_scan_limit(limit),
            )
        else:
            source_events = self.event_reader.events(
                name=ONTOLOGY_REASONING_REQUESTED,
                limit=self.event_scan_limit(limit),
            )
        if self.durable_mailbox_ingress_enabled():
            # Pre-ingress rows that were already consumed by the legacy cursor
            # should be sealed once, otherwise they remain at the front of the
            # bounded repair query forever.
            terminalize = getattr(self.mailbox_store, "terminalize_direct_events", None)
            already_processed = [event for event in source_events if event.event_id in processed]
            if callable(terminalize) and already_processed:
                try:
                    terminalize(
                        already_processed,
                        state="completed",
                        reason="already processed before durable reasoning ingress",
                    )
                except Exception:
                    pass
        ranked_events = []
        for event in source_events:
            if event.event_id in processed or event_changed_count(event) <= 0:
                continue
            if event_symbols(event) and not self.due_event_symbols(event, progress, cursor_payload, priority_symbols):
                continue
            ranked_events.append((
                self.event_fairness_rank(event, progress, cursor_payload, priority_symbols),
                event,
            ))
        ranked_events.sort(
            key=lambda item: (
                *item[0],
                *event_order_key(item[1], priority_symbols),
                1 if str(getattr(item[1], "event_id", "") or "") in progress else 0,
                getattr(item[1], "occurred_at", ""),
                getattr(item[1], "event_id", ""),
            ),
            reverse=True,
        )
        return [item[1] for item in ranked_events[: max(1, int(limit or self.batch_size()))]]

    def durable_mailbox_ingress_enabled(self) -> bool:
        """Whether coalescible events enter MySQL mailbox at publish time."""
        value = self.settings.get("ontologyReasoningMailboxIngressEnabled")
        if not truthy(value, True):
            return False
        return callable(getattr(self.event_reader, "unmaterialized_reasoning_events", None))

    def mailbox_source_requests(self, limit: int = 0) -> List[object]:
        """Read unprocessed fungible events before cadence filtering.

        A symbol may be inside its projection cooldown while a newer quote is
        or generic evidence observation already available. The durable mailbox
        must still receive that newer observation so the eventual TypeDB cycle
        uses current state rather than the first update that happened to become
        due.
        """
        if not self.mailbox_enabled():
            return []
        if self.durable_mailbox_ingress_enabled():
            # The normal path is atomically ingressed with the source event.
            # ``pending_work`` passes any unmaterialized repair candidates to
            # ``synchronize_mailbox`` without reopening the event-log scan.
            return []
        processed = set(self.cursor_store.processed_event_ids())
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
        candidates = []
        for event in source_events or []:
            event_id = str(getattr(event, "event_id", "") or "").strip()
            if not event_id or event_id in processed or event_changed_count(event) <= 0:
                continue
            if self.mailbox_entries_for_event(event):
                candidates.append(event)
        return sorted(candidates, key=event_time_key)

    def event_as_dict(self, event: object) -> Dict[str, object]:
        if hasattr(event, "to_dict"):
            try:
                payload = event.to_dict()
                if isinstance(payload, dict):
                    return dict(payload)
            except Exception:  # noqa: BLE001 - scheduler metadata must not stop a live event.
                pass
        return {
            "name": str(getattr(event, "name", "") or ""),
            "aggregate_id": str(getattr(event, "aggregate_id", "") or ""),
            "payload": event_payload(event),
            "occurred_at": str(getattr(event, "occurred_at", "") or ""),
            "event_id": str(getattr(event, "event_id", "") or ""),
            "correlation_id": str(getattr(event, "correlation_id", "") or ""),
        }

    def mailbox_entries_for_event(self, event: object) -> List[Dict[str, object]]:
        """Expand a fungible observation into one latest-state mailbox slot."""
        coalescing_key = realtime_coalescing_key(event)
        if not coalescing_key:
            return []
        account_scope, trigger, fact_types = coalescing_key
        event_id = str(getattr(event, "event_id", "") or "").strip()
        if not event_id:
            return []
        source_event = self.event_as_dict(event)
        source_event["event_id"] = event_id
        source_event.setdefault("occurred_at", str(getattr(event, "occurred_at", "") or ""))
        family = ",".join(fact_types) or "MarketQuote"
        slot_family = mailbox_slot_family(event, fact_types)
        priority = (
            event_subject_priority(event, self.priority_symbols()) * 10000
            + REVIEW_LEVEL_ORDER.get(event_review_level(event), 0) * 1000
            + TRIGGER_ORDER.get(trigger, 0) * 100
            + (1 if "MarketQuote" in fact_types else 0)
        )
        entries = []
        for symbol in event_symbols(event):
            seed = "|".join([account_scope, symbol, slot_family])
            mailbox_key = hashlib.sha256(seed.encode("utf-8")).hexdigest()
            entries.append({
                "mailboxKey": mailbox_key,
                "sourceEventId": event_id,
                "sourceEvent": source_event,
                "accountScope": account_scope,
                "symbol": symbol,
                "factFamily": family,
                "mailboxSlotFamily": slot_family,
                "trigger": trigger,
                "reviewLevel": event_review_level(event),
                "priorityHint": priority,
                "occurredAt": str(getattr(event, "occurred_at", "") or ""),
                "factRevision": event_fact_revision(event, symbol),
            })
        return entries

    def mailbox_virtual_event(self, entry: Dict[str, object]) -> DomainEvent:
        source = DomainEvent.from_dict(dict(entry.get("sourceEvent") or {}))
        payload = dict(source.payload or {})
        source_event_id = str(entry.get("sourceEventId") or source.event_id or "").strip()
        mailbox_key = str(entry.get("mailboxKey") or "").strip()
        payload["symbols"] = [str(entry.get("symbol") or "").upper().strip()]
        payload["_reasoningMailbox"] = {
            "mailboxKey": mailbox_key,
            "sourceEventId": source_event_id,
            "accountScope": str(entry.get("accountScope") or "market"),
            "factFamily": str(entry.get("factFamily") or ""),
            "factRevision": str(entry.get("factRevision") or "")[:160],
            "enqueuedAt": str(entry.get("occurredAt") or source.occurred_at or ""),
        }
        return DomainEvent(
            name=source.name,
            aggregate_id=source.aggregate_id,
            payload=payload,
            occurred_at=str(entry.get("occurredAt") or source.occurred_at or ""),
            event_id="mailbox:" + mailbox_key,
            correlation_id=source.correlation_id or source_event_id,
        )

    @staticmethod
    def mailbox_metadata(event: object) -> Dict[str, object]:
        payload = event_payload(event)
        raw = payload.get("_reasoningMailbox")
        return dict(raw or {}) if isinstance(raw, dict) else {}

    def mailbox_source_event_id(self, event: object) -> str:
        metadata = self.mailbox_metadata(event)
        return str(metadata.get("sourceEventId") or getattr(event, "event_id", "") or "").strip()

    def synchronize_mailbox(
        self,
        source_requests: Iterable[object],
        enqueue_new: bool = True,
    ) -> Dict[str, object]:
        """Persist newest fungible slots before a TypeDB cycle can be deferred."""
        summary = {
            "enabled": self.mailbox_enabled(),
            "acceptedEventIds": [],
            "knownEventIds": [],
            "handledEventIds": [],
            "candidateEventIds": [],
            "terminalEventStates": {},
            "sameRevisionEntryKeys": [],
            "entryCount": 0,
        }
        if not self.mailbox_enabled():
            return summary
        requests = list(source_requests or [])
        entries_by_event = {}
        for event in requests:
            event_id = str(getattr(event, "event_id", "") or "").strip()
            entries = self.mailbox_entries_for_event(event)
            if event_id and entries:
                entries_by_event[event_id] = entries
        event_ids = list(entries_by_event)
        summary["candidateEventIds"] = list(event_ids)
        if not event_ids:
            return summary
        try:
            known = set(self.mailbox_store.known_event_ids(event_ids))
            terminal = dict(self.mailbox_store.terminal_event_states(event_ids) or {})
            entries = [
                entry
                for event_id, event_entries in entries_by_event.items()
                if event_id not in known
                for entry in event_entries
            ]
            result = self.mailbox_store.enqueue(entries) if enqueue_new and entries else {}
            accepted = list(result.get("enqueuedEventIds") or [])
            handled = set(known) | set(accepted)
            summary.update({
                "acceptedEventIds": accepted,
                "knownEventIds": sorted(known | set(result.get("knownEventIds") or [])),
                "handledEventIds": sorted(handled),
                "terminalEventStates": {**terminal, **dict(result.get("terminalEventStates") or {})},
                "sameRevisionEntryKeys": list(result.get("sameRevisionEntryKeys") or []),
                "entryCount": len(entries),
            })
        except Exception as error:  # noqa: BLE001 - event-log scheduling remains a safe fallback.
            summary.update({
                "enabled": False,
                "reason": "영속 추론 메일박스 동기화 실패: " + str(error)[:180],
            })
        return summary

    def mailbox_pending_requests(self) -> List[object]:
        if not self.mailbox_enabled():
            return []
        try:
            entries = self.mailbox_store.pending(self.mailbox_batch_size())
        except Exception:  # noqa: BLE001 - source-event fallback remains available.
            return []
        return [self.mailbox_virtual_event(entry) for entry in entries if isinstance(entry, dict)]

    @staticmethod
    def is_research_generation_handoff_request(event: object) -> bool:
        """Return whether a direct request needs a generation acknowledgement.

        These events are deliberately not fungible mailbox slots.  A research
        run must be connected to a later, aligned InferenceBox in its own
        account world before its evidence becomes eligible for use.  They can
        nevertheless be completed by a shared full-world projection when the
        same strict generation contract is met.
        """
        payload = event_payload(event)
        handoff = payload.get("reasoningHandoff")
        return bool(
            str(payload.get("researchRunId") or "").strip()
            and isinstance(handoff, dict)
            and str(handoff.get("requestId") or "").strip()
        )

    def pending_research_handoff_requests(
        self,
        source_requests: Iterable[object] = None,
        limit: int = 0,
    ) -> List[object]:
        """Read direct research handoffs independently of symbol cooldowns.

        ``pending_requests`` correctly applies cadence limits to scheduling.
        It may therefore omit an older direct handoff after a newer generic
        request for the same symbol was projected.  The current account-world
        projection can satisfy that handoff without another TypeDB cycle, so
        keep a bounded reconciliation view of direct ingress rows here.
        """
        candidates = list(source_requests or [])
        ingress_reader = getattr(self.event_reader, "unmaterialized_reasoning_events", None)
        if self.durable_mailbox_ingress_enabled() and callable(ingress_reader):
            try:
                candidates.extend(ingress_reader(limit=self.event_scan_limit(limit)) or [])
            except Exception:
                # The normal scheduled source list remains a safe fallback.
                pass
        processed = set()
        if hasattr(self.cursor_store, "processed_event_ids"):
            try:
                processed = {
                    str(event_id or "").strip()
                    for event_id in self.cursor_store.processed_event_ids() or []
                    if str(event_id or "").strip()
                }
            except Exception:
                processed = set()
        result = []
        seen = set()
        for event in candidates:
            event_id = str(getattr(event, "event_id", "") or "").strip()
            if (
                not event_id
                or event_id in seen
                or event_id in processed
                or not self.is_research_generation_handoff_request(event)
            ):
                continue
            seen.add(event_id)
            result.append(event)
        return result

    def recent_terminal_mailbox_events(self) -> Dict[str, str]:
        """Recover source cursors after a process stops between ack and save."""
        if not self.mailbox_enabled():
            return {}
        try:
            return dict(self.mailbox_store.terminal_event_states() or {})
        except Exception:  # noqa: BLE001 - normal source scans remain a safe recovery path.
            return {}

    def coalesced_work(self, requests: Iterable[object]) -> Dict[str, object]:
        requests = list(requests or [])
        superseded_by_lead: Dict[str, List[str]] = {}
        superseded_ids = set()
        for older in requests:
            older_id = str(getattr(older, "event_id", "") or "").strip()
            older_key = realtime_coalescing_key(older)
            older_symbols = set(event_symbols(older))
            if not older_id or not older_key or not older_symbols:
                continue
            leads = [
                newer
                for newer in requests
                if str(getattr(newer, "event_id", "") or "").strip() != older_id
                and realtime_coalescing_key(newer) == older_key
                and event_time_key(newer) > event_time_key(older)
                and set(event_symbols(newer)).issuperset(older_symbols)
            ]
            if not leads:
                continue
            lead = max(leads, key=event_time_key)
            lead_id = str(getattr(lead, "event_id", "") or "").strip()
            if not lead_id:
                continue
            superseded_ids.add(older_id)
            superseded_by_lead.setdefault(lead_id, []).append(older_id)
        active_requests = [
            event for event in requests
            if str(getattr(event, "event_id", "") or "").strip() not in superseded_ids
        ]
        return {
            "requests": active_requests,
            "coalescedEventIds": sorted(superseded_ids),
            "supersededByLead": {
                lead_id: sorted(set(event_ids))
                for lead_id, event_ids in superseded_by_lead.items()
            },
        }

    def event_source_observed_at(self, event: object) -> str:
        payload = event_payload(event)
        for key in ["sourceObservedAt", "sourceAsOf", "observedAt", "generatedAt", "collectedAt"]:
            value = str(payload.get(key) or "").strip()
            if value:
                return value
        return str(getattr(event, "occurred_at", "") or "").strip()

    def generic_research_mailbox_subject(self, event: object) -> Tuple[str, str]:
        """Return the current-state subject for one fungible research slot.

        Mailbox rows are already expanded to one symbol. The explicit
        ``_reasoningMailbox`` metadata prevents a direct/non-fungible research
        request from being discarded merely because it mentions the same
        symbol.
        """
        metadata = self.mailbox_metadata(event)
        if not metadata or not is_generic_research_latest_state(event):
            return ()
        symbols = event_symbols(event)
        symbol = str(symbols[0] if symbols else "").upper().strip()
        if not symbol:
            return ()
        account_scope = str(metadata.get("accountScope") or "market").strip() or "market"
        return account_scope, symbol

    def generic_research_mailbox_order_key(self, event: object) -> Tuple[str, str, str]:
        """Choose the newest evidence observation, not the noisiest trigger."""
        return (
            self.event_source_observed_at(event),
            str(getattr(event, "occurred_at", "") or ""),
            self.mailbox_source_event_id(event),
        )

    @staticmethod
    def timestamp_at_or_after(candidate: object, reference: object) -> bool:
        """Compare operational timestamps without trusting malformed values."""
        candidate_text = str(candidate or "").strip()
        reference_text = str(reference or "").strip()
        if not candidate_text or not reference_text:
            return False
        try:
            candidate_at = datetime.fromisoformat(candidate_text.replace("Z", "+00:00"))
            reference_at = datetime.fromisoformat(reference_text.replace("Z", "+00:00"))
        except ValueError:
            return False
        if candidate_at.tzinfo is None:
            candidate_at = candidate_at.replace(tzinfo=timezone.utc)
        if reference_at.tzinfo is None:
            reference_at = reference_at.replace(tzinfo=timezone.utc)
        return candidate_at.astimezone(timezone.utc) >= reference_at.astimezone(timezone.utc)

    def compact_generic_research_mailbox_requests(
        self,
        requests: Iterable[object],
        persist: bool = False,
    ) -> Dict[str, object]:
        """Keep only the newest generic ResearchEvidence request per subject.

        The active ABox is rebuilt from the full current research repository.
        A later generic research observation for the same account/symbol
        therefore supersedes an older article-analysis or lifecycle trigger,
        even when their fact families differ. This is queue semantics only;
        it never removes the underlying ResearchEvidence facts or a
        non-fungible hypothesis-research handoff.
        """
        original = list(requests or [])
        groups: Dict[Tuple[str, str], List[object]] = {}
        for event in original:
            subject = self.generic_research_mailbox_subject(event)
            if subject:
                groups.setdefault(subject, []).append(event)

        discarded_entries: List[Dict[str, str]] = []
        discarded_keys = set()
        retained_by_subject: Dict[str, str] = {}
        already_reasoned_subjects = []
        last_reasoned_at = self.last_reasoned_at_by_symbol()

        def discard(candidate: object) -> None:
            metadata = self.mailbox_metadata(candidate)
            mailbox_key = str(metadata.get("mailboxKey") or "").strip()
            source_event_id = str(metadata.get("sourceEventId") or "").strip()
            if not mailbox_key or mailbox_key in discarded_keys:
                return
            discarded_keys.add(mailbox_key)
            discarded_entries.append({
                "mailboxKey": mailbox_key,
                "sourceEventId": source_event_id,
            })

        for subject, candidates in groups.items():
            lead = max(candidates, key=self.generic_research_mailbox_order_key)
            source_observed_at = self.event_source_observed_at(lead)
            if self.timestamp_at_or_after(last_reasoned_at.get(subject[1]), source_observed_at):
                # Every TypeDB projection rebuilds the current ABox, so a
                # verified later generation already includes this evidence.
                # This specifically clears orphaned retries from an earlier
                # scheduler process without replaying stale research.
                for candidate in candidates:
                    discard(candidate)
                already_reasoned_subjects.append("|".join(subject))
                continue
            if len(candidates) < 2:
                continue
            lead_metadata = self.mailbox_metadata(lead)
            lead_key = str(lead_metadata.get("mailboxKey") or "").strip()
            retained_by_subject["|".join(subject)] = self.mailbox_source_event_id(lead)
            for candidate in candidates:
                if candidate is lead:
                    continue
                mailbox_key = str(self.mailbox_metadata(candidate).get("mailboxKey") or "").strip()
                if not mailbox_key or mailbox_key == lead_key:
                    continue
                discard(candidate)

        active = [
            event for event in original
            if str(self.mailbox_metadata(event).get("mailboxKey") or "").strip() not in discarded_keys
        ]
        result = {
            "status": "not-needed",
            "requests": active,
            "storedEntryCount": len(original),
            "actionableEntryCount": len(active),
            "discardedEntryCount": len(discarded_entries),
            "discardedEntries": discarded_entries,
            "terminalEventStates": {},
            "retainedSourceEventIdsBySubject": retained_by_subject,
            "alreadyReasonedSubjects": already_reasoned_subjects,
        }
        if not discarded_entries:
            return result
        if not persist:
            result["status"] = "preview"
            return result
        acknowledge = getattr(self.mailbox_store, "acknowledge", None) if self.mailbox_enabled() else None
        if not callable(acknowledge):
            # A store that cannot atomically acknowledge old revisions must
            # retain them; hiding them here would create a permanent queue
            # leak in compatibility environments.
            result.update({
                "status": "unsupported",
                "requests": original,
                "actionableEntryCount": len(original),
                "discardedEntryCount": 0,
                "discardedEntries": [],
            })
            return result
        try:
            terminal = acknowledge(
                discarded_entries,
                state="superseded",
                reason="newer generic ResearchEvidence state or a later TypeDB generation owns this account/symbol mailbox slot",
            ) or {}
        except Exception as error:  # noqa: BLE001 - retain work when durable acknowledgement is uncertain.
            result.update({
                "status": "error",
                "reason": str(error)[:180],
                "requests": original,
                "actionableEntryCount": len(original),
                "discardedEntryCount": 0,
                "discardedEntries": [],
            })
            return result
        result.update({
            "status": "compacted",
            "terminalEventStates": dict(terminal),
        })
        return result

    def is_off_session_reference_transition(self, event: object) -> bool:
        """Discard an old close-transition request before it occupies TypeDB.

        MarketSession is re-evaluated from the current clock inside the ABox,
        so an event containing only the expected switch to last-close data
        does not need one native inference run per holding. The source event
        remains in the audit log and receives an expired mailbox state.
        """
        payload = event_payload(event)
        if str(payload.get("trigger") or "").strip() not in COALESCIBLE_REALTIME_TRIGGERS:
            return False
        symbols = event_symbols(event)
        fields_by_symbol = payload.get("changedFieldsBySymbol")
        if not symbols or not isinstance(fields_by_symbol, dict):
            return False
        for symbol in symbols:
            fields = fields_by_symbol.get(symbol)
            if fields is None:
                fields = fields_by_symbol.get(symbol.upper())
            field_set = {
                str(field or "").strip()
                for field in fields or []
                if str(field or "").strip()
            }
            assessment = materiality_assessment_for_symbol(event, symbol)
            matched = {
                str(item or "").strip()
                for item in assessment.get("matchedConditions") or []
                if str(item or "").strip()
            }
            allowed_matches = {"source-validity-state-change", "data-state-change"}
            if (
                not field_set
                or not field_set.issubset(OFF_SESSION_REFERENCE_FIELDS)
                or "source-validity-state-change" not in matched
                or not matched.issubset(allowed_matches)
                or str(assessment.get("dataState") or "").strip() != "partial"
            ):
                return False
            instrument = known_stock(symbol)
            session = evaluate_market_hours(
                INVESTMENT_INSIGHT,
                {
                    "symbol": symbol,
                    "market": instrument.get("market"),
                    "currency": instrument.get("currency"),
                },
                True,
                ["KR", "US"],
            )
            if str(session.status or "") not in {"closed", "closed_exception"}:
                return False
        return True

    def event_freshness(self, event: object) -> Dict[str, object]:
        if self.is_off_session_reference_transition(event):
            return {
                "status": "off-session-reference",
                "reason": "장 마감 기준 시세 전환만 포함되어 시장 세션 게이트에 맡기고 TypeDB 재추론은 생략합니다.",
                "shouldProcess": False,
            }
        if not self.source_freshness_enabled():
            return {"status": "bypass", "reason": "추론 입력 신선도 정책이 꺼져 있습니다.", "shouldProcess": True}
        trigger = str(event_payload(event).get("trigger") or "").strip()
        if trigger in COALESCIBLE_REALTIME_TRIGGERS:
            maximum = self.realtime_event_max_age_minutes()
        elif trigger in COALESCIBLE_RESEARCH_TRIGGERS or "research" in trigger or trigger == "investment-calendar-update":
            maximum = self.research_event_max_age_minutes()
        else:
            return {"status": "not-required", "reason": "이 요청은 원본 시각 만료 대상이 아닙니다.", "shouldProcess": True}
        stamp = self.event_source_observed_at(event)
        try:
            observed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            return {"status": "unknown", "reason": "원본 관측 시각을 해석하지 못해 최신 스냅샷으로 재검증합니다.", "shouldProcess": True}
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        now = self.now_provider()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        age_minutes = max(0, int((now.astimezone(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds() // 60))
        fresh = age_minutes <= maximum
        return {
            "status": "fresh" if fresh else "stale",
            "reason": "원본 관측 시각 기준 통과" if fresh else "원본 관측 시각이 " + str(maximum) + "분을 넘었습니다.",
            "shouldProcess": fresh,
            "ageMinutes": age_minutes,
            "maxAgeMinutes": maximum,
            "sourceObservedAt": stamp,
        }

    def split_stale_requests(self, requests: Iterable[object]) -> Tuple[List[object], List[Tuple[object, Dict[str, object]]]]:
        active, stale = [], []
        for event in requests or []:
            freshness = self.event_freshness(event)
            if freshness.get("shouldProcess"):
                active.append(event)
            else:
                stale.append((event, freshness))
        return active, stale

    def discard_stale_requests(self, stale_requests: Iterable[Tuple[object, Dict[str, object]]]) -> Dict[str, str]:
        terminal: Dict[str, str] = {}
        mailbox_entries = []
        direct_ids = []
        discard_reasons = []
        for event, freshness in stale_requests or []:
            if str((freshness or {}).get("status") or "") == "off-session-reference":
                discard_reasons.append("off-session last-close reference transition")
            else:
                discard_reasons.append("stale source observation before TypeDB projection")
            metadata = self.mailbox_metadata(event)
            key = str(metadata.get("mailboxKey") or "").strip()
            if key:
                mailbox_entries.append({
                    "mailboxKey": key,
                    "sourceEventId": str(metadata.get("sourceEventId") or "").strip(),
                })
            else:
                event_id = str(getattr(event, "event_id", "") or "").strip()
                if event_id:
                    direct_ids.append(event_id)
        if mailbox_entries and self.mailbox_enabled():
            try:
                terminal.update(self.mailbox_store.acknowledge(
                    mailbox_entries,
                    state="expired",
                    reason=(discard_reasons[0] if len(set(discard_reasons)) == 1 else "stale or non-actionable source observation before TypeDB projection"),
                ) or {})
            except Exception:
                pass
        if direct_ids:
            terminal.update({event_id: "expired" for event_id in direct_ids})
        return terminal

    def persist_terminal_mailbox_events(self, states: Dict[str, object]) -> Dict[str, List[str]]:
        values = {str(event_id or "").strip(): str(state or "").strip() for event_id, state in dict(states or {}).items()}
        existing = set()
        processed_ids = getattr(self.cursor_store, "processed_event_ids", None)
        if callable(processed_ids):
            try:
                existing = {str(event_id or "").strip() for event_id in processed_ids() or []}
            except Exception:  # noqa: BLE001 - terminal replay is idempotent when cursor inspection fails.
                existing = set()
        completed = [
            event_id for event_id, state in values.items()
            if event_id and event_id not in existing and state == "completed"
        ]
        discarded = [
            event_id for event_id, state in values.items()
            if event_id and event_id not in existing and state in {"superseded", "expired"}
        ]
        if completed and hasattr(self.cursor_store, "mark_processed"):
            self.cursor_store.mark_processed(completed)
        if discarded:
            self.persist_superseded_events(discarded)
        # Direct/non-fungible requests do not own a mailbox slot, but they do
        # own an ingress marker. Seal it together with cursor progress so the
        # bounded repair reader cannot replay it after a restart.
        terminalize = getattr(self.mailbox_store, "terminalize_direct_events", None) if self.mailbox_enabled() else None
        if callable(terminalize):
            for state in {"completed", "superseded", "expired"}:
                event_ids = [event_id for event_id, candidate in values.items() if candidate == state]
                if not event_ids:
                    continue
                try:
                    terminalize(event_ids, state=state, reason="reasoning cursor terminal state")
                except Exception:
                    continue
        return {"completed": completed, "discarded": discarded}

    def pending_work(self, limit: int = 0, hydrate_mailbox: bool = True) -> Dict[str, object]:
        """Collapse redundant, lower-materiality realtime snapshot requests.

        A newer market observation may supersede an older one only when it
        includes all of the older event's symbols and exactly the same fact
        family. The older cursor is advanced only after the newer snapshot has
        completed TypeDB projection and inference.
        """
        source_requests = self.pending_requests(limit)
        if hydrate_mailbox and self.durable_mailbox_ingress_enabled():
            mailbox_sources = [event for event in source_requests if self.mailbox_entries_for_event(event)]
        else:
            mailbox_sources = self.mailbox_source_requests(limit) if hydrate_mailbox else source_requests
        mailbox = self.synchronize_mailbox(mailbox_sources, enqueue_new=hydrate_mailbox)
        handled_ids = set(mailbox.get("handledEventIds") or []) if mailbox.get("enabled") else set()
        direct_source_requests = [
            event for event in source_requests
            if str(getattr(event, "event_id", "") or "").strip() not in handled_ids
        ]
        research_handoff_requests = self.pending_research_handoff_requests(
            direct_source_requests,
            limit=limit,
        )
        direct_work = self.coalesced_work(direct_source_requests)
        mailbox_requests = self.mailbox_pending_requests() if mailbox.get("enabled") else []
        mailbox_compaction = self.compact_generic_research_mailbox_requests(
            mailbox_requests,
            persist=bool(hydrate_mailbox and mailbox.get("enabled")),
        )
        mailbox_requests = list(mailbox_compaction.get("requests") or [])
        active_requests = list(mailbox_requests) + list(direct_work.get("requests") or [])
        return {
            "requests": active_requests,
            "rawRequestCount": len(source_requests),
            "sourceRequestCount": len(source_requests),
            "mailboxPendingEntryCount": len(mailbox_requests),
            "mailboxStoredEntryCount": int(mailbox_compaction.get("storedEntryCount") or 0),
            "semanticSupersededMailboxEntryCount": int(mailbox_compaction.get("discardedEntryCount") or 0),
            "mailboxSemanticCompaction": {
                key: value
                for key, value in mailbox_compaction.items()
                if key not in {"requests", "discardedEntries"}
            },
            "mailbox": mailbox,
            "researchHandoffRequests": research_handoff_requests,
            "pendingResearchHandoffCount": len(research_handoff_requests),
            "terminalMailboxEventStates": {
                **dict(mailbox.get("terminalEventStates") or {}),
                **dict(mailbox_compaction.get("terminalEventStates") or {}),
            },
            "coalescedEventIds": list(direct_work.get("coalescedEventIds") or []),
            "supersededByLead": dict(direct_work.get("supersededByLead") or {}),
        }

    def persist_superseded_events(self, event_ids: Iterable[str]) -> List[str]:
        """Durably retire older realtime triggers once a newer snapshot exists.

        Source snapshots are committed before an ontology request is
        published. Retaining every prior trigger adds no recovery value and
        lets a high-frequency stream keep the newest state waiting behind
        obsolete work. Research and urgent requests never enter this path.
        """
        clean_ids = []
        for event_id in event_ids or []:
            clean = str(event_id or "").strip()
            if clean and clean not in clean_ids:
                clean_ids.append(clean)
        if not clean_ids or not self.cursor_store:
            return []
        marker = getattr(self.cursor_store, "mark_superseded", None)
        if callable(marker):
            marker(clean_ids)
            return clean_ids
        payload = self.cursor_payload()
        progress = self.event_symbol_progress(payload)
        for event_id in clean_ids:
            progress.pop(event_id, None)
        if hasattr(self.cursor_store, "save"):
            payload["eventSymbolProgress"] = progress
            self.save_cursor_payload(payload)
        if hasattr(self.cursor_store, "mark_processed"):
            self.cursor_store.mark_processed(clean_ids)
        return clean_ids

    def storage_guard_state(self) -> Dict[str, object]:
        if not callable(self.storage_guard):
            return {"ready": True, "status": "not-configured"}
        try:
            result = self.storage_guard()
        except Exception as error:  # noqa: BLE001 - fail closed when capacity cannot be checked.
            return {
                "ready": False,
                "status": "error",
                "reason": "TypeDB 저장소 여유 공간 확인 실패: " + str(error)[:180],
            }
        state = dict(result or {}) if isinstance(result, dict) else {}
        state["ready"] = bool(state.get("ready"))
        state.setdefault("status", "ready" if state["ready"] else "blocked")
        return state

    def projection_coordinator_state(self) -> Dict[str, object]:
        """Read physical TypeDB writer ownership for operations diagnostics."""
        if not callable(self.projection_coordinator_probe):
            return {"status": "not-configured"}
        try:
            result = self.projection_coordinator_probe()
        except Exception as error:  # noqa: BLE001 - diagnostics must not delay reasoning.
            return {"status": "error", "reason": str(error)[:180]}
        value = dict(result or {}) if isinstance(result, dict) else {"status": "invalid"}
        allowed = {
            "status", "coordinator", "coordinatorVersion", "requestedWorldId",
            "worldId", "leaseOwner", "leaseRemainingSeconds", "updatedAt", "reason",
        }
        return {
            key: item
            for key, item in value.items()
            if key in allowed and item not in (None, "", [], {})
        }

    def publish(self, event) -> None:
        if not self.event_publisher:
            return
        if hasattr(self.event_publisher, "publish"):
            self.event_publisher.publish(event)
        else:
            self.event_publisher.handle(event)

    def request_symbols(self, requests: Iterable[object]) -> List[str]:
        symbols = []
        priorities = self.priority_symbols()
        for event in requests or []:
            for symbol in self.ordered_event_symbols(event, priorities):
                if symbol and symbol not in symbols:
                    symbols.append(symbol)
        return symbols

    def oldest_request_wait_seconds(self, requests: Iterable[object]) -> int:
        """Return the oldest durable request age for operational batching only."""
        now = self.now_provider()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        ages = []
        for event in requests or []:
            raw = str(getattr(event, "occurred_at", "") or "").strip()
            if not raw:
                continue
            try:
                observed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
            ages.append(max(0, int((now.astimezone(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds())))
        return max(ages or [0])

    def batch_runtime_evidence(self) -> Dict[str, object]:
        """Return the latest real projection turn for adaptive batch sizing.

        A quick cooldown probe often follows an expensive TypeDB generation.
        It must not hide that generation from the next batch decision, or a
        queue-pressure burst can repeat the same timeout. This remains
        scheduler telemetry only; no investment fact or RuleBox condition is
        derived from it.
        """
        telemetry = self.execution_telemetry()
        samples = []
        for source, values in (
            ("history", telemetry.get("history") or []),
            ("last", [telemetry.get("last") or {}]),
        ):
            for value in values:
                if not isinstance(value, dict):
                    continue
                candidate = dict(value)
                candidate["batchRuntimeEvidenceSource"] = source
                samples.append(candidate)
        for candidate in reversed(samples):
            status = str(candidate.get("status") or "").strip().lower()
            if status in {"timeout", "error", "partial", "circuit-open"}:
                return candidate
            if status in {"cooldown", "idle", "disabled", "deferred"}:
                continue
            stage = candidate.get("stageTiming")
            stage = stage if isinstance(stage, dict) else {}
            projection = candidate.get("projectionRuntime")
            projection = projection if isinstance(projection, dict) else {}
            if float_value(stage.get("monitorAndProjectionMs"), 0.0) > 0:
                return candidate
            if float_value(projection.get("durationMs"), 0.0) > 0:
                return candidate
        return {}

    def reasoning_batch_plan(self, requests: Iterable[object]) -> Dict[str, object]:
        """Choose a bounded multi-subject TypeDB turn from queue pressure.

        This is an operational throughput decision. TypeDB continues to own
        all investment-rule evaluation for every selected subject.
        """
        pending = list(requests or [])
        hard_limit = self.effective_max_symbols_per_run()
        if not self.native_typedb_rule_execution_enabled() and hard_limit <= 0:
            return {
                "version": "adaptive-reasoning-batch-v1",
                "enabled": False,
                "mode": "static-unbounded",
                "targetSymbolLimit": 0,
                "hardTargetSymbolLimit": 0,
                "steadyTargetSymbolLimit": 0,
                "burstTargetSymbolLimit": 0,
                "pendingRequestCount": len(pending),
                "pendingSymbolCount": len(self.request_symbols(pending)),
                "oldestWaitSeconds": self.oldest_request_wait_seconds(pending),
                "pressure": False,
                "runtimeGuard": False,
                "reasonCodes": ["nonnative-unbounded-symbol-limit"],
            }
        telemetry = self.batch_runtime_evidence()
        return adaptive_reasoning_batch_plan(
            self.settings,
            native_rule_execution=self.native_typedb_rule_execution_enabled(),
            hard_target_symbol_limit=hard_limit,
            pending_request_count=len(pending),
            pending_symbol_count=len(self.request_symbols(pending)),
            oldest_wait_seconds=self.oldest_request_wait_seconds(pending),
            recent_execution=telemetry,
        )

    def request_symbol_batches(
        self,
        requests: Iterable[object],
        max_symbols_override: int = None,
    ) -> Tuple[Dict[str, List[str]], List[str], int]:
        # Snapshot construction still preserves all portfolio and market
        # entities. Native schema-function subjects remain bounded by the
        # configured realtime cap; one coherent ABox/InferenceBox generation
        # can safely include the highest-priority subjects from more than one
        # source event.
        hard_limit = self.effective_max_symbols_per_run()
        if max_symbols_override is None:
            max_symbols = hard_limit
        else:
            requested_limit = int(max_symbols_override or 0)
            if hard_limit <= 0:
                max_symbols = 0
            else:
                max_symbols = max(1, min(hard_limit, requested_limit or hard_limit))
        cursor_payload = self.cursor_payload()
        progress = self.event_symbol_progress(cursor_payload)
        priority_symbols = self.priority_symbols()
        batches: Dict[str, List[str]] = {}
        candidates: Dict[str, Tuple[tuple, str]] = {}
        requested_events = list(requests or [])

        if self.coherent_snapshot_enabled():
            event_candidates = []
            all_due_symbols = []
            global_events = []
            for event_index, event in enumerate(requested_events):
                event_id = str(getattr(event, "event_id", "") or "").strip()
                all_symbols = self.ordered_event_symbols(event, priority_symbols)
                if not all_symbols:
                    if event_id:
                        global_events.append((event_index, event))
                    continue
                due_symbols = self.order_symbols_by_fairness(
                    self.due_event_symbols(event, progress, cursor_payload, priority_symbols),
                    priority_symbols,
                    cursor_payload,
                )
                if not due_symbols:
                    continue
                for symbol in due_symbols:
                    if symbol not in all_due_symbols:
                        all_due_symbols.append(symbol)
                fact_types = {str(item or "").strip() for item in event_payload(event).get("factTypes") or []}
                fairness_rank = max([self.symbol_fairness_rank(symbol, cursor_payload) for symbol in due_symbols] or [(0, 0)])
                rank = (
                    *fairness_rank,
                    max([int(priority_symbols.get(symbol, 0) or 0) for symbol in due_symbols] or [0]),
                    REVIEW_LEVEL_ORDER.get(event_review_level(event), 0),
                    TRIGGER_ORDER.get(str(event_payload(event).get("trigger") or "data-update").strip(), 0),
                    1 if "ResearchEvidence" in fact_types else 0,
                    event_time_key(event),
                    -event_index,
                )
                event_candidates.append((rank, event_id, due_symbols))
            if event_candidates:
                snapshot_limit = self.coherent_snapshot_max_symbols()
                # Coherence refers to the full ABox retained for context, not
                # an unbounded schema-function request. Native TypeDB rules
                # still run only for the configured incremental subjects so a
                # broad market event cannot monopolize the realtime worker.
                if max_symbols > 0:
                    snapshot_limit = min(snapshot_limit, max_symbols)
                selected = []
                selected_set = set()
                # Preserve event-level review/trigger priority, then retain
                # the fairness ordering already applied inside each event.
                # The former single-event selection made a target cap of two
                # ineffective whenever each source event carried one symbol.
                for _rank, event_id, due_symbols in sorted(
                    event_candidates,
                    key=lambda item: item[0],
                    reverse=True,
                ):
                    selected_for_event = []
                    for symbol in due_symbols:
                        if symbol in selected_set:
                            continue
                        if snapshot_limit > 0 and len(selected) >= snapshot_limit:
                            break
                        selected.append(symbol)
                        selected_set.add(symbol)
                        selected_for_event.append(symbol)
                    if selected_for_event:
                        batches[event_id] = selected_for_event
                    if snapshot_limit > 0 and len(selected) >= snapshot_limit:
                        break
                return batches, selected, max(0, len(all_due_symbols) - len(selected))
            if global_events:
                # A subject-less operational/macro update still needs one full
                # portfolio projection. Do not mark other global events until
                # their own turn.
                _index, event = min(global_events, key=lambda item: item[0])
                event_id = str(getattr(event, "event_id", "") or "").strip()
                return ({event_id: []} if event_id else {}), [], 0

        # Pick symbols globally before assigning them to their source events.
        # The previous event-by-event loop could consume the complete per-run
        # allowance with residual background symbols from an older event, even
        # when a newer holding event was already waiting in the same queue.
        for event_index, event in enumerate(requested_events):
            event_id = str(getattr(event, "event_id", "") or "")
            remaining = self.order_symbols_by_fairness(
                self.due_event_symbols(event, progress, cursor_payload, priority_symbols),
                priority_symbols,
                cursor_payload,
            )
            if not self.ordered_event_symbols(event, priority_symbols):
                batches[event_id] = []
                continue
            for symbol_index, symbol in enumerate(remaining):
                rank = (
                    *self.symbol_fairness_rank(symbol, cursor_payload),
                    int(priority_symbols.get(symbol, 0) or 0),
                    REVIEW_LEVEL_ORDER.get(event_review_level(event), 0),
                    TRIGGER_ORDER.get(str(event_payload(event).get("trigger") or "data-update").strip(), 0),
                    1 if "ResearchEvidence" in {str(item or "").strip() for item in event_payload(event).get("factTypes") or []} else 0,
                    -event_index,
                    -symbol_index,
                )
                existing = candidates.get(symbol)
                if existing is None or rank > existing[0]:
                    candidates[symbol] = (rank, event_id)

        ranked_symbols = [
            symbol
            for symbol, _candidate in sorted(
                candidates.items(),
                key=lambda item: (item[1][0], item[0]),
                reverse=True,
            )
        ]
        selected = ranked_symbols if not max_symbols else ranked_symbols[:max_symbols]
        selected_set = set(selected)
        omitted_symbols = [symbol for symbol in ranked_symbols if symbol not in selected_set]

        for event in requested_events:
            event_id = str(getattr(event, "event_id", "") or "")
            if not self.ordered_event_symbols(event, priority_symbols):
                continue
            selected_for_event = [
                symbol
                for symbol in self.order_symbols_by_fairness(
                    self.due_event_symbols(event, progress, cursor_payload, priority_symbols),
                    priority_symbols,
                    cursor_payload,
                )
                if symbol in selected_set
            ]
            if selected_for_event:
                batches[event_id] = selected_for_event
        return batches, selected, len(omitted_symbols)

    def mark_requests_processed(
        self,
        requests: Iterable[object],
        batches: Dict[str, List[str]],
        superseded_by_lead: Dict[str, List[str]] = None,
    ) -> Dict[str, object]:
        cursor_payload = self.cursor_payload()
        progress = self.event_symbol_progress(cursor_payload)
        priority_symbols = self.priority_symbols()
        completed_event_ids: List[str] = []
        partial_event_ids: List[str] = []
        for event in requests or []:
            event_id = str(getattr(event, "event_id", "") or "").strip()
            if not event_id:
                continue
            all_symbols = self.ordered_event_symbols(event, priority_symbols)
            selected_symbols = [symbol for symbol in batches.get(event_id, []) if symbol]
            if not all_symbols:
                if event_id in batches:
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
        superseded_event_ids: List[str] = []
        for event_id in completed_event_ids:
            for superseded_id in (superseded_by_lead or {}).get(event_id, []) or []:
                clean = str(superseded_id or "").strip()
                if clean and clean not in superseded_event_ids:
                    superseded_event_ids.append(clean)
        for event_id in superseded_event_ids:
            progress.pop(event_id, None)
        if hasattr(self.cursor_store, "save"):
            cursor_payload["eventSymbolProgress"] = progress
            self.save_cursor_payload(cursor_payload)
        processed_event_ids = []
        for event_id in completed_event_ids + superseded_event_ids:
            if event_id not in processed_event_ids:
                processed_event_ids.append(event_id)
        if processed_event_ids:
            self.cursor_store.mark_processed(processed_event_ids)
        return {
            "completedEventIds": completed_event_ids,
            "partialEventIds": partial_event_ids,
            "supersededEventIds": superseded_event_ids,
        }

    def projection_gate(self, monitor_runner) -> Dict[str, object]:
        """Keep source events pending until TypeDB finished their graph cycle.

        Monitor runners built by older tests or compatibility callers do not
        expose projection outcomes. Preserve their existing behavior while
        requiring every current runtime runner to prove that TypeDB accepted
        the ABox and returned from its native-rule execution.
        """
        raw_results = getattr(monitor_runner, "last_ontology_projection_results", None)
        if raw_results is None:
            return {"ready": True, "results": [], "compatibility": True}
        if not isinstance(raw_results, dict) or not raw_results:
            return {
                "ready": False,
                "reason": "TypeDB 투영 결과가 기록되지 않았습니다.",
                "results": [],
            }
        accepted_projection_statuses = {
            "ok",
            "partial",
            "unchanged-material-facts",
            "unchanged-material-facts-reasoning-retry",
        }
        retryable_projection_statuses = {
            # ABox and native inference writes deliberately share a durable
            # lease.  Another local worker holding it is back-pressure, not
            # a failed graph projection, so do not open the error circuit.
            "deferred-scoped-write-lease",
            "deferred-projection-coordinator",
            "deferred-inference-write-lease",
            "deferred-pending-scoped-manifest",
            "blocked-pending-abox-activation",
            "pending-abox-activation",
            # A cold TypeDB server compiles the RuleBox in bounded candidate
            # batches. Keep source events pending while the prior aligned
            # generation remains active; this is controlled back-pressure, not
            # an investment inference failure.
            "deferred-schema-function-provisioning",
            # The candidate was safely rolled back to its predecessor because
            # durable InferenceBox readback did not prove the same generation.
            # Keep events pending and retry with back-pressure instead of
            # opening the failure circuit against an already verified ABox.
            "inference-failed-rolled-back",
            # A scoped generation may be valid for one symbol while a caller
            # asks for another. Keep the event pending for that target rather
            # than misclassifying the read as a verified no-signal.
            "not-evaluated",
            # A dependency-selected TypeDB pass may only be delivered after
            # its prior complete native coverage was persisted and verified.
            # Hold the source request for a safe retry instead of treating it
            # as an investment-engine failure.
            "incomplete-native-coverage",
        }
        transient_failure_statuses = {
            "error",
            "failed",
            "missing",
            "disabled",
            "invalid",
            "invalid-abox",
            "incomplete",
            "incomplete-abox",
            "missing-abox",
            "candidate-validation-failed",
            "activation-failed",
            "rulebox-not-ready",
            "stale-generation",
            "unavailable",
        }
        failures: List[Dict[str, str]] = []
        retryable: List[Dict[str, str]] = []

        def add_result(
            account_id: object,
            stage: str,
            status: str,
            reason: object,
            retry_after_seconds: object = 0,
        ) -> None:
            try:
                retry_after = max(0, int(float(retry_after_seconds or 0)))
            except (TypeError, ValueError):
                retry_after = 0
            item = {
                "accountId": str(account_id or ""),
                "stage": stage,
                "status": status,
                "reason": str(reason or "TypeDB ABox 투영이 완료되지 않았습니다."),
            }
            if retry_after:
                item["retryAfterSeconds"] = retry_after
            if status in retryable_projection_statuses:
                retryable.append(item)
            else:
                failures.append(item)

        for account_id, raw_result in raw_results.items():
            result = dict(raw_result or {}) if isinstance(raw_result, dict) else {}
            projection_status = str(result.get("status") or "missing").strip().lower()
            if projection_status not in accepted_projection_statuses:
                add_result(
                    account_id,
                    "projection",
                    projection_status,
                    result.get("reason") or "TypeDB ABox 투영이 완료되지 않았습니다.",
                    result.get("recommendedRetryAfterSeconds") or result.get("retryAfterSeconds"),
                )
                continue
            execution = result.get("ruleboxExecution") if isinstance(result.get("ruleboxExecution"), dict) else {}
            execution_status = str(execution.get("status") or "").strip().lower()
            if execution_status in transient_failure_statuses or execution_status in retryable_projection_statuses:
                add_result(
                    account_id,
                    "native-rule",
                    execution_status,
                    execution.get("reason") or "TypeDB native rule 실행이 완료되지 않았습니다.",
                    execution.get("recommendedRetryAfterSeconds") or execution.get("retryAfterSeconds"),
                )
                continue
            inference = result.get("inferenceBox") if isinstance(result.get("inferenceBox"), dict) else {}
            inference_status = str(inference.get("status") or "missing").strip().lower()
            if (
                not inference
                or inference_status in transient_failure_statuses
                or inference_status in retryable_projection_statuses
            ):
                add_result(
                    account_id,
                    "inferencebox",
                    inference_status,
                    inference.get("reason") or result.get("reason") or "TypeDB InferenceBox 응답이 없습니다.",
                    inference.get("recommendedRetryAfterSeconds") or inference.get("retryAfterSeconds"),
                )
                continue
            if inference_status == "empty":
                verified_no_match = (
                    bool(
                        inference.get("nativeTypeDbReasoningCompleted")
                        or inference.get("typedbNativeRuleEvaluationCompleted")
                    )
                    and bool(inference.get("generationAligned"))
                    and bool(str(inference.get("sourceAboxSnapshotId") or "").strip())
                )
                if not verified_no_match:
                    add_result(
                        account_id,
                        "inferencebox",
                        "empty-unverified",
                        "TypeDB native rule no-match 결과에 현재 ABox 세대 완료 증거가 없습니다.",
                    )
                    continue
            if bool(execution.get("nativeRuleSelectionApplied")):
                replay_validation = result.get("nativeReplayValidation")
                replay_validation = (
                    dict(replay_validation)
                    if isinstance(replay_validation, dict)
                    else native_replay_validation(result)
                )
                if str(replay_validation.get("status") or "") != "verified-prior-coverage":
                    add_result(
                        account_id,
                        "native-rule-coverage",
                        "incomplete-native-coverage",
                        replay_validation.get("reason")
                        or "Dependency-selected TypeDB execution lacks a verified complete coverage proof.",
                    )
        if failures:
            first = failures[0]
            return {
                "ready": False,
                "reason": "TypeDB " + first["stage"] + " 대기: " + first["reason"][:180],
                "results": failures,
            }
        if retryable:
            first = retryable[0]
            return {
                "ready": False,
                "retryable": True,
                "reason": "TypeDB " + first["stage"] + " 직렬화 대기: " + first["reason"][:180],
                "results": retryable,
                "retryAfterSeconds": max(
                    [int(item.get("retryAfterSeconds") or 0) for item in retryable] or [0]
                ),
            }
        return {"ready": True, "results": []}

    def mark_projection_attempt(self, symbols: Iterable[str]) -> None:
        clean_symbols = [str(symbol or "").upper().strip() for symbol in symbols or [] if str(symbol or "").strip()]
        if not clean_symbols or not hasattr(self.cursor_store, "load") or not hasattr(self.cursor_store, "save"):
            return
        payload = self.cursor_payload()
        attempts = self.last_projection_attempt_at_by_symbol(payload)
        now = self.now_provider()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        stamp = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        for symbol in clean_symbols:
            attempts[symbol] = stamp
        payload["lastProjectionAttemptAtBySymbol"] = dict(sorted(attempts.items()))
        self.save_cursor_payload(payload)

    def projection_runtime_summary(self, monitor_runner) -> Dict[str, object]:
        results = getattr(monitor_runner, "last_ontology_projection_results", {}) or {}
        rows = list(results.values()) if isinstance(results, dict) else []
        observations = [
            item.get("runtimeObservation")
            for item in rows
            if isinstance(item, dict) and isinstance(item.get("runtimeObservation"), dict)
        ]
        observation = max(
            observations,
            key=lambda item: int(float_value(item.get("durationMs"), 0.0)),
            default={},
        )
        if not observation:
            stages = [
                dict(item.get("runtimeStages") or {})
                for item in rows
                if isinstance(item, dict) and isinstance(item.get("runtimeStages"), dict)
            ]
            stage = max(stages, key=lambda item: int(float_value(item.get("totalMs"), 0.0)), default={})
            observation = {
                "durationMs": int(float_value(stage.get("totalMs"), 0.0)),
                "nativeInferenceMs": int(float_value(stage.get("nativeInferenceMs"), 0.0)),
                "stages": stage,
            }
        stages = observation.get("stages") if isinstance(observation.get("stages"), dict) else {}
        inference = observation.get("inference") if isinstance(observation.get("inference"), dict) else {}
        scope = observation.get("scope") if isinstance(observation.get("scope"), dict) else {}
        impact = scope.get("impactDiagnostics") if isinstance(scope.get("impactDiagnostics"), dict) else {}
        target_patch = scope.get("targetScopedManifestPatch") if isinstance(scope.get("targetScopedManifestPatch"), dict) else {}
        replay = inference.get("replayValidation") if isinstance(inference.get("replayValidation"), dict) else {}
        runtime_identity = observation.get("runtimeIdentity") if isinstance(observation.get("runtimeIdentity"), dict) else {}
        return {
            "durationMs": int(float_value(observation.get("durationMs"), 0.0)),
            "nativeInferenceMs": int(float_value(
                observation.get("nativeInferenceMs")
                or stages.get("nativeInferenceMs"),
                0.0,
            )),
            "impactPlanningMs": int(float_value(stages.get("impactPlanningMs"), 0.0)),
            "aboxPersistenceMs": int(float_value(
                stages.get("aboxPersistenceMs") or stages.get("persistenceMs"),
                0.0,
            )),
            "ruleboxBootstrapMs": int(float_value(stages.get("ruleboxBootstrapMs"), 0.0)),
            "pendingAboxActivationRecoveryMs": int(float_value(stages.get("pendingAboxActivationRecoveryMs"), 0.0)),
            "scopedAboxIdentityMs": int(float_value(stages.get("scopedAboxIdentityMs"), 0.0)),
            "activeAboxReadMs": int(float_value(stages.get("activeAboxReadMs"), 0.0)),
            "projectionAuditCreateMs": int(float_value(stages.get("projectionAuditCreateMs"), 0.0)),
            "aboxChangedScopeQueryCount": int(float_value(stages.get("aboxChangedScopeQueryCount"), 0.0)),
            "aboxChangedScopeTransactionCount": int(float_value(stages.get("aboxChangedScopeTransactionCount"), 0.0)),
            "aboxChangedScopeTransactionQueryCount": int(float_value(stages.get("aboxChangedScopeTransactionQueryCount"), 0.0)),
            "aboxManifestVerificationReadCount": int(float_value(
                stages.get("aboxManifestVerificationReadCount"),
                0.0,
            )),
            "aboxReusedPhysicalRowCount": int(float_value(
                stages.get("aboxReusedPhysicalRowCount"),
                0.0,
            )),
            "aboxInsertedNodeCount": int(float_value(stages.get("aboxInsertedNodeCount"), 0.0)),
            "aboxInsertedRelationCount": int(float_value(
                stages.get("aboxInsertedRelationCount"),
                0.0,
            )),
            "observedAt": str(observation.get("observedAt") or ""),
            "status": str(observation.get("status") or ""),
            "runtimeIdentity": {
                "contract": str(runtime_identity.get("contract") or ""),
                "version": str(runtime_identity.get("version") or ""),
                "revision": str(runtime_identity.get("revision") or ""),
                "source": str(runtime_identity.get("source") or ""),
            },
            "targetSymbolCount": int(float_value(
                inference.get("targetSymbolCount"),
                0.0,
            )),
            "targetCoverageStatus": str(inference.get("targetCoverageStatus") or ""),
            "candidateRuleCount": int(float_value(inference.get("candidateRuleCount"), 0.0)),
            "enabledRuleCount": int(float_value(inference.get("enabledRuleCount"), 0.0)),
            "candidateRuleRatioPct": float(float_value(inference.get("candidateRuleRatioPct"), 0.0)),
            "executedRuleCount": int(float_value(inference.get("executedRuleCount"), 0.0)),
            "executedRuleWorkCount": int(float_value(inference.get("executedRuleWorkCount"), 0.0)),
            "targetParallelism": int(float_value(inference.get("targetParallelism"), 0.0)),
            "targetWorkShardingUsed": bool(inference.get("targetWorkShardingUsed")),
            "targetWorkShardCount": int(float_value(inference.get("targetWorkShardCount"), 0.0)),
            "targetWorkItemCount": int(float_value(inference.get("targetWorkItemCount"), 0.0)),
            "commitMode": str(inference.get("commitMode") or ""),
            "affectedScopeCount": int(float_value(scope.get("affectedScopeCount"), 0.0)),
            "directChangedScopeCount": int(float_value(scope.get("directChangedScopeCount"), 0.0)),
            "globalImpact": bool(scope.get("globalImpact")),
            "globalImpactDiagnostics": {
                "classification": str(impact.get("classification") or ""),
                "reasonCodes": list(impact.get("reasonCodes") or [])[:12],
                "globalScopeCount": int(float_value(impact.get("globalScopeCount"), 0.0)),
                "globalScopeTypes": list(impact.get("globalScopeTypes") or [])[:8],
                "eventScopeAgreement": str(impact.get("eventScopeAgreement") or ""),
            },
            "targetScopedManifestPatch": {
                "status": str(target_patch.get("status") or ""),
                "mode": str(target_patch.get("mode") or ""),
                "fallbackReason": str(target_patch.get("fallbackReason") or ""),
                "targetSymbolCount": int(float_value(target_patch.get("targetSymbolCount"), 0.0)),
                "selectedIncomingScopeCount": int(float_value(target_patch.get("selectedIncomingScopeCount"), 0.0)),
                "reusedActiveScopeCount": int(float_value(target_patch.get("reusedActiveScopeCount"), 0.0)),
                "deferredScopeCount": int(float_value(target_patch.get("deferredScopeCount"), 0.0)),
            },
            "replayValidation": {
                "status": str(replay.get("status") or ""),
                "verified": bool(replay.get("verified")),
                "selectionApplied": bool(replay.get("selectionApplied")),
                "coverageComplete": bool(replay.get("coverageComplete")),
            },
        }

    def queue_dispatch_summary(
        self,
        requests: Iterable[object],
        selected_requests: Iterable[object] = None,
        selected_symbols: Iterable[str] = None,
        cursor_payload: Dict[str, object] = None,
        fairness_drain: Dict[str, object] = None,
        omitted_symbol_count: int = 0,
        batch_plan: Dict[str, object] = None,
    ) -> Dict[str, object]:
        """Expose the existing adaptive scheduler without changing priority.

        The queue already applies coalescing, materiality, per-symbol cadence,
        runtime backpressure, and fairness draining. This summary tells an
        operator which of those controls selected the next bounded turn.
        """
        pending = list(requests or [])
        selected = list(selected_requests or [])
        payload = self.cursor_payload() if cursor_payload is None else dict(cursor_payload or {})
        pending_symbols = self.request_symbols(pending)
        selected_symbols = [
            str(symbol or "").upper().strip()
            for symbol in selected_symbols or []
            if str(symbol or "").strip()
        ]
        classes = ["research", "realtime-market", "portfolio", "global", "other"]
        pending_counts = {work_class: 0 for work_class in classes}
        selected_counts = {work_class: 0 for work_class in classes}
        observed_at = []
        requested_at = []
        for event in pending:
            pending_counts[event_work_class(event)] += 1
            stamp = self.event_source_observed_at(event)
            if stamp:
                observed_at.append(stamp)
            request_stamp = str(getattr(event, "occurred_at", "") or "").strip()
            if request_stamp:
                requested_at.append(request_stamp)
        for event in selected:
            selected_counts[event_work_class(event)] += 1
        fairness = fairness_drain or self.fairness_drain_state(selected_symbols, payload)
        configured_interval = self.projection_min_interval_seconds(pending)
        effective_interval = self.effective_projection_min_interval_seconds(
            pending,
            payload,
            selected_symbols,
        )
        backpressure_active = bool(
            self.projection_backpressure_enabled()
            and effective_interval > configured_interval
        )
        selected_classes = [
            work_class for work_class in classes
            if selected_counts[work_class]
        ]
        if bool(fairness.get("active")):
            mode = "fairness-drain"
        elif backpressure_active:
            mode = "runtime-backpressure"
        elif selected_classes:
            mode = "priority-selected"
        else:
            mode = "waiting"
        fairness_queue = self.fairness_queue(pending_symbols, payload)
        return {
            "mode": mode,
            "pendingByClass": pending_counts,
            "selectedByClass": selected_counts,
            "selectedWorkClasses": selected_classes,
            "pendingSymbolCount": len(pending_symbols),
            "overduePendingSymbolCount": len([item for item in fairness_queue if item.get("state") == "overdue"]),
            "unseenPendingSymbolCount": len([item for item in fairness_queue if item.get("state") == "unseen"]),
            "selectedSymbols": selected_symbols[:20],
            "omittedSymbolCount": max(0, int(omitted_symbol_count or 0)),
            "fairnessDrainActive": bool(fairness.get("active")),
            "fairnessDrainReason": str(fairness.get("reason") or ""),
            "backpressureActive": backpressure_active,
            "configuredIntervalSeconds": configured_interval,
            "effectiveIntervalSeconds": effective_interval,
            "oldestRequestAt": min(requested_at) if requested_at else "",
            "oldestSourceObservedAt": min(observed_at) if observed_at else "",
            "batchPlan": dict(batch_plan or {}),
        }

    def projection_alert_outcomes(self, monitor_runner) -> List[Dict[str, object]]:
        """Return bounded, non-decision telemetry for the alert handoff.

        The monitor owns candidate creation and cadence. This summary makes a
        completed reasoning run explainable when it produces no notification,
        without feeding delivery data back into RuleBox evaluation.
        """
        results = getattr(monitor_runner, "last_ontology_projection_results", {}) or {}
        outcomes = []
        result_items = results.items() if isinstance(results, dict) else []
        for account_id, raw_result in result_items:
            result = dict(raw_result or {}) if isinstance(raw_result, dict) else {}
            inference = result.get("inferenceBox") if isinstance(result.get("inferenceBox"), dict) else {}
            pipeline = result.get("alertPipeline") if isinstance(result.get("alertPipeline"), dict) else {}
            outcomes.append({
                "accountId": str(account_id or ""),
                "projectionStatus": str(result.get("status") or ""),
                "inferenceStatus": str(inference.get("status") or ""),
                "inferenceGenerationId": str(inference.get("inferenceGenerationId") or ""),
                "sourceAboxSnapshotId": str(inference.get("sourceAboxSnapshotId") or ""),
                "generationAligned": bool(inference.get("generationAligned")),
                "nativeTypeDbReasoningCompleted": bool(
                    inference.get("nativeTypeDbReasoningCompleted")
                    or inference.get("typedbNativeRuleEvaluationCompleted")
                ),
                "nativeInferenceOutcome": str(inference.get("nativeInferenceOutcome") or ""),
                "alertPipeline": {
                    key: pipeline.get(key)
                    for key in [
                        "status", "reason", "detectedCandidateCount", "cadenceReadyCount",
                        "targetSymbols", "requestedSymbols", "nativeInferenceOutcome",
                    ]
                    if key in pipeline
                },
            })
        return outcomes[:100]

    def mark_successful_projection(self, monitor_runner=None) -> None:
        if not hasattr(self.cursor_store, "load") or not hasattr(self.cursor_store, "save"):
            return
        payload = self.cursor_payload()
        now = self.now_provider()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        payload["lastSuccessfulProjectionAt"] = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        payload["projectionCircuit"] = {
            "status": "closed",
            "consecutiveFailures": 0,
            "failureThreshold": self.projection_circuit_failure_threshold(),
            "lastSuccessAt": payload["lastSuccessfulProjectionAt"],
            "openUntil": "",
        }
        prior_timeout_guard = self.execution_timeout_guard_state(payload)
        if prior_timeout_guard:
            payload["executionTimeoutGuard"] = {
                "status": "closed",
                "consecutiveTimeouts": 0,
                "lastTimeoutAt": str(prior_timeout_guard.get("lastTimeoutAt") or ""),
                "lastTimeoutSeconds": int(prior_timeout_guard.get("lastTimeoutSeconds") or 0),
                "lastRecoveredAt": payload["lastSuccessfulProjectionAt"],
                "retryAfterAt": "",
            }
        if monitor_runner is not None:
            runtime = self.projection_runtime_summary(monitor_runner)
            if int(runtime.get("durationMs") or 0) > 0:
                payload["lastProjectionRuntime"] = runtime
        self.save_cursor_payload(payload)
        completion = getattr(self.mailbox_store, "record_completion", None) if self.mailbox_enabled() else None
        if callable(completion):
            try:
                completion({"stage": "typedb-inference-verified"})
            except Exception:
                pass

    def record_execution_timeout(
        self,
        timeout_seconds: int,
        started_at: str = "",
        output: str = "",
        worker_id: str = "",
    ) -> Dict[str, object]:
        """Persist a durable cooldown after the isolated worker is terminated.

        A killed child may have already staged an ABox candidate.  It must not
        be acknowledged as processed, and the next worker has to resume that
        candidate through the normal activation journal.  The parent records
        only operational state here; it never fabricates an investment result.
        """
        payload = self.cursor_payload()
        prior = self.execution_timeout_guard_state(payload)
        now = self.now_provider()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)
        consecutive = int(prior.get("consecutiveTimeouts") or 0) + 1
        backoff_seconds = self.execution_timeout_backoff_seconds()
        retry_at = datetime.fromtimestamp(
            now.timestamp() + backoff_seconds,
            timezone.utc,
        ).isoformat().replace("+00:00", "Z")
        guard = {
            "status": "open",
            "consecutiveTimeouts": consecutive,
            "lastTimeoutAt": now.isoformat().replace("+00:00", "Z"),
            "lastTimeoutSeconds": int(timeout_seconds or self.execution_timeout_seconds()),
            "retryAfterAt": retry_at,
            "retryAfterSeconds": backoff_seconds,
            "reason": "격리된 TypeDB 추론 실행이 시간 상한을 넘어 중지되었습니다. 보류된 ABox 후보는 다음 안전 재시도에서 복구합니다.",
        }
        mailbox_checkpoint = self.mailbox_summary()
        timeout_recovery_count = 0
        timeout_recovery = getattr(self.mailbox_store, "recover_worker_timeout", None) if self.mailbox_enabled() else None
        if callable(timeout_recovery):
            try:
                timeout_recovery_count = int(timeout_recovery(
                    str(worker_id or self.mailbox_worker_id()),
                    backoff_seconds,
                    "isolated reasoning execution timeout",
                ) or 0)
            except Exception:
                timeout_recovery_count = 0
        timeout_recorder = getattr(self.mailbox_store, "record_timeout", None) if self.mailbox_enabled() else None
        if callable(timeout_recorder):
            try:
                timeout_recorder({"stage": str(mailbox_checkpoint.get("lastStage") or "timeout")})
            except Exception:
                pass
        payload["executionTimeoutGuard"] = guard
        summary = {
            "startedAt": str(started_at or ""),
            "finishedAt": guard["lastTimeoutAt"],
            "durationMs": int(timeout_seconds or self.execution_timeout_seconds()) * 1000,
            "status": "timeout",
            "processedCount": 0,
            "alertCount": 0,
            "deferredReason": guard["reason"],
            "timeoutOutput": str(output or "")[-240:],
            "mailboxCheckpoint": {
                key: mailbox_checkpoint.get(key)
                for key in ["pendingEntryCount", "runningEntryCount", "retryingEntryCount", "lastStage", "lastStageAt", "activeWorkerId"]
                if mailbox_checkpoint.get(key) not in (None, "", [], {})
            },
            "mailboxTimeoutRecoveryCount": timeout_recovery_count,
        }
        history = [
            dict(item)
            for item in payload.get("reasoningExecutionHistory") or []
            if isinstance(item, dict)
        ]
        history.append(summary)
        payload["lastReasoningExecution"] = summary
        payload["reasoningExecutionHistory"] = history[-self.telemetry_history_limit():]
        self.save_cursor_payload(payload)
        return {
            "status": "timeout",
            "processedCount": 0,
            "alertCount": 0,
            "retryAfterSeconds": backoff_seconds,
            "deferredReason": guard["reason"],
            "executionTimeoutGuard": guard,
            "executionTelemetry": summary,
        }

    def recover_open_projection_circuit(
        self,
        requests: Iterable[object],
        symbols: Iterable[str],
    ) -> Dict[str, object]:
        """Probe durable TypeDB health before retrying a failed queue."""
        if not callable(self.projection_recovery_probe):
            return {"ready": False, "status": "not-configured"}
        account_ids = []
        for request in requests or []:
            payload = event_payload(request)
            values = [payload.get("accountId")]
            if isinstance(payload.get("accountIds"), list):
                values.extend(payload.get("accountIds") or [])
            for value in values:
                clean = str(value or "").strip()
                if clean and clean not in account_ids:
                    account_ids.append(clean)
        clean_symbols = []
        for symbol in symbols or []:
            clean = str(symbol or "").upper().strip()
            if clean and clean not in clean_symbols:
                clean_symbols.append(clean)
        try:
            payload = self.projection_recovery_probe(account_ids, clean_symbols)
        except Exception as error:  # noqa: BLE001 - preserve the fail-closed circuit when the read fails.
            return {"ready": False, "status": "error", "reason": str(error)[:180]}
        result = dict(payload or {}) if isinstance(payload, dict) else {}
        result["ready"] = bool(result.get("ready"))
        result.setdefault("status", "ready" if result["ready"] else "not-ready")
        result["accountIds"] = account_ids
        result["symbols"] = clean_symbols
        return result

    def clear_projection_circuit_after_verified_recovery(self, recovery: Dict[str, object]) -> None:
        """Clear only the failure latch; pending events still run normally."""
        if not hasattr(self.cursor_store, "load") or not hasattr(self.cursor_store, "save"):
            return
        payload = self.cursor_payload()
        now = self.now_provider()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        prior = self.projection_circuit_state(payload)
        payload["projectionCircuit"] = {
            "status": "closed",
            "consecutiveFailures": 0,
            "failureThreshold": self.projection_circuit_failure_threshold(),
            "lastFailureAt": "",
            "lastFailureReason": "",
            "recentFailures": [],
            "openUntil": "",
            "recoveredAt": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "recoveryReason": "A current TypeDB ABox was read successfully; pending events will materialize a fresh InferenceBox generation.",
            "recoveredFailureCount": int(prior.get("consecutiveFailures") or 0),
            "recovery": {
                key: recovery.get(key)
                for key in ["status", "recoveryMode", "accountIds", "symbols", "accounts"]
                if key in recovery
            },
        }
        self.save_cursor_payload(payload)

    def run_maintenance_if_due(self, force: bool = False) -> Dict[str, object]:
        if not self.maintenance_enabled():
            return {"status": "disabled"}
        if not self.maintenance_runner:
            return {"status": "not-configured"}
        payload = self.cursor_payload()
        state = self.maintenance_state(payload)
        if not force and not self.timestamp_due(
            str(state.get("lastRunAt") or ""),
            self.maintenance_interval_seconds(),
        ):
            return {
                "status": "cooldown",
                "retryAfterSeconds": self.timestamp_remaining_seconds(
                    str(state.get("lastRunAt") or ""),
                    self.maintenance_interval_seconds(),
                ),
                "lastResult": dict(state.get("lastResult") or {}),
            }
        try:
            result = self.maintenance_runner()
        except Exception as error:  # noqa: BLE001 - maintenance must not stop event processing.
            result = {"status": "error", "reason": str(error)[:180]}
        if self.mailbox_enabled():
            prune = getattr(self.mailbox_store, "prune_terminal", None)
            if callable(prune):
                try:
                    result = dict(result or {}) if isinstance(result, dict) else {"status": "invalid"}
                    result["mailboxPrunedTerminalEventCount"] = int(
                        prune(self.mailbox_retention_hours()) or 0
                    )
                except Exception as error:  # noqa: BLE001 - retention cleanup is best effort.
                    result = dict(result or {}) if isinstance(result, dict) else {"status": "invalid"}
                    result["mailboxPruneReason"] = str(error)[:180]
        now = self.now_provider()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        payload["ontologyMaintenance"] = {
            "lastRunAt": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "lastResult": dict(result or {}) if isinstance(result, dict) else {"status": "invalid"},
        }
        self.save_cursor_payload(payload)
        return dict(result or {}) if isinstance(result, dict) else {"status": "invalid"}

    def mailbox_summary(self) -> Dict[str, object]:
        if not self.mailbox_enabled():
            return {"enabled": False}
        summary = getattr(self.mailbox_store, "summary", None)
        if not callable(summary):
            return {"enabled": True, "status": "not-supported"}
        try:
            payload = summary()
        except Exception as error:  # noqa: BLE001 - visibility must not block TypeDB work.
            return {"enabled": True, "status": "error", "reason": str(error)[:180]}
        result = dict(payload or {}) if isinstance(payload, dict) else {}
        result["enabled"] = True
        return result

    def mailbox_actionability_summary(
        self,
        work: Dict[str, object],
        requests: Iterable[object],
        stale_requests: Iterable[Tuple[object, Dict[str, object]]] = None,
    ) -> Dict[str, object]:
        """Expose durable rows separately from actionable queue pressure."""
        summary = self.mailbox_summary()
        summary = dict(summary or {}) if isinstance(summary, dict) else {"enabled": self.mailbox_enabled()}
        active = list(requests or [])
        stale = list(stale_requests or [])
        active_mailbox = [event for event in active if self.mailbox_metadata(event)]
        stale_mailbox = [event for event, _freshness in stale if self.mailbox_metadata(event)]
        summary.update({
            "storedPendingEntryCount": int(
                work.get("mailboxStoredEntryCount")
                or summary.get("pendingEntryCount")
                or 0
            ),
            "actionablePendingEntryCount": len(active_mailbox),
            "semanticSupersededEntryCount": int(work.get("semanticSupersededMailboxEntryCount") or 0),
            "stalePreviewEntryCount": len(stale_mailbox),
        })
        return summary

    def lightweight_queue_state(self, scan_limit: int = 0) -> Dict[str, object]:
        """Return a bounded queue signal without building the full scheduler view.

        Background world projection and ABox retention need only one answer:
        whether live investment reasoning has work waiting.  Calling
        :meth:`status` for that answer also evaluates fairness, account
        priority, storage, and TypeDB diagnostics.  Those reads can contend
        with the very projection that the low-priority worker is meant to
        yield to.  This path deliberately reads only the event cursor, a
        bounded event window, and the durable mailbox summary.
        """
        return lightweight_ontology_reasoning_queue_state(
            self.event_reader,
            self.cursor_store,
            mailbox_store=self.mailbox_store,
            settings=self.settings,
            scan_limit=scan_limit,
        )

    def execution_telemetry(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        source = self.cursor_payload() if payload is None else dict(payload or {})
        last = source.get("lastReasoningExecution")
        history = source.get("reasoningExecutionHistory")
        return {
            "last": dict(last or {}) if isinstance(last, dict) else {},
            "history": [dict(item) for item in history or [] if isinstance(item, dict)][-10:],
        }

    def record_execution_telemetry(
        self,
        result: Dict[str, object],
        started_at: str,
        started_monotonic: float,
        error: Exception = None,
    ) -> Dict[str, object]:
        finished = self.now_provider()
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=timezone.utc)
        summary = {
            "startedAt": started_at,
            "finishedAt": finished.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "durationMs": max(0, int((time.perf_counter() - started_monotonic) * 1000)),
            "status": str(result.get("status") or ("error" if error else "unknown")),
            "processedCount": int(result.get("processedCount") or 0),
            "alertCount": int(result.get("alertCount") or 0),
            "rawRequestCount": int(result.get("rawRequestCount") or 0),
            "mailboxPendingEntryCount": int(result.get("mailboxPendingEntryCount") or 0),
            "sameRevisionEntryCount": int(result.get("sameRevisionEntryCount") or 0),
            "coalescedEventCount": int(result.get("coalescedEventCount") or 0),
            "staleRequestCount": int(result.get("staleRequestCount") or 0),
            "deferredReason": str(result.get("deferredReason") or "")[:240],
        }
        if error is not None:
            summary["error"] = str(error)[:240]
        stage_timing = result.get("stageTiming")
        if isinstance(stage_timing, dict):
            summary["stageTiming"] = {
                key: int(float_value(value, 0.0))
                for key, value in stage_timing.items()
                if key in {"monitorAndProjectionMs", "postProjectionMs", "maintenanceMs"}
                and float_value(value, 0.0) >= 0
            }
        projection_runtime = result.get("lastProjectionRuntime")
        if isinstance(projection_runtime, dict):
            summary["projectionRuntime"] = {
                key: projection_runtime.get(key)
                for key in [
                    "durationMs", "impactPlanningMs", "aboxPersistenceMs", "nativeInferenceMs",
                    "ruleboxBootstrapMs", "pendingAboxActivationRecoveryMs", "scopedAboxIdentityMs",
                    "activeAboxReadMs", "projectionAuditCreateMs", "aboxChangedScopeQueryCount",
                    "aboxChangedScopeTransactionCount", "aboxChangedScopeTransactionQueryCount",
                    "aboxManifestVerificationReadCount",
                    "aboxReusedPhysicalRowCount", "aboxInsertedNodeCount", "aboxInsertedRelationCount",
                    "status", "targetSymbolCount", "candidateRuleCount", "enabledRuleCount",
                    "candidateRuleRatioPct", "executedRuleCount", "executedRuleWorkCount",
                    "targetParallelism", "targetWorkShardingUsed", "targetWorkShardCount",
                    "targetWorkItemCount", "commitMode", "affectedScopeCount",
                    "directChangedScopeCount", "globalImpact", "globalImpactDiagnostics",
                    "targetScopedManifestPatch", "runtimeIdentity", "replayValidation",
                ]
                if key in projection_runtime
            }
        queue_dispatch = result.get("queueDispatch")
        if isinstance(queue_dispatch, dict):
            summary["queueDispatch"] = {
                key: queue_dispatch.get(key)
                for key in [
                    "mode", "selectedWorkClasses", "selectedSymbols", "omittedSymbolCount",
                    "fairnessDrainActive", "fairnessDrainReason", "backpressureActive",
                    "configuredIntervalSeconds", "effectiveIntervalSeconds",
                    "oldestRequestAt", "oldestSourceObservedAt", "pendingSymbolCount",
                    "overduePendingSymbolCount", "unseenPendingSymbolCount", "batchPlan",
                ]
                if key in queue_dispatch
            }
        mailbox = result.get("mailbox")
        if isinstance(mailbox, dict):
            summary["mailbox"] = {
                key: mailbox.get(key)
                for key in ["enabled", "entryCount", "mailboxPendingEntryCount", "pendingEntryCount", "reason"]
                if key in mailbox
            }
        payload = self.cursor_payload()
        history = [dict(item) for item in payload.get("reasoningExecutionHistory") or [] if isinstance(item, dict)]
        history.append(summary)
        payload["lastReasoningExecution"] = summary
        payload["reasoningExecutionHistory"] = history[-self.telemetry_history_limit():]
        self.save_cursor_payload(payload)
        response = dict(result or {})
        response["executionTelemetry"] = summary
        return response

    def record_queue_health(self, result: Dict[str, object]) -> Dict[str, object]:
        """Persist queue pressure after each scheduler turn without changing it.

        Queue health is an operational observation. It neither changes TypeDB
        rule execution nor feeds investment conclusions back into the queue.
        """
        response = dict(result or {})
        service = self.queue_health_service
        if not service or not hasattr(service, "record"):
            return response
        try:
            health, event = service.record(response)
            if isinstance(health, dict):
                response["queueDelayHealth"] = health
            if event is not None:
                self.publish(event)
        except Exception:  # noqa: BLE001 - telemetry must never block graph reasoning.
            return response
        return response

    def run_once(self, limit: int = 0, force: bool = False) -> Dict[str, object]:
        started = self.now_provider()
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        started_at = started.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        started_monotonic = time.perf_counter()
        try:
            result = self._run_once(limit=limit, force=force)
        except Exception as error:
            self.record_execution_telemetry(
                {"status": "error", "processedCount": 0, "alertCount": 0},
                started_at,
                started_monotonic,
                error=error,
            )
            raise
        recorded = self.record_execution_telemetry(result, started_at, started_monotonic)
        return self.record_queue_health(recorded)

    def _run_once(self, limit: int = 0, force: bool = False) -> Dict[str, object]:
        if not self.enabled():
            return {"status": "disabled", "processedCount": 0, "alertCount": 0}
        elapsed_timeout_guard = self.mark_execution_timeout_cooldown_elapsed()
        work = self.pending_work(limit)
        terminal_mailbox = self.persist_terminal_mailbox_events(
            {
                **self.recent_terminal_mailbox_events(),
                **dict(work.get("terminalMailboxEventStates") or {}),
            },
        )
        durable_superseded_ids = self.persist_superseded_events(work.get("coalescedEventIds") or [])
        requests, stale_requests = self.split_stale_requests(work.get("requests") or [])
        stale_terminal = self.persist_terminal_mailbox_events(
            self.discard_stale_requests(stale_requests),
        )
        mailbox_summary = self.mailbox_actionability_summary(work, requests, stale_requests)
        queue_metadata = {
            "rawRequestCount": int(work.get("rawRequestCount") or 0),
            "effectivePendingCount": len(requests),
            "pendingResearchHandoffCount": int(work.get("pendingResearchHandoffCount") or 0),
            "mailboxPendingEntryCount": int(mailbox_summary.get("actionablePendingEntryCount") or 0),
            "mailboxStoredEntryCount": int(mailbox_summary.get("storedPendingEntryCount") or 0),
            "semanticSupersededMailboxEntryCount": int(
                work.get("semanticSupersededMailboxEntryCount") or 0
            ),
            "mailboxSemanticCompaction": dict(work.get("mailboxSemanticCompaction") or {}),
            "sameRevisionEntryCount": len((work.get("mailbox") or {}).get("sameRevisionEntryKeys") or []),
            "staleRequestCount": len(stale_requests),
            "mailbox": mailbox_summary,
            "mailboxTerminalEvents": {
                "completed": list(terminal_mailbox.get("completed") or []) + list(stale_terminal.get("completed") or []),
                "discarded": list(terminal_mailbox.get("discarded") or []) + list(stale_terminal.get("discarded") or []),
            },
        }
        queue_metadata["queueDispatch"] = self.queue_dispatch_summary(requests)
        if not requests:
            maintenance = self.run_maintenance_if_due(force=force)
            return {
                "status": "idle",
                "processedCount": 0,
                "alertCount": 0,
                "coalescedEventCount": len(durable_superseded_ids),
                "maintenance": maintenance,
                **({"executionTimeoutGuard": elapsed_timeout_guard} if elapsed_timeout_guard else {}),
                **queue_metadata,
            }
        execution_timeout_remaining = self.execution_timeout_guard_remaining_seconds()
        if execution_timeout_remaining > 0 and not force:
            timeout_guard = self.execution_timeout_guard_state()
            return {
                "status": "deferred",
                "processedCount": 0,
                "alertCount": 0,
                "retryAfterSeconds": execution_timeout_remaining,
                "deferredReason": str(
                    timeout_guard.get("reason")
                    or "이전 TypeDB 추론 실행이 시간 상한을 넘어 안전 재시도 대기 중입니다."
                ),
                "executionTimeoutGuard": timeout_guard,
                "coalescedEventCount": len(durable_superseded_ids),
                **queue_metadata,
            }
        storage_guard = self.storage_guard_state()
        if not storage_guard.get("ready"):
            return {
                "status": "deferred",
                "processedCount": 0,
                "alertCount": 0,
                "retryAfterSeconds": 60,
                "deferredReason": str(storage_guard.get("reason") or "TypeDB 저장소 여유 공간이 부족해 추론을 보류합니다."),
                "storageGuard": storage_guard,
                "coalescedEventCount": len(durable_superseded_ids),
                **queue_metadata,
            }
        batch_plan = self.reasoning_batch_plan(requests)
        queue_metadata["batchPlan"] = dict(batch_plan)
        symbol_batches, symbols, omitted_symbol_count = self.request_symbol_batches(
            requests,
            # ``0`` remains the historical non-native unbounded setting. Do
            # not coerce it to one while passing the operational plan through
            # this compatibility path.
            max_symbols_override=int(batch_plan.get("targetSymbolLimit") or 0),
        )
        selected_request_ids = {
            str(event_id or "").strip()
            for event_id in symbol_batches
            if str(event_id or "").strip()
        }
        selected_requests = [
            event for event in requests
            if str(getattr(event, "event_id", "") or "").strip() in selected_request_ids
        ]
        queue_metadata["queueDispatch"] = self.queue_dispatch_summary(
            requests,
            selected_requests,
            symbols,
            omitted_symbol_count=omitted_symbol_count,
            batch_plan=batch_plan,
        )
        reasoning_context = reasoning_request_provenance(selected_requests, symbols)
        reasoning_context["queuePressure"] = {
            "effectivePendingCount": len(requests),
            "selectedRequestCount": len(selected_requests),
            "omittedSymbolCount": omitted_symbol_count,
            "hasDeferredWork": bool(
                omitted_symbol_count
                or len(requests) > len(selected_requests)
            ),
        }
        reasoning_context["batchPlan"] = dict(batch_plan)
        # A pending mailbox slot can be temporarily ineligible because its
        # symbol-level interval or retry protection has not elapsed.  Never
        # invoke the monitor with an empty subject filter in that case: older
        # runners interpret it as a whole-portfolio projection.
        if not selected_requests:
            return {
                "status": "cooldown",
                "processedCount": 0,
                "scheduledRequestCount": 0,
                "alertCount": 0,
                "symbols": [],
                "maxSymbolsPerRun": self.effective_max_symbols_per_run(),
                "configuredMaxSymbolsPerRun": self.max_symbols_per_run(),
                "nativeTypeDbTargetSymbolLimit": self.native_typedb_target_symbol_limit() if self.native_typedb_rule_execution_enabled() else None,
                "omittedSymbolCount": omitted_symbol_count,
                "retryAfterSeconds": self.projection_retry_seconds(),
                "deferredReason": "대기 중인 요청은 있으나 이번 주기에 실행 가능한 종목이 없습니다.",
                "coalescedEventCount": len(durable_superseded_ids),
                **queue_metadata,
            }
        cursor_payload = self.cursor_payload()
        circuit_remaining = self.projection_circuit_remaining_seconds(cursor_payload)
        if circuit_remaining > 0 and not force:
            recovery = self.recover_open_projection_circuit(requests, symbols)
            if recovery.get("ready"):
                self.clear_projection_circuit_after_verified_recovery(recovery)
                cursor_payload = self.cursor_payload()
                circuit_remaining = 0
            else:
                circuit = self.projection_circuit_state(cursor_payload)
                return {
                    "status": "circuit-open",
                    "processedCount": 0,
                    "alertCount": 0,
                    "symbols": symbols,
                    "retryAfterSeconds": circuit_remaining,
                    "deferredReason": str(circuit.get("lastFailureReason") or "TypeDB projection circuit is open."),
                    "projectionCircuit": circuit,
                    "projectionRecovery": recovery,
                    "coalescedEventCount": len(durable_superseded_ids),
                    **queue_metadata,
                }
        fairness_drain = self.fairness_drain_state(symbols, cursor_payload)
        queue_metadata["queueDispatch"] = self.queue_dispatch_summary(
            requests,
            selected_requests,
            symbols,
            cursor_payload=cursor_payload,
            fairness_drain=fairness_drain,
            omitted_symbol_count=omitted_symbol_count,
            batch_plan=batch_plan,
        )
        if not force and not self.projection_due(requests, cursor_payload, symbols):
            retry_after_seconds = self.projection_cooldown_remaining_seconds(requests, cursor_payload, symbols)
            return {
                "status": "cooldown",
                "processedCount": 0,
                "alertCount": 0,
                "symbols": symbols,
                "maxSymbolsPerRun": self.effective_max_symbols_per_run(),
                "configuredMaxSymbolsPerRun": self.max_symbols_per_run(),
                "nativeTypeDbTargetSymbolLimit": self.native_typedb_target_symbol_limit() if self.native_typedb_rule_execution_enabled() else None,
                "omittedSymbolCount": omitted_symbol_count,
                "retryAfterSeconds": retry_after_seconds,
                "projectionCooldownSeconds": self.effective_projection_min_interval_seconds(requests, cursor_payload, symbols),
                "configuredProjectionCooldownSeconds": self.projection_min_interval_seconds(requests),
                "fairnessDrain": fairness_drain,
                "lastProjectionRuntime": self.last_projection_runtime(cursor_payload),
                "coalescedEventCount": len(durable_superseded_ids),
                **queue_metadata,
            }
        mailbox_work_entries = self.mailbox_entries_for_requests(selected_requests)
        mailbox_lease = self.claim_mailbox_work(mailbox_work_entries)
        # A real durable lease store failed while claiming the selected
        # revision. Running anyway would turn a transient operational fault
        # into concurrent TypeDB work, so defer and keep the source rows
        # intact. Lightweight/legacy adapters without ``claim`` deliberately
        # retain their compatibility path below.
        if (
            mailbox_work_entries
            and not mailbox_lease.get("enabled")
            and str(mailbox_lease.get("reason") or "").strip()
        ):
            return {
                "status": "deferred",
                "processedCount": 0,
                "alertCount": 0,
                "symbols": symbols,
                "retryAfterSeconds": self.mailbox_work_retry_seconds(),
                "deferredReason": "영속 추론 작업 lease를 확보하지 못해 안전 재시도합니다.",
                "mailboxWorkLease": mailbox_lease,
                "coalescedEventCount": len(durable_superseded_ids),
                **queue_metadata,
            }
        claimed_mailbox_entries = list(mailbox_lease.get("claimed") or []) if mailbox_lease.get("enabled") else list(mailbox_work_entries)
        blocked_mailbox_entries = list(mailbox_lease.get("blocked") or []) if mailbox_lease.get("enabled") else []
        if blocked_mailbox_entries:
            self.release_mailbox_work(
                claimed_mailbox_entries,
                "A newer or actively leased mailbox revision owns this reasoning turn.",
                self.mailbox_work_retry_seconds(),
            )
            return {
                "status": "deferred",
                "processedCount": 0,
                "alertCount": 0,
                "symbols": symbols,
                "retryAfterSeconds": self.mailbox_work_retry_seconds(),
                "deferredReason": "영속 추론 작업이 최신 revision 또는 다른 안전 lease를 기다리고 있습니다.",
                "mailboxWorkLease": mailbox_lease,
                "coalescedEventCount": len(durable_superseded_ids),
                **queue_metadata,
            }
        self.checkpoint_mailbox_work(
            claimed_mailbox_entries,
            "snapshot-preparation",
            {"symbols": symbols, "selectedRequestCount": len(selected_requests)},
        )
        stage_timing: Dict[str, int] = {}
        monitor_started = time.perf_counter()
        runner = self.monitor_runner_factory()
        runner_parameters = inspect.signature(runner.run_once).parameters
        runner_accepts_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in runner_parameters.values()
        )
        runner_kwargs = {"force": force}
        if "symbol_filter" in runner_parameters or runner_accepts_kwargs:
            runner_kwargs["symbol_filter"] = symbols
        if "reasoning_context" in runner_parameters or runner_accepts_kwargs:
            runner_kwargs["reasoning_context"] = reasoning_context
        try:
            alerts = runner.run_once(**runner_kwargs)
        except Exception:
            self.release_mailbox_work(
                claimed_mailbox_entries,
                "The monitor/projection stage raised before a verified TypeDB result.",
                self.mailbox_work_retry_seconds(),
            )
            raise
        stage_timing["monitorAndProjectionMs"] = int((time.perf_counter() - monitor_started) * 1000)
        self.checkpoint_mailbox_work(
            claimed_mailbox_entries,
            "typedb-projection-complete",
            {"monitorAndProjectionMs": stage_timing["monitorAndProjectionMs"]},
        )
        projection_gate = self.projection_gate(runner)
        if not projection_gate.get("ready"):
            self.mark_projection_attempt(symbols)
            if projection_gate.get("retryable"):
                self.release_mailbox_work(
                    claimed_mailbox_entries,
                    str(projection_gate.get("reason") or "TypeDB writer is not ready."),
                    int(projection_gate.get("retryAfterSeconds") or self.projection_retry_seconds()),
                )
                return {
                    "status": "deferred",
                    "processedCount": 0,
                    "alertCount": len(alerts or []),
                    "symbols": symbols,
                    "maxSymbolsPerRun": self.effective_max_symbols_per_run(),
                    "configuredMaxSymbolsPerRun": self.max_symbols_per_run(),
                    "nativeTypeDbTargetSymbolLimit": self.native_typedb_target_symbol_limit() if self.native_typedb_rule_execution_enabled() else None,
                    "omittedSymbolCount": omitted_symbol_count,
                    "retryAfterSeconds": int(
                        projection_gate.get("retryAfterSeconds")
                        or self.projection_retry_seconds()
                    ),
                    "deferredReason": str(projection_gate.get("reason") or "TypeDB graph cycle is serialized by another writer."),
                    "projectionFailures": list(projection_gate.get("results") or []),
                    "projectionCircuit": self.projection_circuit_state(),
                    "reasoningContext": reasoning_context,
                    "coalescedEventCount": len(durable_superseded_ids),
                    **queue_metadata,
                }
            self.release_mailbox_work(
                claimed_mailbox_entries,
                str(projection_gate.get("reason") or "TypeDB graph cycle is not ready."),
                self.projection_circuit_remaining_seconds() or self.projection_retry_seconds(),
            )
            circuit = self.record_projection_failure(
                str(projection_gate.get("reason") or "TypeDB graph cycle is not ready."),
                projection_gate.get("results") or [],
            )
            return {
                "status": "circuit-open" if str(circuit.get("status") or "") == "open" else "deferred",
                "processedCount": 0,
                "alertCount": len(alerts or []),
                "symbols": symbols,
                "maxSymbolsPerRun": self.effective_max_symbols_per_run(),
                "configuredMaxSymbolsPerRun": self.max_symbols_per_run(),
                "nativeTypeDbTargetSymbolLimit": self.native_typedb_target_symbol_limit() if self.native_typedb_rule_execution_enabled() else None,
                "omittedSymbolCount": omitted_symbol_count,
                "retryAfterSeconds": self.projection_circuit_remaining_seconds() or self.projection_retry_seconds(),
                "deferredReason": str(projection_gate.get("reason") or "TypeDB graph cycle is not ready."),
                "projectionFailures": list(projection_gate.get("results") or []),
                "projectionCircuit": circuit,
                "reasoningContext": reasoning_context,
                "coalescedEventCount": len(durable_superseded_ids),
                **queue_metadata,
            }
        self.mark_successful_projection(runner)
        post_projection_started = time.perf_counter()
        account_ids = [getattr(account, "account_id", "") for account in getattr(runner, "accounts", [])]
        trigger_event_ids = []
        for event in selected_requests:
            source_event_id = self.mailbox_source_event_id(event)
            if source_event_id and source_event_id not in trigger_event_ids:
                trigger_event_ids.append(source_event_id)
        research_refresh_requests = []
        seen_research_request_ids = set()
        for event in list(selected_requests) + list(work.get("researchHandoffRequests") or []):
            event_id = str(getattr(event, "event_id", "") or "").strip()
            if not event_id or event_id in seen_research_request_ids:
                continue
            seen_research_request_ids.add(event_id)
            research_refresh_requests.append(event)
        research_refresh = self.research_generation_refresh_results(
            research_refresh_requests,
            getattr(runner, "last_ontology_projection_results", {}),
        )
        projection_outcomes = self.projection_alert_outcomes(runner)
        selected_request_ids = {
            str(getattr(event, "event_id", "") or "").strip()
            for event in selected_requests
            if str(getattr(event, "event_id", "") or "").strip()
        }
        # A shared full-world projection may satisfy older direct research
        # handoffs that were not selected as the scheduling trigger.  Only a
        # blocked request selected for this turn should make this turn partial.
        blocked_request_ids = {
            event_id
            for event_id in research_refresh.get("blockedRequestEventIds") or []
            if event_id in selected_request_ids
        }
        refreshed_research_request_ids = {
            str(event_id or "").strip()
            for event_id in research_refresh.get("refreshedRequestEventIds") or []
            if str(event_id or "").strip()
        }
        reconciled_research_request_ids = refreshed_research_request_ids - selected_request_ids
        research_handoff_terminal = self.persist_terminal_mailbox_events(
            {
                event_id: "completed"
                for event_id in reconciled_research_request_ids
            },
        )
        cursor_requests = [
            event for event in selected_requests
            if str(getattr(event, "event_id", "") or "") not in blocked_request_ids
        ]
        direct_cursor_requests = [
            event for event in cursor_requests
            if not self.mailbox_metadata(event).get("mailboxKey")
        ]
        mailbox_cursor_requests = [
            event for event in cursor_requests
            if self.mailbox_metadata(event).get("mailboxKey")
        ]
        if blocked_request_ids:
            blocked_mailbox = self.mailbox_entries_for_requests(
                event for event in selected_requests
                if str(getattr(event, "event_id", "") or "") in blocked_request_ids
            )
            self.release_mailbox_work(
                blocked_mailbox,
                "Research evidence requires an aligned ABox/InferenceBox generation before acknowledgement.",
                self.projection_retry_seconds(),
            )
        completed = ontology_reasoning_completed_event(
            trigger_event_ids,
            account_ids,
            symbols,
            len(alerts or []),
            status="ok",
            reason=(
                "데이터 변경 이벤트가 온톨로지 추론 사이클을 실행했습니다."
                + (f" 네이티브 추론 대상 상한 {self.effective_max_symbols_per_run()}개가 적용되어 {omitted_symbol_count}개는 다음 사이클로 이월했습니다." if omitted_symbol_count else "")
            ),
            research_generation_refreshes=research_refresh,
            projection_outcomes=projection_outcomes,
        )
        rule_candidate_result = self.propose_rule_candidates(symbols, selected_requests, alerts, force=False)
        self.publish(completed)
        progress_result = self.mark_requests_processed(
            direct_cursor_requests,
            symbol_batches,
            superseded_by_lead=work.get("supersededByLead"),
        )
        direct_terminalize = getattr(self.mailbox_store, "terminalize_direct_events", None) if self.mailbox_enabled() else None
        if callable(direct_terminalize):
            direct_by_id = {
                str(getattr(event, "event_id", "") or "").strip(): event
                for event in direct_cursor_requests
                if str(getattr(event, "event_id", "") or "").strip()
            }
            for state, result_key in (("completed", "completedEventIds"), ("superseded", "supersededEventIds")):
                terminal_events = [
                    direct_by_id[event_id]
                    for event_id in progress_result.get(result_key) or []
                    if event_id in direct_by_id
                ]
                if terminal_events:
                    try:
                        direct_terminalize(
                            terminal_events,
                            state=state,
                            reason="TypeDB projection and native inference completed",
                        )
                    except Exception:
                        pass
        mailbox_entries = []
        for event in mailbox_cursor_requests:
            event_id = str(getattr(event, "event_id", "") or "").strip()
            if not symbol_batches.get(event_id):
                continue
            metadata = self.mailbox_metadata(event)
            mailbox_key = str(metadata.get("mailboxKey") or "").strip()
            source_event_id = str(metadata.get("sourceEventId") or "").strip()
            if mailbox_key and source_event_id:
                mailbox_entries.append({
                    "mailboxKey": mailbox_key,
                    "sourceEventId": source_event_id,
                })
        mailbox_completion = {"completed": [], "discarded": []}
        mailbox_ack_error = ""
        if mailbox_entries and self.mailbox_enabled():
            try:
                terminal_states = self.mailbox_store.acknowledge(
                    mailbox_entries,
                    state="completed",
                    reason="TypeDB projection and native inference completed",
                ) or {}
                mailbox_completion = self.persist_terminal_mailbox_events(terminal_states)
            except Exception as error:  # noqa: BLE001 - retain the mailbox entry for a safe retry.
                mailbox_ack_error = str(error)[:180]
                self.release_mailbox_work(
                    claimed_mailbox_entries,
                    "Mailbox acknowledgement failed after a verified graph cycle: " + mailbox_ack_error,
                    self.mailbox_work_retry_seconds(),
                )
        processed_symbols = []
        for event in cursor_requests:
            for symbol in symbol_batches.get(str(getattr(event, "event_id", "") or ""), []) or []:
                if symbol not in processed_symbols:
                    processed_symbols.append(symbol)
        self.mark_symbols_reasoned(processed_symbols)
        stage_timing["postProjectionMs"] = int((time.perf_counter() - post_projection_started) * 1000)
        maintenance_started = time.perf_counter()
        # Retention owns the same TypeDB writer lease as ABox activation. It
        # must not consume a live inference cycle while the bounded target
        # queue is still draining: a timeout after a valid InferenceBox has
        # been created would otherwise delay every remaining symbol. The idle
        # branch continues to perform the deferred cleanup once the queue is
        # caught up.
        if omitted_symbol_count or fairness_drain.get("active"):
            maintenance = {
                "status": "deferred",
                "reason": "대기열이 남아 있어 TypeDB 세대 정리를 유휴 주기로 이월했습니다.",
                "omittedSymbolCount": omitted_symbol_count,
                "fairnessDrainActive": bool(fairness_drain.get("active")),
            }
        else:
            maintenance = self.run_maintenance_if_due()
        stage_timing["maintenanceMs"] = int((time.perf_counter() - maintenance_started) * 1000)
        status = "partial" if blocked_request_ids or mailbox_ack_error else "ok"
        post_mailbox = self.mailbox_summary()
        post_mailbox.update({
            "storedPendingEntryCount": int(post_mailbox.get("pendingEntryCount") or 0),
            # This turn has acknowledged the selected mailbox revisions. A
            # newly ingressed event may arrive concurrently and is counted on
            # the next durable read rather than guessed here.
            "actionablePendingEntryCount": max(
                0,
                int(queue_metadata["mailbox"].get("actionablePendingEntryCount") or 0)
                - len(mailbox_cursor_requests),
            ),
            "semanticSupersededEntryCount": int(
                queue_metadata["mailbox"].get("semanticSupersededEntryCount") or 0
            ),
            "stalePreviewEntryCount": int(queue_metadata["mailbox"].get("stalePreviewEntryCount") or 0),
        })
        queue_metadata["mailbox"] = post_mailbox
        queue_metadata["mailboxPendingEntryCount"] = int(
            queue_metadata["mailbox"].get("actionablePendingEntryCount") or 0
        )
        return {
            "status": status,
            "processedCount": len(trigger_event_ids),
            "scheduledRequestCount": len(selected_requests),
            "completedEventCount": len(progress_result.get("completedEventIds") or []) + len(mailbox_completion.get("completed") or []),
            "partialEventCount": len(progress_result.get("partialEventIds") or []) + max(0, len(mailbox_entries) - len(mailbox_completion.get("completed") or [])),
            "coalescedEventCount": len(
                set(durable_superseded_ids)
                | set(progress_result.get("supersededEventIds") or [])
                | set(terminal_mailbox.get("discarded") or [])
                | set(stale_terminal.get("discarded") or [])
                | set(mailbox_completion.get("discarded") or [])
            ),
            "alertCount": len(alerts or []),
            "symbols": symbols,
            "maxSymbolsPerRun": self.effective_max_symbols_per_run(),
            "configuredMaxSymbolsPerRun": self.max_symbols_per_run(),
            "nativeTypeDbTargetSymbolLimit": self.native_typedb_target_symbol_limit() if self.native_typedb_rule_execution_enabled() else None,
            "omittedSymbolCount": omitted_symbol_count,
            "accountIds": [item for item in account_ids if item],
            "ruleCandidateResult": rule_candidate_result,
            "refreshedResearchRunIds": research_refresh.get("refreshedRunIds") or [],
            "refreshedResearchRequestEventIds": sorted(refreshed_research_request_ids),
            "reconciledResearchRequestEventIds": sorted(reconciled_research_request_ids),
            "reconciledResearchHandoffTerminalEvents": research_handoff_terminal,
            "pendingResearchHandoffCount": int(work.get("pendingResearchHandoffCount") or 0),
            "blockedResearchRunIds": research_refresh.get("blockedRunIds") or [],
            "blockedResearchRequestEventIds": sorted(blocked_request_ids),
            "unresolvedResearchHandoffRequestEventIds": sorted(
                {
                    str(event_id or "").strip()
                    for event_id in research_refresh.get("blockedRequestEventIds") or []
                    if str(event_id or "").strip()
                } - selected_request_ids
            ),
            "researchGenerationRefresh": research_refresh,
            "projectionOutcomes": projection_outcomes,
            "lastProjectionRuntime": self.last_projection_runtime(),
            "reasoningContext": reasoning_context,
            "mailboxAcknowledgeError": mailbox_ack_error,
            "maintenance": maintenance,
            "stageTiming": stage_timing,
            "deferredReason": (
                "검증 근거가 같은 계정 월드의 새 ABox/InferenceBox 세대에 정렬될 때까지 해당 리서치 요청을 유지합니다."
                if blocked_request_ids
                else ("영속 추론 메일박스 완료 기록 실패로 최신 원본을 보존한 채 재시도합니다: " + mailbox_ack_error if mailbox_ack_error else "")
            ),
            **queue_metadata,
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

    def research_generation_refresh_results(
        self,
        requests: Iterable[object],
        projection_results: Dict[str, object] = None,
    ) -> Dict[str, object]:
        """Apply a research run only after its own account world advanced.

        The monitor may project several accounts in one worker cycle.  A
        general successful cycle is not proof that a research run's evidence
        reached the matching portfolio world, so the original generation and
        the newly aligned generation are compared before the run is promoted.
        """
        outcome = {
            "refreshedRunIds": [],
            "refreshedRequestEventIds": [],
            "blockedRunIds": [],
            "blockedRequestEventIds": [],
            "transitions": [],
        }
        if not self.research_store or not hasattr(self.research_store, "mark_reasoning_refreshed"):
            return outcome
        projections = projection_results if isinstance(projection_results, dict) else {}
        seen_run_ids = set()
        for event in requests or []:
            payload = event_payload(event)
            run_id = str(payload.get("researchRunId") or "").strip()
            event_id = str(getattr(event, "event_id", "") or "").strip()
            if not run_id or run_id in seen_run_ids:
                continue
            seen_run_ids.add(run_id)
            handoff_payload = payload.get("reasoningHandoff")
            if not isinstance(handoff_payload, dict) or not handoff_payload:
                # Historical events did not contain a generation contract. They
                # remain readable during the rolling deployment, while all new
                # research events must pass the strict branch below.
                if self.persist_research_refresh(run_id, True, None):
                    outcome["refreshedRunIds"].append(run_id)
                    if event_id:
                        outcome["refreshedRequestEventIds"].append(event_id)
                continue
            handoff = ResearchReasoningHandoff.from_dict(handoff_payload)
            applied_generation, generation_reason = self.applied_generation_for_research_request(
                payload,
                handoff,
                projections,
            )
            updated_handoff = complete_reasoning_handoff(
                handoff,
                applied_generation,
                generation_reason,
            )
            refreshed = updated_handoff.applied()
            persisted = self.persist_research_refresh(run_id, refreshed, updated_handoff)
            transition = {
                "runId": run_id,
                "requestEventId": event_id,
                "status": updated_handoff.status,
                "reason": updated_handoff.reason,
                "sourceGeneration": updated_handoff.source_generation.to_dict(),
                "appliedGeneration": updated_handoff.applied_generation.to_dict(),
                "persisted": bool(persisted),
            }
            outcome["transitions"].append(transition)
            if refreshed and persisted:
                outcome["refreshedRunIds"].append(run_id)
                if event_id:
                    outcome["refreshedRequestEventIds"].append(event_id)
                continue
            outcome["blockedRunIds"].append(run_id)
            if event_id:
                outcome["blockedRequestEventIds"].append(event_id)
        outcome["refreshedRunIds"] = sorted(set(outcome["refreshedRunIds"]))
        outcome["refreshedRequestEventIds"] = sorted(set(outcome["refreshedRequestEventIds"]))
        outcome["blockedRunIds"] = sorted(set(outcome["blockedRunIds"]))
        outcome["blockedRequestEventIds"] = sorted(set(outcome["blockedRequestEventIds"]))
        return outcome

    def mark_research_runs_refreshed(
        self,
        requests: Iterable[object],
        projection_results: Dict[str, object] = None,
    ) -> List[str]:
        return self.research_generation_refresh_results(
            requests,
            projection_results,
        ).get("refreshedRunIds") or []

    def persist_research_refresh(
        self,
        run_id: str,
        refreshed: bool,
        handoff: ResearchReasoningHandoff = None,
    ) -> bool:
        marker = getattr(self.research_store, "mark_reasoning_refreshed", None)
        if not callable(marker):
            return False
        try:
            if handoff is not None:
                result = marker(run_id, refreshed, handoff.to_dict())
            else:
                result = marker(run_id, refreshed)
        except TypeError:
            try:
                result = marker(run_id, refreshed)
            except Exception:  # noqa: BLE001 - a run audit failure never invalidates TypeDB's active generation.
                return False
        except Exception:  # noqa: BLE001 - a run audit failure never invalidates TypeDB's active generation.
            return False
        return bool(result)

    def applied_generation_for_research_request(
        self,
        payload: Dict[str, object],
        handoff: ResearchReasoningHandoff,
        projection_results: Dict[str, object],
    ) -> Tuple[ReasoningGeneration, str]:
        account_id = str(payload.get("accountId") or "").strip()
        if not account_id:
            return ReasoningGeneration(), "리서치 재추론 요청에 계정 식별자가 없어 다른 계정의 세대를 연결하지 않습니다."
        raw_result = projection_results.get(account_id)
        if not isinstance(raw_result, dict):
            for result in projection_results.values():
                if not isinstance(result, dict):
                    continue
                world = result.get("ontologyWorld") if isinstance(result.get("ontologyWorld"), dict) else {}
                if str(world.get("accountId") or "").strip() == account_id:
                    raw_result = result
                    break
        if not isinstance(raw_result, dict):
            return ReasoningGeneration(), "이 리서치 계정 월드의 ABox 투영 결과를 찾지 못해 재시도합니다."
        result = dict(raw_result)
        projection_status = str(result.get("status") or "").strip().lower()
        accepted_statuses = {
            "ok",
            "partial",
            "unchanged-material-facts",
            "unchanged-material-facts-reasoning-retry",
        }
        if projection_status not in accepted_statuses:
            return ReasoningGeneration(), str(result.get("reason") or "이 계정 월드의 ABox 투영이 완료되지 않았습니다.")[:220]
        inference = result.get("inferenceBox") if isinstance(result.get("inferenceBox"), dict) else {}
        world = result.get("ontologyWorld") if isinstance(result.get("ontologyWorld"), dict) else {}
        generation = ReasoningGeneration.from_dict({
            **inference,
            "worldId": inference.get("worldId") or world.get("worldId") or "",
        })
        if not generation.complete():
            return generation, str(
                inference.get("reason")
                or "새 TypeDB InferenceBox가 활성 ABox Manifest와 정렬되지 않았습니다."
            )[:220]
        result_abox_id = str(result.get("aboxSnapshotId") or "").strip()
        if result_abox_id and result_abox_id != generation.source_abox_snapshot_id:
            return generation, "ABox 투영 결과와 InferenceBox의 원본 Manifest가 달라 재추론 결과를 연결하지 않습니다."
        expected_world = str(handoff.source_generation.world_id or "").strip()
        if expected_world and generation.world_id != expected_world:
            return generation, "리서치 시작 시점과 다른 계정 월드의 InferenceBox 결과입니다."
        return generation, ""

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
        work = self.pending_work(self.batch_size(), hydrate_mailbox=False)
        pending, stale_preview = self.split_stale_requests(work.get("requests") or [])
        cursor_payload = self.cursor_payload()
        progress = self.event_symbol_progress()
        batch_plan = self.reasoning_batch_plan(pending)
        batches, next_symbols, omitted_count = self.request_symbol_batches(
            pending,
            max_symbols_override=int(batch_plan.get("targetSymbolLimit") or 0),
        )
        selected_request_ids = {
            str(event_id or "").strip()
            for event_id in batches
            if str(event_id or "").strip()
        }
        selected_requests = [
            event for event in pending
            if str(getattr(event, "event_id", "") or "").strip() in selected_request_ids
        ]
        pending_symbols = self.request_symbols(pending)
        fairness_queue = self.fairness_queue(pending_symbols, cursor_payload)
        fairness_drain = self.fairness_drain_state(next_symbols, cursor_payload)
        queue_dispatch = self.queue_dispatch_summary(
            pending,
            selected_requests,
            next_symbols,
            cursor_payload=cursor_payload,
            fairness_drain=fairness_drain,
            omitted_symbol_count=omitted_count,
            batch_plan=batch_plan,
        )
        storage_guard = self.storage_guard_state()
        projection_circuit = self.projection_circuit_state()
        execution_timeout_guard = self.execution_timeout_guard_display_state(cursor_payload)
        execution_timeout_remaining = self.execution_timeout_guard_remaining_seconds(cursor_payload)
        mailbox = self.mailbox_actionability_summary(work, pending, stale_preview)
        queue_status = "healthy"
        queue_reason = ""
        if not storage_guard.get("ready"):
            queue_status = "blocked"
            queue_reason = str(storage_guard.get("reason") or "TypeDB 저장소 여유 공간을 확인하지 못했습니다.")
        elif str(projection_circuit.get("status") or "") == "open":
            queue_status = "blocked"
            queue_reason = str(projection_circuit.get("lastFailureReason") or "TypeDB 추론 회로가 복구 대기 중입니다.")
        elif execution_timeout_remaining > 0:
            queue_status = "blocked"
            queue_reason = str(
                execution_timeout_guard.get("reason")
                or "이전 TypeDB 추론 실행이 시간 상한을 넘어 안전 재시도 대기 중입니다."
            )
        elif mailbox.get("status") == "error":
            queue_status = "degraded"
            queue_reason = str(mailbox.get("reason") or "영속 추론 메일박스 상태를 읽지 못했습니다.")
        elif any(item.get("state") == "overdue" for item in fairness_queue):
            queue_status = "degraded"
            queue_reason = "대기 한도를 넘긴 종목이 있어 우선 처리 대기열을 비우고 있습니다."
        return {
            "enabled": self.enabled(),
            "pendingCount": len(pending),
            "batchSize": self.batch_size(),
            "maxSymbolsPerRun": self.effective_max_symbols_per_run(),
            "configuredMaxSymbolsPerRun": self.max_symbols_per_run(),
            "nativeTypeDbTargetSymbolLimit": self.native_typedb_target_symbol_limit() if self.native_typedb_rule_execution_enabled() else None,
            "coherentSnapshotEnabled": self.coherent_snapshot_enabled(),
            "coherentSnapshotMaxSymbols": self.coherent_snapshot_max_symbols(),
            "processedCount": len(self.cursor_store.processed_event_ids()),
            "rawPendingCount": int(work.get("rawRequestCount") or len(pending)),
            "effectivePendingCount": len(pending),
            "coalescedPendingEventCount": len(work.get("coalescedEventIds") or []),
            "mailboxPendingEntryCount": int(mailbox.get("actionablePendingEntryCount") or 0),
            "mailboxStoredEntryCount": int(mailbox.get("storedPendingEntryCount") or 0),
            "semanticSupersededMailboxEntryCount": int(
                work.get("semanticSupersededMailboxEntryCount") or 0
            ),
            "mailboxSemanticCompaction": dict(work.get("mailboxSemanticCompaction") or {}),
            "stalePreviewRequestCount": len(stale_preview),
            "mailbox": mailbox,
            "sourceFreshness": {
                "enabled": self.source_freshness_enabled(),
                "realtimeEventMaxAgeMinutes": self.realtime_event_max_age_minutes(),
                "researchEventMaxAgeMinutes": self.research_event_max_age_minutes(),
            },
            "pendingSymbols": pending_symbols,
            "nextSymbols": next_symbols,
            "nextOmittedSymbolCount": omitted_count,
            "adaptiveBatchPlan": batch_plan,
            "fairnessMaxWaitSeconds": self.fairness_max_wait_seconds(),
            "fairnessDrainEnabled": self.fairness_drain_enabled(),
            "fairnessDrainMinIntervalSeconds": self.fairness_drain_min_interval_seconds(),
            "fairnessDrainActive": bool(fairness_drain.get("active")),
            "fairnessDrainSymbols": list(fairness_drain.get("symbols") or []),
            "fairnessDrainReason": str(fairness_drain.get("reason") or ""),
            "unseenPendingSymbolCount": len([item for item in fairness_queue if item.get("state") == "unseen"]),
            "overduePendingSymbolCount": len([item for item in fairness_queue if item.get("state") == "overdue"]),
            "fairnessQueue": fairness_queue[:20],
            "partialEventCount": len(progress),
            "ruleCandidateAiEnabled": self.rule_candidate_ai_enabled(),
            "ruleCandidateAiDue": self.rule_candidate_due(),
            "projectionBackpressureEnabled": self.projection_backpressure_enabled(),
            "configuredProjectionCooldownSeconds": self.projection_min_interval_seconds(pending),
            "effectiveProjectionCooldownSeconds": self.effective_projection_min_interval_seconds(pending, cursor_payload, next_symbols),
            "lastProjectionRuntime": self.last_projection_runtime(cursor_payload),
            "ontologyMaintenance": self.maintenance_state(cursor_payload),
            "projectionCircuit": projection_circuit,
            "projectionCircuitRetryAfterSeconds": self.projection_circuit_remaining_seconds(),
            "executionProcessIsolationEnabled": self.process_isolation_enabled(),
            "executionTimeoutSeconds": self.execution_timeout_seconds(),
            "executionTimeoutGraceSeconds": self.execution_timeout_grace_seconds(),
            "executionTimeoutBackoffSeconds": self.execution_timeout_backoff_seconds(),
            "executionTimeoutGuard": execution_timeout_guard,
            "executionTimeoutRetryAfterSeconds": execution_timeout_remaining,
            "executionTimeoutGuardCooldownElapsed": (
                str(execution_timeout_guard.get("status") or "") == "cooldown-elapsed"
            ),
            "storageGuard": storage_guard,
            "projectionCoordinator": self.projection_coordinator_state(),
            # Scheduler readiness, durable probe connectivity, and actual
            # queue-delay SLO are intentionally separate signals. A readable
            # queue can still be delayed, and a normal fairness drain is not
            # a TypeDB failure.
            "queueHealth": {"status": queue_status, "reason": queue_reason, "scope": "scheduler-readiness"},
            "probeHealth": {
                "status": "degraded" if mailbox.get("status") == "error" else "healthy",
                "reason": str(mailbox.get("reason") or "") if mailbox.get("status") == "error" else "",
                "scope": "durable-queue-read-model",
            },
            "queueDispatch": queue_dispatch,
            "queueDelayHealth": (
                dict(cursor_payload.get("queueDelayHealth") or {})
                if isinstance(cursor_payload.get("queueDelayHealth"), dict)
                else {}
            ),
            "executionTelemetry": self.execution_telemetry(cursor_payload),
        }
