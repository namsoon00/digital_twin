import inspect
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List

from ..domain.accounts import AccountConfig
from ..domain.events import alerts_detected_event, monitoring_cycle_completed_event, snapshot_collected_event
from ..domain.portfolio import AccountSnapshot, AlertEvent
from ..domain.repositories import MonitorAccountJob, MonitorAccountJobRepository, MonitorStateRepository, MonitoringCycleRecorder, OntologyProjectionRecorder, SnapshotMonitor


class MonitorRunner:
    def __init__(
        self,
        accounts: Iterable[AccountConfig],
        store: MonitorStateRepository,
        monitor: SnapshotMonitor,
        snapshot_builder: Callable[[AccountConfig], AccountSnapshot],
        event_sender: Callable,
        event_publisher=None,
        cycle_recorder: MonitoringCycleRecorder = None,
        ontology_projection_recorder: OntologyProjectionRecorder = None,
        hypothesis_lifecycle_service=None,
        account_job_store: MonitorAccountJobRepository = None,
        account_job_batch_size: int = 10,
        account_job_interval_seconds: int = 180,
        account_job_lock_seconds: int = 600,
        worker_id: str = "",
        progress_callback: Callable[[str, Dict[str, object]], None] = None,
    ):
        self.accounts = list(accounts)
        self.account_map = {account.account_id: account for account in self.accounts}
        self.store = store
        self.monitor = monitor
        self.snapshot_builder = snapshot_builder
        self.event_sender = event_sender
        self.event_publisher = event_publisher
        self.cycle_recorder = cycle_recorder
        self.ontology_projection_recorder = ontology_projection_recorder
        self.hypothesis_lifecycle_service = hypothesis_lifecycle_service
        self.account_job_store = account_job_store
        self.account_job_batch_size = max(1, int(account_job_batch_size or 10))
        self.account_job_interval_seconds = max(30, int(account_job_interval_seconds or 180))
        self.account_job_lock_seconds = max(60, int(account_job_lock_seconds or 600))
        self.worker_id = worker_id or ("monitor-" + uuid.uuid4().hex[:12])
        self.progress_callback = progress_callback
        # The reasoning worker advances its event cursor only after this
        # projection has a usable TypeDB result.
        self.last_ontology_projection_results: Dict[str, Dict[str, object]] = {}

    def run_once(self, dry_run: bool = False, force: bool = False, symbol_filter: Iterable[str] = None) -> List[AlertEvent]:
        if self.use_account_job_queue(dry_run, force, symbol_filter):
            return self.run_due_account_jobs()
        return self.run_all_accounts_once(dry_run=dry_run, force=force, symbol_filter=symbol_filter)

    def progress(self, stage: str, **payload) -> None:
        if not self.progress_callback:
            return
        try:
            self.progress_callback(stage, dict(payload or {}))
        except Exception:
            return

    def use_account_job_queue(self, dry_run: bool, force: bool, symbol_filter: Iterable[str] = None) -> bool:
        return bool(self.account_job_store) and not dry_run and not force and not list(symbol_filter or [])

    def run_all_accounts_once(self, dry_run: bool = False, force: bool = False, symbol_filter: Iterable[str] = None) -> List[AlertEvent]:
        self.last_ontology_projection_results = {}
        self.progress("monitor.start", accountCount=len(self.accounts), dryRun=bool(dry_run), force=bool(force))
        all_events: List[AlertEvent] = []
        snapshots = []
        allowed_symbols = set(str(symbol or "").upper().strip() for symbol in (symbol_filter or []) if str(symbol or "").strip())
        for account in self.accounts:
            self.progress("account.start", accountId=account.account_id, label=account.label)
            snapshot, events = self.collect_account_events(account, allowed_symbols=allowed_symbols, dry_run=dry_run, force=force)
            self.progress("account.done", accountId=account.account_id, eventCount=len(events), mode=snapshot.mode, status=snapshot.status)
            snapshots.append(snapshot)
            if not self.use_cycle_recorder(dry_run) and snapshot.has_live_account_data():
                self.publish(snapshot_collected_event(snapshot))
            all_events.extend(events)
        if self.use_cycle_recorder(dry_run):
            for snapshot in snapshots:
                snapshot.metadata.pop("previousMonitorState", None)
                snapshot.metadata.pop("monitorStateHistory", None)
            self.cycle_recorder.record_cycle(
                [account.account_id for account in self.accounts],
                snapshots,
                all_events,
                dry_run=dry_run,
            )
            if not all_events:
                self.progress("monitor.no_events")
            self.progress("monitor.completed", snapshotCount=len(snapshots), eventCount=len(all_events), cycleRecorder=True)
            return all_events
        if all_events:
            alert_event = alerts_detected_event(all_events)
            self.publish(alert_event)
            self.progress("send.start", eventCount=len(all_events), dryRun=bool(dry_run))
            result = self.send_alert_events(all_events, dry_run=dry_run, source_event=alert_event)
            self.progress("send.done", delivered=bool(result.delivered), label=getattr(result, "label", ""), reason=getattr(result, "reason", ""))
            self.publish_cycle_completed(snapshots, all_events, dry_run, result.delivered)
            if dry_run:
                self.progress("monitor.completed", snapshotCount=len(snapshots), eventCount=len(all_events), delivered=bool(result.delivered))
                return all_events
            if result.delivered:
                self.store.mark_sent(all_events)
        else:
            self.progress("monitor.no_events")
            self.publish_cycle_completed(snapshots, all_events, dry_run, False)
        if not dry_run:
            for snapshot in snapshots:
                snapshot.metadata.pop("previousMonitorState", None)
                snapshot.metadata.pop("monitorStateHistory", None)
                self.store.save_snapshot(snapshot)
            self.store.write()
        self.progress("monitor.completed", snapshotCount=len(snapshots), eventCount=len(all_events))
        return all_events

    def run_due_account_jobs(self) -> List[AlertEvent]:
        self.account_job_store.sync_accounts(self.accounts, self.account_job_interval_seconds)
        jobs = self.account_job_store.claim_due(
            limit=self.account_job_batch_size,
            worker_id=self.worker_id,
            lock_seconds=self.account_job_lock_seconds,
            default_interval_seconds=self.account_job_interval_seconds,
        )
        if not jobs:
            self.progress("jobs.none_due")
            return []
        self.progress("jobs.claimed", jobCount=len(jobs))
        all_events: List[AlertEvent] = []
        for job in jobs:
            account = self.account_map.get(job.account_id)
            if not account:
                self.account_job_store.mark_failed(
                    job.account_id,
                    "계정 설정을 찾을 수 없습니다.",
                    self.next_account_run_at(self.account_job_interval_seconds),
                )
                continue
            try:
                events = self.run_single_account_job(account, job)
                all_events.extend(events)
            except Exception as error:  # noqa: BLE001 - account-level failures must not block other accounts.
                self.account_job_store.mark_failed(
                    job.account_id,
                    str(error)[:500],
                    self.next_account_run_at(self.failure_backoff_seconds(job)),
                )
                self.progress("account.failed", accountId=job.account_id, reason=str(error)[:180])
        if not all_events:
            self.progress("monitor.no_events")
        return all_events

    def run_single_account_job(self, account: AccountConfig, job: MonitorAccountJob) -> List[AlertEvent]:
        self.last_ontology_projection_results = {}
        self.progress("account.start", accountId=account.account_id, label=account.label, job=True)
        snapshot, events = self.collect_account_events(account, allowed_symbols=set(), dry_run=False, force=False)
        if self.cycle_recorder:
            snapshot.metadata.pop("previousMonitorState", None)
            snapshot.metadata.pop("monitorStateHistory", None)
            self.cycle_recorder.record_cycle([account.account_id], [snapshot], events, dry_run=False)
        else:
            if snapshot.has_live_account_data():
                self.publish(snapshot_collected_event(snapshot))
            if events:
                alert_event = alerts_detected_event(events)
                self.publish(alert_event)
                result = self.send_alert_events(events, dry_run=False, source_event=alert_event)
                self.publish_cycle_completed([snapshot], events, False, result.delivered)
                if result.delivered:
                    self.store.mark_sent(events)
            else:
                self.publish_cycle_completed([snapshot], events, False, False)
            self.store.save_snapshot(snapshot)
            self.store.write()
        self.account_job_store.mark_done(account.account_id, self.next_account_run_at(self.account_job_interval_seconds))
        self.progress("account.done", accountId=account.account_id, eventCount=len(events), job=True)
        return events

    def collect_account_events(
        self,
        account: AccountConfig,
        allowed_symbols: set = None,
        dry_run: bool = False,
        force: bool = False,
    ):
        self.progress("snapshot.start", accountId=account.account_id)
        snapshot = self.snapshot_builder(account)
        self.progress(
            "snapshot.done",
            accountId=account.account_id,
            mode=snapshot.mode,
            status=snapshot.status,
            positionCount=len(snapshot.positions or []),
            watchlistCount=len(snapshot.watchlist or []),
        )
        previous = self.store.previous.get(snapshot.account_id) or {}
        snapshot.metadata["previousMonitorState"] = self.compact_previous_state(previous)
        snapshot.metadata["monitorStateHistory"] = self.compact_monitor_history(
            self.load_snapshot_history(snapshot.account_id)
        )
        snapshot.metadata.setdefault("ontology", {})["previousStateAvailable"] = bool(previous)
        # Dry-run still needs graph inference to simulate the same investment
        # judgement path as live monitoring; delivery and monitor persistence
        # remain gated by dry_run below.
        self.progress("ontology_projection.start", accountId=account.account_id)
        self.record_ontology_projection(snapshot, target_symbols=allowed_symbols)
        projection = snapshot.metadata.get("ontology", {}).get("projection") if isinstance(snapshot.metadata.get("ontology"), dict) else {}
        self.progress(
            "ontology_projection.done",
            accountId=account.account_id,
            status=projection.get("status") if isinstance(projection, dict) else "",
            graphStore=projection.get("graphStore") if isinstance(projection, dict) else "",
        )
        self.record_hypothesis_lifecycle(snapshot)
        events = self.monitor.events_for_snapshot(snapshot, previous)
        if force and hasattr(self.monitor, "forced_holdings_snapshot_events"):
            events.extend(self.monitor.forced_holdings_snapshot_events(snapshot))
        self.progress("events.detected", accountId=account.account_id, eventCount=len(events))
        if allowed_symbols:
            events = self.filter_events_by_symbol(events, allowed_symbols)
            self.progress("events.filtered", accountId=account.account_id, eventCount=len(events), symbolCount=len(allowed_symbols))
        detected_events = list(events)
        events = self.monitor.apply_cadence(events, self.store, force=force)
        self.record_investment_alert_pipeline(
            snapshot,
            projection if isinstance(projection, dict) else {},
            detected_events,
            events,
            allowed_symbols=allowed_symbols,
        )
        self.last_ontology_projection_results[account.account_id] = (
            dict(projection) if isinstance(projection, dict) and projection else {"status": "missing"}
        )
        self.progress("events.ready", accountId=account.account_id, eventCount=len(events), force=bool(force))
        return snapshot, events

    def record_investment_alert_pipeline(
        self,
        snapshot: AccountSnapshot,
        projection: dict,
        detected_events: List[AlertEvent],
        ready_events: List[AlertEvent],
        allowed_symbols: set = None,
    ) -> None:
        """Keep the no-alert path observable without changing delivery policy.

        This is an operational read model. It never promotes, suppresses, or
        changes an alert; the existing cadence and notification workers retain
        ownership of delivery decisions.
        """
        inference = projection.get("inferenceBox") if isinstance(projection.get("inferenceBox"), dict) else {}
        inference_status = str(inference.get("status") or "").strip().lower()
        native_completed = bool(
            inference.get("nativeTypeDbReasoningCompleted")
            or inference.get("typedbNativeRuleEvaluationCompleted")
        )
        generation_aligned = bool(inference.get("generationAligned"))
        no_match = (
            inference_status == "empty"
            and native_completed
            and generation_aligned
            and bool(str(inference.get("sourceAboxSnapshotId") or ""))
        )
        investment_types = {"investmentInsight", "holdingTiming", "watchlistOntologySignal"}
        detected = [event for event in detected_events or [] if str(getattr(event, "message_type", "") or "") in investment_types]
        ready = [event for event in ready_events or [] if str(getattr(event, "message_type", "") or "") in investment_types]
        selected_symbols = sorted({
            str(symbol or "").upper().strip()
            for symbol in allowed_symbols or set()
            if str(symbol or "").strip()
        })
        if no_match:
            status = "no-signal"
            reason = "TypeDB native rules evaluated the current ABox successfully, but no investment relation matched."
        elif str(projection.get("status") or "").lower() not in {
            "ok", "partial", "unchanged-material-facts", "unchanged-material-facts-reasoning-retry",
        }:
            status = "blocked"
            reason = str(projection.get("reason") or "The ontology projection is not ready for investment alert generation.")
        elif detected and not ready:
            status = "cadence-suppressed"
            reason = "Investment alert candidates were detected but the local cadence policy held them back."
        elif ready:
            status = "delivery-ready"
            reason = "Investment alert candidates passed the local cadence policy and will enter the delivery path."
        else:
            status = "no-material-alert"
            reason = "The current inference did not create a material investment alert candidate."
        payload = {
            "status": status,
            "reason": reason,
            "inferenceStatus": inference_status,
            "nativeInferenceOutcome": str(inference.get("nativeInferenceOutcome") or ""),
            "nativeTypeDbReasoningCompleted": native_completed,
            "generationAligned": generation_aligned,
            "sourceAboxSnapshotId": str(inference.get("sourceAboxSnapshotId") or ""),
            "inferenceGenerationId": str(inference.get("inferenceGenerationId") or ""),
            "targetSymbols": list(inference.get("targetSymbols") or [])[:80],
            "requestedSymbols": selected_symbols,
            "detectedCandidateCount": len(detected),
            "cadenceReadyCount": len(ready),
            "detectedMessageTypes": sorted({str(getattr(event, "message_type", "") or "") for event in detected}),
            "cadenceReadyMessageTypes": sorted({str(getattr(event, "message_type", "") or "") for event in ready}),
        }
        snapshot.metadata.setdefault("ontology", {})["alertPipeline"] = payload
        if isinstance(projection, dict):
            projection["alertPipeline"] = payload

    def next_account_run_at(self, seconds: int) -> str:
        return (datetime.now(timezone.utc) + timedelta(seconds=max(1, int(seconds or 1)))).isoformat().replace("+00:00", "Z")

    def failure_backoff_seconds(self, job: MonitorAccountJob) -> int:
        attempts = max(1, int(getattr(job, "attempts", 0) or 0))
        return min(self.account_job_interval_seconds * 8, self.account_job_interval_seconds * attempts)

    def filter_events_by_symbol(self, events: List[AlertEvent], allowed_symbols: set) -> List[AlertEvent]:
        return [
            event
            for event in events
            if str(event.symbol or "").upper().strip() in allowed_symbols
        ]

    def use_cycle_recorder(self, dry_run: bool) -> bool:
        return self.cycle_recorder is not None and not dry_run

    def publish_cycle_completed(self, snapshots, events, dry_run: bool, delivered: bool) -> None:
        self.publish(monitoring_cycle_completed_event(
            [account.account_id for account in self.accounts],
            len(snapshots),
            len(events),
            dry_run,
            delivered,
        ))

    def publish(self, event) -> None:
        if self.event_publisher:
            self.event_publisher.publish(event)

    def record_ontology_projection(self, snapshot: AccountSnapshot, target_symbols: set = None) -> None:
        if not self.ontology_projection_recorder or not self.has_ontology_projection_data(snapshot):
            return
        try:
            selected_symbols = {
                str(symbol or "").upper().strip()
                for symbol in target_symbols or set()
                if str(symbol or "").strip()
            }
            if selected_symbols:
                self.ontology_projection_recorder.record_snapshot(
                    snapshot,
                    target_symbols=sorted(selected_symbols),
                )
            else:
                self.ontology_projection_recorder.record_snapshot(snapshot)
        except Exception as error:  # noqa: BLE001 - graph persistence must not block monitoring.
            result = {"saved": False, "status": "error", "reason": str(error)[:180]}
            snapshot.metadata.setdefault("ontology", {})["projection"] = result

    def persist_ontology(self, snapshot: AccountSnapshot) -> None:
        self.record_ontology_projection(snapshot)

    def record_hypothesis_lifecycle(self, snapshot: AccountSnapshot) -> None:
        if not self.hypothesis_lifecycle_service:
            return
        try:
            result = self.hypothesis_lifecycle_service.observe_snapshot(snapshot)
            self.progress(
                "hypothesis_lifecycle.done",
                accountId=snapshot.account_id,
                status=result.get("status") if isinstance(result, dict) else "",
                transitionCount=result.get("transitionCount") if isinstance(result, dict) else 0,
            )
        except Exception as error:  # noqa: BLE001 - audit persistence must not block graph-backed judgement.
            snapshot.metadata["hypothesisLifecycle"] = {
                "status": "error",
                "reason": str(error)[:180],
                "bySymbol": {},
            }
            self.progress("hypothesis_lifecycle.error", accountId=snapshot.account_id)

    def has_ontology_projection_data(self, snapshot: AccountSnapshot) -> bool:
        # A failed account request can still contain stale watchlist or market
        # payloads. They must not replace the live ABox or start a new
        # investment judgement cycle. The last verified live generation stays
        # active until a complete live account snapshot arrives.
        return snapshot.has_live_account_data()

    def compact_previous_state(self, previous: dict) -> dict:
        if not isinstance(previous, dict) or not previous:
            return {}
        return {
            "generatedAt": previous.get("generatedAt"),
            "portfolio": previous.get("portfolio") if isinstance(previous.get("portfolio"), dict) else {},
            "positions": previous.get("positions") if isinstance(previous.get("positions"), dict) else {},
            "watchlist": previous.get("watchlist") if isinstance(previous.get("watchlist"), dict) else {},
            "decisions": previous.get("decisions") if isinstance(previous.get("decisions"), dict) else {},
            "externalSignals": previous.get("externalSignals") if isinstance(previous.get("externalSignals"), dict) else {},
        }

    def load_snapshot_history(self, account_id: str) -> List[dict]:
        if hasattr(self.store, "load_history"):
            return list(self.store.load_history(account_id, limit=self.temporal_history_limit()) or [])
        return []

    def compact_monitor_history(self, history: List[dict]) -> List[dict]:
        compacted = []
        for item in history or []:
            compacted_item = self.compact_previous_state(item)
            if compacted_item:
                compacted.append(compacted_item)
        return compacted[-self.temporal_history_limit():]

    def temporal_history_limit(self) -> int:
        settings = getattr(self.monitor, "settings", None)
        settings = settings if isinstance(settings, dict) else {}
        raw = settings.get("temporalWindowHistoryLimit") or settings.get("TEMPORAL_WINDOW_HISTORY_LIMIT") or 96
        try:
            value = int(float(str(raw).strip()))
        except (TypeError, ValueError):
            value = 96
        return max(6, min(500, value))

    def send_alert_events(self, events, dry_run: bool, source_event):
        parameters = inspect.signature(self.event_sender).parameters
        if "source_event" in parameters or any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        ):
            return self.event_sender(
                events,
                dry_run=dry_run,
                accounts=self.account_map,
                source_event=source_event,
            )
        return self.event_sender(events, dry_run=dry_run, accounts=self.account_map)
