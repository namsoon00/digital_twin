from datetime import datetime, timezone

from .document_recovery import document_recovery_request
from .contracts import bounded_int, setting_enabled


class OfficialDocumentRecoveryService:
    """Recover missing bodies through the existing document queue and projector."""

    def __init__(self, settings, candidate_reader, store, registry, projector, access_ready, now=None):
        self.settings = settings
        self.candidate_reader = candidate_reader
        self.store = store
        self.registry = registry
        self.projector = projector
        self.access_ready = access_ready
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.last_run = None
        self.after_id = ""
        self.last_result = {"status": "not-run"}

    def run_once(self, force=False):
        if not setting_enabled(self.settings, "externalDocumentRecoveryEnabled", True):
            return {"status": "disabled"}
        now = self.now()
        if not force and self.last_run and (now - self.last_run).total_seconds() < 900:
            return self.last_result
        # Back off failed scans too; the durable document queue owns vendor retries.
        self.last_run = now
        self.last_result = {"status": "running"}
        limit = bounded_int(self.settings.get("externalDocumentRecoveryBatchSize"), 25, 1, 100)
        candidates = self.candidate_reader(limit=limit, after_id=self.after_id)
        result = {"status": "ok", "scannedCount": len(candidates), "queuedCount": 0, "restoredCount": 0, "blockedCount": 0, "invalidCount": 0, "deferredCount": 0, "reasons": []}
        plans = []
        replay_attempted = False
        for item in candidates:
            request = document_recovery_request(item)
            if not request:
                result["invalidCount"] += 1
                continue
            descriptor = self.registry.adapter(request.dataset_id).descriptor
            if not descriptor.enabled(self.settings):
                result["blockedCount"] += 1
                result["reasons"].append(request.dataset_id + ":disabled")
                continue
            # Already collected bodies can be replayed even if vendor access is now unavailable.
            fact = self.store.retained_document_fact(request)
            if fact:
                # One existing body may invoke AI. Do not run a whole historical AI batch inline.
                if replay_attempted:
                    result["deferredCount"] += 1
                    continue
                replay_attempted = True
                restored = self.projector.project_fact(fact, allow_alert=False)
                result["restoredCount"] += int(restored.get("writtenCount") or 0)
                continue
            if not self.access_ready(request.dataset_id):
                result["blockedCount"] += 1
                result["reasons"].append(request.dataset_id + ":contact-or-key-required")
                continue
            plans.append((descriptor, request))
        result["queuedCount"] = int(self.store.enqueue_followups(plans, now=now) or 0)
        self.after_id = candidates[-1].evidence_id if len(candidates) == limit else ""
        self.last_run = now
        result["reasons"] = sorted(set(result["reasons"]))
        self.last_result = result
        return result

    def record_failure(self, error):
        self.last_result = {"status": "error", "reason": str(error)[:240]}
