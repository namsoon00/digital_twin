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
    "investment-calendar-update": 5,
    "market-data-update": 4,
    "kis-realtime-websocket": 3,
    "kis-realtime-update": 3,
    "portfolio-snapshot-update": 2,
    "data-update": 1,
}

# A KIS or market-data event represents a new observation of a complete live
# portfolio snapshot. It is safe to replace an older event only when the newer
# event covers every older subject and fact family. Research/calendar events
# deliberately never enter this path because their evidence is not fungible.
COALESCIBLE_REALTIME_TRIGGERS = {
    "market-data-update",
    "kis-realtime-update",
    "kis-realtime-websocket",
    "portfolio-snapshot-update",
}


def materiality_assessments(event: object) -> List[Dict[str, object]]:
    raw = event_payload(event).get("materialityAssessments") or []
    if isinstance(raw, dict):
        raw = list(raw.values())
    return [dict(item) for item in raw if isinstance(item, dict)]


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


def realtime_coalescing_key(event: object) -> Tuple[str, Tuple[str, ...]]:
    """Return a conservative replacement key for redundant live observations."""
    payload = event_payload(event)
    trigger = str(payload.get("trigger") or "").strip()
    review_level = event_review_level(event)
    if trigger not in COALESCIBLE_REALTIME_TRIGGERS:
        return ()
    if REVIEW_LEVEL_ORDER.get(review_level, 0) > REVIEW_LEVEL_ORDER["check"]:
        return ()
    symbols = event_symbols(event)
    if not symbols:
        return ()
    fact_types = tuple(sorted({str(item or "").strip() for item in payload.get("factTypes") or [] if str(item or "").strip()}))
    return trigger, fact_types


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

    def projection_circuit_state(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        payload = self.cursor_payload() if payload is None else dict(payload or {})
        raw = payload.get("projectionCircuit") if isinstance(payload.get("projectionCircuit"), dict) else {}
        return dict(raw or {})

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

    def projection_due(self, requests: Iterable[object], payload: Dict[str, object] = None) -> bool:
        return self.timestamp_due(
            self.last_successful_projection_at(payload),
            self.projection_min_interval_seconds(requests),
        )

    def projection_cooldown_remaining_seconds(
        self,
        requests: Iterable[object],
        payload: Dict[str, object] = None,
    ) -> int:
        return self.timestamp_remaining_seconds(
            self.last_successful_projection_at(payload),
            self.projection_min_interval_seconds(requests),
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
            and (not event_symbols(event) or self.due_event_symbols(event, progress, cursor_payload, priority_symbols))
        ]
        events.sort(
            key=lambda event: (
                *event_order_key(event, priority_symbols),
                1 if str(getattr(event, "event_id", "") or "") in progress else 0,
                getattr(event, "occurred_at", ""),
                getattr(event, "event_id", ""),
            ),
            reverse=True,
        )
        return events[: max(1, int(limit or self.batch_size()))]

    def pending_work(self, limit: int = 0) -> Dict[str, object]:
        """Collapse redundant, lower-materiality realtime snapshot requests.

        A newer market observation may supersede an older one only when it
        includes all of the older event's symbols and exactly the same fact
        family. The older cursor is advanced only after the newer snapshot has
        completed TypeDB projection and inference.
        """
        requests = self.pending_requests(limit)
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
            "rawRequestCount": len(requests),
            "coalescedEventIds": sorted(superseded_ids),
            "supersededByLead": {
                lead_id: sorted(set(event_ids))
                for lead_id, event_ids in superseded_by_lead.items()
            },
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
        for event in requests or []:
            for symbol in event_symbols(event):
                if symbol and symbol not in symbols:
                    symbols.append(symbol)
        return symbols

    def request_symbol_batches(self, requests: Iterable[object]) -> Tuple[Dict[str, List[str]], List[str], int]:
        # Snapshot construction still preserves all portfolio and market
        # entities.  Only the native schema-function subjects are serialized;
        # a multi-symbol function batch can otherwise exceed the realtime
        # boundary and repeatedly roll back the candidate ABox generation.
        max_symbols = self.effective_max_symbols_per_run()
        cursor_payload = self.cursor_payload()
        progress = self.event_symbol_progress(cursor_payload)
        priority_symbols = self.priority_symbols()
        batches: Dict[str, List[str]] = {}
        candidates: Dict[str, Tuple[Tuple[int, int, int, int, int, int], str]] = {}
        requested_events = list(requests or [])

        if self.coherent_snapshot_enabled():
            event_candidates = []
            all_due_symbols = []
            global_events = []
            for event_index, event in enumerate(requested_events):
                event_id = str(getattr(event, "event_id", "") or "").strip()
                all_symbols = event_symbols(event)
                if not all_symbols:
                    if event_id:
                        global_events.append((event_index, event))
                    continue
                due_symbols = self.due_event_symbols(event, progress, cursor_payload, priority_symbols)
                if not due_symbols:
                    continue
                for symbol in due_symbols:
                    if symbol not in all_due_symbols:
                        all_due_symbols.append(symbol)
                fact_types = {str(item or "").strip() for item in event_payload(event).get("factTypes") or []}
                rank = (
                    max([int(priority_symbols.get(symbol, 0) or 0) for symbol in due_symbols] or [0]),
                    REVIEW_LEVEL_ORDER.get(event_review_level(event), 0),
                    TRIGGER_ORDER.get(str(event_payload(event).get("trigger") or "data-update").strip(), 0),
                    1 if "ResearchEvidence" in fact_types else 0,
                    event_time_key(event),
                    -event_index,
                )
                event_candidates.append((rank, event_id, due_symbols))
            if event_candidates:
                _rank, event_id, due_symbols = max(event_candidates, key=lambda item: item[0])
                snapshot_limit = self.coherent_snapshot_max_symbols()
                # Coherence refers to the full ABox retained for context, not
                # an unbounded schema-function request. Native TypeDB rules
                # still run only for the configured incremental subjects so a
                # broad market event cannot monopolize the realtime worker.
                if max_symbols > 0:
                    snapshot_limit = min(snapshot_limit, max_symbols)
                selected = list(due_symbols[:snapshot_limit])
                batches[event_id] = selected
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
            remaining = self.due_event_symbols(event, progress, cursor_payload, priority_symbols)
            if not event_symbols(event):
                batches[event_id] = []
                continue
            for symbol_index, symbol in enumerate(remaining):
                rank = (
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
            if not event_symbols(event):
                continue
            selected_for_event = [
                symbol
                for symbol in self.due_event_symbols(event, progress, cursor_payload, priority_symbols)
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
        completed_event_ids: List[str] = []
        partial_event_ids: List[str] = []
        for event in requests or []:
            event_id = str(getattr(event, "event_id", "") or "").strip()
            if not event_id:
                continue
            all_symbols = event_symbols(event)
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
            "deferred-inference-write-lease",
            "deferred-pending-scoped-manifest",
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

        def add_result(account_id: object, stage: str, status: str, reason: object) -> None:
            item = {
                "accountId": str(account_id or ""),
                "stage": stage,
                "status": status,
                "reason": str(reason or "TypeDB ABox 투영이 완료되지 않았습니다."),
            }
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

    def mark_successful_projection(self) -> None:
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
        self.save_cursor_payload(payload)

    def run_once(self, limit: int = 0, force: bool = False) -> Dict[str, object]:
        if not self.enabled():
            return {"status": "disabled", "processedCount": 0, "alertCount": 0}
        work = self.pending_work(limit)
        requests = list(work.get("requests") or [])
        if not requests:
            return {
                "status": "idle",
                "processedCount": 0,
                "alertCount": 0,
                "coalescedEventCount": len(work.get("coalescedEventIds") or []),
            }
        symbol_batches, symbols, omitted_symbol_count = self.request_symbol_batches(requests)
        cursor_payload = self.cursor_payload()
        circuit_remaining = self.projection_circuit_remaining_seconds(cursor_payload)
        if circuit_remaining > 0 and not force:
            circuit = self.projection_circuit_state(cursor_payload)
            return {
                "status": "circuit-open",
                "processedCount": 0,
                "alertCount": 0,
                "symbols": symbols,
                "retryAfterSeconds": circuit_remaining,
                "deferredReason": str(circuit.get("lastFailureReason") or "TypeDB projection circuit is open."),
                "projectionCircuit": circuit,
                "coalescedEventCount": len(work.get("coalescedEventIds") or []),
            }
        if not force and not self.projection_due(requests, cursor_payload):
            retry_after_seconds = self.projection_cooldown_remaining_seconds(requests, cursor_payload)
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
                "projectionCooldownSeconds": self.projection_min_interval_seconds(requests),
                "coalescedEventCount": len(work.get("coalescedEventIds") or []),
            }
        runner = self.monitor_runner_factory()
        if "symbol_filter" in inspect.signature(runner.run_once).parameters:
            alerts = runner.run_once(force=force, symbol_filter=symbols)
        else:
            alerts = runner.run_once(force=force)
        projection_gate = self.projection_gate(runner)
        if not projection_gate.get("ready"):
            self.mark_projection_attempt(symbols)
            if projection_gate.get("retryable"):
                return {
                    "status": "deferred",
                    "processedCount": 0,
                    "alertCount": len(alerts or []),
                    "symbols": symbols,
                    "maxSymbolsPerRun": self.effective_max_symbols_per_run(),
                    "configuredMaxSymbolsPerRun": self.max_symbols_per_run(),
                    "nativeTypeDbTargetSymbolLimit": self.native_typedb_target_symbol_limit() if self.native_typedb_rule_execution_enabled() else None,
                    "omittedSymbolCount": omitted_symbol_count,
                    "retryAfterSeconds": self.projection_retry_seconds(),
                    "deferredReason": str(projection_gate.get("reason") or "TypeDB graph cycle is serialized by another writer."),
                    "projectionFailures": list(projection_gate.get("results") or []),
                    "projectionCircuit": self.projection_circuit_state(),
                    "coalescedEventCount": len(work.get("coalescedEventIds") or []),
                }
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
                "coalescedEventCount": len(work.get("coalescedEventIds") or []),
            }
        self.mark_successful_projection()
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
                + (f" 네이티브 추론 대상 상한 {self.effective_max_symbols_per_run()}개가 적용되어 {omitted_symbol_count}개는 다음 사이클로 이월했습니다." if omitted_symbol_count else "")
            ),
        )
        rule_candidate_result = self.propose_rule_candidates(symbols, requests, alerts, force=False)
        refreshed_research_runs = self.mark_research_runs_refreshed(requests)
        self.publish(completed)
        progress_result = self.mark_requests_processed(
            requests,
            symbol_batches,
            superseded_by_lead=work.get("supersededByLead"),
        )
        self.mark_symbols_reasoned(symbols)
        return {
            "status": "ok",
            "processedCount": len(trigger_event_ids),
            "completedEventCount": len(progress_result.get("completedEventIds") or []),
            "partialEventCount": len(progress_result.get("partialEventIds") or []),
            "coalescedEventCount": len(progress_result.get("supersededEventIds") or []),
            "alertCount": len(alerts or []),
            "symbols": symbols,
            "maxSymbolsPerRun": self.effective_max_symbols_per_run(),
            "configuredMaxSymbolsPerRun": self.max_symbols_per_run(),
            "nativeTypeDbTargetSymbolLimit": self.native_typedb_target_symbol_limit() if self.native_typedb_rule_execution_enabled() else None,
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
        work = self.pending_work(self.batch_size())
        pending = list(work.get("requests") or [])
        progress = self.event_symbol_progress()
        _batches, next_symbols, omitted_count = self.request_symbol_batches(pending)
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
            "coalescedPendingEventCount": len(work.get("coalescedEventIds") or []),
            "pendingSymbols": self.request_symbols(pending),
            "nextSymbols": next_symbols,
            "nextOmittedSymbolCount": omitted_count,
            "partialEventCount": len(progress),
            "ruleCandidateAiEnabled": self.rule_candidate_ai_enabled(),
            "ruleCandidateAiDue": self.rule_candidate_due(),
            "projectionCircuit": self.projection_circuit_state(),
            "projectionCircuitRetryAfterSeconds": self.projection_circuit_remaining_seconds(),
        }
