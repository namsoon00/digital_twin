import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from ..domain.events import DomainEvent, MONITORING_ALERTS_DETECTED
from ..domain.model_review import ModelReviewJob
from ..domain.notification_templates import DEFAULT_NOTIFICATION_TEMPLATES, NotificationTemplate, render_notification
from ..domain.notifications import NotificationJob
from ..domain.portfolio import AccountSnapshot, AlertEvent
from .settings import data_dir, read_json, service_db_path, settings_path, utc_now


def json_dumps(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


class OperationalConnection:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path or service_db_path()).resolve()
        self.ensure_schema()

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path))
        connection.row_factory = sqlite3.Row
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        return connection

    def ensure_schema(self) -> None:
        with self.connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS monitor_snapshots (
                    account_id TEXT PRIMARY KEY,
                    account_label TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL DEFAULT '',
                    mode TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    generated_at TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS monitor_sent (
                    key TEXT PRIMARY KEY,
                    sent_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS domain_events (
                    event_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    aggregate_id TEXT NOT NULL DEFAULT '',
                    occurred_at TEXT NOT NULL,
                    correlation_id TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL,
                    event_json TEXT NOT NULL
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_domain_events_name_time ON domain_events(name, occurred_at)")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS model_review_jobs (
                    job_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL DEFAULT '',
                    account_label TEXT NOT NULL DEFAULT '',
                    symbol TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '',
                    alert_key TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT '',
                    result TEXT NOT NULL DEFAULT '',
                    last_error TEXT NOT NULL DEFAULT '',
                    alert_lines_json TEXT NOT NULL DEFAULT '[]',
                    payload_json TEXT NOT NULL
                )
            """)
            connection.execute("CREATE INDEX IF NOT EXISTS idx_model_review_jobs_status ON model_review_jobs(status, created_at)")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS notification_jobs (
                    job_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL DEFAULT '',
                    account_label TEXT NOT NULL DEFAULT '',
                    message_type TEXT NOT NULL DEFAULT 'notification',
                    source_event_id TEXT NOT NULL DEFAULT '',
                    source_event_name TEXT NOT NULL DEFAULT '',
                    dedupe_key TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT '',
                    last_error TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL
                )
            """)
            self.ensure_columns(
                connection,
                "notification_jobs",
                {
                    "source_event_id": "TEXT NOT NULL DEFAULT ''",
                    "source_event_name": "TEXT NOT NULL DEFAULT ''",
                    "dedupe_key": "TEXT NOT NULL DEFAULT ''",
                },
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_notification_jobs_status ON notification_jobs(status, created_at)")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_notification_jobs_dedupe ON notification_jobs(dedupe_key) WHERE dedupe_key != ''")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS runtime_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS app_store (
                    store_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS notification_templates (
                    message_type TEXT PRIMARY KEY,
                    template TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                )
            """)

    def ensure_columns(self, connection, table: str, columns: Dict[str, str]) -> None:
        existing = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(" + table + ")").fetchall()
        }
        for name, definition in columns.items():
            if name not in existing:
                connection.execute("ALTER TABLE " + table + " ADD COLUMN " + name + " " + definition)


class SQLiteNotificationTemplateStore(OperationalConnection):
    def __init__(self, path: Optional[Path] = None):
        super().__init__(path)
        self.seed_defaults()

    def seed_defaults(self) -> None:
        stamp = utc_now()
        with self.connect() as connection:
            for message_type, payload in DEFAULT_NOTIFICATION_TEMPLATES.items():
                connection.execute(
                    """
                    INSERT OR IGNORE INTO notification_templates (
                        message_type, template, description, enabled, updated_at
                    )
                    VALUES (?, ?, ?, 1, ?)
                    """,
                    (
                        message_type,
                        str(payload.get("template") or ""),
                        str(payload.get("description") or ""),
                        stamp,
                    ),
                )

    def row_to_template(self, row) -> NotificationTemplate:
        return NotificationTemplate(
            message_type=row["message_type"],
            template=row["template"],
            description=row["description"],
            enabled=bool(row["enabled"]),
            updated_at=row["updated_at"],
        )

    def list(self) -> List[NotificationTemplate]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT message_type, template, description, enabled, updated_at FROM notification_templates ORDER BY message_type"
            ).fetchall()
        return [self.row_to_template(row) for row in rows]

    def get(self, message_type: str) -> NotificationTemplate:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT message_type, template, description, enabled, updated_at
                FROM notification_templates
                WHERE message_type = ?
                """,
                (str(message_type or "notification"),),
            ).fetchone()
            if not row:
                row = connection.execute(
                    """
                    SELECT message_type, template, description, enabled, updated_at
                    FROM notification_templates
                    WHERE message_type = 'default'
                    """
                ).fetchone()
        return self.row_to_template(row) if row else NotificationTemplate.default("default")

    def upsert(self, message_type: str, template: str, description: str = "", enabled: bool = True) -> NotificationTemplate:
        stamp = utc_now()
        key = str(message_type or "").strip()
        if not key:
            raise ValueError("message_type is required")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO notification_templates (message_type, template, description, enabled, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(message_type) DO UPDATE SET
                    template = excluded.template,
                    description = excluded.description,
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (key, str(template or ""), str(description or ""), 1 if enabled else 0, stamp),
            )
        return self.get(key)

    def reset(self, message_type: str) -> NotificationTemplate:
        key = str(message_type or "").strip() or "default"
        configured = DEFAULT_NOTIFICATION_TEMPLATES.get(key) or DEFAULT_NOTIFICATION_TEMPLATES["default"]
        return self.upsert(key, configured["template"], configured.get("description", ""), True)

    def render(self, message_type: str, context: Dict[str, object]) -> str:
        return render_notification(self.get(message_type), context)

    def render_job(self, job: NotificationJob) -> str:
        context = dict(job.context or {})
        context.setdefault("body", job.text)
        context.setdefault("messageType", job.message_type)
        context.setdefault("accountId", job.account_id)
        context.setdefault("accountLabel", job.account_label)
        return self.render(job.message_type, context)


class SQLiteAppStore(OperationalConnection):
    def __init__(self, path: Optional[Path] = None, legacy_path: Optional[Path] = None):
        self.legacy_path = legacy_path or data_dir() / "store.json"
        super().__init__(path)
        self.migrate_legacy_if_needed()

    def migrate_legacy_if_needed(self) -> None:
        if not self.legacy_path.exists():
            return
        with self.connect() as connection:
            existing = connection.execute("SELECT store_id FROM app_store WHERE store_id = 'default'").fetchone()
        if existing:
            return
        payload = read_json(self.legacy_path, {})
        if isinstance(payload, dict) and payload:
            self.replace(payload)

    def load(self) -> Dict[str, object]:
        with self.connect() as connection:
            row = connection.execute("SELECT payload_json FROM app_store WHERE store_id = 'default'").fetchone()
        if not row:
            return {}
        try:
            payload = json.loads(row["payload_json"])
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            return {}

    def replace(self, payload: Dict[str, object]) -> None:
        stamp = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO app_store (store_id, payload_json, updated_at)
                VALUES ('default', ?, ?)
                ON CONFLICT(store_id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (json_dumps(payload), stamp),
            )


class SQLiteRuntimeSettingsStore(OperationalConnection):
    def __init__(self, path: Optional[Path] = None, legacy_path: Optional[Path] = None):
        self.legacy_path = legacy_path or settings_path()
        super().__init__(path)
        self.migrate_legacy_if_needed()

    def migrate_legacy_if_needed(self) -> None:
        if not self.legacy_path.exists():
            return
        with self.connect() as connection:
            existing = int(connection.execute("SELECT COUNT(*) AS count FROM runtime_settings").fetchone()["count"])
        if existing:
            return
        payload = read_json(self.legacy_path, {})
        if isinstance(payload, dict):
            self.replace({str(key): str(value or "") for key, value in payload.items()})

    def load(self) -> Dict[str, str]:
        with self.connect() as connection:
            rows = connection.execute("SELECT key, value FROM runtime_settings ORDER BY key").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def replace(self, settings: Dict[str, object]) -> None:
        stamp = utc_now()
        with self.connect() as connection:
            connection.execute("DELETE FROM runtime_settings")
            for key, value in settings.items():
                connection.execute(
                    "INSERT INTO runtime_settings (key, value, updated_at) VALUES (?, ?, ?)",
                    (str(key), str(value or ""), stamp),
                )

    def save(self, settings: Dict[str, object]) -> None:
        stamp = utc_now()
        with self.connect() as connection:
            for key, value in settings.items():
                connection.execute(
                    """
                    INSERT INTO runtime_settings (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    (str(key), str(value or ""), stamp),
                )


class SQLiteMonitorStore(OperationalConnection):
    def __init__(self, path: Optional[Path] = None, legacy_path: Optional[Path] = None):
        self.legacy_path = legacy_path or data_dir() / "python-monitor-state.json"
        super().__init__(path)
        self.migrate_legacy_if_needed()
        self.payload = {"previous": self.load_previous(), "sent": self.load_sent()}

    def migrate_legacy_if_needed(self) -> None:
        if not self.legacy_path.exists():
            return
        with self.connect() as connection:
            snapshot_count = int(connection.execute("SELECT COUNT(*) AS count FROM monitor_snapshots").fetchone()["count"])
            sent_count = int(connection.execute("SELECT COUNT(*) AS count FROM monitor_sent").fetchone()["count"])
        if snapshot_count or sent_count:
            return
        payload = read_json(self.legacy_path, {"previous": {}, "sent": {}})
        if not isinstance(payload, dict):
            return
        for account_id, state in (payload.get("previous") or {}).items():
            if isinstance(state, dict):
                self.upsert_snapshot_state(str(account_id), state)
        stamp = utc_now()
        with self.connect() as connection:
            for key, sent_at in (payload.get("sent") or {}).items():
                connection.execute(
                    "INSERT OR REPLACE INTO monitor_sent (key, sent_at) VALUES (?, ?)",
                    (str(key), str(sent_at or stamp)),
                )

    def load_previous(self) -> Dict[str, object]:
        with self.connect() as connection:
            rows = connection.execute("SELECT account_id, payload_json FROM monitor_snapshots").fetchall()
        previous = {}
        for row in rows:
            try:
                previous[row["account_id"]] = json.loads(row["payload_json"])
            except json.JSONDecodeError:
                previous[row["account_id"]] = {}
        return previous

    def load_sent(self) -> Dict[str, object]:
        with self.connect() as connection:
            rows = connection.execute("SELECT key, sent_at FROM monitor_sent").fetchall()
        return {row["key"]: row["sent_at"] for row in rows}

    @property
    def previous(self) -> Dict[str, object]:
        return self.payload["previous"]

    @property
    def sent(self) -> Dict[str, object]:
        return self.payload["sent"]

    def upsert_snapshot_state(self, account_id: str, state: Dict[str, object]) -> None:
        stamp = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO monitor_snapshots (
                    account_id, account_label, provider, mode, status, generated_at, payload_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    account_label = excluded.account_label,
                    provider = excluded.provider,
                    mode = excluded.mode,
                    status = excluded.status,
                    generated_at = excluded.generated_at,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    account_id,
                    str(state.get("accountLabel") or ""),
                    str(state.get("provider") or ""),
                    str(state.get("mode") or ""),
                    str(state.get("status") or ""),
                    str(state.get("generatedAt") or ""),
                    json_dumps(state),
                    stamp,
                ),
            )

    def save_snapshot(self, snapshot: AccountSnapshot) -> None:
        state = snapshot.to_monitor_state()
        self.previous[snapshot.account_id] = state
        self.upsert_snapshot_state(snapshot.account_id, state)

    def mark_sent(self, events: Iterable[AlertEvent]) -> None:
        stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        keys = []
        for event in events:
            keys.extend([event.key, event.cadence_key()])
            self.sent[event.key] = stamp
            self.sent[event.cadence_key()] = stamp
        with self.connect() as connection:
            for key in keys:
                connection.execute(
                    "INSERT OR REPLACE INTO monitor_sent (key, sent_at) VALUES (?, ?)",
                    (key, stamp),
                )

    def write(self) -> None:
        pass


class SQLiteEventLog(OperationalConnection):
    def __init__(self, path: Optional[Path] = None, legacy_path: Optional[Path] = None):
        self.legacy_path = legacy_path or data_dir() / "domain-events.jsonl"
        super().__init__(path)
        self.migrate_legacy_if_needed()

    def migrate_legacy_if_needed(self) -> None:
        if not self.legacy_path.exists():
            return
        with self.connect() as connection:
            existing = int(connection.execute("SELECT COUNT(*) AS count FROM domain_events").fetchone()["count"])
        if existing:
            return
        with self.legacy_path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                self.insert_event_dict(payload)

    def insert_event_dict(self, event: Dict[str, object]) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO domain_events (
                    event_id, name, aggregate_id, occurred_at, correlation_id, payload_json, event_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(event.get("event_id") or event.get("eventId") or ""),
                    str(event.get("name") or ""),
                    str(event.get("aggregate_id") or event.get("aggregateId") or ""),
                    str(event.get("occurred_at") or event.get("occurredAt") or ""),
                    str(event.get("correlation_id") or event.get("correlationId") or ""),
                    json_dumps(event.get("payload") or {}),
                    json_dumps(event),
                ),
            )

    def handle(self, event: DomainEvent) -> None:
        self.insert_event_dict(event.to_dict())

    def events(self, name: str = "", aggregate_id: str = "", limit: int = 0) -> List[DomainEvent]:
        clauses = []
        params = []
        if name:
            clauses.append("name = ?")
            params.append(name)
        if aggregate_id:
            clauses.append("aggregate_id = ?")
            params.append(aggregate_id)
        sql = "SELECT event_json FROM domain_events"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY occurred_at, event_id"
        if limit:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        events = []
        for row in rows:
            try:
                events.append(DomainEvent.from_dict(json.loads(row["event_json"])))
            except json.JSONDecodeError:
                continue
        return events

    def event_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for event in self.events():
            counts[event.name] = counts.get(event.name, 0) + 1
        return counts


class SQLiteModelReviewJobStore(OperationalConnection):
    def __init__(self, path: Optional[Path] = None, legacy_path: Optional[Path] = None):
        self.legacy_path = legacy_path or data_dir() / "model-review-queue.json"
        super().__init__(path)
        self.migrate_legacy_if_needed()

    def migrate_legacy_if_needed(self) -> None:
        if not self.legacy_path.exists():
            return
        with self.connect() as connection:
            existing = int(connection.execute("SELECT COUNT(*) AS count FROM model_review_jobs").fetchone()["count"])
        if existing:
            return
        payload = read_json(self.legacy_path, {"jobs": []})
        for item in payload.get("jobs") or []:
            if isinstance(item, dict):
                self.upsert_job(ModelReviewJob.from_dict(item))

    def jobs(self) -> List[ModelReviewJob]:
        with self.connect() as connection:
            rows = connection.execute("SELECT payload_json FROM model_review_jobs ORDER BY created_at, job_id").fetchall()
        jobs = []
        for row in rows:
            try:
                jobs.append(ModelReviewJob.from_dict(json.loads(row["payload_json"])))
            except json.JSONDecodeError:
                continue
        return jobs

    def write_jobs(self, jobs: Iterable[ModelReviewJob]) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM model_review_jobs")
            for job in jobs:
                self.upsert_job_with_connection(connection, job)

    def upsert_job_with_connection(self, connection, job: ModelReviewJob) -> None:
        payload = job.to_dict()
        connection.execute(
            """
            INSERT INTO model_review_jobs (
                job_id, account_id, account_label, symbol, title, alert_key, status, attempts,
                created_at, updated_at, result, last_error, alert_lines_json, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                account_id = excluded.account_id,
                account_label = excluded.account_label,
                symbol = excluded.symbol,
                title = excluded.title,
                alert_key = excluded.alert_key,
                status = excluded.status,
                attempts = excluded.attempts,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at,
                result = excluded.result,
                last_error = excluded.last_error,
                alert_lines_json = excluded.alert_lines_json,
                payload_json = excluded.payload_json
            """,
            (
                job.job_id,
                job.account_id,
                job.account_label,
                job.symbol,
                job.title,
                job.alert_key,
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
        with self.connect() as connection:
            self.upsert_job_with_connection(connection, job)

    def enqueue(self, job: ModelReviewJob) -> bool:
        with self.connect() as connection:
            existing = connection.execute("SELECT job_id FROM model_review_jobs WHERE job_id = ?", (job.job_id,)).fetchone()
            if existing:
                return False
            self.upsert_job_with_connection(connection, job)
        return True

    def enqueue_from_event(self, event: DomainEvent) -> int:
        if event.name != MONITORING_ALERTS_DETECTED:
            return 0
        count = 0
        for item in event.payload.get("events") or []:
            if not isinstance(item, dict) or item.get("rule") != "monitorDecisionChange":
                continue
            if self.enqueue(ModelReviewJob.create(item)):
                count += 1
        return count

    def pending(self, limit: int = 1) -> List[ModelReviewJob]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM model_review_jobs
                WHERE status IN ('pending', 'failed')
                ORDER BY created_at, job_id
                LIMIT ?
                """,
                (int(limit or 1),),
            ).fetchall()
        jobs = []
        for row in rows:
            try:
                jobs.append(ModelReviewJob.from_dict(json.loads(row["payload_json"])))
            except json.JSONDecodeError:
                continue
        return jobs

    def update(self, updated: ModelReviewJob) -> None:
        self.upsert_job(updated)

    def mark_processing(self, job: ModelReviewJob) -> ModelReviewJob:
        job.status = "processing"
        job.attempts += 1
        job.updated_at = utc_now()
        self.update(job)
        return job

    def mark_done(self, job: ModelReviewJob, result: str) -> None:
        job.status = "done"
        job.result = result
        job.last_error = ""
        job.updated_at = utc_now()
        self.update(job)

    def mark_failed(self, job: ModelReviewJob, error: str) -> None:
        job.status = "failed"
        job.last_error = error
        job.updated_at = utc_now()
        self.update(job)

    def summary(self) -> Dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute("SELECT status, COUNT(*) AS count FROM model_review_jobs GROUP BY status").fetchall()
        return {row["status"]: int(row["count"]) for row in rows}


class SQLiteNotificationJobStore(OperationalConnection):
    def jobs(self) -> List[NotificationJob]:
        with self.connect() as connection:
            rows = connection.execute("SELECT payload_json FROM notification_jobs ORDER BY created_at, job_id").fetchall()
        jobs = []
        for row in rows:
            try:
                jobs.append(NotificationJob.from_dict(json.loads(row["payload_json"])))
            except json.JSONDecodeError:
                continue
        return jobs

    def upsert_job_with_connection(self, connection, job: NotificationJob) -> None:
        payload = job.to_dict()
        connection.execute(
            """
            INSERT INTO notification_jobs (
                job_id, account_id, account_label, message_type, source_event_id, source_event_name, dedupe_key, status, attempts,
                created_at, updated_at, last_error, text, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                account_id = excluded.account_id,
                account_label = excluded.account_label,
                message_type = excluded.message_type,
                source_event_id = excluded.source_event_id,
                source_event_name = excluded.source_event_name,
                dedupe_key = excluded.dedupe_key,
                status = excluded.status,
                attempts = excluded.attempts,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at,
                last_error = excluded.last_error,
                text = excluded.text,
                payload_json = excluded.payload_json
            """,
            (
                job.job_id,
                job.account_id,
                job.account_label,
                job.message_type,
                job.source_event_id,
                job.source_event_name,
                job.dedupe_key,
                job.status,
                job.attempts,
                job.created_at,
                job.updated_at,
                job.last_error,
                job.text,
                json_dumps(payload),
            ),
        )

    def upsert_job(self, job: NotificationJob) -> None:
        with self.connect() as connection:
            self.upsert_job_with_connection(connection, job)

    def enqueue(self, job: NotificationJob) -> bool:
        if not job.text.strip():
            return False
        with self.connect() as connection:
            existing = connection.execute("SELECT job_id FROM notification_jobs WHERE job_id = ?", (job.job_id,)).fetchone()
            if existing:
                return False
            if job.dedupe_key:
                existing = connection.execute(
                    "SELECT job_id FROM notification_jobs WHERE dedupe_key = ?",
                    (job.dedupe_key,),
                ).fetchone()
                if existing:
                    return False
            self.upsert_job_with_connection(connection, job)
        return True

    def pending(self, limit: int = 10) -> List[NotificationJob]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM notification_jobs
                WHERE status IN ('pending', 'failed')
                ORDER BY created_at, job_id
                LIMIT ?
                """,
                (int(limit or 10),),
            ).fetchall()
        jobs = []
        for row in rows:
            try:
                jobs.append(NotificationJob.from_dict(json.loads(row["payload_json"])))
            except json.JSONDecodeError:
                continue
        return jobs

    def update(self, updated: NotificationJob) -> None:
        self.upsert_job(updated)

    def mark_processing(self, job: NotificationJob) -> NotificationJob:
        job.status = "processing"
        job.attempts += 1
        job.updated_at = utc_now()
        self.update(job)
        return job

    def mark_done(self, job: NotificationJob) -> None:
        job.status = "done"
        job.last_error = ""
        job.updated_at = utc_now()
        self.update(job)

    def mark_failed(self, job: NotificationJob, error: str) -> None:
        job.status = "failed"
        job.last_error = error
        job.updated_at = utc_now()
        self.update(job)

    def summary(self) -> Dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute("SELECT status, COUNT(*) AS count FROM notification_jobs GROUP BY status").fetchall()
        return {row["status"]: int(row["count"]) for row in rows}
