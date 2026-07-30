import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

from ..domain.accounts import AccountConfig, split_symbols
from ..domain.data_freshness import evaluate_notification_data_freshness
from ..domain.events import (
    DomainEvent,
    ONTOLOGY_REASONING_REQUESTED,
    alerts_detected_event,
    monitoring_cycle_completed_event,
    snapshot_collected_event,
)
from ..domain.fact_changes import fact_signature, research_evidence_fact_payload
from ..domain.investment_research import ResearchEvidence
from ..domain.investment_strategy_guidance import append_strategy_block, merge_strategy_context
from ..domain.market_observations import apply_market_observation_outbox_baselines, hydrate_market_observation_baselines
from ..domain.model_review import ModelReviewJob
from ..domain.notification_rules import (
    DEFAULT_NOTIFICATION_RULES,
    NotificationRuleConfig,
    apply_market_hours_rule,
    apply_similarity_rule,
    apply_state_cooldown_rule,
    default_notification_rule,
    evaluate_notification_rule,
    notification_fingerprint,
)
from ..domain.notification_templates import DEFAULT_NOTIFICATION_TEMPLATES, NotificationTemplate, alert_context, render_notification
from ..domain.notifications import NotificationJob, notification_debug_number
from ..domain.ontology_quality import OntologyQualitySample, build_ontology_quality_sample
from ..domain.ontology_projection_input import compact_monitor_state_for_ontology
from ..domain.portfolio import AccountSnapshot, AlertEvent, monitor_state_has_live_account_data
from ..domain.repositories import MonitoringCycleRecordResult
from ..domain.symbol_universe import ListedSymbol, normalize_market, normalize_symbol, utc_now_iso as symbol_utc_now_iso
from ..domain.verified_snapshot_reasoning import verified_monitor_snapshot_reasoning_event
from .model_review_queue import model_review_payloads_from_event
from .mysql_monitoring import MySQLDependencyError, MySQLMonitorAccountJobStore, ensure_mysql_database_exists, mysql_settings
from .operational_common import (
    MAX_NOTIFICATION_DELIVERY_ATTEMPTS,
    NOTIFICATION_HISTORY_LOOKBACK_LIMIT,
    age_minutes_since,
    json_dumps,
    notification_history_is_recent_in_flight,
    research_evidence_from_row,
    rule_from_row,
    template_from_row,
)
from .settings import read_json, settings_path, utc_now
from .mysql_notification_jobs import MySQLNotificationJobStore
from .mysql_operational_connection import MYSQL_SCHEMA, MySQLConnectionProxy, MySQLOperationalConnection
from .mysql_operational_events import domain_event_from_row, insert_domain_event_with_connection
from .mysql_reasoning_mailbox import MySQLOntologyReasoningMailboxStore
from .mysql_operational_helpers import (
    _is_duplicate_key_error,
    _json_loads,
    _sent_key_hash,
    research_evidence_change_payload,
)


from .mysql_notification_config import MySQLNotificationTemplateStore
from .mysql_market_stores import MySQLModelReviewJobStore
from .mysql_market_time_series import MySQLMarketTimeSeriesStore


def snapshot_state_for_persistence(snapshot: AccountSnapshot, previous: Dict[str, object] = None) -> Dict[str, object]:
    """Keep the last verified account data when a provider request fails.

    A temporary authentication failure is operational state, not a new
    portfolio state. Persisting it over the live snapshot made the next live
    cycle lose its price baseline and blocked the TypeDB ABox projection.
    The retained state carries a small failure marker so connection alerts can
    still count consecutive failures and report recovery.
    """
    current = snapshot.to_monitor_state()
    if snapshot.has_live_account_data():
        current = hydrate_market_observation_baselines(current, previous or {})
    if snapshot.has_live_account_data() or not monitor_state_has_live_account_data(previous or {}):
        return current

    retained = copy.deepcopy(previous)
    current_metadata = dict(current.get("metadata") or {})
    metadata = dict(retained.get("metadata") or {})
    failure = {
        "mode": str(snapshot.mode or ""),
        "status": str(snapshot.status or ""),
        "generatedAt": str(snapshot.generated_at or ""),
        "connectionFailureStreak": int(current_metadata.get("connectionFailureStreak") or 0),
    }
    metadata["connectionFailureStreak"] = failure["connectionFailureStreak"]
    metadata["lastConnectionFailure"] = failure
    retained["metadata"] = metadata
    return retained


class MySQLMonitorStore(MySQLOperationalConnection):
    def __init__(self, settings: Dict[str, str] = None):
        super().__init__(settings)
        self.payload = {"previous": self.load_previous(), "sent": self.load_sent()}

    def load_previous(self) -> Dict[str, object]:
        with self.connect() as connection:
            rows = connection.execute("SELECT account_id, payload_json FROM monitor_snapshots").fetchall()
        previous = {}
        for row in rows:
            previous[row["account_id"]] = _json_loads(row["payload_json"], {})
        return previous

    def snapshot_metadata(self, account_id: str) -> Dict[str, object]:
        """Read the snapshot boundary without decoding its full payload.

        Isolated ontology scheduling calls this before a TypeDB generation.
        Keeping it narrow prevents a large research archive from becoming a
        prerequisite for learning that a newer source snapshot is still due.
        """
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT account_id, mode, status, generated_at, updated_at
                FROM monitor_snapshots
                WHERE account_id = %s
                LIMIT 1
                """,
                (str(account_id or ""),),
            ).fetchone() or {}
        return {
            "accountId": str(row.get("account_id") or account_id or ""),
            "mode": str(row.get("mode") or ""),
            "status": str(row.get("status") or ""),
            "generatedAt": str(row.get("generated_at") or ""),
            "updatedAt": str(row.get("updated_at") or ""),
        }

    def load_history(self, account_id: str, limit: int = 6) -> List[Dict[str, object]]:
        with self.connect() as connection:
            # New rows carry a compact temporal projection.  Do not deserialize
            # the research archive for every historical snapshot in the live
            # TypeDB path.  A small raw fallback keeps pre-migration history
            # usable until fresh monitor cycles replace it.
            rows = connection.execute(
                """
                SELECT projection_payload_json AS payload_json FROM monitor_snapshot_history
                WHERE account_id = %s
                  AND projection_payload_json <> ''
                ORDER BY generated_at DESC
                LIMIT %s
                """,
                (str(account_id or ""), max(1, int(limit or 6))),
            ).fetchall()
            if not rows:
                rows = connection.execute(
                    """
                    SELECT payload_json FROM monitor_snapshot_history
                    WHERE account_id = %s
                    ORDER BY generated_at DESC
                    LIMIT %s
                    """,
                    (str(account_id or ""), min(6, max(1, int(limit or 6)))),
                ).fetchall()
        history = [_json_loads(row["payload_json"], {}) for row in reversed(rows)]
        return [item for item in history if item]

    def load_sent(self) -> Dict[str, object]:
        with self.connect() as connection:
            rows = connection.execute("SELECT sent_key, sent_at FROM monitor_sent").fetchall()
        return {row["sent_key"]: row["sent_at"] for row in rows}

    @property
    def previous(self) -> Dict[str, object]:
        return self.payload["previous"]

    @property
    def sent(self) -> Dict[str, object]:
        return self.payload["sent"]

    def upsert_snapshot_state_with_connection(self, connection, account_id: str, state: Dict[str, object], stamp: str = "") -> None:
        updated_at = stamp or utc_now()
        generated_at = str(state.get("generatedAt") or updated_at)
        temporal_projection = compact_monitor_state_for_ontology(
            state,
            settings=self.runtime_settings,
        )
        connection.execute(
            """
            INSERT INTO monitor_snapshots (
                account_id, account_label, provider, mode, status, generated_at, payload_json, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE account_label = VALUES(account_label), provider = VALUES(provider),
                mode = VALUES(mode), status = VALUES(status), generated_at = VALUES(generated_at),
                payload_json = VALUES(payload_json), updated_at = VALUES(updated_at)
            """,
            (
                account_id,
                str(state.get("accountLabel") or ""),
                str(state.get("provider") or ""),
                str(state.get("mode") or ""),
                str(state.get("status") or ""),
                generated_at,
                json_dumps(state),
                updated_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO monitor_snapshot_history (
                account_id, generated_at, payload_json, projection_payload_json, created_at
            )
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE payload_json = VALUES(payload_json),
                projection_payload_json = VALUES(projection_payload_json), created_at = VALUES(created_at)
            """,
            (account_id, generated_at, json_dumps(state), json_dumps(temporal_projection), updated_at),
        )

    def upsert_snapshot_state(self, account_id: str, state: Dict[str, object]) -> None:
        with self.transaction() as connection:
            self.upsert_snapshot_state_with_connection(connection, account_id, state)

    def save_snapshot(self, snapshot: AccountSnapshot) -> None:
        state = snapshot_state_for_persistence(snapshot, self.previous.get(snapshot.account_id))
        self.upsert_snapshot_state(snapshot.account_id, state)
        self.previous[snapshot.account_id] = state

    def sent_entries(self, events: Iterable[AlertEvent], stamp: str) -> Dict[str, str]:
        entries: Dict[str, str] = {}
        for event in events:
            entries[event.key] = stamp
            entries[event.cadence_key()] = stamp
        return entries

    def mark_sent_with_connection(self, connection, events: Iterable[AlertEvent], stamp: str) -> Dict[str, str]:
        entries = self.sent_entries(events, stamp)
        for key, sent_at in entries.items():
            connection.execute(
                """
                INSERT INTO monitor_sent (sent_key_hash, sent_key, sent_at)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE sent_at = VALUES(sent_at)
                """,
                (_sent_key_hash(key), key, sent_at),
            )
        return entries

    def mark_sent(self, events: Iterable[AlertEvent]) -> None:
        stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with self.transaction() as connection:
            entries = self.mark_sent_with_connection(connection, events, stamp)
        self.sent.update(entries)

    def record_cycle(
        self,
        account_ids: List[str],
        snapshots: List[AccountSnapshot],
        alert_events: List[AlertEvent],
        dry_run: bool = False,
        delivery_guard=None,
        source_snapshot_replay: bool = False,
    ):
        return MySQLMonitoringCycleRecorder(
            self.runtime_settings,
            monitor_store=self,
            market_time_series_store=MySQLMarketTimeSeriesStore(self.runtime_settings),
        ).record_cycle(
            account_ids,
            snapshots,
            alert_events,
            dry_run=dry_run,
            delivery_guard=delivery_guard,
            source_snapshot_replay=source_snapshot_replay,
        )

    def write(self) -> None:
        pass

class MySQLMonitoringCycleRecorder(MySQLOperationalConnection):
    def __init__(
        self,
        settings: Dict[str, str] = None,
        monitor_store: MySQLMonitorStore = None,
        market_time_series_store=None,
    ):
        self.monitor_store = monitor_store
        self.market_time_series_store = market_time_series_store
        super().__init__(settings)
        if self.monitor_store is None:
            self.monitor_store = MySQLMonitorStore(settings)
        if self.market_time_series_store is None:
            self.market_time_series_store = MySQLMarketTimeSeriesStore(settings)

    def account_context_map(self) -> Dict[str, AccountConfig]:
        try:
            with self.connect() as connection:
                rows = connection.execute(
                    """
                    SELECT id, label, provider, enabled, watchlist_symbols, quiet_hours_enabled,
                           quiet_hours_start, quiet_hours_end, quiet_hours_timezone,
                           message_delivery_level, investment_strategy_profile
                    FROM service_accounts
                    """
                ).fetchall()
        except Exception:  # noqa: BLE001 - monitoring delivery should continue without strategy lookup.
            rows = []
        accounts: Dict[str, AccountConfig] = {}
        for row in rows:
            account = AccountConfig(
                account_id=row["id"],
                label=row["label"],
                provider=row["provider"],
                base_url=self.runtime_settings.get("tossApiBaseUrl", ""),
                client_id="",
                client_secret="",
                account_seq="",
                watchlist_symbols=split_symbols(row["watchlist_symbols"] or ""),
                enabled=bool(row["enabled"]),
                quiet_hours_enabled=bool(row["quiet_hours_enabled"]),
                quiet_hours_start=row["quiet_hours_start"] or "22:00",
                quiet_hours_end=row["quiet_hours_end"] or "05:00",
                quiet_hours_timezone=row["quiet_hours_timezone"] or "Asia/Seoul",
                message_delivery_level=row["message_delivery_level"] or self.runtime_settings.get("messageDeliveryLevel", "absoluteBeginner"),
                investment_strategy_profile=row["investment_strategy_profile"] or self.runtime_settings.get("investmentStrategyProfile", "balanced"),
            )
            accounts[account.account_id] = account
        return accounts

    def record_cycle(
        self,
        account_ids: List[str],
        snapshots: List[AccountSnapshot],
        alert_events: List[AlertEvent],
        dry_run: bool = False,
        delivery_guard=None,
        source_snapshot_replay: bool = False,
    ):
        if dry_run:
            return MonitoringCycleRecordResult(False, 0, "dry-run")
        snapshot_states = {}
        live_snapshots = []
        if not source_snapshot_replay:
            snapshot_states = {
                snapshot.account_id: snapshot_state_for_persistence(
                    snapshot,
                    self.monitor_store.previous.get(snapshot.account_id),
                )
                for snapshot in snapshots
            }
            live_snapshots = [snapshot for snapshot in snapshots if snapshot.has_live_account_data()]
        stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        queued = 0
        sent_entries: Dict[str, str] = {}
        notification_store = MySQLNotificationJobStore(self.runtime_settings)
        model_review_store = MySQLModelReviewJobStore(self.runtime_settings)
        account_contexts = self.account_context_map() if alert_events else {}
        guarded_events = list(alert_events or [])
        delivery_guard_result: Dict[str, object] = {"status": "not-applied"}
        with self.transaction() as connection:
            if callable(delivery_guard) and guarded_events:
                try:
                    guard_value = delivery_guard(connection, list(guarded_events))
                    if isinstance(guard_value, tuple) and len(guard_value) == 2:
                        candidate_events, candidate_details = guard_value
                        guarded_events = list(candidate_events or [])
                        delivery_guard_result = dict(candidate_details or {})
                    elif isinstance(guard_value, dict):
                        candidate_events = guard_value.get("events")
                        if candidate_events is not None:
                            guarded_events = list(candidate_events or [])
                        delivery_guard_result = {
                            key: value
                            for key, value in guard_value.items()
                            if key != "events"
                        }
                    elif guard_value is not None:
                        guarded_events = list(guard_value or [])
                        delivery_guard_result = {"status": "applied"}
                    delivery_guard_result.setdefault("status", "applied")
                except Exception as error:  # noqa: BLE001 - retain the source work for a safe retry.
                    # Continuing would persist the new monitor state while
                    # discarding its only delivery candidate. Roll the cycle
                    # back instead: the reasoning worker releases the mailbox
                    # lease and retries the same latest fact revision.
                    raise RuntimeError(
                        "Notification delivery revision guard failed: " + str(error)[:180]
                    ) from error
            delivered = bool(guarded_events)
            alert_source_event = alerts_detected_event(guarded_events) if guarded_events else None
            self.market_time_series_store.record_snapshots_with_connection(connection, live_snapshots)
            for snapshot in live_snapshots:
                insert_domain_event_with_connection(connection, snapshot_collected_event(snapshot))
            outboxed_events: List[AlertEvent] = []
            if alert_source_event:
                insert_domain_event_with_connection(connection, alert_source_event)
                for event in guarded_events:
                    account_context = account_contexts.get(event.account_id)
                    context = merge_strategy_context(alert_context(event), account_context, self.runtime_settings)
                    if str(event.rule or "") == "investmentInsight":
                        context = append_strategy_block(context)
                    message = MySQLNotificationTemplateStore(self.runtime_settings).render(event.rule, context)
                    job = NotificationJob.create(
                        message,
                        account_id=event.account_id,
                        account_label=event.account_label,
                        message_type=event.rule or "alert",
                        source_event_id=alert_source_event.event_id,
                        source_event_name=alert_source_event.name,
                        dedupe_key=":".join(["outbox", alert_source_event.event_id, event.key]),
                        context=context,
                    )
                    if notification_store.enqueue_with_connection(connection, job):
                        queued += 1
                        outboxed_events.append(event)
                model_review_store.enqueue_from_event_with_connection(connection, alert_source_event)
                sent_entries = self.monitor_store.mark_sent_with_connection(connection, guarded_events, stamp)
            if outboxed_events:
                for account_id, state in list(snapshot_states.items()):
                    account_events = [event for event in outboxed_events if event.account_id == account_id]
                    if account_events:
                        snapshot_states[account_id] = apply_market_observation_outbox_baselines(state, account_events)
            # Persist the source snapshot before publishing its replayable
            # TypeDB work. The two writes share this transaction, so a worker
            # can never see a barrier without the exact snapshot it names.
            for account_id, state in snapshot_states.items():
                self.monitor_store.upsert_snapshot_state_with_connection(connection, account_id, state, stamp)
            for snapshot in live_snapshots:
                reasoning_event = verified_monitor_snapshot_reasoning_event(
                    snapshot,
                    self.monitor_store.previous.get(snapshot.account_id),
                    self.runtime_settings,
                )
                if not reasoning_event:
                    continue
                insert_domain_event_with_connection(connection, reasoning_event)
                try:
                    MySQLOntologyReasoningMailboxStore.ingress_event_with_connection(connection, reasoning_event)
                except Exception:
                    # The event remains durable and the bounded repair path
                    # can recreate its mailbox row after an interrupted
                    # operational write.
                    pass
            insert_domain_event_with_connection(
                connection,
                monitoring_cycle_completed_event(list(account_ids or []), len(snapshots), len(guarded_events), False, delivered),
            )
        self.monitor_store.previous.update(snapshot_states)
        self.monitor_store.sent.update(sent_entries)
        return MonitoringCycleRecordResult(
            delivered,
            queued,
            "queued=" + str(queued),
            delivered_events=list(guarded_events),
            details={
                "deliveryGuard": delivery_guard_result,
                "inputEventCount": len(alert_events or []),
                "deliveredEventCount": len(guarded_events),
                "suppressedEventCount": max(0, len(alert_events or []) - len(guarded_events)),
                "sourceSnapshotReplay": bool(source_snapshot_replay),
            },
        )

class MySQLEventLog(MySQLOperationalConnection):
    def handle(self, event: DomainEvent) -> None:
        with self.transaction() as connection:
            insert_domain_event_with_connection(connection, event)
            if str(getattr(event, "name", "") or "") == ONTOLOGY_REASONING_REQUESTED:
                try:
                    MySQLOntologyReasoningMailboxStore.ingress_event_with_connection(connection, event)
                except Exception:
                    # The event remains durable and the runner's bounded
                    # reconciliation query will recover it.  A queue-summary
                    # migration must never reject a source fact.
                    pass

    def unmaterialized_reasoning_events(self, limit: int = 0) -> List[DomainEvent]:
        """Read only legacy reasoning events missing a mailbox ingress row.

        This is a bounded, low-frequency repair path for an interrupted
        ingress transaction. Direct/non-fungible work has its own indexed
        mailbox-event query below, so normal scheduler and status reads do
        not need to scan the append-only event log.
        """
        bounded = max(1, min(1000, int(limit or 200)))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT events.event_id, events.name, events.aggregate_id, events.occurred_at,
                       events.correlation_id, events.payload_json, events.event_json
                FROM domain_events events
                LEFT JOIN ontology_reasoning_mailbox_events mailbox
                  ON mailbox.event_id = events.event_id
                WHERE events.name = %s
                  AND mailbox.event_id IS NULL
                ORDER BY events.occurred_at DESC, events.event_id DESC
                LIMIT %s
                """,
                (ONTOLOGY_REASONING_REQUESTED, bounded),
            ).fetchall()
        return [domain_event_from_row(row) for row in reversed(rows)]

    def reasoning_ingress_repair_page(
        self,
        after_occurred_at: str = "",
        after_event_id: str = "",
        limit: int = 0,
    ) -> Dict[str, object]:
        """Advance a small indexed page of legacy mailbox-ingress recovery.

        Event ingress is atomic in the normal path, so recovery is exceptional.
        The former anti-join scanned the append-only event log on every retry.
        This cursor page scans only event identifiers in index order, then
        loads full payloads solely for rows that genuinely lack a mailbox
        marker. The cursor advances even when every scanned event is already
        materialized.
        """
        bounded = max(1, min(250, int(limit or 100)))
        after_time = str(after_occurred_at or "")
        after_id = str(after_event_id or "")
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT event_id, occurred_at
                FROM domain_events
                WHERE name = %s
                  AND (
                    occurred_at > %s
                    OR (occurred_at = %s AND event_id > %s)
                  )
                ORDER BY occurred_at, event_id
                LIMIT %s
                """,
                (ONTOLOGY_REASONING_REQUESTED, after_time, after_time, after_id, bounded),
            ).fetchall()
            event_ids = [str(row.get("event_id") or "").strip() for row in rows or []]
            event_ids = [event_id for event_id in event_ids if event_id]
            materialized = set()
            if event_ids:
                placeholders = ", ".join(["%s"] * len(event_ids))
                marker_rows = connection.execute(
                    "SELECT event_id FROM ontology_reasoning_mailbox_events "
                    "WHERE event_id IN (" + placeholders + ")",
                    tuple(event_ids),
                ).fetchall()
                materialized = {
                    str(row.get("event_id") or "").strip()
                    for row in marker_rows or []
                    if str(row.get("event_id") or "").strip()
                }
            missing_ids = [event_id for event_id in event_ids if event_id not in materialized]
            event_rows = []
            if missing_ids:
                placeholders = ", ".join(["%s"] * len(missing_ids))
                event_rows = connection.execute(
                    """
                    SELECT event_id, name, aggregate_id, occurred_at,
                           correlation_id, payload_json, event_json
                    FROM domain_events
                    WHERE event_id IN (""" + placeholders + ")",
                    tuple(missing_ids),
                ).fetchall()
        rows_by_id = {
            str(row.get("event_id") or "").strip(): row
            for row in event_rows or []
            if str(row.get("event_id") or "").strip()
        }
        events = [
            domain_event_from_row(rows_by_id[event_id])
            for event_id in missing_ids
            if event_id in rows_by_id
        ]
        last = (rows or [])[-1] if rows else {}
        return {
            "events": events,
            "cursor": {
                "occurredAt": str(last.get("occurred_at") or after_time),
                "eventId": str(last.get("event_id") or after_id),
            },
            "scannedCount": len(event_ids),
            "recoveredEventCount": len(events),
            "exhausted": len(rows or []) < bounded,
        }

    def direct_pending_reasoning_events(self, limit: int = 0) -> List[DomainEvent]:
        """Read non-fungible reasoning handoffs from their durable marker.

        Direct handoffs store the complete source event in
        ``ontology_reasoning_mailbox_events``. Querying the marker by state
        avoids repeatedly joining and scanning the append-only event log just
        to find a handful of pending research acknowledgements.
        """
        bounded = max(1, min(250, int(limit or 50)))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT event_id, occurred_at, event_json
                FROM ontology_reasoning_mailbox_events
                WHERE state = 'direct-pending'
                ORDER BY occurred_at, event_id
                LIMIT %s
                """,
                (bounded,),
            ).fetchall()
        events = []
        for row in rows or []:
            payload = _json_loads(row.get("event_json"), {})
            if not payload:
                continue
            event = DomainEvent.from_dict(payload)
            if str(event.name or "") != ONTOLOGY_REASONING_REQUESTED:
                continue
            events.append(event)
        return events

    def insert_event_dict(self, event: Dict[str, object]) -> None:
        self.handle(DomainEvent.from_dict(event))

    def events(self, name: str = "", aggregate_id: str = "", limit: int = 0) -> List[DomainEvent]:
        clauses = []
        params = []
        if name:
            clauses.append("name = %s")
            params.append(name)
        if aggregate_id:
            clauses.append("aggregate_id = %s")
            params.append(aggregate_id)
        sql = "SELECT event_id, name, aggregate_id, occurred_at, correlation_id, payload_json, event_json FROM domain_events"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY occurred_at, event_id"
        if limit:
            sql += " LIMIT %s"
            params.append(int(limit))
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [domain_event_from_row(row) for row in rows]

    def recent_events(self, name: str = "", aggregate_id: str = "", limit: int = 0) -> List[DomainEvent]:
        clauses = []
        params = []
        if name:
            clauses.append("name = %s")
            params.append(name)
        if aggregate_id:
            clauses.append("aggregate_id = %s")
            params.append(aggregate_id)
        sql = "SELECT event_id, name, aggregate_id, occurred_at, correlation_id, payload_json, event_json FROM domain_events"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY occurred_at DESC, event_id DESC"
        if limit:
            sql += " LIMIT %s"
            params.append(int(limit))
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [domain_event_from_row(row) for row in reversed(rows)]

    def latest_events(self, limit: int = 12) -> List[DomainEvent]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT event_id, name, aggregate_id, occurred_at, correlation_id, payload_json, event_json FROM domain_events ORDER BY occurred_at DESC, event_id DESC LIMIT %s",
                (max(1, min(200, int(limit or 12))),),
            ).fetchall()
        return [domain_event_from_row(row) for row in reversed(rows)]

    def latest_events_by_name(self, names: Iterable[str]) -> Dict[str, DomainEvent]:
        result = {}
        with self.connect() as connection:
            for name in [str(item or "").strip() for item in names or [] if str(item or "").strip()]:
                row = connection.execute(
                    "SELECT event_id, name, aggregate_id, occurred_at, correlation_id, payload_json, event_json FROM domain_events WHERE name = %s ORDER BY occurred_at DESC, event_id DESC LIMIT 1",
                    (name,),
                ).fetchone()
                if row:
                    result[name] = domain_event_from_row(row)
        return result

    def event_counts(self) -> Dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute("SELECT name, COUNT(*) AS count FROM domain_events GROUP BY name").fetchall()
        return {row["name"]: int(row["count"] or 0) for row in rows}
