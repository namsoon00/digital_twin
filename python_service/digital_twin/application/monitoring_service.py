import inspect
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List

from ..domain.accounts import AccountConfig
from ..domain.events import alerts_detected_event, monitoring_cycle_completed_event, snapshot_collected_event
from ..domain.ontology_projection_input import (
    compact_monitor_state_for_ontology,
    frozen_monitor_state_for_reasoning,
)
from ..domain.ontology_projection_status import VERIFIED_MONITOR_SNAPSHOT_QUEUED
from ..domain.ontology_projection_status import projection_reuses_unchanged_inference
from ..domain.message_types import PORTFOLIO_ONTOLOGY_SIGNAL
from ..domain.portfolio import AccountSnapshot, AlertEvent, account_snapshot_from_monitor_state
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
        ontology_projection_enabled: bool = True,
        account_job_store: MonitorAccountJobRepository = None,
        account_job_batch_size: int = 10,
        account_job_interval_seconds: int = 180,
        account_job_lock_seconds: int = 600,
        worker_id: str = "",
        progress_callback: Callable[[str, Dict[str, object]], None] = None,
        source_snapshot_replay: bool = False,
        projection_symbol_filters_by_account: Dict[str, Iterable[str]] = None,
        portfolio_lifecycle_observer=None,
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
        # The normal monitor owns source collection and its durable commit.
        # In production the isolated ontology worker owns the expensive
        # TypeDB generation after that commit.  Keep the switch explicit so
        # compatibility callers and deliberate synchronous runs can retain
        # their existing projection behavior.
        self.ontology_projection_enabled = bool(ontology_projection_enabled)
        self.account_job_store = account_job_store
        self.account_job_batch_size = max(1, int(account_job_batch_size or 10))
        self.account_job_interval_seconds = max(30, int(account_job_interval_seconds or 180))
        self.account_job_lock_seconds = max(60, int(account_job_lock_seconds or 600))
        self.worker_id = worker_id or ("monitor-" + uuid.uuid4().hex[:12])
        self.progress_callback = progress_callback
        # ``reasoningSnapshotReplay`` is an internal source-adapter marker,
        # not persisted monitor data.  Treat replay as an explicit runner
        # mode so a stale marker can never make the ordinary monitor skip its
        # source commit and the resulting verified reasoning request.
        self.source_snapshot_replay = bool(source_snapshot_replay)
        self.projection_symbol_filters_by_account = {
            str(account_id or ""): {
                str(symbol or "").upper().strip()
                for symbol in symbols or []
                if str(symbol or "").strip()
            }
            for account_id, symbols in dict(
                projection_symbol_filters_by_account or {}
            ).items()
            if str(account_id or "")
        }
        self.portfolio_lifecycle_observer = portfolio_lifecycle_observer
        # The reasoning worker advances its event cursor only after this
        # projection has a usable TypeDB result.
        self.last_ontology_projection_results: Dict[str, Dict[str, object]] = {}
        self.last_cycle_record_result = None
        self.last_delivery_guard_result: Dict[str, object] = {}
        self.last_portfolio_lifecycle_results: Dict[str, Dict[str, object]] = {}
        self.last_detected_alert_events: List[AlertEvent] = []
        self.last_reasoning_source_states: Dict[str, Dict[str, object]] = {}
        self.last_projection_runtime_contexts: Dict[str, Dict[str, object]] = {}

    def run_once(
        self,
        dry_run: bool = False,
        force: bool = False,
        symbol_filter: Iterable[str] = None,
        reasoning_context: Dict[str, object] = None,
        delivery_guard=None,
    ) -> List[AlertEvent]:
        if self.use_account_job_queue(dry_run, force, symbol_filter):
            return self.run_due_account_jobs()
        return self.run_all_accounts_once(
            dry_run=dry_run,
            force=force,
            symbol_filter=symbol_filter,
            reasoning_context=reasoning_context,
            delivery_guard=delivery_guard,
        )

    def progress(self, stage: str, **payload) -> None:
        if not self.progress_callback:
            return
        try:
            self.progress_callback(stage, dict(payload or {}))
        except Exception:
            return

    def use_account_job_queue(self, dry_run: bool, force: bool, symbol_filter: Iterable[str] = None) -> bool:
        return bool(self.account_job_store) and not dry_run and not force and not list(symbol_filter or [])

    def run_all_accounts_once(
        self,
        dry_run: bool = False,
        force: bool = False,
        symbol_filter: Iterable[str] = None,
        reasoning_context: Dict[str, object] = None,
        delivery_guard=None,
    ) -> List[AlertEvent]:
        self.last_ontology_projection_results = {}
        self.last_detected_alert_events = []
        self.last_reasoning_source_states = {}
        self.last_projection_runtime_contexts = {}
        self.progress("monitor.start", accountCount=len(self.accounts), dryRun=bool(dry_run), force=bool(force))
        all_events: List[AlertEvent] = []
        snapshots = []
        allowed_symbols = set(str(symbol or "").upper().strip() for symbol in (symbol_filter or []) if str(symbol or "").strip())
        for account in self.accounts:
            self.progress("account.start", accountId=account.account_id, label=account.label)
            snapshot, events = self.collect_account_events(
                account,
                allowed_symbols=allowed_symbols,
                dry_run=dry_run,
                force=force,
                reasoning_context=reasoning_context,
            )
            self.progress("account.done", accountId=account.account_id, eventCount=len(events), mode=snapshot.mode, status=snapshot.status)
            snapshots.append(snapshot)
            if not self.use_cycle_recorder(dry_run) and snapshot.has_live_account_data():
                self.publish(snapshot_collected_event(snapshot))
            all_events.extend(events)
        if self.use_cycle_recorder(dry_run):
            for snapshot in snapshots:
                snapshot.metadata.pop("previousMonitorState", None)
                snapshot.metadata.pop("monitorStateHistory", None)
            recorder = self.cycle_recorder.record_cycle
            parameters = inspect.signature(recorder).parameters
            accepts_kwargs = any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in parameters.values()
            )
            kwargs = {"dry_run": dry_run}
            if delivery_guard is not None and ("delivery_guard" in parameters or accepts_kwargs):
                kwargs["delivery_guard"] = delivery_guard
            source_snapshot_replay = bool(self.source_snapshot_replay)
            if source_snapshot_replay and ("source_snapshot_replay" in parameters or accepts_kwargs):
                kwargs["source_snapshot_replay"] = True
            self.progress(
                "cycle_record.start",
                snapshotCount=len(snapshots),
                eventCount=len(all_events),
            )
            cycle_result = recorder(
                [account.account_id for account in self.accounts],
                snapshots,
                all_events,
                **kwargs,
            )
            self.progress(
                "cycle_record.done",
                snapshotCount=len(snapshots),
                eventCount=len(all_events),
            )
            self.last_cycle_record_result = cycle_result
            details = getattr(cycle_result, "details", {}) if cycle_result is not None else {}
            self.last_delivery_guard_result = dict((details or {}).get("deliveryGuard") or {})
            delivered_events = getattr(cycle_result, "delivered_events", None) if cycle_result is not None else None
            if delivered_events is not None:
                all_events = list(delivered_events or [])
            self.observe_portfolio_lifecycle(snapshots)
            if not all_events:
                self.progress("monitor.no_events")
            self.progress(
                "monitor.completed",
                snapshotCount=len(snapshots),
                eventCount=len(all_events),
                cycleRecorder=True,
                deliveryGuard=self.last_delivery_guard_result,
                sourceSnapshotReplay=source_snapshot_replay,
            )
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
            self.observe_portfolio_lifecycle(snapshots)
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
            self.observe_portfolio_lifecycle([snapshot])
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
        reasoning_context: Dict[str, object] = None,
    ):
        self.progress("snapshot.start", accountId=account.account_id)
        snapshot = self.build_snapshot(account, reasoning_context=reasoning_context)
        self.progress(
            "snapshot.done",
            accountId=account.account_id,
            mode=snapshot.mode,
            status=snapshot.status,
            positionCount=len(snapshot.positions or []),
            watchlistCount=len(snapshot.watchlist or []),
        )
        if not self.source_snapshot_replay:
            # A normal provider snapshot must not inherit this isolated
            # worker control-plane field through an older persisted payload.
            # Leaving it in place previously made the cycle recorder skip
            # both ``snapshot_collected`` and the market-observation
            # follow-up request while still allowing the raw price message.
            snapshot.metadata.pop("reasoningSnapshotReplay", None)
        replay = self.reasoning_snapshot_replay(snapshot) if self.source_snapshot_replay else None
        if self.source_snapshot_replay and replay is None:
            projection = {
                "saved": False,
                "status": "deferred-source-snapshot",
                "reason": "The isolated reasoning worker did not receive a verified monitor snapshot replay marker.",
                "retryable": True,
                "recommendedRetryAfterSeconds": 30,
            }
            snapshot.metadata.setdefault("ontology", {})["projection"] = projection
            self.last_ontology_projection_results[account.account_id] = projection
            self.progress(
                "ontology_projection.deferred",
                accountId=account.account_id,
                status=projection["status"],
                reason=projection["reason"],
            )
            return snapshot, []
        if replay is not None and str(replay.get("status") or "") != "ready":
            projection = {
                "saved": False,
                "status": "deferred-source-snapshot",
                "reason": str(
                    replay.get("reason")
                    or "The latest verified monitor snapshot is not ready for this fact revision."
                )[:220],
                "retryable": True,
                "recommendedRetryAfterSeconds": int(replay.get("retryAfterSeconds") or 30),
                "sourceSnapshotReplay": dict(replay),
            }
            snapshot.metadata.setdefault("ontology", {})["projection"] = projection
            self.last_ontology_projection_results[account.account_id] = projection
            self.progress(
                "ontology_projection.deferred",
                accountId=account.account_id,
                status=projection["status"],
                reason=projection["reason"],
            )
            return snapshot, []
        refresh_reasoning_anchors = getattr(
            self.store,
            "refresh_market_observation_reasoning_anchors",
            None,
        )
        if callable(refresh_reasoning_anchors) and not self.source_snapshot_replay:
            try:
                refresh_reasoning_anchors(snapshot.account_id)
            except Exception:
                # Anchor refresh is an operational optimization. The durable
                # pending request remains authoritative when this read fails.
                pass
        previous = self.store.previous.get(snapshot.account_id) or {}
        persisted_previous = (
            snapshot.metadata.get("previousMonitorState")
            if self.source_snapshot_replay and isinstance(snapshot.metadata, dict)
            else None
        )
        if isinstance(persisted_previous, dict):
            persisted_at = str(persisted_previous.get("generatedAt") or "")
            current_at = str(snapshot.generated_at or "")
            if persisted_at and current_at and persisted_at < current_at:
                previous = persisted_previous
        immutable_shadow_input = bool(
            self.source_snapshot_replay
            and isinstance(replay, dict)
            and replay.get("immutableInput")
        )
        if not immutable_shadow_input:
            snapshot.metadata["previousMonitorState"] = self.compact_previous_state(previous)
            snapshot.metadata["monitorStateHistory"] = self.compact_monitor_history(
                self.load_snapshot_history(snapshot.account_id)
            )
        snapshot.metadata.setdefault("ontology", {})["previousStateAvailable"] = bool(previous)
        if immutable_shadow_input:
            # ``FrozenReasoningSnapshotSource`` already contains the exact
            # packet consumed by V1. Compacting it a second time can trim a
            # bounded history or target-scoped collection again, making V2's
            # facts differ even though the durable input is unchanged.
            frozen_source_state = snapshot.to_monitor_state()
        else:
            frozen_source_state = frozen_monitor_state_for_reasoning(
                snapshot.to_monitor_state(),
                target_symbols=allowed_symbols,
                settings=getattr(self.monitor, "settings", None),
            )
        self.last_reasoning_source_states[snapshot.account_id] = frozen_source_state
        if self.source_snapshot_replay and not immutable_shadow_input:
            normalized_snapshot = account_snapshot_from_monitor_state(frozen_source_state)
            if normalized_snapshot is None:
                raise RuntimeError("The immutable reasoning source packet cannot be rehydrated")
            snapshot = normalized_snapshot
        # Dry-run and the isolated reasoning replay still execute the exact
        # TypeDB path.  The ordinary realtime monitor, however, persists the
        # verified source snapshot first and leaves the generation to the
        # single durable reasoning worker.  Running both paths concurrently
        # duplicates an ABox write and makes the single TypeDB writer queue
        # compete with itself.
        projection_enabled = self.ontology_projection_enabled or dry_run
        if projection_enabled:
            self.progress("ontology_projection.start", accountId=account.account_id)
            projection_symbols = self.projection_symbol_filters_by_account.get(
                account.account_id,
                allowed_symbols,
            )
            self.record_ontology_projection(
                snapshot,
                target_symbols=projection_symbols,
                reasoning_context=reasoning_context,
            )
            projection = snapshot.metadata.get("ontology", {}).get("projection") if isinstance(snapshot.metadata.get("ontology"), dict) else {}
            self.progress(
                "ontology_projection.done",
                accountId=account.account_id,
                status=projection.get("status") if isinstance(projection, dict) else "",
                graphStore=projection.get("graphStore") if isinstance(projection, dict) else "",
            )
        else:
            projection = {
                "saved": False,
                "status": VERIFIED_MONITOR_SNAPSHOT_QUEUED,
                "reason": "확정 모니터 스냅샷을 저장한 뒤 전용 온톨로지 추론 워커가 TypeDB 세대를 처리합니다.",
                "eventuallyConsistent": True,
            }
            snapshot.metadata.setdefault("ontology", {})["projection"] = projection
            self.progress(
                "ontology_projection.queued",
                accountId=account.account_id,
                status=projection["status"],
            )
        if self.projection_inference_verified(projection):
            inference = projection.get("inferenceBox") if isinstance(projection, dict) else {}
            self.progress(
                "ontology_projection.verified",
                accountId=account.account_id,
                sourceAboxSnapshotId=str((inference or {}).get("sourceAboxSnapshotId") or ""),
                inferenceGenerationId=str((inference or {}).get("inferenceGenerationId") or ""),
            )
        # Hypothesis lifecycle observations refer to the verified native
        # generation.  Defer them with the generation instead of recording a
        # stale monitor-side placeholder.
        if projection_enabled:
            self.record_hypothesis_lifecycle(snapshot)
        event_builder = self.monitor.events_for_snapshot
        event_builder_parameters = inspect.signature(event_builder).parameters
        event_builder_accepts_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in event_builder_parameters.values()
        )
        if reasoning_context is not None and (
            "reasoning_context" in event_builder_parameters or event_builder_accepts_kwargs
        ):
            events = event_builder(snapshot, previous, reasoning_context=reasoning_context)
        else:
            events = event_builder(snapshot, previous)
        if force and hasattr(self.monitor, "forced_holdings_snapshot_events"):
            events.extend(self.monitor.forced_holdings_snapshot_events(snapshot))
        self.progress("events.detected", accountId=account.account_id, eventCount=len(events))
        if allowed_symbols:
            events = self.filter_events_by_symbol(
                events,
                allowed_symbols,
                account_id=account.account_id,
                reasoning_context=reasoning_context,
            )
            self.progress("events.filtered", accountId=account.account_id, eventCount=len(events), symbolCount=len(allowed_symbols))
        detected_events = list(events)
        self.last_detected_alert_events.extend(detected_events)
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

    def build_snapshot(self, account: AccountConfig, reasoning_context: Dict[str, object] = None) -> AccountSnapshot:
        """Pass request provenance to snapshot adapters that support replay.

        Normal provider builders keep their one-argument compatibility
        contract.  The ontology worker's persisted-snapshot adapter receives
        bounded provenance only to verify source freshness; it never uses it
        as investment evidence.
        """
        if reasoning_context is None:
            return self.snapshot_builder(account)
        try:
            parameters = inspect.signature(self.snapshot_builder).parameters
            accepts_kwargs = any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in parameters.values()
            )
        except (TypeError, ValueError):
            parameters = {}
            accepts_kwargs = False
        if "reasoning_context" in parameters or accepts_kwargs:
            return self.snapshot_builder(account, reasoning_context=dict(reasoning_context or {}))
        return self.snapshot_builder(account)

    @staticmethod
    def reasoning_snapshot_replay(snapshot: AccountSnapshot) -> Dict[str, object]:
        metadata = snapshot.metadata if isinstance(getattr(snapshot, "metadata", None), dict) else {}
        replay = metadata.get("reasoningSnapshotReplay") if isinstance(metadata.get("reasoningSnapshotReplay"), dict) else None
        return dict(replay) if replay else None

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
        investment_types = {
            "investmentInsight",
            "holdingTiming",
            "watchlistOntologySignal",
            "cryptoOntologySignal",
            PORTFOLIO_ONTOLOGY_SIGNAL,
        }

        def event_message_type(event: AlertEvent) -> str:
            return str(
                getattr(event, "rule", "")
                or getattr(event, "message_type", "")
                or ""
            ).strip()

        detected = [event for event in detected_events or [] if event_message_type(event) in investment_types]
        ready = [event for event in ready_events or [] if event_message_type(event) in investment_types]
        selected_symbols = sorted({
            str(symbol or "").upper().strip()
            for symbol in allowed_symbols or set()
            if str(symbol or "").strip()
        })
        target_symbols = sorted({
            str(symbol or "").upper().strip()
            for symbol in inference.get("targetSymbols") or []
            if str(symbol or "").strip()
        })
        detected_symbols = {
            str(getattr(event, "symbol", "") or "").upper().strip()
            for event in detected
            if str(getattr(event, "symbol", "") or "").strip()
        }
        ready_symbols = {
            str(getattr(event, "symbol", "") or "").upper().strip()
            for event in ready
            if str(getattr(event, "symbol", "") or "").strip()
        }
        unchanged_inference = projection_reuses_unchanged_inference(projection)
        if unchanged_inference:
            status = "unchanged-inference"
            reason = "실질 입력과 검증된 TypeDB 관계 판단이 같아 투자 이벤트와 AI 실행을 생략했습니다."
        elif no_match:
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
        symbol_outcomes = []
        for symbol in selected_symbols or target_symbols:
            if unchanged_inference:
                symbol_status = "unchanged-inference"
                symbol_reason = "동일한 material fingerprint와 InferenceBox를 재사용해 새 판단을 만들지 않았습니다."
            elif symbol in ready_symbols:
                symbol_status = "delivery-ready"
                symbol_reason = "An investment alert candidate passed cadence."
            elif symbol in detected_symbols:
                symbol_status = "cadence-suppressed"
                symbol_reason = "An investment alert candidate was held by cadence."
            elif target_symbols and symbol not in target_symbols:
                symbol_status = "not-evaluated"
                symbol_reason = "The verified TypeDB generation did not include this symbol."
            elif no_match:
                symbol_status = "no-signal"
                symbol_reason = "TypeDB evaluated the symbol but no investment relation matched."
            elif status == "blocked":
                symbol_status = "blocked"
                symbol_reason = reason
            else:
                symbol_status = "no-material-alert"
                symbol_reason = "The evaluated relations did not create a material alert candidate."
            symbol_outcomes.append({
                "symbol": symbol,
                "status": symbol_status,
                "reason": symbol_reason,
            })
        payload = {
            "status": status,
            "reason": reason,
            "inferenceStatus": inference_status,
            "nativeInferenceOutcome": str(inference.get("nativeInferenceOutcome") or ""),
            "nativeTypeDbReasoningCompleted": native_completed,
            "generationAligned": generation_aligned,
            "sourceAboxSnapshotId": str(inference.get("sourceAboxSnapshotId") or ""),
            "inferenceGenerationId": str(inference.get("inferenceGenerationId") or ""),
            "targetSymbols": target_symbols[:80],
            "requestedSymbols": selected_symbols,
            "detectedCandidateCount": len(detected),
            "cadenceReadyCount": len(ready),
            "detectedMessageTypes": sorted({event_message_type(event) for event in detected}),
            "cadenceReadyMessageTypes": sorted({event_message_type(event) for event in ready}),
            "symbolOutcomes": symbol_outcomes[:80],
        }
        snapshot.metadata.setdefault("ontology", {})["alertPipeline"] = payload
        if isinstance(projection, dict):
            projection["alertPipeline"] = payload

    @staticmethod
    def projection_inference_verified(projection: object) -> bool:
        """Return only a transport checkpoint, never a Python judgement.

        This mirrors the minimum TypeDB generation proof needed to safely
        persist a worker checkpoint. Rule matching and investment action stay
        entirely in the projection/InferenceBox path.
        """
        values = dict(projection or {}) if isinstance(projection, dict) else {}
        if str(values.get("status") or "").lower() not in {
            "ok",
            "partial",
            "unchanged-material-facts",
            "unchanged-material-facts-reasoning-retry",
        }:
            return False
        inference = values.get("inferenceBox") if isinstance(values.get("inferenceBox"), dict) else {}
        return bool(
            inference
            and (
                inference.get("nativeTypeDbReasoningCompleted")
                or inference.get("typedbNativeRuleEvaluationCompleted")
            )
            and inference.get("generationAligned")
            and str(inference.get("sourceAboxSnapshotId") or "").strip()
        )

    def next_account_run_at(self, seconds: int) -> str:
        return (datetime.now(timezone.utc) + timedelta(seconds=max(1, int(seconds or 1)))).isoformat().replace("+00:00", "Z")

    def failure_backoff_seconds(self, job: MonitorAccountJob) -> int:
        attempts = max(1, int(getattr(job, "attempts", 0) or 0))
        return min(self.account_job_interval_seconds * 8, self.account_job_interval_seconds * attempts)

    def filter_events_by_symbol(
        self,
        events: List[AlertEvent],
        allowed_symbols: set,
        account_id: str = "",
        reasoning_context: Dict[str, object] = None,
    ) -> List[AlertEvent]:
        context = dict(reasoning_context or {})
        subject_kinds = {
            str(item or "").upper().strip()
            for item in context.get("subjectKinds") or []
            if str(item or "").strip()
        }
        subject_ids = {
            str(item or "").strip()
            for item in context.get("subjectIds") or []
            if str(item or "").strip()
        }
        account_ids = {
            str(item or "").strip()
            for item in context.get("accountIds") or []
            if str(item or "").strip()
        }
        portfolio_ids = {"portfolio:" + str(account_id or "").strip(), str(account_id or "").strip()}
        portfolio_scope = bool(
            "PORTFOLIO" in subject_kinds
            and (not account_ids or str(account_id or "") in account_ids)
            and (not subject_ids or bool(subject_ids.intersection(portfolio_ids)))
        )
        return [
            event
            for event in events
            if str(event.symbol or "").upper().strip() in allowed_symbols
            or (
                portfolio_scope
                and not str(event.symbol or "").strip()
                and str(event.account_id or "") == str(account_id or "")
                and str(event.rule or "") in {PORTFOLIO_ONTOLOGY_SIGNAL, "investmentInsight"}
            )
        ]

    def use_cycle_recorder(self, dry_run: bool) -> bool:
        return self.cycle_recorder is not None and not dry_run

    def observe_portfolio_lifecycle(self, snapshots: Iterable[AccountSnapshot]) -> None:
        if self.source_snapshot_replay or not self.portfolio_lifecycle_observer:
            return
        observer = getattr(self.portfolio_lifecycle_observer, "observe_snapshot", None)
        if not callable(observer):
            return
        for snapshot in snapshots or []:
            if not snapshot.has_live_account_data():
                continue
            try:
                result = dict(observer(snapshot) or {})
                self.last_portfolio_lifecycle_results[snapshot.account_id] = result
                self.progress(
                    "portfolio_lifecycle.observed",
                    accountId=snapshot.account_id,
                    status=result.get("status") or "ready",
                    reconciliationStatus=(result.get("reconciliation") or {}).get("status"),
                )
            except Exception as error:  # noqa: BLE001 - source snapshot remains the retry boundary.
                self.last_portfolio_lifecycle_results[snapshot.account_id] = {
                    "status": "error",
                    "reason": str(error)[:220],
                }
                self.progress(
                    "portfolio_lifecycle.failed",
                    accountId=snapshot.account_id,
                    reason=str(error)[:180],
                )

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

    def record_ontology_projection(
        self,
        snapshot: AccountSnapshot,
        target_symbols: set = None,
        reasoning_context: Dict[str, object] = None,
    ) -> None:
        if not self.ontology_projection_recorder or not self.has_ontology_projection_data(snapshot):
            return
        try:
            selected_symbols = {
                str(symbol or "").upper().strip()
                for symbol in target_symbols or set()
                if str(symbol or "").strip()
            }
            recorder = self.ontology_projection_recorder.record_snapshot
            parameters = inspect.signature(recorder).parameters
            accepts_kwargs = any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in parameters.values()
            )
            kwargs = {}
            if selected_symbols and ("target_symbols" in parameters or accepts_kwargs):
                kwargs["target_symbols"] = sorted(selected_symbols)
            if reasoning_context and ("reasoning_context" in parameters or accepts_kwargs):
                kwargs["reasoning_context"] = dict(reasoning_context)
            if "progress_callback" in parameters or accepts_kwargs:
                def projection_progress(stage: str, payload: Dict[str, object] = None) -> None:
                    details = dict(payload or {}) if isinstance(payload, dict) else {}
                    details.setdefault("accountId", snapshot.account_id)
                    self.progress(str(stage or "ontology_projection.unknown"), **details)

                kwargs["progress_callback"] = projection_progress
            recorder(snapshot, **kwargs)
            contexts = getattr(
                self.ontology_projection_recorder,
                "last_runtime_contexts",
                {},
            ) or {}
            runtime_context = contexts.get(snapshot.account_id)
            if isinstance(runtime_context, dict):
                self.last_projection_runtime_contexts[snapshot.account_id] = deepcopy(
                    runtime_context
                )
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
        # Historical temporal concepts need facts, not the full provider
        # archive.  Persisting raw article bodies here previously made every
        # live projection decode the archive once per history row.
        return compact_monitor_state_for_ontology(
            previous,
            settings=getattr(self.monitor, "settings", None),
        )

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
