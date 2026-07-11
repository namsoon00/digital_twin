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


class MySQLModelReviewJobStore(MySQLOperationalConnection):
    def jobs(self) -> List[ModelReviewJob]:
        with self.connect() as connection:
            rows = connection.execute("SELECT payload_json FROM model_review_jobs ORDER BY created_at, job_id").fetchall()
        return [ModelReviewJob.from_dict(_json_loads(row["payload_json"], {})) for row in rows]

    def upsert_job_with_connection(self, connection, job: ModelReviewJob) -> None:
        payload = job.to_dict()
        connection.execute(
            """
            INSERT INTO model_review_jobs (
                job_id, account_id, account_label, symbol, title, alert_key, status, attempts,
                created_at, updated_at, result, last_error, alert_lines_json, payload_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE account_id = VALUES(account_id), account_label = VALUES(account_label),
                symbol = VALUES(symbol), title = VALUES(title), alert_key = VALUES(alert_key),
                status = VALUES(status), attempts = VALUES(attempts), updated_at = VALUES(updated_at),
                result = VALUES(result), last_error = VALUES(last_error),
                alert_lines_json = VALUES(alert_lines_json), payload_json = VALUES(payload_json)
            """,
            (
                job.job_id,
                job.account_id,
                job.account_label,
                job.symbol,
                job.title,
                str(job.alert_key or "")[:191],
                job.status,
                job.attempts,
                job.created_at,
                job.updated_at,
                job.result,
                job.last_error,
                json_dumps(job.alert_lines),
                json_dumps(payload),
            ),
        )

    def upsert_job(self, job: ModelReviewJob) -> None:
        with self.transaction() as connection:
            self.upsert_job_with_connection(connection, job)

    def enqueue_with_connection(self, connection, job: ModelReviewJob) -> bool:
        existing = connection.execute("SELECT job_id FROM model_review_jobs WHERE job_id = %s", (job.job_id,)).fetchone()
        if existing:
            return False
        self.upsert_job_with_connection(connection, job)
        return True

    def enqueue(self, job: ModelReviewJob) -> bool:
        with self.transaction() as connection:
            return self.enqueue_with_connection(connection, job)

    def enqueue_from_event_with_connection(self, connection, event: DomainEvent) -> int:
        count = 0
        for item in model_review_payloads_from_event(event):
            if self.enqueue_with_connection(connection, ModelReviewJob.create(item)):
                count += 1
        return count

    def enqueue_from_event(self, event: DomainEvent) -> int:
        with self.transaction() as connection:
            return self.enqueue_from_event_with_connection(connection, event)

    def claim_pending(self, limit: int = 1, stale_after_minutes: int = 30) -> List[ModelReviewJob]:
        stamp = utc_now()
        with self.transaction() as connection:
            rows = connection.execute(
                """
                SELECT job_id, payload_json FROM model_review_jobs
                WHERE status IN ('pending', 'failed')
                ORDER BY created_at, job_id
                LIMIT %s
                FOR UPDATE SKIP LOCKED
                """,
                (max(1, int(limit or 1)),),
            ).fetchall()
            jobs = []
            for row in rows:
                job = ModelReviewJob.from_dict(_json_loads(row["payload_json"], {}))
                job.status = "processing"
                job.attempts += 1
                job.updated_at = stamp
                job.last_error = ""
                payload = job.to_dict()
                connection.execute(
                    """
                    UPDATE model_review_jobs
                    SET status = %s, attempts = %s, updated_at = %s, last_error = %s,
                        processing_started_at = %s, payload_json = %s
                    WHERE job_id = %s
                    """,
                    (job.status, job.attempts, job.updated_at, job.last_error, stamp, json_dumps(payload), job.job_id),
                )
                jobs.append(job)
        return jobs

    def pending(self, limit: int = 1) -> List[ModelReviewJob]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM model_review_jobs WHERE status IN ('pending', 'failed') ORDER BY created_at, job_id LIMIT %s",
                (int(limit or 1),),
            ).fetchall()
        return [ModelReviewJob.from_dict(_json_loads(row["payload_json"], {})) for row in rows]

    def mark_done(self, job: ModelReviewJob, result: str) -> None:
        job.status = "done"
        job.result = result
        job.last_error = ""
        job.updated_at = utc_now()
        self.upsert_job(job)

    def mark_failed(self, job: ModelReviewJob, error: str) -> None:
        job.status = "failed"
        job.last_error = error
        job.updated_at = utc_now()
        self.upsert_job(job)

    def mark_processing(self, job: ModelReviewJob) -> ModelReviewJob:
        job.status = "processing"
        job.attempts += 1
        job.updated_at = utc_now()
        self.upsert_job(job)
        return job

    def update(self, updated: ModelReviewJob) -> None:
        self.upsert_job(updated)

    def summary(self) -> Dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute("SELECT status, COUNT(*) AS count FROM model_review_jobs GROUP BY status").fetchall()
        return {row["status"]: int(row["count"] or 0) for row in rows}
