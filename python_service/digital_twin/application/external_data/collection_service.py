import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List

from ...domain.events import external_fact_changed_event
from .contracts import DatasetDescriptor, ExternalSubject, setting_enabled
from .fact_transition_service import ExternalFactTransitionService


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    current = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def integer_setting(
    settings: Dict[str, object],
    key: str,
    fallback: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(float(str(settings.get(key) or fallback)))
    except (TypeError, ValueError):
        value = fallback
    return max(minimum, min(maximum, value))


def next_due_at(descriptor: DatasetDescriptor, settings: Dict[str, object], partition_key: str) -> str:
    cadence = descriptor.resolved_cadence_seconds(settings)
    digest = hashlib.sha256((descriptor.dataset_id + ":" + partition_key).encode("utf-8")).digest()
    jitter_ratio = (int.from_bytes(digest[:2], "big") / 65535.0 - 0.5) * 0.1
    return iso(utc_now() + timedelta(seconds=max(10, int(cadence * (1.0 + jitter_ratio)))))


class ExternalDataCollectionService:
    def __init__(
        self,
        settings: Dict[str, object],
        registry,
        store,
        transition_service=None,
        legacy_importer=None,
        evidence_reconciler=None,
        worker_id: str = "external-data-1",
        now_provider=None,
    ):
        self.settings = dict(settings or {})
        self.registry = registry
        self.store = store
        self.transition_service = transition_service or ExternalFactTransitionService()
        self.legacy_importer = legacy_importer
        self.evidence_reconciler = evidence_reconciler
        self.worker_id = str(worker_id or "external-data-1")
        self.now_provider = now_provider or utc_now
        self._last_partition_sync_at = None
        self._last_cleanup_at = None

    def enabled(self) -> bool:
        return setting_enabled(self.settings, "externalDataPlatformEnabled", True)

    def interval_seconds(self) -> int:
        return integer_setting(self.settings, "externalDataWorkerIntervalSeconds", 15, 5, 3600)

    def sync_interval_seconds(self) -> int:
        return integer_setting(self.settings, "externalDataSubjectRefreshSeconds", 300, 30, 86400)

    def batch_size(self) -> int:
        return integer_setting(self.settings, "externalDataWorkerBatchSize", 6, 1, 100)

    def concurrency(self) -> int:
        return integer_setting(self.settings, "externalDataWorkerConcurrency", 3, 1, 12)

    def lease_seconds(self) -> int:
        return integer_setting(self.settings, "externalDataLeaseSeconds", 180, 30, 3600)

    def inline_rate_limit_wait_seconds(self) -> int:
        return integer_setting(self.settings, "externalDataRateLimitInlineWaitSeconds", 2, 0, 10)

    def should_sync_partitions(self, force: bool = False) -> bool:
        if force or not self._last_partition_sync_at:
            return True
        return (self.now_provider() - self._last_partition_sync_at).total_seconds() >= self.sync_interval_seconds()

    def sync_partitions(self, subjects: Iterable[ExternalSubject] = None, force: bool = False) -> Dict[str, object]:
        if not self.should_sync_partitions(force=force) and subjects is None:
            return {"status": "fresh", "synced": False, "partitionCount": 0}
        subject_rows = list(subjects) if subjects is not None else self.store.list_subjects()
        partitions = self.registry.desired_partitions(subject_rows, self.settings)
        adapters = self.registry.adapters()
        descriptor_by_dataset = {adapter.descriptor.dataset_id: adapter.descriptor for adapter in adapters}
        plans = [(descriptor_by_dataset[item.dataset_id], item) for item in partitions]
        saved = self.store.sync_partitions(
            plans,
            descriptor_by_dataset.keys(),
            now=self.now_provider(),
        )
        self._last_partition_sync_at = self.now_provider()
        return {
            "status": "ok",
            "synced": True,
            "subjectCount": len(subject_rows),
            "partitionCount": len(partitions),
            "savedCount": saved,
        }

    def request_subjects(self, subjects: Iterable[ExternalSubject]) -> Dict[str, object]:
        """Register work without calling a vendor from the requesting path."""
        return self.sync_partitions(subjects=list(subjects or []), force=True)

    def run_once(self, force: bool = False) -> Dict[str, object]:
        if not self.enabled() and not force:
            return {"status": "disabled", "processedCount": 0}
        migration = self.legacy_importer.import_if_empty() if self.legacy_importer else {"status": "not-configured"}
        projection_before = self.reconcile_official_evidence()
        cleanup = self.cleanup_history_if_due(force=force)
        sync = self.sync_partitions(force=force)
        if force and hasattr(self.store, "make_due"):
            self.store.make_due()
        jobs = self.store.claim_due(
            self.worker_id,
            self.batch_size(),
            self.lease_seconds(),
            now=self.now_provider(),
        )
        if not jobs:
            return {
                "status": "idle",
                "processedCount": 0,
                "partitionSync": sync,
                "legacyMigration": migration,
                "historyCleanup": cleanup,
                "officialEvidenceProjection": projection_before,
                "summary": self.store.summary(),
            }
        provider_groups: Dict[str, List[object]] = {}
        for job in jobs:
            provider_groups.setdefault(str(job.provider_id or job.dataset_id), []).append(job)
        groups = list(provider_groups.values())
        if self.concurrency() <= 1 or len(groups) == 1:
            results = [result for group in groups for result in self._process_provider_jobs(group)]
        else:
            results: List[Dict[str, object]] = []
            with ThreadPoolExecutor(max_workers=min(self.concurrency(), len(groups))) as executor:
                futures = {executor.submit(self._process_provider_jobs, group): group for group in groups}
                for future in as_completed(futures):
                    try:
                        results.extend(future.result())
                    except Exception as error:  # noqa: BLE001 - one collection partition cannot stop the batch.
                        for job in futures[future]:
                            results.append({
                                "datasetId": job.dataset_id,
                                "partitionKey": job.partition_key,
                                "status": "error",
                                "error": str(error)[:500],
                            })
        failures = [item for item in results if item.get("status") == "error"]
        deferred = [item for item in results if item.get("status") == "deferred"]
        projection_after = self.reconcile_official_evidence()
        return {
            "status": "partial" if failures else "ok",
            "processedCount": len(results),
            "successCount": len(results) - len(failures) - len(deferred),
            "failureCount": len(failures),
            "deferredCount": len(deferred),
            "results": results,
            "partitionSync": sync,
            "legacyMigration": migration,
            "historyCleanup": cleanup,
            "officialEvidenceProjection": {
                "before": projection_before,
                "after": projection_after,
            },
            "summary": self.store.summary(),
        }

    def reconcile_official_evidence(self) -> Dict[str, object]:
        if not self.evidence_reconciler:
            return {"status": "not-configured", "processedCount": 0, "projectedCount": 0}
        try:
            return dict(self.evidence_reconciler.run_once() or {})
        except Exception as error:  # noqa: BLE001 - durable cursor keeps the failed event replayable.
            return {
                "status": "error",
                "processedCount": 0,
                "projectedCount": 0,
                "reason": str(error)[:500],
            }

    def cleanup_history_if_due(self, force: bool = False) -> Dict[str, object]:
        if not callable(getattr(self.store, "cleanup_history", None)):
            return {"status": "not-supported"}
        interval = integer_setting(self.settings, "externalDataRetentionCheckIntervalSeconds", 21600, 300, 86400)
        current = self.now_provider()
        if not force and self._last_cleanup_at and (current - self._last_cleanup_at).total_seconds() < interval:
            return {"status": "fresh"}
        self._last_cleanup_at = current
        return self.store.cleanup_history(
            run_retention_days=integer_setting(self.settings, "externalDataRunRetentionDays", 30, 1, 365),
            revision_retention_days=integer_setting(self.settings, "externalDataRevisionRetentionDays", 365, 30, 3650),
            batch_size=integer_setting(self.settings, "externalDataRetentionBatchSize", 1000, 10, 10000),
            now=current,
        )

    def _process_provider_jobs(self, jobs) -> List[Dict[str, object]]:
        """Keep each vendor serial while independent vendors run concurrently."""
        return [self._process_job(job) for job in jobs]

    def _process_job(self, job) -> Dict[str, object]:
        adapter = self.registry.adapter(job.dataset_id)
        descriptor = adapter.descriptor
        started = self.now_provider()
        started_at = iso(started)
        reservation = self.store.reserve_provider_call(descriptor, now=started)
        if reservation.get("reason") == "rate-limited" and self.inline_rate_limit_wait_seconds() > 0:
            try:
                retry_at = datetime.fromisoformat(str(reservation.get("nextAllowedAt") or "").replace("Z", "+00:00"))
                delay = max(0.0, (retry_at - utc_now()).total_seconds())
            except (TypeError, ValueError):
                delay = self.inline_rate_limit_wait_seconds() + 1
            if delay <= self.inline_rate_limit_wait_seconds():
                time.sleep(delay + 0.05)
                reservation = self.store.reserve_provider_call(descriptor, now=utc_now())
        if not reservation.get("allowed"):
            next_allowed = str(reservation.get("nextAllowedAt") or next_due_at(descriptor, self.settings, job.partition_key))
            reason = str(reservation.get("reason") or "provider-deferred")
            self.store.defer_job(job, next_allowed, reason)
            self.store.record_run(
                job,
                "deferred",
                started_at,
                iso(self.now_provider()),
                0,
                error_message=reason,
            )
            return {
                "datasetId": job.dataset_id,
                "partitionKey": job.partition_key,
                "status": "deferred",
                "reason": reason,
                "nextDueAt": next_allowed,
            }
        try:
            observation = adapter.fetch(job, self.settings)
            previous = self.store.current_fact(observation.dataset_id, observation.subject_key)
            transition = self.transition_service.assess(
                observation.dataset_id,
                previous,
                observation.payload,
                observation.source_revision,
            )
            event = None
            if transition.material:
                event = external_fact_changed_event(
                    observation.dataset_id,
                    observation.subject_key,
                    observation.provider_id,
                    observation.source_revision,
                    observation.source_as_of,
                    transition.change_type,
                    transition.changed_fields,
                    transition.reason,
                )
            due_at = next_due_at(descriptor, self.settings, job.partition_key)
            committed = self.store.complete_observation(
                job,
                descriptor,
                observation,
                due_at,
                event=event,
            )
            self.store.mark_provider_success(descriptor)
            completed = self.now_provider()
            response_bytes = len(json.dumps(observation.payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            duration_ms = max(0, int((completed - started).total_seconds() * 1000))
            self.store.record_run(
                job,
                "success",
                started_at,
                iso(completed),
                duration_ms,
                response_bytes=response_bytes,
                source_as_of=observation.source_as_of,
                source_revision=observation.source_revision,
                material_change=bool(transition.material and committed.get("changed")),
            )
            return {
                "datasetId": job.dataset_id,
                "partitionKey": job.partition_key,
                "status": "success",
                "durationMs": duration_ms,
                "responseBytes": response_bytes,
                "sourceAsOf": observation.source_as_of,
                "changed": bool(committed.get("changed")),
                "materialChange": bool(transition.material and committed.get("changed")),
                "nextDueAt": due_at,
            }
        except Exception as error:  # noqa: BLE001 - provider failures are durable operational state.
            completed = self.now_provider()
            failure_delay = min(
                descriptor.resolved_cadence_seconds(self.settings),
                max(60, 30 * (2 ** min(6, max(0, job.attempt_count - 1)))),
            )
            due_at = iso(completed + timedelta(seconds=failure_delay))
            failure = self.store.fail_job(job, descriptor, error, due_at)
            duration_ms = max(0, int((completed - started).total_seconds() * 1000))
            self.store.record_run(
                job,
                "error",
                started_at,
                iso(completed),
                duration_ms,
                error_message=str(error)[:500],
            )
            return {
                "datasetId": job.dataset_id,
                "partitionKey": job.partition_key,
                "status": "error",
                "durationMs": duration_ms,
                "error": str(error)[:500],
                "providerState": failure.get("state"),
                "nextDueAt": due_at,
            }

    def status(self) -> Dict[str, object]:
        return {
            "enabled": self.enabled(),
            "workerId": self.worker_id,
            "intervalSeconds": self.interval_seconds(),
            "batchSize": self.batch_size(),
            "concurrency": self.concurrency(),
            "leaseSeconds": self.lease_seconds(),
            "registry": self.registry.descriptors(self.settings),
            "officialEvidenceProjection": dict(getattr(self.evidence_reconciler, "last_result", {}) or {}),
            **self.store.summary(),
        }
