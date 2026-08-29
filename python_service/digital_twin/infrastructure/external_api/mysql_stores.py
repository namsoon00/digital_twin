import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List

from ...application.external_data.contracts import (
    CollectionJob,
    CollectionPartition,
    DatasetDescriptor,
    ExternalSubject,
    SourceObservation,
)
from ...domain.events import (
    DomainEvent,
    external_provider_health_changed_event,
)
from ..mysql_operational_connection import MySQLOperationalConnection
from ..mysql_operational_events import insert_domain_event_with_connection
from ..mysql_operational_helpers import _json_loads
from ..operational_common import json_dumps


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    current = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: object):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def payload_hash(payload: Dict[str, object]) -> str:
    def stable(value: object):
        if isinstance(value, dict):
            return {
                str(key): stable(item)
                for key, item in value.items()
                if str(key) not in {"fetchedAt", "collectedAt", "checkedAt", "cryptoLastAttemptAt"}
            }
        if isinstance(value, list):
            return [stable(item) for item in value]
        return value

    raw = json.dumps(stable(payload if isinstance(payload, dict) else {}), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class MySQLExternalDataStore(MySQLOperationalConnection):
    """Durable work, provider budget, current facts, and collection telemetry."""

    def list_subjects(self) -> List[ExternalSubject]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT symbol, payload_json
                FROM market_quote_cache
                WHERE provider = 'toss' AND account_id = '__market_data__'
                ORDER BY updated_at DESC, symbol
                """
            ).fetchall()
        result: List[ExternalSubject] = []
        seen = set()
        fallback: List[ExternalSubject] = []
        for row in rows:
            payload = _json_loads(row.get("payload_json"), {})
            symbol = str(payload.get("symbol") or row.get("symbol") or "").upper().strip()
            if not symbol or symbol in seen:
                continue
            subject = ExternalSubject(
                subject_key=symbol,
                symbol=symbol,
                name=str(payload.get("name") or symbol),
                market=str(payload.get("market") or payload.get("exchange") or "").upper().strip(),
                currency=str(payload.get("currency") or "").upper().strip(),
                sector=str(payload.get("sector") or "").strip(),
                source=str(payload.get("collectionTarget") or payload.get("collectionPurpose") or "market-cache"),
            )
            fallback.append(subject)
            if str(payload.get("collectionPurpose") or "") == "account-focus":
                seen.add(symbol)
                result.append(subject)
        if result:
            return result
        return fallback

    def opendart_corp_code_assignments(self) -> Dict[str, str]:
        with self.connect() as connection:
            fact_rows = connection.execute(
                "SELECT subject_key, payload_json FROM external_fact_current "
                "WHERE dataset_id = 'opendart.disclosures' ORDER BY subject_key LIMIT 5000"
            ).fetchall()
            state_rows = connection.execute(
                "SELECT partition_key, watermark_json FROM external_dataset_state "
                "WHERE dataset_id IN ('opendart.disclosures', 'opendart.company_facts') "
                "ORDER BY partition_key LIMIT 10000"
            ).fetchall()
        assignments: Dict[str, str] = {}
        for row in fact_rows or []:
            symbol = str(row.get("subject_key") or "").upper().strip()
            payload = _json_loads(row.get("payload_json"), {})
            disclosures = payload.get("dartDisclosures") if isinstance(payload.get("dartDisclosures"), dict) else {}
            disclosure = disclosures.get(symbol) if isinstance(disclosures.get(symbol), dict) else {}
            corp_code = str(disclosure.get("corpCode") or "").strip()
            if symbol and corp_code:
                assignments[symbol] = corp_code.zfill(8)
        for row in state_rows or []:
            symbol = str(row.get("partition_key") or "").upper().strip()
            watermark = _json_loads(row.get("watermark_json"), {})
            corp_code = str(watermark.get("corpCode") or "").strip()
            if symbol and corp_code:
                assignments[symbol] = corp_code.zfill(8)
        return assignments

    def sync_partitions(
        self,
        plans: Iterable[tuple],
        known_dataset_ids: Iterable[str],
        now: datetime = None,
    ) -> int:
        current = now or utc_now()
        stamp = iso(current)
        rows = list(plans or [])
        datasets = sorted({str(item or "") for item in known_dataset_ids or [] if str(item or "")})

        def mutation(connection):
            if datasets:
                placeholders = ", ".join(["%s"] * len(datasets))
                connection.execute(
                    "UPDATE external_dataset_state SET active = 0, updated_at = %s "
                    "WHERE dataset_id IN (" + placeholders + ")",
                    tuple([stamp] + datasets),
                )
            saved = 0
            for descriptor, partition in rows:
                if not isinstance(descriptor, DatasetDescriptor) or not isinstance(partition, CollectionPartition):
                    continue
                connection.execute(
                    """
                    INSERT INTO external_dataset_state (
                        dataset_id, partition_key, provider_id, subject_json, watermark_json,
                        priority, active, job_status, next_due_at, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, '{}', %s, 1, 'pending', %s, %s, %s)
                    ON DUPLICATE KEY UPDATE provider_id = VALUES(provider_id),
                        subject_json = VALUES(subject_json), priority = VALUES(priority),
                        active = 1, updated_at = VALUES(updated_at)
                    """,
                    (
                        descriptor.dataset_id,
                        partition.partition_key,
                        descriptor.provider_id,
                        json_dumps(partition.subject.to_dict()),
                        int(partition.priority),
                        stamp,
                        stamp,
                        stamp,
                    ),
                )
                saved += 1
            return saved

        return int(self.transaction_with_deadlock_retry("external-data-sync-partitions", mutation) or 0)

    def enqueue_followups(self, plans: Iterable[tuple], now: datetime = None) -> int:
        """Insert immutable document work once; normal leases own retries."""
        current = now or utc_now()
        stamp = iso(current)
        rows = list(plans or [])
        if not rows:
            return 0

        def mutation(connection):
            saved = 0
            for descriptor, request in rows:
                if not isinstance(descriptor, DatasetDescriptor):
                    continue
                cursor = connection.execute(
                    """
                    INSERT IGNORE INTO external_dataset_state (
                        dataset_id, partition_key, provider_id, subject_json, watermark_json,
                        priority, active, job_status, next_due_at, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, 1, 'pending', %s, %s, %s)
                    """,
                    (
                        descriptor.dataset_id,
                        str(request.partition_key or "")[:191],
                        descriptor.provider_id,
                        json_dumps(request.subject.to_dict()),
                        json_dumps(request.watermark),
                        int(request.priority or descriptor.priority),
                        stamp,
                        stamp,
                        stamp,
                    ),
                )
                saved += int(cursor.rowcount or 0)
            return saved

        return int(self.transaction_with_deadlock_retry("external-data-enqueue-followups", mutation) or 0)

    def claim_due(
        self,
        worker_id: str,
        limit: int,
        lease_seconds: int,
        now: datetime = None,
    ) -> List[CollectionJob]:
        current = now or utc_now()
        stamp = iso(current)
        lease_until = iso(current + timedelta(seconds=max(15, int(lease_seconds or 120))))
        row_limit = max(1, min(100, int(limit or 1)))

        def mutation(connection):
            rows = connection.execute(
                """
                SELECT dataset_id, partition_key, provider_id, subject_json, watermark_json,
                       priority, attempt_count, lease_owner, lease_until
                FROM external_dataset_state
                WHERE active = 1
                  AND next_due_at <= %s
                  AND (job_status = 'pending' OR lease_until = '' OR lease_until <= %s)
                ORDER BY priority DESC, next_due_at, dataset_id, partition_key
                LIMIT %s
                FOR UPDATE SKIP LOCKED
                """,
                (stamp, stamp, row_limit),
            ).fetchall()
            jobs: List[CollectionJob] = []
            for row in rows:
                connection.execute(
                    """
                    UPDATE external_dataset_state
                    SET job_status = 'leased', lease_owner = %s, lease_until = %s,
                        last_attempt_at = %s, attempt_count = attempt_count + 1, updated_at = %s
                    WHERE dataset_id = %s AND partition_key = %s
                    """,
                    (
                        worker_id,
                        lease_until,
                        stamp,
                        stamp,
                        row["dataset_id"],
                        row["partition_key"],
                    ),
                )
                jobs.append(CollectionJob(
                    dataset_id=str(row.get("dataset_id") or ""),
                    partition_key=str(row.get("partition_key") or ""),
                    provider_id=str(row.get("provider_id") or ""),
                    priority=int(row.get("priority") or 0),
                    subject=ExternalSubject.from_dict(_json_loads(row.get("subject_json"), {})),
                    attempt_count=int(row.get("attempt_count") or 0) + 1,
                    lease_owner=str(worker_id or ""),
                    lease_until=lease_until,
                    watermark=_json_loads(row.get("watermark_json"), {}),
                ))
            return jobs

        return list(self.transaction_with_deadlock_retry("external-data-claim-due", mutation) or [])

    def reserve_provider_call(
        self,
        descriptor: DatasetDescriptor,
        now: datetime = None,
    ) -> Dict[str, object]:
        current = now or utc_now()
        stamp = iso(current)
        today = current.date().isoformat()
        dataset_bucket = descriptor.dataset_id
        provider_bucket = "__provider__"

        def mutation(connection):
            connection.execute(
                """
                INSERT IGNORE INTO external_provider_state (
                    provider_id, bucket_id, window_date, updated_at
                ) VALUES (%s, %s, %s, %s), (%s, %s, %s, %s)
                """,
                (
                    descriptor.provider_id,
                    provider_bucket,
                    today,
                    stamp,
                    descriptor.provider_id,
                    dataset_bucket,
                    today,
                    stamp,
                ),
            )
            provider_row = connection.execute(
                """
                SELECT * FROM external_provider_state
                WHERE provider_id = %s AND bucket_id = %s
                FOR UPDATE
                """,
                (descriptor.provider_id, provider_bucket),
            ).fetchone() or {}
            dataset_row = connection.execute(
                """
                SELECT * FROM external_provider_state
                WHERE provider_id = %s AND bucket_id = %s
                FOR UPDATE
                """,
                (descriptor.provider_id, dataset_bucket),
            ).fetchone() or {}
            if str(provider_row.get("window_date") or "") != today:
                provider_row["window_date"] = today
                provider_row["request_count"] = 0
            if str(dataset_row.get("window_date") or "") != today:
                dataset_row["window_date"] = today
                dataset_row["request_count"] = 0
            circuit_until = parse_iso(dataset_row.get("circuit_open_until"))
            if circuit_until and circuit_until > current:
                return {
                    "allowed": False,
                    "reason": "circuit-open",
                    "nextAllowedAt": iso(circuit_until),
                }
            next_allowed = parse_iso(provider_row.get("next_allowed_at"))
            if next_allowed and next_allowed > current:
                return {
                    "allowed": False,
                    "reason": "rate-limited",
                    "nextAllowedAt": iso(next_allowed),
                }
            dataset_count = int(dataset_row.get("request_count") or 0)
            if descriptor.daily_request_budget and dataset_count >= descriptor.daily_request_budget:
                tomorrow = datetime.combine(current.date() + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
                connection.execute(
                    """
                    UPDATE external_provider_state
                    SET next_allowed_at = %s, health_state = 'deferred', updated_at = %s
                    WHERE provider_id = %s AND bucket_id = %s
                    """,
                    (iso(tomorrow), stamp, descriptor.provider_id, dataset_bucket),
                )
                return {
                    "allowed": False,
                    "reason": "daily-budget",
                    "nextAllowedAt": iso(tomorrow),
                }
            next_stamp = iso(current + timedelta(seconds=max(0, descriptor.rate_limit_seconds)))
            connection.execute(
                """
                UPDATE external_provider_state
                SET window_date = %s, request_count = %s, next_allowed_at = %s,
                    last_attempt_at = %s, updated_at = %s
                WHERE provider_id = %s AND bucket_id = %s
                """,
                (
                    today,
                    int(provider_row.get("request_count") or 0) + 1,
                    next_stamp,
                    stamp,
                    stamp,
                    descriptor.provider_id,
                    provider_bucket,
                ),
            )
            connection.execute(
                """
                UPDATE external_provider_state
                SET window_date = %s, request_count = %s, next_allowed_at = '',
                    last_attempt_at = %s, updated_at = %s
                WHERE provider_id = %s AND bucket_id = %s
                """,
                (
                    today,
                    dataset_count + 1,
                    stamp,
                    stamp,
                    descriptor.provider_id,
                    dataset_bucket,
                ),
            )
            return {
                "allowed": True,
                "requestCount": dataset_count + 1,
                "providerRequestCount": int(provider_row.get("request_count") or 0) + 1,
                "nextAllowedAt": next_stamp,
            }

        return dict(self.transaction_with_deadlock_retry("external-provider-reserve", mutation) or {})

    def defer_job(self, job: CollectionJob, next_due_at: str, reason: str = "") -> None:
        stamp = iso(utc_now())
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE external_dataset_state
                SET job_status = 'pending', next_due_at = %s, lease_owner = '', lease_until = '',
                    last_error = %s, updated_at = %s
                WHERE dataset_id = %s AND partition_key = %s AND lease_owner = %s
                """,
                (
                    str(next_due_at or stamp),
                    str(reason or "")[:500],
                    stamp,
                    job.dataset_id,
                    job.partition_key,
                    job.lease_owner,
                ),
            )

    def make_due(self, dataset_ids: Iterable[str] = None) -> int:
        datasets = sorted({str(item or "") for item in dataset_ids or [] if str(item or "")})
        stamp = iso(utc_now())
        sql = "UPDATE external_dataset_state SET next_due_at = %s, job_status = 'pending', lease_owner = '', lease_until = '', updated_at = %s WHERE active = 1"
        params: List[object] = [stamp, stamp]
        if datasets:
            placeholders = ", ".join(["%s"] * len(datasets))
            sql += " AND dataset_id IN (" + placeholders + ")"
            params.extend(datasets)
        with self.transaction() as connection:
            cursor = connection.execute(sql, tuple(params))
        return int(cursor.rowcount or 0)

    def current_fact(self, dataset_id: str, subject_key: str) -> Dict[str, object]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM external_fact_current
                WHERE dataset_id = %s AND subject_key = %s
                LIMIT 1
                """,
                (str(dataset_id or ""), str(subject_key or "")),
            ).fetchone() or {}
        return self._fact_row(row)

    def seed_fact(
        self,
        dataset_id: str,
        subject_key: str,
        provider_id: str,
        source_revision: str,
        source_as_of: str,
        fetched_at: str,
        freshness_seconds: int,
        payload: Dict[str, object],
        quality: Dict[str, object] = None,
    ) -> bool:
        stamp = iso(utc_now())
        content_hash = payload_hash(payload)
        fetched = parse_iso(fetched_at) or utc_now()
        expires_at = iso(fetched + timedelta(seconds=max(10, int(freshness_seconds or 60))))
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT IGNORE INTO external_fact_current (
                    dataset_id, subject_key, provider_id, source_revision, payload_hash,
                    source_as_of, fetched_at, expires_at, payload_json, quality_json, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(dataset_id or "")[:191],
                    str(subject_key or "global")[:191],
                    str(provider_id or "")[:96],
                    str(source_revision or content_hash)[:191],
                    content_hash,
                    str(source_as_of or "")[:80],
                    iso(fetched),
                    expires_at,
                    json_dumps(payload),
                    json_dumps(quality or {"dataUsable": True, "migration": "legacy-external-signals"}),
                    stamp,
                ),
            )
        return bool(cursor.rowcount)

    def complete_observation(
        self,
        job: CollectionJob,
        descriptor: DatasetDescriptor,
        observation: SourceObservation,
        next_due_at: str,
        event: DomainEvent = None,
    ) -> Dict[str, object]:
        stamp = iso(utc_now())
        content_hash = payload_hash(observation.payload)
        expires_at = iso(utc_now() + timedelta(seconds=descriptor.resolved_freshness_seconds(self.runtime_settings)))

        def mutation(connection):
            previous = connection.execute(
                """
                SELECT payload_hash, source_revision FROM external_fact_current
                WHERE dataset_id = %s AND subject_key = %s
                FOR UPDATE
                """,
                (observation.dataset_id, observation.subject_key),
            ).fetchone() or {}
            changed = str(previous.get("payload_hash") or "") != content_hash
            connection.execute(
                """
                INSERT INTO external_fact_current (
                    dataset_id, subject_key, provider_id, source_revision, payload_hash,
                    source_as_of, fetched_at, expires_at, payload_json, quality_json, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE provider_id = VALUES(provider_id),
                    source_revision = VALUES(source_revision), payload_hash = VALUES(payload_hash),
                    source_as_of = VALUES(source_as_of), fetched_at = VALUES(fetched_at),
                    expires_at = VALUES(expires_at), payload_json = VALUES(payload_json),
                    quality_json = VALUES(quality_json), updated_at = VALUES(updated_at)
                """,
                (
                    observation.dataset_id,
                    observation.subject_key,
                    observation.provider_id,
                    str(observation.source_revision or content_hash)[:191],
                    content_hash,
                    str(observation.source_as_of or "")[:80],
                    observation.fetched_at,
                    expires_at,
                    json_dumps(observation.payload),
                    json_dumps(observation.quality),
                    stamp,
                ),
            )
            revision_inserted = False
            if changed and descriptor.revision_mode != "none":
                revision_id = hashlib.sha256(
                    (observation.dataset_id + "\n" + observation.subject_key + "\n" + str(observation.source_revision or content_hash)).encode("utf-8")
                ).hexdigest()
                cursor = connection.execute(
                    """
                    INSERT IGNORE INTO external_fact_revision (
                        revision_id, dataset_id, subject_key, provider_id, source_revision,
                        payload_hash, source_as_of, fetched_at, payload_json, quality_json, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        revision_id,
                        observation.dataset_id,
                        observation.subject_key,
                        observation.provider_id,
                        str(observation.source_revision or content_hash)[:191],
                        content_hash,
                        str(observation.source_as_of or "")[:80],
                        observation.fetched_at,
                        json_dumps(observation.payload),
                        json_dumps(observation.quality),
                        stamp,
                    ),
                )
                revision_inserted = bool(cursor.rowcount)
            if changed and event:
                insert_domain_event_with_connection(connection, event)
            completed_once = descriptor.completion_mode == "once"
            connection.execute(
                """
                UPDATE external_dataset_state
                SET active = %s, job_status = %s, next_due_at = %s, last_success_at = %s,
                    source_as_of = %s, watermark_json = %s, lease_owner = '', lease_until = '',
                    attempt_count = 0, consecutive_failures = 0, last_error = '', updated_at = %s
                WHERE dataset_id = %s AND partition_key = %s AND lease_owner = %s
                """,
                (
                    0 if completed_once else 1,
                    "completed" if completed_once else "pending",
                    "" if completed_once else next_due_at,
                    stamp,
                    str(observation.source_as_of or "")[:80],
                    json_dumps(observation.watermark),
                    stamp,
                    job.dataset_id,
                    job.partition_key,
                    job.lease_owner,
                ),
            )
            return {"changed": changed, "revisionInserted": revision_inserted, "payloadHash": content_hash}

        return dict(self.transaction_with_deadlock_retry("external-data-complete-observation", mutation) or {})

    def complete_empty_observation(
        self,
        job: CollectionJob,
        observation: SourceObservation,
        next_due_at: str,
    ) -> Dict[str, object]:
        """Complete a valid no-data poll without erasing the last usable fact."""
        stamp = iso(utc_now())

        def mutation(connection):
            previous = connection.execute(
                """
                SELECT 1 AS present
                FROM external_fact_current
                WHERE dataset_id = %s AND subject_key = %s
                LIMIT 1
                """,
                (observation.dataset_id, observation.subject_key),
            ).fetchone() or {}
            connection.execute(
                """
                UPDATE external_dataset_state
                SET job_status = 'pending', next_due_at = %s, last_success_at = %s,
                    source_as_of = %s, watermark_json = %s, lease_owner = '', lease_until = '',
                    attempt_count = 0, consecutive_failures = 0, last_error = '', updated_at = %s
                WHERE dataset_id = %s AND partition_key = %s AND lease_owner = %s
                """,
                (
                    next_due_at,
                    stamp,
                    str(observation.source_as_of or "")[:80],
                    json_dumps(observation.watermark),
                    stamp,
                    job.dataset_id,
                    job.partition_key,
                    job.lease_owner,
                ),
            )
            return {"changed": False, "retainedPreviousFact": bool(previous.get("present"))}

        return dict(self.transaction_with_deadlock_retry("external-data-complete-empty", mutation) or {})

    def fail_job(
        self,
        job: CollectionJob,
        descriptor: DatasetDescriptor,
        error: Exception,
        next_due_at: str,
    ) -> Dict[str, object]:
        stamp = iso(utc_now())
        message = str(error or type(error).__name__)[:500]

        def mutation(connection):
            state = connection.execute(
                """
                SELECT health_state, consecutive_failures
                FROM external_provider_state
                WHERE provider_id = %s AND bucket_id = %s
                FOR UPDATE
                """,
                (descriptor.provider_id, descriptor.dataset_id),
            ).fetchone() or {}
            previous_state = str(state.get("health_state") or "unknown")
            failures = int(state.get("consecutive_failures") or 0) + 1
            current_state = "circuit_open" if failures >= descriptor.failure_threshold else "failed"
            circuit_until = (
                iso(utc_now() + timedelta(seconds=descriptor.circuit_cooldown_seconds))
                if current_state == "circuit_open"
                else ""
            )
            connection.execute(
                """
                INSERT INTO external_provider_state (
                    provider_id, bucket_id, window_date, consecutive_failures, health_state,
                    circuit_open_until, last_attempt_at, last_error, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE consecutive_failures = VALUES(consecutive_failures),
                    health_state = VALUES(health_state), circuit_open_until = VALUES(circuit_open_until),
                    last_attempt_at = VALUES(last_attempt_at), last_error = VALUES(last_error),
                    updated_at = VALUES(updated_at)
                """,
                (
                    descriptor.provider_id,
                    descriptor.dataset_id,
                    utc_now().date().isoformat(),
                    failures,
                    current_state,
                    circuit_until,
                    stamp,
                    message,
                    stamp,
                ),
            )
            connection.execute(
                """
                UPDATE external_dataset_state
                SET job_status = 'pending', next_due_at = %s, lease_owner = '', lease_until = '',
                    consecutive_failures = consecutive_failures + 1, last_error = %s, updated_at = %s
                WHERE dataset_id = %s AND partition_key = %s AND lease_owner = %s
                """,
                (next_due_at, message, stamp, job.dataset_id, job.partition_key, job.lease_owner),
            )
            health_changed = previous_state != current_state
            if health_changed:
                insert_domain_event_with_connection(
                    connection,
                    external_provider_health_changed_event(
                        descriptor.provider_id,
                        descriptor.dataset_id,
                        previous_state,
                        current_state,
                        message,
                    ),
                )
            return {"state": current_state, "healthChanged": health_changed, "circuitOpenUntil": circuit_until}

        return dict(self.transaction_with_deadlock_retry("external-data-fail-job", mutation) or {})

    def mark_provider_success(self, descriptor: DatasetDescriptor) -> Dict[str, object]:
        stamp = iso(utc_now())

        def mutation(connection):
            row = connection.execute(
                """
                SELECT health_state FROM external_provider_state
                WHERE provider_id = %s AND bucket_id = %s
                FOR UPDATE
                """,
                (descriptor.provider_id, descriptor.dataset_id),
            ).fetchone() or {}
            previous = str(row.get("health_state") or "unknown")
            connection.execute(
                """
                INSERT INTO external_provider_state (
                    provider_id, bucket_id, window_date, health_state,
                    last_success_at, last_attempt_at, updated_at
                ) VALUES (%s, %s, %s, 'healthy', %s, %s, %s)
                ON DUPLICATE KEY UPDATE consecutive_failures = 0, health_state = 'healthy',
                    circuit_open_until = '', last_error = '', last_success_at = VALUES(last_success_at),
                    last_attempt_at = VALUES(last_attempt_at), updated_at = VALUES(updated_at)
                """,
                (
                    descriptor.provider_id,
                    descriptor.dataset_id,
                    utc_now().date().isoformat(),
                    stamp,
                    stamp,
                    stamp,
                ),
            )
            changed = previous not in {"unknown", "healthy"}
            if changed:
                insert_domain_event_with_connection(
                    connection,
                    external_provider_health_changed_event(
                        descriptor.provider_id,
                        descriptor.dataset_id,
                        previous,
                        "healthy",
                        "provider collection recovered",
                    ),
                )
            return {"previousState": previous, "state": "healthy", "healthChanged": changed}

        return dict(self.transaction_with_deadlock_retry("external-provider-success", mutation) or {})

    def record_run(
        self,
        job: CollectionJob,
        status: str,
        started_at: str,
        completed_at: str,
        duration_ms: int,
        response_bytes: int = 0,
        source_as_of: str = "",
        source_revision: str = "",
        material_change: bool = False,
        error_message: str = "",
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO external_collection_runs (
                    run_id, dataset_id, partition_key, provider_id, worker_id, run_status,
                    started_at, completed_at, duration_ms, response_bytes, source_as_of,
                    source_revision, material_change, error_message, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    uuid.uuid4().hex,
                    job.dataset_id,
                    job.partition_key,
                    job.provider_id,
                    job.lease_owner,
                    str(status or "unknown")[:32],
                    started_at,
                    completed_at,
                    max(0, int(duration_ms or 0)),
                    max(0, int(response_bytes or 0)),
                    str(source_as_of or "")[:80],
                    str(source_revision or "")[:191],
                    1 if material_change else 0,
                    str(error_message or "")[:500],
                    completed_at,
                ),
            )

    def cleanup_history(
        self,
        run_retention_days: int = 30,
        revision_retention_days: int = 365,
        batch_size: int = 1000,
        now: datetime = None,
    ) -> Dict[str, object]:
        current = now or utc_now()
        bounded = max(10, min(10000, int(batch_size or 1000)))
        run_cutoff = iso(current - timedelta(days=max(1, int(run_retention_days or 30))))
        revision_cutoff = iso(current - timedelta(days=max(30, int(revision_retention_days or 365))))
        with self.transaction() as connection:
            run_cursor = connection.execute(
                "DELETE FROM external_collection_runs WHERE completed_at < %s ORDER BY completed_at LIMIT %s",
                (run_cutoff, bounded),
            )
            revision_cursor = connection.execute(
                "DELETE FROM external_fact_revision WHERE created_at < %s ORDER BY created_at LIMIT %s",
                (revision_cutoff, bounded),
            )
        return {
            "status": "ok",
            "deletedRuns": int(run_cursor.rowcount or 0),
            "deletedRevisions": int(revision_cursor.rowcount or 0),
            "runCutoff": run_cutoff,
            "revisionCutoff": revision_cutoff,
        }

    def list_current(self, subject_keys: Iterable[str] = None) -> List[Dict[str, object]]:
        subjects = sorted({str(item or "").strip() for item in subject_keys or [] if str(item or "").strip()})
        sql = "SELECT * FROM external_fact_current"
        params: List[object] = []
        if subjects:
            placeholders = ", ".join(["%s"] * len(subjects))
            sql += " WHERE subject_key = 'global' OR subject_key IN (" + placeholders + ")"
            params.extend(subjects)
        sql += " ORDER BY dataset_id, subject_key"
        with self.connect() as connection:
            rows = connection.execute(sql, tuple(params)).fetchall()
        return [self._fact_row(row) for row in rows]

    def provider_statuses(self) -> List[Dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT provider_id, bucket_id, request_count, next_allowed_at, circuit_open_until,
                       consecutive_failures, health_state, last_attempt_at, last_success_at,
                       last_error, updated_at
                FROM external_provider_state
                ORDER BY provider_id, bucket_id
                """
            ).fetchall()
        shared = {
            str(row.get("provider_id") or ""): row
            for row in rows
            if str(row.get("bucket_id") or "") == "__provider__"
        }
        dataset_rows = [row for row in rows if str(row.get("bucket_id") or "") != "__provider__"]
        return [
            {
                "providerId": str(row.get("provider_id") or ""),
                "datasetId": str(row.get("bucket_id") or ""),
                "requestCount": int(row.get("request_count") or 0),
                "providerRequestCount": int((shared.get(str(row.get("provider_id") or "")) or {}).get("request_count") or 0),
                "nextAllowedAt": str((shared.get(str(row.get("provider_id") or "")) or {}).get("next_allowed_at") or row.get("next_allowed_at") or ""),
                "circuitOpenUntil": str(row.get("circuit_open_until") or ""),
                "consecutiveFailures": int(row.get("consecutive_failures") or 0),
                "state": str(row.get("health_state") or "unknown"),
                "lastAttemptAt": str(row.get("last_attempt_at") or ""),
                "lastSuccessAt": str(row.get("last_success_at") or ""),
                "lastError": str(row.get("last_error") or ""),
                "updatedAt": str(row.get("updated_at") or ""),
            }
            for row in dataset_rows
        ]

    def summary(self) -> Dict[str, object]:
        with self.connect() as connection:
            dataset_rows = connection.execute(
                """
                SELECT dataset_id, provider_id, job_status, COUNT(*) AS row_count,
                       SUM(CASE WHEN active = 1 THEN 1 ELSE 0 END) AS active_count,
                       SUM(CASE WHEN active = 1 AND next_due_at <= %s THEN 1 ELSE 0 END) AS due_count,
                       MIN(next_due_at) AS next_due_at, MAX(last_success_at) AS last_success_at,
                       MAX(source_as_of) AS source_as_of, MAX(last_error) AS last_error
                FROM external_dataset_state
                WHERE active = 1
                   OR (job_status = 'completed' AND dataset_id IN ('opendart.document', 'sec.document'))
                GROUP BY dataset_id, provider_id, job_status
                ORDER BY dataset_id, job_status
                """,
                (iso(utc_now()),),
            ).fetchall()
            fact_row = connection.execute(
                """
                SELECT COUNT(*) AS fact_count, COALESCE(SUM(OCTET_LENGTH(payload_json)), 0) AS payload_bytes,
                       MIN(expires_at) AS oldest_expiry, MAX(updated_at) AS latest_update
                FROM external_fact_current
                """
            ).fetchone() or {}
            run_rows = connection.execute(
                """
                SELECT dataset_id, run_status, COUNT(*) AS run_count,
                       ROUND(AVG(duration_ms), 1) AS average_duration_ms,
                       MAX(duration_ms) AS max_duration_ms, MAX(completed_at) AS last_completed_at
                FROM external_collection_runs
                WHERE completed_at >= %s
                GROUP BY dataset_id, run_status
                ORDER BY dataset_id, run_status
                """,
                (iso(utc_now() - timedelta(hours=24)),),
            ).fetchall()
        return {
            "datasets": [
                {
                    "datasetId": str(row.get("dataset_id") or ""),
                    "providerId": str(row.get("provider_id") or ""),
                    "status": str(row.get("job_status") or ""),
                    "partitionCount": int(row.get("row_count") or 0),
                    "activeCount": int(row.get("active_count") or 0),
                    "completedCount": int(row.get("row_count") or 0) if str(row.get("job_status") or "") == "completed" else 0,
                    "dueCount": int(row.get("due_count") or 0),
                    "nextDueAt": str(row.get("next_due_at") or ""),
                    "lastSuccessAt": str(row.get("last_success_at") or ""),
                    "sourceAsOf": str(row.get("source_as_of") or ""),
                    "lastError": str(row.get("last_error") or ""),
                }
                for row in dataset_rows
            ],
            "facts": {
                "count": int(fact_row.get("fact_count") or 0),
                "payloadBytes": int(fact_row.get("payload_bytes") or 0),
                "oldestExpiry": str(fact_row.get("oldest_expiry") or ""),
                "latestUpdate": str(fact_row.get("latest_update") or ""),
            },
            "providers": self.provider_statuses(),
            "runs24h": [
                {
                    "datasetId": str(row.get("dataset_id") or ""),
                    "status": str(row.get("run_status") or ""),
                    "count": int(row.get("run_count") or 0),
                    "averageDurationMs": float(row.get("average_duration_ms") or 0),
                    "maxDurationMs": int(row.get("max_duration_ms") or 0),
                    "lastCompletedAt": str(row.get("last_completed_at") or ""),
                }
                for row in run_rows
            ],
        }

    @staticmethod
    def _fact_row(row: Dict[str, object]) -> Dict[str, object]:
        if not row:
            return {}
        expires_at = str(row.get("expires_at") or "")
        expiry = parse_iso(expires_at)
        freshness = "fresh" if expiry and expiry >= utc_now() else "stale"
        return {
            "datasetId": str(row.get("dataset_id") or ""),
            "subjectKey": str(row.get("subject_key") or ""),
            "providerId": str(row.get("provider_id") or ""),
            "sourceRevision": str(row.get("source_revision") or ""),
            "payloadHash": str(row.get("payload_hash") or ""),
            "sourceAsOf": str(row.get("source_as_of") or ""),
            "fetchedAt": str(row.get("fetched_at") or ""),
            "expiresAt": expires_at,
            "freshnessState": freshness,
            "payload": _json_loads(row.get("payload_json"), {}),
            "quality": _json_loads(row.get("quality_json"), {}),
            "updatedAt": str(row.get("updated_at") or ""),
        }
