import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

from ..domain.accounts import AccountConfig, split_symbols
from ..domain.data_freshness import evaluate_notification_data_freshness
from ..domain.events import (
    DomainEvent,
    alerts_detected_event,
    monitoring_cycle_completed_event,
    snapshot_collected_event,
)
from ..domain.fact_changes import fact_signature, research_evidence_fact_payload
from ..domain.investment_research import ResearchEvidence
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
from ..domain.portfolio import AccountSnapshot, AlertEvent
from ..domain.repositories import MonitoringCycleRecordResult
from ..domain.symbol_universe import ListedSymbol, normalize_market, normalize_symbol, utc_now_iso as symbol_utc_now_iso
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
from .mysql_operational_events import insert_domain_event_with_connection
from .mysql_operational_helpers import (
    _is_duplicate_key_error,
    _json_loads,
    _sent_key_hash,
    research_evidence_change_payload,
)


from .mysql_notification_config import MySQLNotificationTemplateStore
from .mysql_market_stores import MySQLModelReviewJobStore
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

    def load_history(self, account_id: str, limit: int = 6) -> List[Dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM monitor_snapshot_history
                WHERE account_id = %s
                ORDER BY generated_at DESC
                LIMIT %s
                """,
                (str(account_id or ""), max(1, int(limit or 6))),
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
            INSERT INTO monitor_snapshot_history (account_id, generated_at, payload_json, created_at)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE payload_json = VALUES(payload_json), created_at = VALUES(created_at)
            """,
            (account_id, generated_at, json_dumps(state), updated_at),
        )

    def upsert_snapshot_state(self, account_id: str, state: Dict[str, object]) -> None:
        with self.transaction() as connection:
            self.upsert_snapshot_state_with_connection(connection, account_id, state)

    def save_snapshot(self, snapshot: AccountSnapshot) -> None:
        state = snapshot.to_monitor_state()
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

    def record_cycle(self, account_ids: List[str], snapshots: List[AccountSnapshot], alert_events: List[AlertEvent], dry_run: bool = False):
        return MySQLMonitoringCycleRecorder(self.runtime_settings, monitor_store=self).record_cycle(account_ids, snapshots, alert_events, dry_run=dry_run)

    def write(self) -> None:
        pass

class MySQLMonitoringCycleRecorder(MySQLOperationalConnection):
    def __init__(self, settings: Dict[str, str] = None, monitor_store: MySQLMonitorStore = None):
        self.monitor_store = monitor_store
        super().__init__(settings)
        if self.monitor_store is None:
            self.monitor_store = MySQLMonitorStore(settings)

    def record_cycle(self, account_ids: List[str], snapshots: List[AccountSnapshot], alert_events: List[AlertEvent], dry_run: bool = False):
        if dry_run:
            return MonitoringCycleRecordResult(False, 0, "dry-run")
        snapshot_states = {snapshot.account_id: snapshot.to_monitor_state() for snapshot in snapshots}
        delivered = bool(alert_events)
        alert_source_event = alerts_detected_event(alert_events) if alert_events else None
        stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        queued = 0
        sent_entries: Dict[str, str] = {}
        notification_store = MySQLNotificationJobStore(self.runtime_settings)
        model_review_store = MySQLModelReviewJobStore(self.runtime_settings)
        with self.transaction() as connection:
            for snapshot in snapshots:
                insert_domain_event_with_connection(connection, snapshot_collected_event(snapshot))
            if alert_source_event:
                insert_domain_event_with_connection(connection, alert_source_event)
                for event in alert_events:
                    context = alert_context(event)
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
                model_review_store.enqueue_from_event_with_connection(connection, alert_source_event)
                sent_entries = self.monitor_store.mark_sent_with_connection(connection, alert_events, stamp)
            insert_domain_event_with_connection(
                connection,
                monitoring_cycle_completed_event(list(account_ids or []), len(snapshots), len(alert_events), False, delivered),
            )
            for account_id, state in snapshot_states.items():
                self.monitor_store.upsert_snapshot_state_with_connection(connection, account_id, state, stamp)
        self.monitor_store.previous.update(snapshot_states)
        self.monitor_store.sent.update(sent_entries)
        return MonitoringCycleRecordResult(delivered, queued, "queued=" + str(queued))

class MySQLEventLog(MySQLOperationalConnection):
    def handle(self, event: DomainEvent) -> None:
        with self.transaction() as connection:
            insert_domain_event_with_connection(connection, event)

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
        sql = "SELECT event_json FROM domain_events"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY occurred_at, event_id"
        if limit:
            sql += " LIMIT %s"
            params.append(int(limit))
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [DomainEvent.from_dict(_json_loads(row["event_json"], {})) for row in rows]

    def latest_events(self, limit: int = 12) -> List[DomainEvent]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT event_json FROM domain_events ORDER BY occurred_at DESC, event_id DESC LIMIT %s",
                (max(1, min(200, int(limit or 12))),),
            ).fetchall()
        return [DomainEvent.from_dict(_json_loads(row["event_json"], {})) for row in reversed(rows)]

    def latest_events_by_name(self, names: Iterable[str]) -> Dict[str, DomainEvent]:
        result = {}
        with self.connect() as connection:
            for name in [str(item or "").strip() for item in names or [] if str(item or "").strip()]:
                row = connection.execute(
                    "SELECT event_json FROM domain_events WHERE name = %s ORDER BY occurred_at DESC, event_id DESC LIMIT 1",
                    (name,),
                ).fetchone()
                if row:
                    result[name] = DomainEvent.from_dict(_json_loads(row["event_json"], {}))
        return result

    def event_counts(self) -> Dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute("SELECT name, COUNT(*) AS count FROM domain_events GROUP BY name").fetchall()
        return {row["name"]: int(row["count"] or 0) for row in rows}
